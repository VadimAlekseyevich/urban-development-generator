from __future__ import annotations

import hashlib
import math
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, cast

import pandas as pd
from geoalchemy2.shape import from_shape
from pyproj import CRS
from pyproj.exceptions import CRSError
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry
from sqlalchemy import Table, delete, func, insert, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.db.session import SessionLocal
from backend.app.models.dataset import Dataset, DatasetVersion
from backend.app.models.project import Project
from backend.app.models.source_layer import (
    SourceBuilding,
    SourceConstraint,
    SourceFacility,
    SourceLanduse,
    SourceRoad,
    SourceWater,
)
from backend.app.services.vector_normalization import NormalizedVectorBatch
from core.urban_generator.domain import DataError


class SourceLayerPersistenceError(DataError):
    """Base error for canonical source-layer batch persistence."""


class SourceLayerImmutableError(SourceLayerPersistenceError):
    """Raised before writing rows owned by a ready DatasetVersion."""


class CanonicalSourceLayer(StrEnum):
    ROADS = "roads"
    BUILDINGS = "buildings"
    LANDUSE = "landuse"
    WATER = "water"
    FACILITIES = "facilities"
    CONSTRAINTS = "constraints"


class PostLoadAnalyzePolicy(StrEnum):
    NEVER = "never"
    IF_ROWS = "if_rows"
    ALWAYS = "always"


@dataclass(frozen=True, slots=True)
class SourceLayerWriteResult:
    dataset_version_id: uuid.UUID
    layer: CanonicalSourceLayer
    working_srid: int
    deleted_rows: int
    inserted_rows: int
    input_batches: int
    insert_statements: int
    analyzed: bool


@dataclass(frozen=True, slots=True)
class _LayerSpec:
    table: Table
    required_columns: frozenset[str]
    canonical_columns: frozenset[str]
    defaults: Mapping[str, object]
    force_multiline: bool = False
    force_multipolygon: bool = False


_LAYER_SPECS: dict[CanonicalSourceLayer, _LayerSpec] = {
    CanonicalSourceLayer.ROADS: _LayerSpec(
        table=cast(Table, SourceRoad.__table__),
        required_columns=frozenset({"road_class"}),
        canonical_columns=frozenset(
            {
                "road_class",
                "name",
                "lanes",
                "max_speed_kph",
                "one_way",
                "one_way_direction",
                "bridge",
                "tunnel",
                "layer",
            }
        ),
        defaults={
            "name": None,
            "lanes": None,
            "max_speed_kph": None,
            "one_way": False,
            "one_way_direction": "both",
            "bridge": False,
            "tunnel": False,
            "layer": 0,
        },
        force_multiline=True,
    ),
    CanonicalSourceLayer.BUILDINGS: _LayerSpec(
        table=cast(Table, SourceBuilding.__table__),
        required_columns=frozenset({"building_class"}),
        canonical_columns=frozenset(
            {"building_class", "name", "levels", "height_m"}
        ),
        defaults={"name": None, "levels": None, "height_m": None},
        force_multipolygon=True,
    ),
    CanonicalSourceLayer.LANDUSE: _LayerSpec(
        table=cast(Table, SourceLanduse.__table__),
        required_columns=frozenset({"landuse_class"}),
        canonical_columns=frozenset({"landuse_class"}),
        defaults={},
        force_multipolygon=True,
    ),
    CanonicalSourceLayer.WATER: _LayerSpec(
        table=cast(Table, SourceWater.__table__),
        required_columns=frozenset({"water_class"}),
        canonical_columns=frozenset({"water_class"}),
        defaults={},
    ),
    CanonicalSourceLayer.FACILITIES: _LayerSpec(
        table=cast(Table, SourceFacility.__table__),
        required_columns=frozenset({"facility_class"}),
        canonical_columns=frozenset({"facility_class", "name", "capacity"}),
        defaults={"name": None, "capacity": None},
    ),
    CanonicalSourceLayer.CONSTRAINTS: _LayerSpec(
        table=cast(Table, SourceConstraint.__table__),
        required_columns=frozenset({"constraint_code", "severity", "scope"}),
        canonical_columns=frozenset({"constraint_code", "severity", "scope"}),
        defaults={},
    ),
}

