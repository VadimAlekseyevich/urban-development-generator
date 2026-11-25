"""The planning preview creates typed config, never generation state."""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.app.adapters.stage_config_resolver import (
    COMPLETE_CONFIG_SCHEMA_VERSION,
    CanonicalStageConfigResolver,
)
from backend.app.application.planning_config import PlanningParameters, build_planning_config
from backend.app.main import app
from core.urban_generator.domain import ConfigRef
from core.urban_generator.stages import CANONICAL_STAGE_ORDER


@pytest.mark.parametrize("source", ["slope", "landuse"])
def test_profile_is_complete_and_deterministic(source: str) -> None:
    params = PlanningParameters.model_validate({"suitability_source": source})
    first = build_planning_config(params)
    assert first == build_planning_config(params)
    assert first["schema_version"] == COMPLETE_CONFIG_SCHEMA_VERSION
    bindings = CanonicalStageConfigResolver(
        schema_version=COMPLETE_CONFIG_SCHEMA_VERSION,
    ).resolve(
        config_json=first, schema_version=COMPLETE_CONFIG_SCHEMA_VERSION,
        source=ConfigRef(name="generation", ref="test-profile"),
    )
    assert tuple(binding.stage_name for binding in bindings) == CANONICAL_STAGE_ORDER
    suitability = first["stages"]["suitability"]["config"]
    assert suitability["suitability"]["factors"][0]["code"] == source
    assert (suitability["landuse_weights"] is None) == (source == "slope")


def test_parameters_map_to_existing_stage_contracts() -> None:
    config = build_planning_config(PlanningParameters(
        cell_size_m=20, residential_percent=50, mixed_percent=30,
        floors=6, coverage_ratio=0.3, zoning_seeds=20, road_anchors=32,
        road_growth_iterations=25, building_size_m=12,
        target_population=3000, area_per_person_m2=35,
        max_block_area_m2=30000, clinic_distance_m=1200, clinic_capacity=2000,
    ))["stages"]
    assert config["prepare_snapshot"]["config"]["cell_size_m"] == 20
    assert config["zoning"]["config"]["seed_count"] == 20
    assert config["zoning"]["config"]["zoning"]["zones"][0]["target_share"] == 0.5
    assert config["roads"]["config"]["anchor_policy"]["max_candidates"] == 32
    assert config["roads"]["config"]["anchor_policy"]["spatially_balanced"] is True
    assert config["roads"]["config"]["growth_policy"]["max_iterations"] == 25
    assert config["blocks_and_parcels"]["config"]["parcel_policy"]["minimum_parcel_area_m2"] == 480
    assert config["blocks_and_parcels"]["config"]["split_policy"]["max_area_m2"] == 30000
    building = config["buildings"]["config"]
    assert building["building"]["archetypes"][0]["placement_scope"] == "parcel_or_block"
    assert all(rule["min_floors"] == rule["max_floors"] == 6
               for rule in building["attributes"]["rules"])
    assert building["archetype_specs"][0]["planning_floor_area_multiplier"] == 6
    assert building["zone_targets"][0]["target_far"] == pytest.approx(1.8)
    population = config["demography"]["config"]["scenario"]
    assert population["population_target"]["value"] == 3000
    assert population["residential_area_per_person_m2"] == 35
    clinic = config["infrastructure"]["config"]["infrastructure_types"][0]
    assert clinic["capacity"] == 2000
    assert clinic["max_network_distance_m"] == 1200


@pytest.mark.parametrize("invalid", [
    {"residential_percent": 90},
    {"floors": True},
    {"floors": 0},
    {"coverage_ratio": float("nan")},
    {"road_anchors": 10000},
    {"road_growth_iterations": 201},
    {"building_size_m": 0},
    {"suitability_source": "unsupported"},
    {"surprise": 1},
])
def test_bad_parameters_fail_closed(invalid: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        PlanningParameters.model_validate(invalid)


def test_preview_http_contract() -> None:
    client = TestClient(app)
    url = "/api/v1/generation/planning-config"
    good = client.post(url, json={"floors": 5})
    assert good.status_code == 200, good.text
    assert len(good.json()["stages"]) == 12
    assert good.json()["stages"]["buildings"]["config"]["attributes"]["rules"][0][
        "min_floors"
    ] == 5
    assert client.post(url, json={"residential_percent": 90}).status_code == 422
    assert client.post(url, json={"floors": "5"}).status_code == 422
