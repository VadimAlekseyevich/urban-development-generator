import re
import unicodedata
import uuid
from contextlib import suppress
from dataclasses import dataclass
from typing import BinaryIO, Protocol, cast

from backend.app.models.artifact import Artifact, ArtifactLifecycleState
from core.urban_generator.domain import ArtifactRef, ArtifactStat, ArtifactStore

_MAX_FILENAME_LENGTH = 180
_RESERVED_WINDOWS_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)


class UploadTooLargeError(ValueError):
    """Raised when an upload exceeds the configured per-file limit."""

    def __init__(self, *, max_size_bytes: int, observed_size_bytes: int) -> None:
        self.max_size_bytes = max_size_bytes
        self.observed_size_bytes = observed_size_bytes
        super().__init__(
            f"upload exceeds {max_size_bytes} byte limit "
            f"(observed at least {observed_size_bytes} bytes)"
        )


class ArtifactRepository(Protocol):
    """Persistence port for storage metadata created by the upload use case."""

    def create(self, artifact: Artifact) -> Artifact: ...


@dataclass(frozen=True, slots=True)
class UploadedArtifact:
    """Application result for one completed, persisted upload."""

    artifact: Artifact
    stat: ArtifactStat
    filename: str


class UploadService:
    """Coordinate bounded upload streaming, artifact promotion and DB metadata."""

    def __init__(
        self,
        store: ArtifactStore,
        repository: ArtifactRepository,
        *,
        max_size_bytes: int,
    ) -> None:
        if max_size_bytes <= 0:
            raise ValueError("max_size_bytes must be positive")
        self._store = store
        self._repository = repository
        self._max_size_bytes = max_size_bytes

    def upload(
        self,
        source: BinaryIO,
        *,
        filename: str | None,
        content_type: str | None,
        size_hint: int | None = None,
    ) -> UploadedArtifact:
        if size_hint is not None:
            if size_hint < 0:
                raise ValueError("size_hint must be non-negative")
            if size_hint > self._max_size_bytes:
                raise UploadTooLargeError(
                    max_size_bytes=self._max_size_bytes,
                    observed_size_bytes=size_hint,
                )

        safe_filename = sanitize_upload_filename(filename)
        temporary_ref = ArtifactRef(key=f"uploads/{uuid.uuid4().hex}/{safe_filename}")
        limited_source = _SizeLimitedReader(
            source,
            max_size_bytes=self._max_size_bytes,
        )
        normalized_content_type = _normalize_content_type(content_type)

        try:
            self._store.put(
                temporary_ref,
                cast(BinaryIO, limited_source),
                content_type=normalized_content_type,
            )
            ready_stat = self._store.promote(temporary_ref)
            artifact = Artifact(
                uri=_artifact_uri(ready_stat.ref),
                checksum=ready_stat.checksum,
                size_bytes=ready_stat.size_bytes,
                content_type=ready_stat.content_type,
                state=ArtifactLifecycleState.READY.value,
                owner_type=None,
                owner_id=None,
            )
            persisted = self._repository.create(artifact)
        except Exception:
            self._cleanup(temporary_ref)
            raise

        return UploadedArtifact(
            artifact=persisted,
            stat=ready_stat,
            filename=safe_filename,
        )

    def _cleanup(self, temporary_ref: ArtifactRef) -> None:
        for ref in (temporary_ref, temporary_ref.as_ready()):
            with suppress(Exception):
                self._store.delete(ref)


def sanitize_upload_filename(filename: str | None) -> str:
    """Return a safe leaf filename without trusting browser-provided paths."""

    normalized = unicodedata.normalize("NFKC", filename or "")
    leaf = normalized.replace("\\", "/").rsplit("/", 1)[-1].strip()
    sanitized = "".join(
        character if character.isalnum() or character in "._-" else "_"
        for character in leaf
    )
    sanitized = re.sub(r"_+", "_", sanitized)
    sanitized = re.sub(r"_+\.", ".", sanitized).strip(" ._-")
    if not sanitized:
        sanitized = "upload.bin"

    stem = sanitized.split(".", 1)[0].upper()
    if stem in _RESERVED_WINDOWS_NAMES:
        sanitized = f"_{sanitized}"

    if len(sanitized) > _MAX_FILENAME_LENGTH:
        dot_index = sanitized.rfind(".")
        suffix = (
            sanitized[dot_index:]
            if dot_index > 0 and len(sanitized) - dot_index <= 24
            else ""
        )
        stem_part = sanitized[: -len(suffix)] if suffix else sanitized
        stem_limit = _MAX_FILENAME_LENGTH - len(suffix)
        sanitized = stem_part[:stem_limit].rstrip(" ._-") + suffix
        if not sanitized:
            sanitized = "upload.bin"
    return sanitized


class _SizeLimitedReader:
    def __init__(self, source: BinaryIO, *, max_size_bytes: int) -> None:
        self._source = source
        self._max_size_bytes = max_size_bytes
        self._bytes_read = 0

    def read(self, size: int = -1) -> bytes:
        if size == 0:
            return b""

        remaining_probe = self._max_size_bytes - self._bytes_read + 1
        request_size = remaining_probe if size < 0 else min(size, remaining_probe)
        chunk = self._source.read(request_size)
        if not isinstance(chunk, bytes):
            raise TypeError("artifact upload source must yield bytes")

        self._bytes_read += len(chunk)
        if self._bytes_read > self._max_size_bytes:
            raise UploadTooLargeError(
                max_size_bytes=self._max_size_bytes,
                observed_size_bytes=self._bytes_read,
            )
        return chunk


def _artifact_uri(ref: ArtifactRef) -> str:
    return f"artifact://{ref.key}"


def _normalize_content_type(content_type: str | None) -> str | None:
    if content_type is None:
        return None
    normalized = content_type.strip()
    return normalized or None