_RESERVED_INPUT_COLUMNS = frozenset(
    {"geometry", "source_feature_id", "attributes_json"}
)


class SqlAlchemySourceLayerBatchWriter:
    """Replace one canonical source layer atomically from normalized vector batches.

    A fresh SQLAlchemy Session is created per call, so deletion of a retry's previous rows,
    all bounded bulk INSERT statements, and optional ANALYZE belong to one transaction.
    If any batch fails, PostgreSQL restores the previous committed layer contents.
    """

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session] = SessionLocal,
        max_insert_rows: int = 5000,
        analyze_policy: PostLoadAnalyzePolicy = PostLoadAnalyzePolicy.IF_ROWS,
    ) -> None:
        if (
            isinstance(max_insert_rows, bool)
            or not isinstance(max_insert_rows, int)
            or max_insert_rows <= 0
        ):
            raise ValueError("max_insert_rows must be a positive integer")
        self._session_factory = session_factory
        self._max_insert_rows = max_insert_rows
        self._analyze_policy = analyze_policy

    def replace(
        self,
        *,
        dataset_version_id: uuid.UUID,
        layer: CanonicalSourceLayer,
        batches: Iterable[NormalizedVectorBatch],
    ) -> SourceLayerWriteResult:
        if not isinstance(dataset_version_id, uuid.UUID):
            raise TypeError("dataset_version_id must be UUID")
        spec = _LAYER_SPECS[layer]

        try:
            with self._session_factory() as session:
                with session.begin():
                    status, working_srid = self._load_dataset_version_context(
                        session, dataset_version_id
                    )
                    if status == "ready":
                        raise SourceLayerImmutableError(
                            "canonical source rows of a ready dataset version are immutable"
                        )

                    deleted_rows = int(
                        session.scalar(
                            select(func.count())
                            .select_from(spec.table)
                            .where(spec.table.c.dataset_version_id == dataset_version_id)
                        )
                        or 0
                    )
                    session.execute(
                        delete(spec.table).where(
                            spec.table.c.dataset_version_id == dataset_version_id
                        )
                    )

                    inserted_rows = 0
                    input_batches = 0
                    insert_statements = 0
                    for batch in batches:
                        input_batches += 1
                        rows = self._rows_from_batch(
                            batch,
                            dataset_version_id=dataset_version_id,
                            working_srid=working_srid,
                            spec=spec,
                        )
                        for start in range(0, len(rows), self._max_insert_rows):
                            chunk = rows[start : start + self._max_insert_rows]
                            if not chunk:
                                continue
                            session.execute(insert(spec.table), chunk)
                            inserted_rows += len(chunk)
                            insert_statements += 1

                    analyzed = self._should_analyze(inserted_rows)
                    if analyzed:
                        session.execute(text(f'ANALYZE "{spec.table.name}"'))

            return SourceLayerWriteResult(
                dataset_version_id=dataset_version_id,
                layer=layer,
                working_srid=working_srid,
                deleted_rows=deleted_rows,
                inserted_rows=inserted_rows,
                input_batches=input_batches,
                insert_statements=insert_statements,
                analyzed=analyzed,
            )
        except SourceLayerPersistenceError:
            raise
        except SQLAlchemyError as exc:
            raise SourceLayerPersistenceError(
                f"unable to persist canonical source layer {layer.value!r}"
            ) from exc

    @staticmethod
    def _load_dataset_version_context(
        session: Session,
        dataset_version_id: uuid.UUID,
    ) -> tuple[str, int]:
        statement = (
            select(DatasetVersion.status, Project.working_srid)
            .join(Dataset, Dataset.id == DatasetVersion.dataset_id)
            .join(Project, Project.id == Dataset.project_id)
            .where(DatasetVersion.id == dataset_version_id)
        )
        row = session.execute(statement).one_or_none()
        if row is None:
            raise SourceLayerPersistenceError(
                f"dataset version {dataset_version_id} does not exist"
            )
        status = str(row[0])
        working_srid = int(row[1])
        if working_srid <= 0:
            raise SourceLayerPersistenceError(
                "project working_srid must be a positive integer"
            )
        return status, working_srid

    def _rows_from_batch(
        self,
        batch: NormalizedVectorBatch,
        *,
        dataset_version_id: uuid.UUID,
        working_srid: int,
        spec: _LayerSpec,
    ) -> list[dict[str, object]]:
        if batch.working_srid != working_srid:
            raise SourceLayerPersistenceError(
                f"batch working_srid {batch.working_srid} does not match "
                f"project working_srid {working_srid}"
            )
        frame = batch.frame
        if frame.crs is None:
            raise SourceLayerPersistenceError(
                f"normalized batch {batch.layer_name!r} has no CRS"
            )
        try:
            frame_crs = CRS.from_user_input(frame.crs)
            target_crs = CRS.from_epsg(working_srid)
        except CRSError as exc:
            raise SourceLayerPersistenceError(
                f"unable to validate normalized batch CRS for {batch.layer_name!r}"
            ) from exc
        if frame_crs != target_crs:
            raise SourceLayerPersistenceError(
                f"normalized batch {batch.layer_name!r} CRS does not match EPSG:{working_srid}"
            )

        geometry_column = frame.geometry.name
        columns = tuple(str(column) for column in frame.columns)
        if geometry_column not in frame.columns:
            raise SourceLayerPersistenceError(
                f"normalized batch {batch.layer_name!r} has no active geometry column"
            )

        missing_required = spec.required_columns.difference(columns)
        if missing_required:
            missing = ", ".join(sorted(missing_required))
            raise SourceLayerPersistenceError(
                f"normalized batch {batch.layer_name!r} is missing canonical columns: {missing}"
            )

        rows: list[dict[str, object]] = []
        for local_position, values in enumerate(frame.itertuples(index=False, name=None)):
            source = dict(zip(columns, values, strict=True))
            geometry_value = source.pop(geometry_column)
            if not isinstance(geometry_value, BaseGeometry):
                raise SourceLayerPersistenceError(
                    f"normalized batch {batch.layer_name!r} contains non-geometry value"
                )
            geometry = _coerce_geometry(geometry_value, spec=spec)

            explicit_attributes = source.pop("attributes_json", None)
            source_feature_id = _source_feature_id(
                source.pop("source_feature_id", None),
                layer_name=batch.layer_name,
                position=batch.start_feature + local_position,
            )

            typed: dict[str, object] = {}
            for column in spec.canonical_columns:
                if column in source:
                    value = _python_scalar(source.pop(column))
                else:
                    value = spec.defaults.get(column)
                if column in spec.required_columns and value is None:
                    raise SourceLayerPersistenceError(
                        f"canonical column {column!r} is null in layer {batch.layer_name!r}"
                    )
                typed[column] = value

            attributes = {
                key: _json_value(value, context=f"{batch.layer_name}.{key}")
                for key, value in source.items()
                if key not in _RESERVED_INPUT_COLUMNS
            }
            if explicit_attributes is not None:
                if not isinstance(explicit_attributes, Mapping):
                    raise SourceLayerPersistenceError(
                        "attributes_json must be a mapping when provided"
                    )
                attributes.update(
                    {
                        str(key): _json_value(
                            value,
                            context=f"{batch.layer_name}.attributes_json.{key}",
                        )
                        for key, value in explicit_attributes.items()
                    }
                )

            row: dict[str, object] = {
                "id": uuid.uuid4(),
                "dataset_version_id": dataset_version_id,
                "source_feature_id": source_feature_id,
                "attributes_json": attributes,
                "geometry": from_shape(geometry, srid=working_srid),
            }
            row.update(typed)
            rows.append(row)
        return rows

    def _should_analyze(self, inserted_rows: int) -> bool:
        if self._analyze_policy is PostLoadAnalyzePolicy.NEVER:
            return False
        if self._analyze_policy is PostLoadAnalyzePolicy.ALWAYS:
            return True
        return inserted_rows > 0


