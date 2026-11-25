"""S13-T01 canonical LayerCatalog contract, ownership and endpoint consistency."""

from __future__ import annotations

import ast
import uuid
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from backend.app.application.layer_catalog import (
    CANONICAL_LAYER_DEFINITIONS,
    LAYER_CATALOG_SCHEMA_VERSION,
    LayerCatalog,
    LayerCatalogEntry,
    LayerCatalogError,
    LayerDeliveryKind,
    LayerGeometryKind,
    LayerOwnerRef,
    LayerOwnerScope,
    LayerSourceKind,
    layer_catalog_for_context,
)
from backend.app.application.source_layers import SourceLayerName

PROJECT_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
VERSION_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")
RUN_ID = uuid.UUID("33333333-3333-4333-8333-333333333333")
ARTIFACT_ID = uuid.UUID("44444444-4444-4444-8444-444444444444")


def _owner(scope: LayerOwnerScope) -> LayerOwnerRef:
    if scope is LayerOwnerScope.PROJECT:
        return LayerOwnerRef(scope=scope, project_id=PROJECT_ID)
    if scope is LayerOwnerScope.DATASET_VERSION:
        return LayerOwnerRef(scope=scope, project_id=PROJECT_ID, dataset_version_id=VERSION_ID)
    if scope is LayerOwnerScope.RUN:
        return LayerOwnerRef(scope=scope, project_id=PROJECT_ID, run_id=RUN_ID)
    return LayerOwnerRef(scope=scope, artifact_id=ARTIFACT_ID)


def test_canonical_catalog_is_deterministic_bound_and_separates_owner_from_kind() -> None:
    a = layer_catalog_for_context(
        project_id=PROJECT_ID,
        dataset_version_id=VERSION_ID,
        run_id=RUN_ID,
        suitability_artifact_id=ARTIFACT_ID,
    )
    b = layer_catalog_for_context(
        project_id=PROJECT_ID,
        dataset_version_id=VERSION_ID,
        run_id=RUN_ID,
        suitability_artifact_id=ARTIFACT_ID,
    )
    assert a == b
    assert a.schema_version == LAYER_CATALOG_SCHEMA_VERSION
    assert len(a.entries) == len(CANONICAL_LAYER_DEFINITIONS) == 18
    assert [entry.definition for entry in a.entries] == list(CANONICAL_LAYER_DEFINITIONS)
    assert len({entry.instance_key for entry in a.entries}) == len(a.entries)
    assert [definition.layer_id for definition in CANONICAL_LAYER_DEFINITIONS] == [
        entry.definition.layer_id for entry in a.entries
    ]
    assert all(
        entry.definition.presentation_crs == "EPSG:4326"
        for entry in a.entries
    )

    boundary = a.get("project.boundary")
    assert boundary is not None
    assert boundary.owner.scope is LayerOwnerScope.PROJECT
    assert boundary.owner.project_id == PROJECT_ID
    assert boundary.owner.dataset_version_id is None
    assert boundary.definition.delivery_kind is LayerDeliveryKind.PROJECT_GEOJSON
    assert boundary.definition.max_features is None

    source = a.get("source.roads")
    assert source is not None
    assert source.owner == _owner(LayerOwnerScope.DATASET_VERSION)
    assert source.definition.source_kind is LayerSourceKind.SOURCE
    assert source.path.endswith(
        f"/dataset-versions/{VERSION_ID}/source-layers/roads/geojson"
    )
    run = a.get("generated.roads")
    assert run is not None
    assert run.owner == _owner(LayerOwnerScope.RUN)
    assert run.definition.source_kind is LayerSourceKind.GENERATED
    assert f"/road-runs/{RUN_ID}/" in run.path
    existing = a.get("run.existing_facilities")
    assert existing is not None
    assert existing.owner.scope is LayerOwnerScope.RUN
    assert existing.definition.source_kind is LayerSourceKind.SOURCE
    assert existing.path.endswith("?origin=existing")
    assert a.get("generated.facilities") is not None
    assert a.get("generated.facilities").path.endswith("?origin=generated")

    validation = a.get("validation.violations")
    assert validation is not None
    assert validation.definition.source_kind is LayerSourceKind.VALIDATION
    assert validation.definition.max_features == 1000
    assert validation.owner.run_id == RUN_ID

    suitability = a.get("analysis.suitability")
    assert suitability is not None
    assert suitability.owner == _owner(LayerOwnerScope.ARTIFACT)
    assert suitability.owner.project_id is None  # no invented project ownership
    assert suitability.definition.delivery_kind is LayerDeliveryKind.ARTIFACT_IMAGE
    assert suitability.definition.geometry_kind is LayerGeometryKind.RASTER
    assert suitability.path == f"/suitability-artifacts/{ARTIFACT_ID}/preview.png"
    assert suitability.metadata_path == f"/suitability-artifacts/{ARTIFACT_ID}"
    assert suitability.definition.max_features is None
    assert a.get("nonexistent") is None


