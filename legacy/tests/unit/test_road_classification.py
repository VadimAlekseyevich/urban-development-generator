from __future__ import annotations

import pytest

from core.urban_generator.roads import (
    GeneratedRoadClass,
    RoadClassificationError,
    RoadClassificationOrigin,
    RoadClassificationPolicy,
    RoadClassificationReason,
    RoadClassificationStrategy,
    RoadClassificationSubject,
    RoadGrowthIntent,
    RuleBasedRoadClassifier,
)


def existing(road_id: str, road_class: str) -> RoadClassificationSubject:
    return RoadClassificationSubject(
        road_id=road_id,
        origin=RoadClassificationOrigin.EXISTING,
        existing_class=road_class,
    )


def baseline(road_id: str, length_m: float) -> RoadClassificationSubject:
    return RoadClassificationSubject(
        road_id=road_id,
        origin=RoadClassificationOrigin.BASELINE_GENERATED,
        length_m=length_m,
    )


def growth(
    road_id: str,
    length_m: float,
    intent: RoadGrowthIntent,
) -> RoadClassificationSubject:
    return RoadClassificationSubject(
        road_id=road_id,
        origin=RoadClassificationOrigin.GROWTH_GENERATED,
        length_m=length_m,
        growth_intent=intent,
    )


def by_id(result):
    return {road.road_id: road for road in result.roads}


def test_classifier_satisfies_pluggable_strategy_protocol() -> None:
    assert isinstance(RuleBasedRoadClassifier(), RoadClassificationStrategy)


def test_existing_class_is_preserved_exactly_and_not_canonicalized() -> None:
    result = RuleBasedRoadClassifier().classify(
        (
            existing("existing-1", "primary_link"),
            existing("existing-2", "Custom Existing Class"),
        )
    )

    roads = by_id(result)
    assert roads["existing-1"].road_class == "primary_link"
    assert roads["existing-2"].road_class == "Custom Existing Class"
    assert roads["existing-1"].generated_class is None
    assert roads["existing-1"].reason is RoadClassificationReason.EXISTING_PRESERVED
    assert result.diagnostics.existing_preserved_count == 2
    assert result.diagnostics.generated_count == 0


def test_default_generated_rules_apply_baseline_and_growth_floors() -> None:
    result = RuleBasedRoadClassifier().classify(
        (
            baseline("baseline-short", 100.0),
            growth("growth-local-short", 100.0, RoadGrowthIntent.LOCAL),
            growth("growth-collector-short", 100.0, RoadGrowthIntent.COLLECTOR),
        )
    )

    roads = by_id(result)
    assert roads["baseline-short"].generated_class is GeneratedRoadClass.COLLECTOR
    assert roads["baseline-short"].reason is RoadClassificationReason.BASELINE_FLOOR
    assert roads["growth-local-short"].generated_class is GeneratedRoadClass.LOCAL
    assert roads["growth-local-short"].reason is RoadClassificationReason.LOCAL_DEFAULT
    assert roads["growth-collector-short"].generated_class is GeneratedRoadClass.COLLECTOR
    assert (
        roads["growth-collector-short"].reason
        is RoadClassificationReason.COLLECTOR_INTENT_FLOOR
    )


def test_length_rules_promote_collector_then_arterial() -> None:
    result = RuleBasedRoadClassifier().classify(
        (
            growth("collector-by-length", 500.0, RoadGrowthIntent.LOCAL),
            growth("arterial-by-length", 2_000.0, RoadGrowthIntent.LOCAL),
        )
    )

    roads = by_id(result)
    assert roads["collector-by-length"].generated_class is GeneratedRoadClass.COLLECTOR
    assert roads["collector-by-length"].reason is RoadClassificationReason.COLLECTOR_LENGTH
    assert roads["arterial-by-length"].generated_class is GeneratedRoadClass.ARTERIAL
    assert roads["arterial-by-length"].reason is RoadClassificationReason.ARTERIAL_LENGTH
    assert result.diagnostics.collector_count == 1
    assert result.diagnostics.arterial_count == 1


