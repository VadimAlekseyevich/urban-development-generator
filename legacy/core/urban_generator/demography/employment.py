from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass

from core.urban_generator.buildings import (
    AssignedBuildingAttributes,
    BuildingAreaMetrics,
    BuildingUse,
)
from core.urban_generator.demography.allocation import PopulationAllocationResult
from core.urban_generator.demography.config import DemographicScenario

DEFAULT_MAX_EMPLOYMENT_SUBJECTS = 100_000

_JOB_USE_ORDER = (
    BuildingUse.MIXED,
    BuildingUse.PUBLIC,
    BuildingUse.COMMERCIAL,
)
_JOB_USE_SET = set(_JOB_USE_ORDER)
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class EmploymentEstimateError(ValueError):
    """Raised when S09-T05 jobs/workforce inputs violate the contract."""


@dataclass(frozen=True, slots=True)
class JobDensityRule:
    """Explicit non-residential GFA per one approximate job."""

    use: BuildingUse
    area_per_job_m2: float

    def __post_init__(self) -> None:
        if not isinstance(self.use, BuildingUse):
            raise EmploymentEstimateError("use must be a BuildingUse value")
        if self.use not in _JOB_USE_SET:
            raise EmploymentEstimateError(
                "job-density rules are allowed only for mixed/public/commercial uses"
            )
        area_per_job = _require_positive_finite(
            "area_per_job_m2",
            self.area_per_job_m2,
        )
        object.__setattr__(self, "area_per_job_m2", area_per_job)


@dataclass(frozen=True, slots=True)
class EmploymentConfig:
    """Versioned explicit job-density assumptions for non-residential uses."""

    version: str
    rules: tuple[JobDensityRule, ...]

    def __post_init__(self) -> None:
        _validate_version(self.version)
        if not isinstance(self.rules, tuple):
            raise EmploymentEstimateError("rules must be an immutable tuple")
        if any(not isinstance(item, JobDensityRule) for item in self.rules):
            raise EmploymentEstimateError(
                "rules must contain JobDensityRule values"
            )
        uses = tuple(item.use for item in self.rules)
        if len(uses) != len(set(uses)):
            raise EmploymentEstimateError("job-density rule uses must be unique")
        if set(uses) != _JOB_USE_SET:
            missing = sorted(use.value for use in _JOB_USE_SET - set(uses))
            raise EmploymentEstimateError(
                "employment config must define mixed/public/commercial rules"
                + (f"; missing={','.join(missing)}" if missing else "")
            )
        canonical = tuple(
            sorted(
                self.rules,
                key=lambda item: _JOB_USE_ORDER.index(item.use),
            )
        )
        object.__setattr__(self, "rules", canonical)

    def rule(self, use: BuildingUse) -> JobDensityRule:
        if not isinstance(use, BuildingUse):
            raise EmploymentEstimateError(
                "job-density lookup requires a BuildingUse value"
            )
        for rule in self.rules:
            if rule.use is use:
                return rule
        raise EmploymentEstimateError(
            f"no job-density rule for use: {use.value}"
        )

    @property
    def fingerprint(self) -> str:
        payload = {
            "version": self.version,
            "rules": [
                {
                    "use": rule.use.value,
                    "area_per_job_m2": rule.area_per_job_m2,
                }
                for rule in self.rules
            ],
        }
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class EmploymentSubject:
    """Authoritative S08 GFA/use pair used for job estimation."""

    metrics: BuildingAreaMetrics
    attributes: AssignedBuildingAttributes

    def __post_init__(self) -> None:
        if not isinstance(self.metrics, BuildingAreaMetrics):
            raise EmploymentEstimateError(
                "metrics must be BuildingAreaMetrics"
            )
        if not isinstance(self.attributes, AssignedBuildingAttributes):
            raise EmploymentEstimateError(
                "attributes must be AssignedBuildingAttributes"
            )
        if self.metrics.building_id != self.attributes.building_id:
            raise EmploymentEstimateError(
                "building metric and attribute ids must match"
            )
        if self.metrics.floors != self.attributes.floors:
            raise EmploymentEstimateError(
                "building metric and attribute floors must match"
            )

    @property
    def building_id(self) -> str:
        return self.metrics.building_id