def test_context_only_binds_supplied_owners_never_falls_back_between_runs() -> None:
    empty = layer_catalog_for_context()
    assert empty.entries == ()
    only_project = layer_catalog_for_context(project_id=PROJECT_ID)
    assert [entry.definition.layer_id for entry in only_project.entries] == [
        "project.boundary"
    ]
    only_artifact = layer_catalog_for_context(suitability_artifact_id=ARTIFACT_ID)
    assert [entry.definition.layer_id for entry in only_artifact.entries] == [
        "analysis.suitability"
    ]
    scoped = layer_catalog_for_context(project_id=PROJECT_ID, run_id=RUN_ID)
    assert all(
        entry.owner.scope in {LayerOwnerScope.PROJECT, LayerOwnerScope.RUN}
        for entry in scoped.entries
    )
    assert scoped.get("source.roads") is None
    next_run = uuid.UUID("55555555-5555-4555-8555-555555555555")
    switched = layer_catalog_for_context(project_id=PROJECT_ID, run_id=next_run)
    assert scoped.get("generated.roads").instance_key != switched.get(
        "generated.roads"
    ).instance_key
    assert scoped.get("project.boundary").instance_key == switched.get(
        "project.boundary"
    ).instance_key
    assert str(RUN_ID) not in switched.get("generated.roads").path