def test_policy_can_disable_floors_and_change_thresholds() -> None:
    classifier = RuleBasedRoadClassifier(
        policy=RoadClassificationPolicy(
            collector_min_length_m=100.0,
            arterial_min_length_m=300.0,
            baseline_min_class=GeneratedRoadClass.LOCAL,
            collector_intent_min_class=GeneratedRoadClass.LOCAL,
        )
    )
    result = classifier.classify(
        (
            baseline("baseline-short", 50.0),
            growth("collector-intent-short", 50.0, RoadGrowthIntent.COLLECTOR),
            growth("medium", 150.0, RoadGrowthIntent.LOCAL),
            growth("long", 350.0, RoadGrowthIntent.LOCAL),
        )
    )

    roads = by_id(result)
    assert roads["baseline-short"].generated_class is GeneratedRoadClass.LOCAL
    assert roads["collector-intent-short"].generated_class is GeneratedRoadClass.LOCAL
    assert roads["medium"].generated_class is GeneratedRoadClass.COLLECTOR
    assert roads["long"].generated_class is GeneratedRoadClass.ARTERIAL


def test_policy_floor_can_promote_generated_road_to_arterial() -> None:
    classifier = RuleBasedRoadClassifier(
        policy=RoadClassificationPolicy(
            baseline_min_class=GeneratedRoadClass.ARTERIAL,
        )
    )
    result = classifier.classify((baseline("baseline", 10.0),))

    road = result.roads[0]
    assert road.generated_class is GeneratedRoadClass.ARTERIAL
    assert road.reason is RoadClassificationReason.BASELINE_FLOOR


def test_input_order_does_not_change_output_order_or_classes() -> None:
    subjects = (
        growth("c", 100.0, RoadGrowthIntent.LOCAL),
        existing("a", "secondary"),
        baseline("b", 100.0),
    )

    forward = RuleBasedRoadClassifier().classify(subjects)
    reverse = RuleBasedRoadClassifier().classify(tuple(reversed(subjects)))

    assert forward == reverse
    assert tuple(road.road_id for road in forward.roads) == ("a", "b", "c")


def test_subject_contract_rejects_existing_generated_field_mixups() -> None:
    with pytest.raises(RoadClassificationError, match="requires existing_class"):
        RoadClassificationSubject(
            road_id="existing",
            origin=RoadClassificationOrigin.EXISTING,
        )
    with pytest.raises(RoadClassificationError, match="must not carry generated growth_intent"):
        RoadClassificationSubject(
            road_id="existing",
            origin=RoadClassificationOrigin.EXISTING,
            existing_class="primary",
            growth_intent=RoadGrowthIntent.LOCAL,
        )
    with pytest.raises(RoadClassificationError, match="generated road requires length_m"):
        RoadClassificationSubject(
            road_id="generated",
            origin=RoadClassificationOrigin.BASELINE_GENERATED,
        )
    with pytest.raises(RoadClassificationError, match="requires RoadGrowthIntent"):
        RoadClassificationSubject(
            road_id="growth",
            origin=RoadClassificationOrigin.GROWTH_GENERATED,
            length_m=10.0,
        )


def test_classifier_rejects_duplicate_ids_and_subject_limit() -> None:
    duplicate = (
        existing("same", "primary"),
        baseline("same", 100.0),
    )
    with pytest.raises(RoadClassificationError, match="road_id values must be unique"):
        RuleBasedRoadClassifier().classify(duplicate)

    classifier = RuleBasedRoadClassifier(
        policy=RoadClassificationPolicy(max_subjects=1)
    )
    with pytest.raises(RoadClassificationError, match="subject limit exceeded"):
        classifier.classify(
            (
                existing("a", "primary"),
                existing("b", "secondary"),
            )
        )


def test_policy_requires_ordered_positive_thresholds() -> None:
    with pytest.raises(RoadClassificationError, match="must be greater"):
        RoadClassificationPolicy(
            collector_min_length_m=500.0,
            arterial_min_length_m=500.0,
        )
    with pytest.raises(RoadClassificationError, match="positive finite"):
        RoadClassificationPolicy(collector_min_length_m=0.0)