@dataclass(frozen=True, slots=True)
class BuildingJobEstimate:
    """Approximate jobs supported by one generated building."""

    building_id: str
    use: BuildingUse
    total_gfa_m2: float
    job_supporting_gfa_m2: float
    area_per_job_m2: float | None
    jobs_estimate: float

    def __post_init__(self) -> None:
        if not isinstance(self.building_id, str) or not self.building_id:
            raise EmploymentEstimateError(
                "building_id must be a non-empty string"
            )
        if not isinstance(self.use, BuildingUse):
            raise EmploymentEstimateError("use must be a BuildingUse value")
        total_gfa = _require_non_negative_finite(
            "total_gfa_m2",
            self.total_gfa_m2,
        )
        job_gfa = _require_non_negative_finite(
            "job_supporting_gfa_m2",
            self.job_supporting_gfa_m2,
        )
        jobs = _require_non_negative_finite(
            "jobs_estimate",
            self.jobs_estimate,
        )
        if job_gfa > total_gfa + _AREA_EPSILON_M2:
            raise EmploymentEstimateError(
                "job_supporting_gfa_m2 cannot exceed total_gfa_m2"
            )

        if self.use is BuildingUse.RESIDENTIAL:
            if self.area_per_job_m2 is not None:
                raise EmploymentEstimateError(
                    "residential estimate must not carry area_per_job_m2"
                )
            if job_gfa != 0.0 or jobs != 0.0:
                raise EmploymentEstimateError(
                    "residential building must have zero job estimate"
                )
        else:
            if self.area_per_job_m2 is None:
                raise EmploymentEstimateError(
                    "non-residential job estimate requires area_per_job_m2"
                )
            area_per_job = _require_positive_finite(
                "area_per_job_m2",
                self.area_per_job_m2,
            )
            expected_jobs = job_gfa / area_per_job
            if not math.isclose(
                jobs,
                expected_jobs,
                rel_tol=1e-12,
                abs_tol=1e-12,
            ):
                raise EmploymentEstimateError(
                    "jobs_estimate must equal job-supporting GFA / area_per_job"
                )
            object.__setattr__(self, "area_per_job_m2", area_per_job)

        object.__setattr__(self, "total_gfa_m2", total_gfa)
        object.__setattr__(self, "job_supporting_gfa_m2", job_gfa)
        object.__setattr__(self, "jobs_estimate", jobs)


