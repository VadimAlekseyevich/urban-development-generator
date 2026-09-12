import re
from dataclasses import dataclass
from enum import StrEnum
from typing import BinaryIO, Protocol, runtime_checkable


class ArtifactContractError(ValueError):
    """Raised when an artifact-store value object violates the public contract."""


class ArtifactState(StrEnum):
    """Storage visibility state exposed by the core artifact contract."""

    TEMPORARY = "temporary"
    READY = "ready"


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    """Opaque logical artifact reference with no filesystem or storage URI leakage."""

    key: str
    state: ArtifactState = ArtifactState.TEMPORARY

    def __post_init__(self) -> None:
        _validate_artifact_key(self.key)
        if not isinstance(self.state, ArtifactState):
            raise ArtifactContractError("artifact state must be an ArtifactState value")

    def as_ready(self) -> "ArtifactRef":
        if self.state is ArtifactState.READY:
            return self
        return ArtifactRef(key=self.key, state=ArtifactState.READY)


@dataclass(frozen=True, slots=True)
class ArtifactStat:
    """Stable metadata returned by ArtifactStore implementations."""

    ref: ArtifactRef
    size_bytes: int
    checksum: str
    content_type: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.ref, ArtifactRef):
            raise ArtifactContractError("artifact stat ref must be an ArtifactRef")
        if isinstance(self.size_bytes, bool) or not isinstance(self.size_bytes, int):
            raise ArtifactContractError("artifact size_bytes must be an integer")
        if self.size_bytes < 0:
            raise ArtifactContractError("artifact size_bytes must be non-negative")
        if not isinstance(self.checksum, str) or _CHECKSUM_RE.fullmatch(self.checksum) is None:
            raise ArtifactContractError("artifact checksum must use sha256:<64 lowercase hex chars>")
        if self.content_type is not None:
            if not isinstance(self.content_type, str) or not self.content_type.strip():
                raise ArtifactContractError("artifact content_type must be a non-empty string or None")
            if "\n" in self.content_type or "\r" in self.content_type:
                raise ArtifactContractError("artifact content_type must not contain line breaks")


@runtime_checkable
class ArtifactStore(Protocol):
    """Storage-agnostic port for temporary and ready artifact blobs."""

    def put(
        self,
        ref: ArtifactRef,
        source: BinaryIO,
        *,
        content_type: str | None = None,
    ) -> ArtifactStat:
        """Write or replace a temporary artifact and return its computed metadata."""

    def open(self, ref: ArtifactRef) -> BinaryIO:
        """Open an artifact as a binary stream without exposing its backing storage path."""

    def stat(self, ref: ArtifactRef) -> ArtifactStat:
        """Return immutable artifact metadata for the referenced object."""

    def delete(self, ref: ArtifactRef) -> None:
        """Delete an artifact; implementations should make repeated deletes safe."""

    def promote(self, ref: ArtifactRef) -> ArtifactStat:
        """Promote temporary content to ready and support idempotent retries."""


def require_temporary_artifact_ref(ref: ArtifactRef) -> ArtifactRef:
    """Validate that a write target belongs to the temporary namespace."""

    if not isinstance(ref, ArtifactRef):
        raise ArtifactContractError("artifact ref must be an ArtifactRef")
    if ref.state is not ArtifactState.TEMPORARY:
        raise ArtifactContractError("artifact put target must be temporary")
    return ref


def _validate_artifact_key(key: str) -> None:
    if not isinstance(key, str) or not key.strip():
        raise ArtifactContractError("artifact key must be a non-empty string")
    if len(key) > 512:
        raise ArtifactContractError("artifact key must be at most 512 characters")
    if "\x00" in key:
        raise ArtifactContractError("artifact key must not contain NUL bytes")
    if key.startswith("/") or "\\" in key or _WINDOWS_DRIVE_RE.match(key):
        raise ArtifactContractError("artifact key must be logical, not an absolute filesystem path")
    if "://" in key:
        raise ArtifactContractError("artifact key must not expose a storage URI")

    segments = key.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise ArtifactContractError("artifact key must not contain empty, dot, or parent segments")


_CHECKSUM_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")
