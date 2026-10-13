import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, TypeVar, runtime_checkable

from core.urban_generator.domain.run_context import RunContext
from core.urban_generator.domain.territory import TerritorySnapshot


class StageContractError(ValueError):
    """Raised when a stage contract or result is malformed."""


class StageDiagnosticLevel(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"


@dataclass(frozen=True, slots=True)
class StageDiagnostic:
    """Structured non-fatal information emitted by a stage execution."""

    code: str
    message: str
    level: StageDiagnosticLevel = StageDiagnosticLevel.INFO

    def __post_init__(self) -> None:
        _require_pattern("diagnostic code", self.code, _DIAGNOSTIC_CODE_RE)
        if not isinstance(self.message, str) or not self.message.strip():
            raise StageContractError("diagnostic message must be a non-empty string")
        if not isinstance(self.level, StageDiagnosticLevel):
            raise StageContractError("diagnostic level must be a StageDiagnosticLevel value")


@dataclass(frozen=True, slots=True)
class StageFingerprint:
    """Stable content fingerprint produced from canonical stage-defined parts."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or _FINGERPRINT_RE.fullmatch(self.value) is None:
            raise StageContractError("fingerprint must use sha256:<64 lowercase hex chars>")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class StageResult[T]:
    """Immutable typed output of one stage execution."""

    output: T
    fingerprint: StageFingerprint
    diagnostics: tuple[StageDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.fingerprint, StageFingerprint):
            raise StageContractError("fingerprint must be a StageFingerprint")
        if not isinstance(self.diagnostics, tuple):
            raise StageContractError("diagnostics must be an immutable tuple")
        if any(not isinstance(item, StageDiagnostic) for item in self.diagnostics):
            raise StageContractError("diagnostics must contain only StageDiagnostic values")


StageInputT = TypeVar("StageInputT")
StageConfigT_contra = TypeVar("StageConfigT_contra", contravariant=True)
StageOutputT_co = TypeVar("StageOutputT_co", covariant=True)


@runtime_checkable
class Stage(Protocol[StageInputT, StageConfigT_contra, StageOutputT_co]):
    """Infrastructure-independent contract implemented by generation stages."""

    name: str
    version: str
    dependencies: tuple[str, ...]

    def validate_input(self, value: object) -> StageInputT:
        """Validate and narrow an untrusted stage input value."""

    def execute(
        self,
        *,
        snapshot: TerritorySnapshot,
        context: RunContext,
        stage_input: StageInputT,
        config: StageConfigT_contra,
    ) -> StageResult[StageOutputT_co]:
        """Execute the stage without HTTP, persistence, queue, or UI dependencies."""


def validate_stage_metadata(name: str, version: str, dependencies: tuple[str, ...]) -> None:
    """Validate stable stage identity and dependency names."""

    _require_pattern("stage name", name, _STAGE_NAME_RE)
    _require_pattern("stage version", version, _STAGE_VERSION_RE)
    if not isinstance(dependencies, tuple):
        raise StageContractError("stage dependencies must be an immutable tuple")

    seen: set[str] = set()
    for dependency in dependencies:
        _require_pattern("stage dependency", dependency, _STAGE_NAME_RE)
        if dependency == name:
            raise StageContractError("stage cannot depend on itself")
        if dependency in seen:
            raise StageContractError(f"duplicate stage dependency: {dependency}")
        seen.add(dependency)


def require_stage_input[T](
    value: object,
    expected_type: type[T],
    *,
    stage_name: str,
) -> T:
    """Reusable runtime type guard for simple typed stage inputs."""

    _require_pattern("stage name", stage_name, _STAGE_NAME_RE)
    if not isinstance(value, expected_type):
        raise StageContractError(
            f"{stage_name} input must be {expected_type.__name__}, got {type(value).__name__}"
        )
    return value


class StageFingerprintWriter:
    """Bounded-memory streaming form of the canonical v1 stage fingerprint.

    Each part must have its full byte length before streaming. The wire format
    remains: type marker, unsigned 8-byte length, followed by exact raw bytes.
    """

    def __init__(self) -> None:
        self._digest = hashlib.sha256()
        self._digest.update(b"urban-development-generator:stage-fingerprint:v1\0")
        self._parts = 0

    def add_text(self, value: str) -> None:
        if not isinstance(value, str):
            raise StageContractError("stage fingerprint text part must be str")
        self._add(b"s", value.encode("utf-8"))

    def add_bytes(self, value: bytes) -> None:
        if not isinstance(value, bytes):
            raise StageContractError("stage fingerprint binary part must be bytes")
        self._add(b"b", value)

    def _add(self, marker: bytes, payload: bytes) -> None:
        self._digest.update(marker)
        self._digest.update(len(payload).to_bytes(8, "big", signed=False))
        self._digest.update(payload)
        self._parts += 1

    def add_binary_chunks(self, *, byte_length: int, chunks: Iterable[bytes]) -> None:
        """Hash a byte stream as ONE canonical bytes part, rejecting truncation."""

        if isinstance(byte_length, bool) or not isinstance(byte_length, int):
            raise StageContractError("binary fingerprint length must be an integer")
        if byte_length < 0 or byte_length >= 1 << 64:
            raise StageContractError("binary fingerprint length is outside uint64")
        self._digest.update(b"b")
        self._digest.update(byte_length.to_bytes(8, "big", signed=False))
        consumed = 0
        for chunk in chunks:
            if not isinstance(chunk, bytes):
                raise StageContractError("fingerprint stream must yield bytes")
            consumed += len(chunk)
            if consumed > byte_length:
                raise StageContractError("fingerprint stream exceeds declared length")
            self._digest.update(chunk)
        if consumed != byte_length:
            raise StageContractError("fingerprint stream is shorter than declared length")
        self._parts += 1

    def finalize(self) -> StageFingerprint:
        if self._parts == 0:
            raise StageContractError("stage fingerprint requires at least one canonical part")
        return StageFingerprint(value=f"sha256:{self._digest.hexdigest()}")


def build_stage_fingerprint(*parts: str | bytes) -> StageFingerprint:
    """Hash canonical ordered parts without ambiguous string concatenation."""

    writer = StageFingerprintWriter()
    for part in parts:
        if isinstance(part, str):
            writer.add_text(part)
        elif isinstance(part, bytes):
            writer.add_bytes(part)
        else:
            raise StageContractError("stage fingerprint parts must be str or bytes")
    return writer.finalize()


def _require_pattern(field_name: str, value: str, pattern: re.Pattern[str]) -> None:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise StageContractError(f"invalid {field_name}: {value!r}")


_STAGE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_STAGE_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")
_DIAGNOSTIC_CODE_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_FINGERPRINT_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