@dataclass(frozen=True, slots=True)
class EmploymentEstimateSummary:
    """Aggregate generated jobs and generated-resident workforce estimates."""

    total_jobs_estimate: float
    generated_population: int
    working_population_ratio: float
    generated_workforce_estimate: float

    def __post_init__(self) -> None:
        total_jobs = _require_non_negative_finite(
            "total_jobs_estimate",
            self.total_jobs_estimate,
        )
        _require_non_negative_int(
            "generated_population",
            self.generated_population,
        )
        working_ratio = _require_ratio(
            "working_population_ratio",
            self.working_population_ratio,
        )
        workforce = _require_non_negative_finite(
            "generated_workforce_estimate",
            self.generated_workforce_estimate,
        )
        expected_workforce = self.generated_population * working_ratio
        if not math.isclose(
            workforce,
            expected_workforce,
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            raise EmploymentEstimateError(
                "generated workforce must equal population * working ratio"
            )
        object.__setattr__(self, "total_jobs_estimate", total_jobs)
        object.__setattr__(self, "working_population_ratio", working_ratio)
        object.__setattr__(self, "generated_workforce_estimate", workforce)


@dataclass(frozen=True, slots=True)
class EmploymentEstimateResult:
    """Canonical S09-T05 estimates with scenario/config provenance."""

    scenario_version: str
    scenario_fingerprint: str
    config_version: str
    config_fingerprint: str
    buildings: tuple[BuildingJobEstimate, ...]
    summary: EmploymentEstimateSummary

    def __post_init__(self) -> None:
        _require_non_empty_string("scenario_version", self.scenario_version)
        _require_sha256("scenario_fingerprint", self.scenario_fingerprint)
        _require_non_empty_string("config_version", self.config_version)
        _require_sha256("config_fingerprint", self.config_fingerprint)
        if not isinstance(self.buildings, tuple):
            raise EmploymentEstimateError(
                "buildings must be an immutable tuple"
            )
        if any(not isinstance(item, BuildingJobEstimate) for item in self.buildings):
            raise EmploymentEstimateError(
                "buildings must contain BuildingJobEstimate values"
            )
        ids = tuple(item.building_id for item in self.buildings)
        if ids != tuple(sorted(ids)) or len(ids) != len(set(ids)):
            raise EmploymentEstimateError(
                "building job estimate ids must be sorted and unique"
            )
        if not isinstance(self.summary, EmploymentEstimateSummary):
            raise EmploymentEstimateError(
                "summary must be EmploymentEstimateSummary"
            )
        jobs_total = math.fsum(item.jobs_estimate for item in self.buildings)
        if not math.isclose(
            jobs_total,
            self.summary.total_jobs_estimate,
            rel_tol=1e-12,
            abs_tol=1e-9,
        ):
            raise EmploymentEstimateError(
                "building job estimates must sum to summary total"
            )


class EmploymentEstimator:
    """Estimate generated jobs and workforce from explicit assumptions."""

    def __init__(
        self,
        *,
        max_subjects: int = DEFAULT_MAX_EMPLOYMENT_SUBJECTS,
    ) -> None:
        _require_positive_int("max_subjects", max_subjects)
        self.max_subjects = max_subjects

    def estimate(
        self,
        subjects: tuple[EmploymentSubject, ...],
        *,
        population: PopulationAllocationResult,
        scenario: DemographicScenario,
        config: EmploymentConfig,
    ) -> EmploymentEstimateResult:
        self._validate_inputs(
            subjects=subjects,
            population=population,
            scenario=scenario,
            config=config,
        )

        buildings = tuple(
            self._estimate_building(
                subject,
                scenario=scenario,
                config=config,
            )
            for subject in sorted(subjects, key=lambda item: item.building_id)
        )
        total_jobs = math.fsum(item.jobs_estimate for item in buildings)
        generated_population = (
            population.diagnostics.allocated_generated_population
        )
        summary = EmploymentEstimateSummary(
            total_jobs_estimate=total_jobs,
            generated_population=generated_population,
            working_population_ratio=scenario.working_population_ratio,
            generated_workforce_estimate=(
                generated_population * scenario.working_population_ratio
            ),
        )
        return EmploymentEstimateResult(
            scenario_version=scenario.version,
            scenario_fingerprint=scenario.fingerprint,
            config_version=config.version,
            config_fingerprint=config.fingerprint,
            buildings=buildings,
            summary=summary,
        )

    def _estimate_building(
        self,
        subject: EmploymentSubject,
        *,
        scenario: DemographicScenario,
        config: EmploymentConfig,
    ) -> BuildingJobEstimate:
        use = subject.attributes.use
        total_gfa = subject.metrics.gfa_m2
        if use is BuildingUse.RESIDENTIAL:
            return BuildingJobEstimate(
                building_id=subject.building_id,
                use=use,
                total_gfa_m2=total_gfa,
                job_supporting_gfa_m2=0.0,
                area_per_job_m2=None,
                jobs_estimate=0.0,
            )

        job_gfa = (
            total_gfa * (1.0 - scenario.residential_gfa_share)
            if use is BuildingUse.MIXED
            else total_gfa
        )
        rule = config.rule(use)
        return BuildingJobEstimate(
            building_id=subject.building_id,
            use=use,
            total_gfa_m2=total_gfa,
            job_supporting_gfa_m2=job_gfa,
            area_per_job_m2=rule.area_per_job_m2,
            jobs_estimate=job_gfa / rule.area_per_job_m2,
        )

    def _validate_inputs(
        self,
        *,
        subjects: tuple[EmploymentSubject, ...],
        population: PopulationAllocationResult,
        scenario: DemographicScenario,
        config: EmploymentConfig,
    ) -> None:
        if not isinstance(subjects, tuple):
            raise EmploymentEstimateError(
                "subjects must be an immutable tuple"
            )
        if len(subjects) > self.max_subjects:
            raise EmploymentEstimateError(
                "employment subject limit exceeded: "
                f"{len(subjects)} > {self.max_subjects}"
            )
        if any(not isinstance(item, EmploymentSubject) for item in subjects):
            raise EmploymentEstimateError(
                "subjects must contain EmploymentSubject values"
            )
        ids = tuple(item.building_id for item in subjects)
        if len(ids) != len(set(ids)):
            raise EmploymentEstimateError("building ids must be unique")
        if not isinstance(population, PopulationAllocationResult):
            raise EmploymentEstimateError(
                "population must be PopulationAllocationResult"
            )
        if not isinstance(scenario, DemographicScenario):
            raise EmploymentEstimateError(
                "scenario must be DemographicScenario"
            )
        if not isinstance(config, EmploymentConfig):
            raise EmploymentEstimateError(
                "config must be EmploymentConfig"
            )
        if population.scenario_version != scenario.version:
            raise EmploymentEstimateError(
                "population scenario version must match employment scenario"
            )
        if population.scenario_fingerprint != scenario.fingerprint:
            raise EmploymentEstimateError(
                "population scenario fingerprint must match employment scenario"
            )


def _validate_version(value: str) -> None:
    if not isinstance(value, str) or _VERSION_RE.fullmatch(value) is None:
        raise EmploymentEstimateError(
            f"invalid employment config version: {value!r}"
        )


def _require_non_empty_string(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value:
        raise EmploymentEstimateError(
            f"{field_name} must be a non-empty string"
        )


def _require_sha256(field_name: str, value: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise EmploymentEstimateError(
            f"{field_name} must be a lowercase SHA-256 hex digest"
        )


def _require_positive_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise EmploymentEstimateError(
            f"{field_name} must be a positive integer"
        )


def _require_non_negative_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise EmploymentEstimateError(
            f"{field_name} must be a non-negative integer"
        )


def _require_non_negative_finite(field_name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EmploymentEstimateError(
            f"{field_name} must be a finite non-negative number"
        )
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise EmploymentEstimateError(
            f"{field_name} must be a finite non-negative number"
        )
    return number


def _require_positive_finite(field_name: str, value: float) -> float:
    number = _require_non_negative_finite(field_name, value)
    if number <= 0.0:
        raise EmploymentEstimateError(
            f"{field_name} must be greater than zero"
        )
    return number


def _require_ratio(field_name: str, value: float) -> float:
    number = _require_non_negative_finite(field_name, value)
    if number > 1.0:
        raise EmploymentEstimateError(
            f"{field_name} must be inside 0..1"
        )
    return number


_AREA_EPSILON_M2 = 1e-9