def _source_feature_id(
    value: object,
    *,
    layer_name: str,
    position: int,
) -> str:
    scalar = _python_scalar(value)
    if scalar is None:
        layer_hash = hashlib.sha256(layer_name.encode("utf-8")).hexdigest()[:12]
        return f"{layer_hash}:{position}"
    result = str(scalar).strip()
    if not result:
        raise SourceLayerPersistenceError("source_feature_id must not be blank")
    if len(result) > 255:
        raise SourceLayerPersistenceError("source_feature_id must be at most 255 characters")
    return result


def _coerce_geometry(geometry: BaseGeometry, *, spec: _LayerSpec) -> BaseGeometry:
    if geometry.is_empty or not geometry.is_valid:
        raise SourceLayerPersistenceError(
            "persistence accepts only non-empty valid normalized geometries"
        )
    if spec.force_multiline:
        if isinstance(geometry, LineString):
            return MultiLineString([geometry])
        if isinstance(geometry, MultiLineString):
            return geometry
        raise SourceLayerPersistenceError(
            f"canonical line layer cannot store geometry type {geometry.geom_type!r}"
        )
    if spec.force_multipolygon:
        if isinstance(geometry, Polygon):
            return MultiPolygon([geometry])
        if isinstance(geometry, MultiPolygon):
            return geometry
        raise SourceLayerPersistenceError(
            f"canonical polygon layer cannot store geometry type {geometry.geom_type!r}"
        )
    return geometry


