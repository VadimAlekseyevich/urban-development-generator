"""PostGIS MVT implementation reusing the S13-T02 fixed 15-layer SQL allowlist.

The database first applies a transformed tile-envelope GiST prefilter,
exact ST_Intersects, owner predicate, stable id order and LIMIT N+1 in a
materialized CTE. Only the first N candidates undergo ST_AsMVTGeom/AsMVT.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import String, func, literal, literal_column, select
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from backend.app.application.layer_catalog import LayerCatalogEntry, LayerOwnerScope
from backend.app.application.mvt_tiles import (
    MVT_BUFFER,
    MVT_EXTENT,
    MVT_STATEMENT_TIMEOUT_MS,
)
from backend.app.db.vector_layer_query_repository import (
    _VECTOR_TABLES,
    SqlAlchemyVectorLayerRepository,
)
from backend.app.models.generation_run import generation_run_dataset_versions


class SqlAlchemyMvtTileRepository(SqlAlchemyVectorLayerRepository):
    """Fixed-table scoped MVT adapter with bounded source rows and SQL timeout."""

    def __init__(self, session: Session) -> None:
        super().__init__(session)
        self._session = session

    def encode_tile(
        self,
        *,
        entry: LayerCatalogEntry,
        working_srid: int,
        z: int,
        x: int,
        y: int,
        feature_limit: int,
    ) -> tuple[bytes, int]:
        spec = _VECTOR_TABLES[entry.definition.layer_id]
        table = spec.table
        owner = entry.owner
        mercator_tile = func.ST_TileEnvelope(z, x, y)
        tile_buffer_m = (
            func.ST_XMax(mercator_tile) - func.ST_XMin(mercator_tile)
        ) * (MVT_BUFFER / MVT_EXTENT)
        buffered_bounds = func.ST_Expand(mercator_tile, tile_buffer_m)
        working_bounds = func.ST_Transform(buffered_bounds, working_srid)
        original = table.c.geometry

        columns: list[ColumnElement[Any]] = [
            table.c.id,
            original.label("geometry"),
            table.c.attributes_json,
            *(
                table.c[name] for name in spec.columns if name != "diagnostics_json"
            ),
        ]
        if spec.source:
            columns.append(table.c.source_feature_id)
        if entry.definition.layer_id == "run.existing_facilities":
            columns.append(table.c.dataset_version_id)

        statement = select(*columns)
        if owner.scope is LayerOwnerScope.DATASET_VERSION:
            statement = statement.where(
                table.c.dataset_version_id == owner.dataset_version_id,
            )
        elif entry.definition.layer_id == "run.existing_facilities":
            statement = statement.join(
                generation_run_dataset_versions,
                generation_run_dataset_versions.c.dataset_version_id
                == table.c.dataset_version_id,
            ).where(generation_run_dataset_versions.c.run_id == owner.run_id)
        else:
            statement = statement.where(table.c.run_id == owner.run_id)

        statement = statement.where(
            original.op("&&")(working_bounds),
            func.ST_Intersects(original, working_bounds),
        )
        if spec.derived_json_key is not None:
            statement = statement.where(
                func.jsonb_typeof(
                    table.c.attributes_json.op("->")(spec.derived_json_key),
                ) == "object"
            )

        # MATERIALIZED ensures that geometry encoding and row counting share
        # a strictly bounded candidate set. No whole-layer ST_AsMVT or OFFSET.
        candidates = (
            statement.order_by(table.c.id)
            .limit(feature_limit + 1)
            .cte("tile_candidates")
            .prefix_with("MATERIALIZED", dialect="postgresql")
        )
        visible = (
            select(candidates)
            .order_by(candidates.c.id)
            .limit(feature_limit)
            .subquery("tile_visible")
        )
        attributes: ColumnElement[Any] = visible.c.attributes_json
        if spec.derived_json_key is not None:
            attributes = visible.c.attributes_json.op("->")(
                literal(spec.derived_json_key)
            )
        fields: list[ColumnElement[Any]] = [
            visible.c.id.cast(String).label("feature_id"),
        ]
        for name in spec.columns:
            if name == "diagnostics_json":
                continue  # nested diagnostics do not belong in flat MVT props
            column: ColumnElement[Any] = visible.c[name]
            if isinstance(column.type, PgUUID):
                column = column.cast(String)
            fields.append(column.label(name))
        if spec.source:
            fields.append(visible.c.source_feature_id)
        if entry.definition.layer_id == "run.existing_facilities":
            fields.extend(
                (
                    visible.c.dataset_version_id.cast(String).label(
                        "dataset_version_id"
                    ),
                    literal("existing").label("origin"),
                )
            )
        if entry.definition.layer_id == "generated.facilities":
            fields.append(literal("generated").label("origin"))
        fields.append(attributes.label("properties"))

        # MVT geom operates on Web Mercator, with the tile envelope defining
        # clipping and the buffer keeping features crossing adjacent edges.
        fields.append(
            func.ST_AsMVTGeom(
                func.ST_Transform(visible.c.geometry, 3857),
                mercator_tile,
                MVT_EXTENT,
                MVT_BUFFER,
                True,
            ).label("geom")
        )
        mvt_rows = select(*fields).select_from(visible).subquery("mvt_rows")
        count = (
            select(func.count())
            .select_from(candidates)
            .scalar_subquery()
            .label("candidate_count")
        )
        # The fixed quoted alias denotes the row record required by AsMVT,
        # not any user-controlled SQL or dynamic table identifier.
        encoded = func.ST_AsMVT(
            literal_column("mvt_rows"),
            entry.definition.layer_id,
            MVT_EXTENT,
            "geom",
        ).label("tile_data")

        # This session's active transaction only; other API requests retain
        # their own timeout configuration and connection-pool defaults.
        self._session.scalar(
            select(func.set_config(
                "statement_timeout", str(MVT_STATEMENT_TIMEOUT_MS), True,
            ))
        )
        row = self._session.execute(
            select(encoded, count).select_from(mvt_rows)
        ).mappings().one()
        raw: object = row["tile_data"]
        if raw is None:
            payload = b""
        elif isinstance(raw, (bytes, bytearray, memoryview)):
            payload = bytes(raw)
        else:
            raise ValueError("PostGIS returned a non-binary MVT payload")
        return payload, int(row["candidate_count"])
