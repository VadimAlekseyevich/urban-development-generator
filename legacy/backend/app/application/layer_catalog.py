"""Canonical presentation-layer catalog contract (S13-T01).

This is *application* metadata: core Stage/Snapshot/Metric identities and
persistence remain authoritative. Definitions declare already implemented
delivery routes without querying them or claiming that a read model exists.
The next S13 tasks add generic bbox/MVT delivery and declarative UI adapters.
"""

from __future__ import annotations

import math
import re
import uuid
from dataclasses import dataclass
from enum import StrEnum
from string import Formatter

LAYER_CATALOG_SCHEMA_VERSION = "layer-catalog-v1"
_LAYER_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_DEFINITION_VERSION_RE = re.compile(r"^[1-9][0-9]*$")


class LayerCatalogError(ValueError):
    """An owner, definition, metadata or catalog violates the public contract."""


class LayerOwnerScope(StrEnum):
    PROJECT = "project"
    DATASET_VERSION = "dataset_version"
    RUN = "run"
    ARTIFACT = "artifact"


class LayerSourceKind(StrEnum):
    SOURCE = "source"
    GENERATED = "generated"
    VALIDATION = "validation"
    ANALYSIS = "analysis"


class LayerGeometryKind(StrEnum):
    POINT = "point"
    LINE = "line"
    POLYGON = "polygon"
    MIXED = "mixed"
    RASTER = "raster"


class LayerDeliveryKind(StrEnum):
    PROJECT_GEOJSON = "project_geojson"
    BBOX_GEOJSON = "bbox_geojson"
    ARTIFACT_IMAGE = "artifact_image"


_ALLOWED_ORIGIN: frozenset[tuple[LayerOwnerScope, LayerSourceKind]] = frozenset(
    {
        (LayerOwnerScope.PROJECT, LayerSourceKind.SOURCE),
        (LayerOwnerScope.DATASET_VERSION, LayerSourceKind.SOURCE),
        (LayerOwnerScope.RUN, LayerSourceKind.SOURCE),  # existing facilities in run read model
        (LayerOwnerScope.RUN, LayerSourceKind.GENERATED),
        (LayerOwnerScope.RUN, LayerSourceKind.VALIDATION),
        (LayerOwnerScope.ARTIFACT, LayerSourceKind.ANALYSIS),
    }
)
_OWNER_FIELDS: dict[LayerOwnerScope, frozenset[str]] = {
    LayerOwnerScope.PROJECT: frozenset({"project_id"}),
    LayerOwnerScope.DATASET_VERSION: frozenset({"project_id", "dataset_version_id"}),
    LayerOwnerScope.RUN: frozenset({"project_id", "run_id"}),
    LayerOwnerScope.ARTIFACT: frozenset({"artifact_id"}),
}


def _require_uuid(name: str, value: object, *, required: bool) -> None:
    if value is None and not required:
        return
    if not isinstance(value, uuid.UUID):
        raise LayerCatalogError(f"{name} must be a UUID" + ("" if required else " or None"))


@dataclass(frozen=True, slots=True)
class LayerOwnerRef:
    """Exact persistence identity, never a mixed run/dataset/artifact fallback.

    A suitability artifact currently has its own global API and is not falsely
    assigned to a project until an authoritative ownership link exists.
    """

    scope: LayerOwnerScope
    project_id: uuid.UUID | None = None
    dataset_version_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None
    artifact_id: uuid.UUID | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.scope, LayerOwnerScope):
            raise LayerCatalogError("owner scope must be LayerOwnerScope")
        fields = _OWNER_FIELDS[self.scope]
        for name in ("project_id", "dataset_version_id", "run_id", "artifact_id"):
            value = getattr(self, name)
            _require_uuid(name, value, required=name in fields)
            if name not in fields and value is not None:
                raise LayerCatalogError(f"{self.scope.value} owner cannot set {name}")

    @property
    def identity(self) -> str:
        """Stable distinct owner key used in frontend caching and future tiles."""

        return ":".join(
            str(getattr(self, name))
            for name in ("project_id", "dataset_version_id", "run_id", "artifact_id")
            if getattr(self, name) is not None
        )

    def path_params(self) -> dict[str, str]:
        return {
            name: str(getattr(self, name))
            for name in _OWNER_FIELDS[self.scope]
        }