def _python_scalar(value: object) -> object:
    if _is_missing_scalar(value):
        return None
    item = getattr(value, "item", None)
    if callable(item) and not isinstance(value, (str, bytes, bytearray)):
        try:
            converted = item()
        except (TypeError, ValueError):
            return value
        if converted is not value:
            return _python_scalar(converted)
    return value


def _is_missing_scalar(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, (Mapping, list, tuple, set, frozenset)):
        return False
    try:
        missing = pd.isna(value)
    except (TypeError, ValueError):
        return False
    if isinstance(missing, bool):
        return missing
    item = getattr(missing, "item", None)
    if callable(item):
        try:
            return bool(item())
        except (TypeError, ValueError):
            return False
    return False


def _json_value(value: object, *, context: str) -> Any:
    scalar = _python_scalar(value)
    if scalar is None or isinstance(scalar, (str, bool, int)):
        return scalar
    if isinstance(scalar, float):
        if not math.isfinite(scalar):
            raise SourceLayerPersistenceError(
                f"non-finite numeric attribute is not JSON-safe: {context}"
            )
        return scalar
    if isinstance(scalar, Decimal):
        converted = float(scalar)
        if not math.isfinite(converted):
            raise SourceLayerPersistenceError(
                f"non-finite decimal attribute is not JSON-safe: {context}"
            )
        return converted
    if isinstance(scalar, (datetime, date)):
        return scalar.isoformat()
    if isinstance(scalar, Mapping):
        return {
            str(key): _json_value(item, context=f"{context}.{key}")
            for key, item in scalar.items()
        }
    if isinstance(scalar, (list, tuple)):
        return [
            _json_value(item, context=f"{context}[]")
            for item in scalar
        ]
    raise SourceLayerPersistenceError(
        f"attribute {context!r} has unsupported JSON value type {type(scalar).__name__}"
    )
