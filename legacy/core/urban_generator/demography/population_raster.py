from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from core.urban_generator.domain.crs import require_working_crs

DEFAULT_MAX_POPULATION_RASTER_SUBJECTS = 100_000
DEFAULT_MAX_POPULATION_RASTER_WINDOWS_PER_SUBJECT = 128
DEFAULT_MAX_POPULATION_RASTER_SAMPLE_CELLS = 25_000_000


class PopulationRasterError(ValueError):
    """Raised when S09-T07 population-raster inputs violate the adapter contract."""


class PopulationRasterSampleLimitError(PopulationRasterError):
    """Raised before an unbounded population-raster read is attempted."""


class PopulationRasterValueKind(StrEnum):
    """Meaning of one finite non-negative raster cell value."""

    POPULATION_PER_CELL = "population_per_cell"
    DENSITY_PER_KM2 = "density_per_km2"


class PopulationRasterNoDataPolicy(StrEnum):
    """How nodata cells affect one subject sample."""

    IGNORE = "IGNORE"
    REJECT = "REJECT"


@dataclass(frozen=True, slots=True)
class PopulationRasterWindow:
    """One bounded read window in raster pixel coordinates."""

    row_off: int
    col_off: int
    height: int
    width: int

    def __post_init__(self) -> None:
        for field_name in ("row_off", "col_off", "height", "width"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise PopulationRasterError(f"{field_name} must be an integer")
        if self.row_off < 0 or self.col_off < 0:
            raise PopulationRasterError(
                "population raster window offsets must be non-negative"
            )
        if self.height <= 0 or self.width <= 0:
            raise PopulationRasterError(
                "population raster window dimensions must be positive"
            )

    @property
    def cell_count(self) -> int:
        return self.height * self.width


class PopulationRasterSource(Protocol):
    """Minimal aligned windowed source required by demographic calibration."""

    working_srid: int
    width: int
    height: int
    cell_area_m2: float
    value_kind: PopulationRasterValueKind

    def read_window(
        self,
        *,
        window: PopulationRasterWindow,
    ) -> tuple[tuple[float | int | None, ...], ...]:
        """Return exactly height × width cells with nodata mapped to None."""


@dataclass(frozen=True, slots=True)
class PopulationRasterSamplingPolicy:
    """Bounded read and nodata semantics for the optional calibration source."""

    nodata_policy: PopulationRasterNoDataPolicy = PopulationRasterNoDataPolicy.IGNORE
    max_subjects: int = DEFAULT_MAX_POPULATION_RASTER_SUBJECTS
    max_windows_per_subject: int = DEFAULT_MAX_POPULATION_RASTER_WINDOWS_PER_SUBJECT
    max_sample_cells: int = DEFAULT_MAX_POPULATION_RASTER_SAMPLE_CELLS

    def __post_init__(self) -> None:
        if not isinstance(self.nodata_policy, PopulationRasterNoDataPolicy):
            raise PopulationRasterError(
                "nodata_policy must be a PopulationRasterNoDataPolicy value"
            )
        _require_positive_int("max_subjects", self.max_subjects)
        _require_positive_int(
            "max_windows_per_subject",
            self.max_windows_per_subject,
        )
        _require_positive_int("max_sample_cells", self.max_sample_cells)


@dataclass(frozen=True, slots=True)
class PopulationRasterSubject:
    """Raster windows associated with one calibration subject."""

    subject_id: str
    windows: tuple[PopulationRasterWindow, ...]
    working_srid: int

    def __post_init__(self) -> None:
        _require_id("subject_id", self.subject_id)
        require_working_crs(self.working_srid)
        if not isinstance(self.windows, tuple):
            raise PopulationRasterError(
                "population raster windows must be an immutable tuple"
            )
        if not self.windows:
            raise PopulationRasterError(
                "population raster subject must contain at least one window"
            )
        if any(
            not isinstance(window, PopulationRasterWindow)
            for window in self.windows
        ):
            raise PopulationRasterError(
                "windows must contain PopulationRasterWindow values"
            )
        _validate_non_overlapping_windows(self.windows)


@dataclass(frozen=True, slots=True)
class PopulationRasterSample:
    """Normalized population evidence sampled for one spatial subject."""

    subject_id: str
    requested_cell_count: int
    valid_cell_count: int
    nodata_cell_count: int
    sampled_area_m2: float
    sampled_population: float
    mean_density_per_km2: float

    def __post_init__(self) -> None:
        _require_id("subject_id", self.subject_id)
        _require_positive_int(
            "requested_cell_count",
            self.requested_cell_count,
        )
        _require_non_negative_int("valid_cell_count", self.valid_cell_count)
        _require_non_negative_int("nodata_cell_count", self.nodata_cell_count)
        if (
            self.valid_cell_count + self.nodata_cell_count
            != self.requested_cell_count
        ):
            raise PopulationRasterError(
                "valid and nodata cell counts must equal requested_cell_count"
            )
        sampled_area = _require_non_negative_finite(
            "sampled_area_m2",
            self.sampled_area_m2,
        )
        sampled_population = _require_non_negative_finite(
            "sampled_population",
            self.sampled_population,
        )
        density = _require_non_negative_finite(
            "mean_density_per_km2",
            self.mean_density_per_km2,
        )
        if self.valid_cell_count == 0:
            if sampled_area != 0.0 or sampled_population != 0.0 or density != 0.0:
                raise PopulationRasterError(
                    "all-nodata sample must have zero area/population/density"
                )
        elif sampled_area <= 0.0:
            raise PopulationRasterError(
                "sampled_area_m2 must be positive when valid cells exist"
            )
        object.__setattr__(self, "sampled_area_m2", sampled_area)
        object.__setattr__(self, "sampled_population", sampled_population)
        object.__setattr__(self, "mean_density_per_km2", density)

    @property
    def has_valid_data(self) -> bool:
        return self.valid_cell_count > 0

    @property
    def valid_fraction(self) -> float:
        return self.valid_cell_count / self.requested_cell_count


@dataclass(frozen=True, slots=True)
class PopulationRasterSamplingResult:
    """Canonical samples plus source semantics required by S09-T08."""

    working_srid: int
    value_kind: PopulationRasterValueKind
    cell_area_m2: float
    samples: tuple[PopulationRasterSample, ...]

    def __post_init__(self) -> None:
        require_working_crs(self.working_srid)
        if not isinstance(self.value_kind, PopulationRasterValueKind):
            raise PopulationRasterError(
                "value_kind must be a PopulationRasterValueKind"
            )
        cell_area = _require_positive_finite(
            "cell_area_m2",
            self.cell_area_m2,
        )
        if not isinstance(self.samples, tuple):
            raise PopulationRasterError("samples must be an immutable tuple")
        if any(
            not isinstance(item, PopulationRasterSample)
            for item in self.samples
        ):
            raise PopulationRasterError(
                "samples must contain PopulationRasterSample values"
            )
        ids = tuple(item.subject_id for item in self.samples)
        if ids != tuple(sorted(ids)) or len(ids) != len(set(ids)):
            raise PopulationRasterError(
                "population raster samples must be sorted and unique"
            )
        object.__setattr__(self, "cell_area_m2", cell_area)


class PopulationRasterSampler:
    """Read bounded population-raster windows without coupling base demography to rasterio."""

    def __init__(
        self,
        *,
        source: PopulationRasterSource,
        policy: PopulationRasterSamplingPolicy | None = None,
    ) -> None:
        _validate_source(source)
        self.source = source
        self.policy = (
            policy if policy is not None else PopulationRasterSamplingPolicy()
        )
        if not isinstance(self.policy, PopulationRasterSamplingPolicy):
            raise PopulationRasterError(
                "policy must be a PopulationRasterSamplingPolicy"
            )

    def sample(
        self,
        subjects: tuple[PopulationRasterSubject, ...],
    ) -> PopulationRasterSamplingResult:
        self._validate_subjects(subjects)

        samples = tuple(
            self._sample_subject(subject)
            for subject in sorted(subjects, key=lambda item: item.subject_id)
        )
        return PopulationRasterSamplingResult(
            working_srid=self.source.working_srid,
            value_kind=self.source.value_kind,
            cell_area_m2=float(self.source.cell_area_m2),
            samples=samples,
        )

    def _validate_subjects(
        self,
        subjects: tuple[PopulationRasterSubject, ...],
    ) -> None:
        if not isinstance(subjects, tuple):
            raise PopulationRasterError(
                "subjects must be an immutable tuple"
            )
        if len(subjects) > self.policy.max_subjects:
            raise PopulationRasterSampleLimitError(
                "population raster subject limit exceeded: "
                f"{len(subjects)} > {self.policy.max_subjects}"
            )
        if any(
            not isinstance(item, PopulationRasterSubject)
            for item in subjects
        ):
            raise PopulationRasterError(
                "subjects must contain PopulationRasterSubject values"
            )
        ids = tuple(item.subject_id for item in subjects)
        if len(ids) != len(set(ids)):
            raise PopulationRasterError(
                "population raster subject ids must be unique"
            )

        total_cells = 0
        for subject in subjects:
            if subject.working_srid != self.source.working_srid:
                raise PopulationRasterError(
                    "subject working_srid must match population raster source"
                )
            if len(subject.windows) > self.policy.max_windows_per_subject:
                raise PopulationRasterSampleLimitError(
                    "population raster window limit exceeded for subject "
                    f"{subject.subject_id!r}: {len(subject.windows)} > "
                    f"{self.policy.max_windows_per_subject}"
                )
            for window in subject.windows:
                _validate_window_bounds(
                    window=window,
                    raster_height=self.source.height,
                    raster_width=self.source.width,
                )
                total_cells += window.cell_count

        if total_cells > self.policy.max_sample_cells:
            raise PopulationRasterSampleLimitError(
                "population raster sample-cell limit exceeded: "
                f"{total_cells} > {self.policy.max_sample_cells}"
            )

    def _sample_subject(
        self,
        subject: PopulationRasterSubject,
    ) -> PopulationRasterSample:
        valid_count = 0
        nodata_count = 0
        population_values: list[float] = []

        for window in subject.windows:
            raw = self.source.read_window(window=window)
            _validate_window_values(window=window, values=raw)
            for row in raw:
                for raw_value in row:
                    if raw_value is None:
                        if (
                            self.policy.nodata_policy
                            is PopulationRasterNoDataPolicy.REJECT
                        ):
                            raise PopulationRasterError(
                                "population raster nodata rejected for subject "
                                f"{subject.subject_id!r}"
                            )
                        nodata_count += 1
                        continue

                    value = float(raw_value)
                    valid_count += 1
                    population_values.append(
                        _cell_population(
                            value=value,
                            value_kind=self.source.value_kind,
                            cell_area_m2=float(self.source.cell_area_m2),
                        )
                    )

        requested_count = valid_count + nodata_count
        sampled_population = math.fsum(population_values)
        sampled_area = valid_count * float(self.source.cell_area_m2)
        mean_density = (
            sampled_population / sampled_area * 1_000_000.0
            if sampled_area > 0.0
            else 0.0
        )
        return PopulationRasterSample(
            subject_id=subject.subject_id,
            requested_cell_count=requested_count,
            valid_cell_count=valid_count,
            nodata_cell_count=nodata_count,
            sampled_area_m2=sampled_area,
            sampled_population=sampled_population,
            mean_density_per_km2=mean_density,
        )


def _cell_population(
    *,
    value: float,
    value_kind: PopulationRasterValueKind,
    cell_area_m2: float,
) -> float:
    if value_kind is PopulationRasterValueKind.POPULATION_PER_CELL:
        return value
    if value_kind is PopulationRasterValueKind.DENSITY_PER_KM2:
        return value * cell_area_m2 / 1_000_000.0
    raise PopulationRasterError(
        f"unsupported population raster value kind: {value_kind!r}"
    )


def _validate_source(source: PopulationRasterSource) -> None:
    try:
        working_srid = source.working_srid
        width = source.width
        height = source.height
        cell_area_m2 = source.cell_area_m2
        value_kind = source.value_kind
        read_window = source.read_window
    except AttributeError as exc:
        raise PopulationRasterError(
            "source must expose CRS, dimensions, cell area, value kind and read_window"
        ) from exc

    require_working_crs(working_srid)
    _require_positive_int("source width", width)
    _require_positive_int("source height", height)
    _require_positive_finite("source cell_area_m2", cell_area_m2)
    if not isinstance(value_kind, PopulationRasterValueKind):
        raise PopulationRasterError(
            "source value_kind must be a PopulationRasterValueKind"
        )
    if not callable(read_window):
        raise PopulationRasterError("source read_window must be callable")


def _validate_window_bounds(
    *,
    window: PopulationRasterWindow,
    raster_height: int,
    raster_width: int,
) -> None:
    if window.row_off + window.height > raster_height:
        raise PopulationRasterError(
            "population raster window exceeds source height"
        )
    if window.col_off + window.width > raster_width:
        raise PopulationRasterError(
            "population raster window exceeds source width"
        )


def _validate_window_values(
    *,
    window: PopulationRasterWindow,
    values: tuple[tuple[float | int | None, ...], ...],
) -> None:
    if not isinstance(values, tuple) or len(values) != window.height:
        raise PopulationRasterError(
            "source returned an unexpected population raster window height"
        )
    for row in values:
        if not isinstance(row, tuple) or len(row) != window.width:
            raise PopulationRasterError(
                "source returned an unexpected population raster window width"
            )
        for value in row:
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise PopulationRasterError(
                    "population raster samples must be numeric or None"
                )
            number = float(value)
            if not math.isfinite(number) or number < 0.0:
                raise PopulationRasterError(
                    "population raster samples must be finite and non-negative"
                )


def _validate_non_overlapping_windows(
    windows: tuple[PopulationRasterWindow, ...],
) -> None:
    for index, first in enumerate(windows):
        for second in windows[index + 1 :]:
            if _windows_overlap(first, second):
                raise PopulationRasterError(
                    "population raster windows within one subject must not overlap"
                )


def _windows_overlap(
    first: PopulationRasterWindow,
    second: PopulationRasterWindow,
) -> bool:
    return not (
        first.row_off + first.height <= second.row_off
        or second.row_off + second.height <= first.row_off
        or first.col_off + first.width <= second.col_off
        or second.col_off + second.width <= first.col_off
    )


def _require_id(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise PopulationRasterError(
            f"{field_name} must be a non-empty string"
        )
    if "\n" in value or "\r" in value:
        raise PopulationRasterError(
            f"{field_name} must not contain line breaks"
        )


def _require_positive_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise PopulationRasterError(
            f"{field_name} must be a positive integer"
        )


def _require_non_negative_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PopulationRasterError(
            f"{field_name} must be a non-negative integer"
        )


def _require_non_negative_finite(
    field_name: str,
    value: int | float,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PopulationRasterError(
            f"{field_name} must be a finite non-negative number"
        )
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise PopulationRasterError(
            f"{field_name} must be a finite non-negative number"
        )
    return number


def _require_positive_finite(
    field_name: str,
    value: int | float,
) -> float:
    number = _require_non_negative_finite(field_name, value)
    if number <= 0.0:
        raise PopulationRasterError(
            f"{field_name} must be greater than zero"
        )
    return number