@dataclass(frozen=True, slots=True)
class LayerRenderMetadata:
    """UI-neutral render hints; S13-T05 owns concrete MapLibre style expressions."""

    style_key: str
    legend_key: str
    z_index: int
    default_visible: bool
    default_opacity: float

    def __post_init__(self) -> None:
        for name in ("style_key", "legend_key"):
            token = getattr(self, name)
            if not isinstance(token, str) or _LAYER_ID_RE.fullmatch(token) is None:
                raise LayerCatalogError(f"{name} must be a stable lowercase token")
        if isinstance(self.z_index, bool) or not isinstance(self.z_index, int):
            raise LayerCatalogError("z_index must be an integer")
        if not 0 <= self.z_index <= 1000:
            raise LayerCatalogError("z_index must be 0–1000")
        if not isinstance(self.default_visible, bool):
            raise LayerCatalogError("default_visible must be bool")
        if (
            isinstance(self.default_opacity, bool)
            or not isinstance(self.default_opacity, (int, float))
            or not math.isfinite(self.default_opacity)
            or not 0.0 <= self.default_opacity <= 1.0
        ):
            raise LayerCatalogError("default_opacity must be a finite ratio 0–1")


@dataclass(frozen=True, slots=True)
class LayerDefinition:
    """Stable logical layer and its existing read route, independent of owner UUIDs."""

    layer_id: str
    definition_version: str
    owner_scope: LayerOwnerScope
    source_kind: LayerSourceKind
    geometry_kind: LayerGeometryKind
    delivery_kind: LayerDeliveryKind
    path_template: str
    render: LayerRenderMetadata
    max_features: int | None = None
    metadata_path_template: str | None = None
    presentation_crs: str = "EPSG:4326"

    def __post_init__(self) -> None:
        if not isinstance(self.layer_id, str) or _LAYER_ID_RE.fullmatch(self.layer_id) is None:
            raise LayerCatalogError("layer_id must be a stable lowercase token")
        if (
            not isinstance(self.definition_version, str)
            or _DEFINITION_VERSION_RE.fullmatch(self.definition_version) is None
        ):
            raise LayerCatalogError("definition_version must be a positive decimal version")
        if not isinstance(self.owner_scope, LayerOwnerScope):
            raise LayerCatalogError("owner_scope must be LayerOwnerScope")
        if not isinstance(self.source_kind, LayerSourceKind):
            raise LayerCatalogError("source_kind must be LayerSourceKind")
        if (self.owner_scope, self.source_kind) not in _ALLOWED_ORIGIN:
            raise LayerCatalogError("layer source kind is incompatible with owner scope")
        if not isinstance(self.geometry_kind, LayerGeometryKind):
            raise LayerCatalogError("geometry_kind must be LayerGeometryKind")
        if not isinstance(self.delivery_kind, LayerDeliveryKind):
            raise LayerCatalogError("delivery_kind must be LayerDeliveryKind")
        if not isinstance(self.render, LayerRenderMetadata):
            raise LayerCatalogError("render must be LayerRenderMetadata")
        if self.presentation_crs != "EPSG:4326":
            raise LayerCatalogError("map presentation coordinates must use EPSG:4326")

        if self.delivery_kind is LayerDeliveryKind.BBOX_GEOJSON:
            if (
                isinstance(self.max_features, bool)
                or not isinstance(self.max_features, int)
                or not 1 <= self.max_features <= 5000
            ):
                raise LayerCatalogError("bbox GeoJSON must declare a bounded max_features")
        elif self.max_features is not None:
            raise LayerCatalogError("non-bbox delivery cannot declare max_features")

        if (
            self.delivery_kind is LayerDeliveryKind.PROJECT_GEOJSON
            and self.owner_scope is not LayerOwnerScope.PROJECT
        ):
            raise LayerCatalogError("project GeoJSON must have project owner")
        if (
            self.delivery_kind is LayerDeliveryKind.BBOX_GEOJSON
            and self.owner_scope not in {LayerOwnerScope.DATASET_VERSION, LayerOwnerScope.RUN}
        ):
            raise LayerCatalogError("bbox GeoJSON must have dataset-version or run owner")

        if self.delivery_kind is LayerDeliveryKind.ARTIFACT_IMAGE:
            if self.owner_scope is not LayerOwnerScope.ARTIFACT:
                raise LayerCatalogError("artifact image must be artifact-owned")
            if self.geometry_kind is not LayerGeometryKind.RASTER:
                raise LayerCatalogError("artifact image must have raster geometry")
            if self.metadata_path_template is None:
                raise LayerCatalogError("artifact preview requires georeferencing metadata route")
        elif (
            self.geometry_kind is LayerGeometryKind.RASTER
            or self.metadata_path_template is not None
        ):
            raise LayerCatalogError("GeoJSON must be vector and has no raster metadata route")

        _check_route(self.path_template, _OWNER_FIELDS[self.owner_scope])
        if self.metadata_path_template is not None:
            _check_route(self.metadata_path_template, _OWNER_FIELDS[self.owner_scope])