@pytest.mark.parametrize(
    "kwargs",
    (
        {"project_id": "bad"},
        {"project_id": PROJECT_ID, "dataset_version_id": "bad"},
        {"project_id": PROJECT_ID, "run_id": "bad"},
        {"suitability_artifact_id": "bad"},
        {"dataset_version_id": VERSION_ID},
        {"run_id": RUN_ID},
        {"dataset_version_id": VERSION_ID, "suitability_artifact_id": ARTIFACT_ID},
    ),
)
def test_context_requires_explicit_valid_project_scopes(kwargs: dict[str, object]) -> None:
    with pytest.raises(LayerCatalogError):
        layer_catalog_for_context(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "kwargs",
    (
        {"scope": LayerOwnerScope.PROJECT},
        {"scope": LayerOwnerScope.RUN, "project_id": PROJECT_ID},
        {"scope": LayerOwnerScope.DATASET_VERSION, "dataset_version_id": VERSION_ID},
        {"scope": LayerOwnerScope.ARTIFACT, "artifact_id": ARTIFACT_ID, "project_id": PROJECT_ID},
        {"scope": LayerOwnerScope.RUN, "project_id": PROJECT_ID, "run_id": RUN_ID,
         "dataset_version_id": VERSION_ID},
        {"scope": LayerOwnerScope.DATASET_VERSION, "project_id": PROJECT_ID,
         "dataset_version_id": VERSION_ID, "run_id": RUN_ID},
        {"scope": "run", "project_id": PROJECT_ID, "run_id": RUN_ID},
    ),
)
def test_owner_cannot_mix_incompatible_persistence_identities(
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(LayerCatalogError):
        LayerOwnerRef(**kwargs)  # type: ignore[arg-type]


def test_owner_and_catalog_are_frozen_and_duplicate_entries_rejected() -> None:
    entry = layer_catalog_for_context(project_id=PROJECT_ID).entries[0]
    with pytest.raises(FrozenInstanceError):
        entry.owner.project_id = RUN_ID  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        entry.definition.render.default_visible = False  # type: ignore[misc]
    with pytest.raises(LayerCatalogError, match="duplicate"):
        LayerCatalog(entries=(entry, entry))
    with pytest.raises(LayerCatalogError, match="duplicate logical"):
        LayerCatalog(entries=(
            LayerCatalogEntry(
                definition=CANONICAL_LAYER_DEFINITIONS[0],
                owner=LayerOwnerRef(scope=LayerOwnerScope.ARTIFACT, artifact_id=ARTIFACT_ID),
            ),
            LayerCatalogEntry(
                definition=CANONICAL_LAYER_DEFINITIONS[0],
                owner=LayerOwnerRef(
                    scope=LayerOwnerScope.ARTIFACT,
                    artifact_id=uuid.UUID("66666666-6666-4666-8666-666666666666"),
                ),
            ),
        ))
    with pytest.raises(LayerCatalogError, match="canonical"):
        LayerCatalog(
            entries=(LayerCatalogEntry(
                definition=replace(entry.definition, definition_version="2"),
                owner=entry.owner,
            ),)
        )
    with pytest.raises(LayerCatalogError, match="order"):
        catalog = layer_catalog_for_context(project_id=PROJECT_ID, dataset_version_id=VERSION_ID)
        LayerCatalog(entries=(catalog.entries[1], catalog.entries[0]))
    with pytest.raises(LayerCatalogError, match="unsupported"):
        LayerCatalog(entries=(), schema_version="layer-catalog-v999")


def test_render_metadata_rejects_invalid_tokens_and_opacity() -> None:
    original = CANONICAL_LAYER_DEFINITIONS[0].render
    for wrong in (float("nan"), float("inf"), -0.1, 1.1, True, "0.5"):
        with pytest.raises(LayerCatalogError, match="opacity"):
            replace(original, default_opacity=wrong)
    for wrong in ("../roads", "", "Roads", "not a token"):
        with pytest.raises(LayerCatalogError, match="token"):
            replace(original, style_key=wrong)
    with pytest.raises(LayerCatalogError, match="z_index"):
        replace(original, z_index=True)
    with pytest.raises(LayerCatalogError, match="bool"):
        replace(original, default_visible=1)


def test_definition_rejects_cross_scope_cross_source_or_unsafe_routes() -> None:
    good = next(d for d in CANONICAL_LAYER_DEFINITIONS if d.layer_id == "source.roads")
    for updates in (
        {"layer_id": "../roads"},
        {"definition_version": "0"},
        {"owner_scope": LayerOwnerScope.ARTIFACT},
        {"source_kind": LayerSourceKind.GENERATED},
        {"geometry_kind": LayerGeometryKind.RASTER},
        {"presentation_crs": "EPSG:3857"},
        {"max_features": 0},
        {"max_features": 5001},
        {"max_features": None},
        {"max_features": True},
        {"metadata_path_template": "/suitability-artifacts/{artifact_id}"},
        {"path_template": "https://other.host/path/{project_id}/{dataset_version_id}"},
        {"path_template": "/layers/{run_id}"},
        {"path_template": "/layers/{project_id}/{dataset_version_id}/{run_id}"},
        {"path_template": "/layers/{project_id}/{dataset_version_id!r}"},
        {"path_template": "/layers/{project_id}/{dataset_version_id:8}"},
        {"path_template": "/layers/{project_id}/{dataset_version_id}/../bad"},
        {"delivery_kind": LayerDeliveryKind.ARTIFACT_IMAGE},
    ):
        with pytest.raises(LayerCatalogError):
            replace(good, **updates)
    with pytest.raises(LayerCatalogError, match="scope"):
        LayerCatalogEntry(definition=good, owner=_owner(LayerOwnerScope.RUN))


def test_registry_points_only_to_existing_project_scoped_api_routes() -> None:
    """Verify endpoint declarations without relying on mutable ASGI app state."""
    endpoint_dir = (
        Path(__file__).resolve().parents[2] / "backend" / "app" / "api" / "v1"
        / "endpoints"
    )
    modules = (
        "source_layers.py",
        "zoning.py",
        "roads.py",
        "blocks_parcels.py",
        "buildings.py",
        "demography.py",
        "infrastructure.py",
        "validation.py",
        "suitability.py",
    )
    declared_get_paths: set[str] = set()
    for name in modules:
        tree = ast.parse((endpoint_dir / name).read_text(encoding="utf-8"))
        router_prefix = ""
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            if not any(
                isinstance(target, ast.Name) and target.id == "router"
                for target in node.targets
            ):
                continue
            if not isinstance(node.value, ast.Call):
                continue
            for keyword in node.value.keywords:
                if keyword.arg == "prefix" and isinstance(keyword.value, ast.Constant):
                    assert isinstance(keyword.value.value, str)
                    router_prefix = keyword.value.value

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if (
                    isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Attribute)
                    and decorator.func.attr == "get"
                    and isinstance(decorator.func.value, ast.Name)
                    and decorator.func.value.id == "router"
                    and decorator.args
                    and isinstance(decorator.args[0], ast.Constant)
                    and isinstance(decorator.args[0].value, str)
                ):
                    declared_get_paths.add(router_prefix + decorator.args[0].value)

    assert declared_get_paths
    supported_source_layers = {name.value for name in SourceLayerName}
    for definition in CANONICAL_LAYER_DEFINITIONS:
        declared_path = urlsplit(definition.path_template).path
        if definition.owner_scope is LayerOwnerScope.DATASET_VERSION:
            source_name = definition.layer_id.removeprefix("source.")
            assert source_name in supported_source_layers
            # The source router uses one validated {layer} path parameter
            # rather than six literal paths for the six catalog categories.
            declared_path = declared_path.replace(
                f"/source-layers/{source_name}/geojson",
                "/source-layers/{layer}/geojson",
            )
        assert declared_path in declared_get_paths
        if definition.metadata_path_template is not None:
            assert definition.metadata_path_template in declared_get_paths
        if definition.delivery_kind is LayerDeliveryKind.BBOX_GEOJSON:
            assert definition.max_features is not None
            assert definition.max_features <= 5000
        else:
            assert definition.max_features is None
    assert len({d.layer_id for d in CANONICAL_LAYER_DEFINITIONS}) == len(
        CANONICAL_LAYER_DEFINITIONS
    )