def _check_route(template: str, required_fields: frozenset[str]) -> None:
    """No arbitrary URLs, formatting directives or cross-owner template fields."""

    if (
        not isinstance(template, str)
        or not template.startswith("/")
        or "://" in template
        or ".." in template
        or "#" in template
    ):
        raise LayerCatalogError("delivery route must be a local, fixed API path")
    fields: list[str] = []
    try:
        parts = tuple(Formatter().parse(template))
    except ValueError as exc:
        raise LayerCatalogError("invalid delivery route template") from exc
    for _literal, field_name, format_spec, conversion in parts:
        if field_name is not None:
            if format_spec or conversion or field_name not in required_fields:
                raise LayerCatalogError("route contains a forbidden owner field/format")
            fields.append(field_name)
    if set(fields) != required_fields or len(fields) != len(required_fields):
        raise LayerCatalogError("route must contain exactly its owner's UUID fields")


@dataclass(frozen=True, slots=True)
class LayerCatalogEntry:
    definition: LayerDefinition
    owner: LayerOwnerRef

    def __post_init__(self) -> None:
        if not isinstance(self.definition, LayerDefinition):
            raise LayerCatalogError("definition must be LayerDefinition")
        if not isinstance(self.owner, LayerOwnerRef):
            raise LayerCatalogError("owner must be LayerOwnerRef")
        if self.definition.owner_scope is not self.owner.scope:
            raise LayerCatalogError("layer owner does not match definition scope")

    @property
    def instance_key(self) -> str:
        return f"{self.definition.layer_id}@{self.owner.scope.value}:{self.owner.identity}"

    @property
    def path(self) -> str:
        return self.definition.path_template.format(**self.owner.path_params())

    @property
    def metadata_path(self) -> str | None:
        template = self.definition.metadata_path_template
        return template.format(**self.owner.path_params()) if template is not None else None


@dataclass(frozen=True, slots=True)
class LayerCatalog:
    """Deterministic, bounded, immutable catalog for an explicitly selected context."""

    entries: tuple[LayerCatalogEntry, ...]
    schema_version: str = LAYER_CATALOG_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != LAYER_CATALOG_SCHEMA_VERSION:
            raise LayerCatalogError("unsupported catalog schema version")
        if not isinstance(self.entries, tuple):
            raise LayerCatalogError("catalog entries must be a tuple")
        if len(self.entries) > len(CANONICAL_LAYER_DEFINITIONS):
            raise LayerCatalogError("catalog exceeds canonical definition count")
        if any(not isinstance(entry, LayerCatalogEntry) for entry in self.entries):
            raise LayerCatalogError("catalog contains a non-entry value")
        keys = tuple(entry.instance_key for entry in self.entries)
        if len(keys) != len(set(keys)):
            raise LayerCatalogError("duplicate owner-qualified layer identity")
        logical_ids = tuple(entry.definition.layer_id for entry in self.entries)
        if len(logical_ids) != len(set(logical_ids)):
            raise LayerCatalogError("duplicate logical layer across different owners")
        canonical_order = {
            definition.layer_id: i
            for i, definition in enumerate(CANONICAL_LAYER_DEFINITIONS)
        }
        positions: list[int] = []
        for entry in self.entries:
            position = canonical_order.get(entry.definition.layer_id)
            known = CANONICAL_LAYER_DEFINITIONS[position] if position is not None else None
            if entry.definition != known:
                raise LayerCatalogError("catalog entry is not a canonical layer definition")
            positions.append(canonical_order[entry.definition.layer_id])
        if positions != sorted(positions):
            raise LayerCatalogError("catalog entries must use canonical rendering order")

    def get(self, layer_id: str) -> LayerCatalogEntry | None:
        return next(
            (entry for entry in self.entries if entry.definition.layer_id == layer_id),
            None,
        )


def _render(
    token: str,
    z_index: int,
    *,
    visible: bool = True,
    opacity: float = 1.0,
) -> LayerRenderMetadata:
    return LayerRenderMetadata(
        style_key=token,
        legend_key=token,
        z_index=z_index,
        default_visible=visible,
        default_opacity=opacity,
    )


def _definition(
    layer_id: str,
    scope: LayerOwnerScope,
    source: LayerSourceKind,
    geometry: LayerGeometryKind,
    path: str,
    render: LayerRenderMetadata,
    *,
    max_features: int | None = 5000,
    metadata_path: str | None = None,
    delivery: LayerDeliveryKind = LayerDeliveryKind.BBOX_GEOJSON,
) -> LayerDefinition:
    return LayerDefinition(
        layer_id=layer_id,
        definition_version="1",
        owner_scope=scope,
        source_kind=source,
        geometry_kind=geometry,
        delivery_kind=delivery,
        path_template=path,
        render=render,
        max_features=max_features,
        metadata_path_template=metadata_path,
    )


_PROJECT = LayerOwnerScope.PROJECT
_DATASET = LayerOwnerScope.DATASET_VERSION
_RUN = LayerOwnerScope.RUN
_ARTIFACT = LayerOwnerScope.ARTIFACT
_SOURCE = LayerSourceKind.SOURCE
_GENERATED = LayerSourceKind.GENERATED
_VALIDATION = LayerSourceKind.VALIDATION
_ANALYSIS = LayerSourceKind.ANALYSIS
_POLYGON = LayerGeometryKind.POLYGON
_LINE = LayerGeometryKind.LINE
_MIXED = LayerGeometryKind.MIXED
_BBOX = "/projects/{project_id}/dataset-versions/{dataset_version_id}/source-layers/"
_RUN_PATH = "/projects/{project_id}/"
_MAX_VALIDATION_GEOJSON = 1000

# This is a registry of delivered read contracts, NOT an availability assertion.
# Z-order explicitly separates raster, source, generated and validation layers.
CANONICAL_LAYER_DEFINITIONS: tuple[LayerDefinition, ...] = (
    _definition(
        "analysis.suitability", _ARTIFACT, _ANALYSIS, LayerGeometryKind.RASTER,
        "/suitability-artifacts/{artifact_id}/preview.png",
        _render("analysis.suitability", 5, visible=False, opacity=0.65),
        delivery=LayerDeliveryKind.ARTIFACT_IMAGE,
        max_features=None,
        metadata_path="/suitability-artifacts/{artifact_id}",
    ),
    _definition(
        "project.boundary", _PROJECT, _SOURCE, _POLYGON,
        "/projects/{project_id}/boundary/geojson", _render("project.boundary", 10),
        delivery=LayerDeliveryKind.PROJECT_GEOJSON, max_features=None,
    ),
    _definition(
        "source.landuse", _DATASET, _SOURCE, _POLYGON,
        _BBOX + "landuse/geojson", _render("source.landuse", 20, opacity=0.45),
    ),
    _definition(
        "source.water", _DATASET, _SOURCE, _MIXED,
        _BBOX + "water/geojson", _render("source.water", 30, opacity=0.8),
    ),
    _definition(
        "source.constraints", _DATASET, _SOURCE, _MIXED,
        _BBOX + "constraints/geojson", _render("source.constraints", 35, visible=False),
    ),
    _definition(
        "source.facilities", _DATASET, _SOURCE, _MIXED,
        _BBOX + "facilities/geojson", _render("source.facilities", 40, visible=False),
    ),
    _definition(
        "source.buildings", _DATASET, _SOURCE, _POLYGON,
        _BBOX + "buildings/geojson", _render("source.buildings", 50, opacity=0.8),
    ),
    _definition(
        "source.roads", _DATASET, _SOURCE, _LINE,
        _BBOX + "roads/geojson", _render("source.roads", 60),
    ),
    _definition(
        "generated.zones", _RUN, _GENERATED, _POLYGON,
        _RUN_PATH + "zoning-runs/{run_id}/zones/geojson",
        _render("generated.zones", 100, opacity=0.48),
    ),
    _definition(
        "generated.demography", _RUN, _GENERATED, _POLYGON,
        _RUN_PATH + "demography-runs/{run_id}/blocks/geojson",
        _render("generated.demography", 110, visible=False, opacity=0.55),
    ),
    _definition(
        "generated.blocks", _RUN, _GENERATED, _POLYGON,
        _RUN_PATH + "block-runs/{run_id}/blocks/geojson",
        _render("generated.blocks", 120, opacity=0.4),
    ),
    _definition(
        "generated.parcels", _RUN, _GENERATED, _POLYGON,
        _RUN_PATH + "block-runs/{run_id}/parcels/geojson",
        _render("generated.parcels", 130, opacity=0.3),
    ),
    _definition(
        "generated.buildings", _RUN, _GENERATED, _POLYGON,
        _RUN_PATH + "building-runs/{run_id}/buildings/geojson",
        _render("generated.buildings", 140, opacity=0.8),
    ),
    _definition(
        "generated.roads", _RUN, _GENERATED, _LINE,
        _RUN_PATH + "road-runs/{run_id}/roads/geojson",
        _render("generated.roads", 150),
    ),
    _definition(
        "run.existing_facilities", _RUN, _SOURCE, _MIXED,
        _RUN_PATH + "infrastructure-runs/{run_id}/facilities/geojson?origin=existing",
        _render("run.existing_facilities", 160),
    ),
    _definition(
        "generated.facilities", _RUN, _GENERATED, _MIXED,
        _RUN_PATH + "infrastructure-runs/{run_id}/facilities/geojson?origin=generated",
        _render("generated.facilities", 170),
    ),
    _definition(
        "generated.infrastructure_demand", _RUN, _GENERATED, _POLYGON,
        _RUN_PATH + "infrastructure-runs/{run_id}/demand/geojson",
        _render("generated.infrastructure_demand", 180, visible=False, opacity=0.55),
    ),
    _definition(
        "validation.violations", _RUN, _VALIDATION, _MIXED,
        _RUN_PATH + "validation-runs/{run_id}/violations/geojson",
        _render("validation.violations", 200),
        max_features=_MAX_VALIDATION_GEOJSON,
    ),
)


def layer_catalog_for_context(
    *,
    project_id: uuid.UUID | None = None,
    dataset_version_id: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
    suitability_artifact_id: uuid.UUID | None = None,
) -> LayerCatalog:
    """Bind canonical definitions ONLY to explicitly supplied owner UUIDs.

    No DB access, availability claim, fallback to a different run, cache lookup
    or cross-project inference. Adapters must authorize project/run/dataset
    ownership before serving a bound path or claiming that data is materialized.
    """

    for name, value in (
        ("project_id", project_id),
        ("dataset_version_id", dataset_version_id),
        ("run_id", run_id),
        ("suitability_artifact_id", suitability_artifact_id),
    ):
        _require_uuid(name, value, required=False)
    if project_id is None and (dataset_version_id is not None or run_id is not None):
        raise LayerCatalogError("dataset_version_id/run_id requires project_id")

    owners = {
        _PROJECT: LayerOwnerRef(scope=_PROJECT, project_id=project_id)
        if project_id is not None else None,
        _DATASET: LayerOwnerRef(
            scope=_DATASET, project_id=project_id, dataset_version_id=dataset_version_id
        ) if project_id is not None and dataset_version_id is not None else None,
        _RUN: LayerOwnerRef(scope=_RUN, project_id=project_id, run_id=run_id)
        if project_id is not None and run_id is not None else None,
        _ARTIFACT: LayerOwnerRef(scope=_ARTIFACT, artifact_id=suitability_artifact_id)
        if suitability_artifact_id is not None else None,
    }
    return LayerCatalog(
        entries=tuple(
            LayerCatalogEntry(definition=definition, owner=owner)
            for definition in CANONICAL_LAYER_DEFINITIONS
            if (owner := owners[definition.owner_scope]) is not None
        )
    )
