import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import BinaryIO, Final

from core.urban_generator.domain import (
    ArtifactContractError,
    ArtifactRef,
    ArtifactStat,
    ArtifactState,
    ArtifactStore,
    require_temporary_artifact_ref,
)

_METADATA_VERSION: Final = 1
_DEFAULT_CHUNK_SIZE: Final = 1024 * 1024


class LocalArtifactStore:
    """Filesystem ArtifactStore adapter rooted in one configured directory."""

    def __init__(
        self,
        root: str | os.PathLike[str],
        *,
        chunk_size: int = _DEFAULT_CHUNK_SIZE,
    ) -> None:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")

        root_path = Path(root).expanduser()
        root_path.mkdir(parents=True, exist_ok=True)
        self._root = root_path.resolve()
        self._chunk_size = chunk_size
        self._payload_roots = {
            ArtifactState.TEMPORARY: self._root / "temporary",
            ArtifactState.READY: self._root / "ready",
        }
        self._metadata_roots = {
            ArtifactState.TEMPORARY: self._root / ".metadata" / "temporary",
            ArtifactState.READY: self._root / ".metadata" / "ready",
        }
        for directory in (*self._payload_roots.values(), *self._metadata_roots.values()):
            directory.mkdir(parents=True, exist_ok=True)

    def put(
        self,
        ref: ArtifactRef,
        source: BinaryIO,
        *,
        content_type: str | None = None,
    ) -> ArtifactStat:
        require_temporary_artifact_ref(ref)
        # Validate optional metadata before touching storage.
        ArtifactStat(
            ref=ref,
            size_bytes=0,
            checksum="sha256:" + "0" * 64,
            content_type=content_type,
        )

        ready_ref = ref.as_ready()
        if self._artifact_exists(ready_ref):
            raise ArtifactContractError("ready artifact already exists")

        payload_path = self._payload_path(ref)
        metadata_path = self._metadata_path(ref)
        self._ensure_parent(self._payload_roots[ref.state], payload_path.parent)
        self._ensure_parent(self._metadata_roots[ref.state], metadata_path.parent)

        payload_stage: Path | None = None
        metadata_stage: Path | None = None
        try:
            payload_stage, size_bytes, checksum = self._stage_payload(payload_path, source)
            stat = ArtifactStat(
                ref=ref,
                size_bytes=size_bytes,
                checksum=checksum,
                content_type=content_type,
            )
            metadata_stage = self._stage_metadata(metadata_path, stat)

            os.replace(payload_stage, payload_path)
            payload_stage = None
            os.replace(metadata_stage, metadata_path)
            metadata_stage = None
            return stat
        finally:
            self._unlink_if_exists(payload_stage)
            self._unlink_if_exists(metadata_stage)

    def open(self, ref: ArtifactRef) -> BinaryIO:
        payload_path = self._payload_path(ref)
        if not self._regular_file_exists(payload_path):
            raise KeyError(ref)
        return payload_path.open("rb")

    def stat(self, ref: ArtifactRef) -> ArtifactStat:
        payload_path = self._payload_path(ref)
        metadata_path = self._metadata_path(ref)
        if not self._regular_file_exists(payload_path):
            raise KeyError(ref)
        if not self._regular_file_exists(metadata_path):
            raise ArtifactContractError(f"artifact metadata is missing for {ref.key}")

        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ArtifactContractError(f"artifact metadata is invalid for {ref.key}") from exc

        if metadata.get("version") != _METADATA_VERSION:
            raise ArtifactContractError(f"unsupported artifact metadata version for {ref.key}")

        stat = ArtifactStat(
            ref=ref,
            size_bytes=metadata.get("size_bytes"),
            checksum=metadata.get("checksum"),
            content_type=metadata.get("content_type"),
        )
        if payload_path.stat().st_size != stat.size_bytes:
            raise ArtifactContractError(
                f"artifact payload size does not match metadata for {ref.key}"
            )
        return stat

    def delete(self, ref: ArtifactRef) -> None:
        payload_path = self._payload_path(ref)
        metadata_path = self._metadata_path(ref)
        self._unlink_if_exists(payload_path)
        self._unlink_if_exists(metadata_path)
        self._prune_empty_parents(payload_path.parent, self._payload_roots[ref.state])
        self._prune_empty_parents(metadata_path.parent, self._metadata_roots[ref.state])

    def promote(self, ref: ArtifactRef) -> ArtifactStat:
        if ref.state is ArtifactState.READY:
            return self.stat(ref)
        require_temporary_artifact_ref(ref)

        ready_ref = ref.as_ready()
        temporary_payload = self._payload_path(ref)
        temporary_metadata = self._metadata_path(ref)
        ready_payload = self._payload_path(ready_ref)
        ready_metadata = self._metadata_path(ready_ref)

        temporary_payload_exists = self._regular_file_exists(temporary_payload)
        temporary_metadata_exists = self._regular_file_exists(temporary_metadata)
        ready_payload_exists = self._regular_file_exists(ready_payload)
        ready_metadata_exists = self._regular_file_exists(ready_metadata)

        if ready_payload_exists and ready_metadata_exists:
            if temporary_payload_exists or temporary_metadata_exists:
                raise ArtifactContractError("ready artifact already exists")
            return self.stat(ready_ref)

        if not any(
            (
                temporary_payload_exists,
                temporary_metadata_exists,
                ready_payload_exists,
                ready_metadata_exists,
            )
        ):
            raise KeyError(ref)

        if ready_payload_exists and temporary_payload_exists:
            raise ArtifactContractError("ready artifact already exists")
        if ready_metadata_exists and temporary_metadata_exists:
            raise ArtifactContractError("ready artifact already exists")
        if not (temporary_payload_exists or ready_payload_exists):
            raise ArtifactContractError(f"artifact payload is missing for {ref.key}")
        if not (temporary_metadata_exists or ready_metadata_exists):
            raise ArtifactContractError(f"artifact metadata is missing for {ref.key}")

        self._ensure_parent(self._payload_roots[ArtifactState.READY], ready_payload.parent)
        self._ensure_parent(self._metadata_roots[ArtifactState.READY], ready_metadata.parent)

        # These two replaces are individually atomic and retry-safe. The mixed-state
        # branches above finish an interrupted promotion on the next attempt.
        if temporary_payload_exists:
            os.replace(temporary_payload, ready_payload)
        if temporary_metadata_exists:
            os.replace(temporary_metadata, ready_metadata)

        self._prune_empty_parents(
            temporary_payload.parent,
            self._payload_roots[ArtifactState.TEMPORARY],
        )
        self._prune_empty_parents(
            temporary_metadata.parent,
            self._metadata_roots[ArtifactState.TEMPORARY],
        )
        return self.stat(ready_ref)

    def _stage_payload(self, destination: Path, source: BinaryIO) -> tuple[Path, int, str]:
        digest = hashlib.sha256()
        size_bytes = 0
        handle = tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
        )
        stage_path = Path(handle.name)
        try:
            with handle:
                while True:
                    chunk = source.read(self._chunk_size)
                    if not chunk:
                        break
                    if not isinstance(chunk, bytes):
                        raise TypeError("artifact source must yield bytes")
                    handle.write(chunk)
                    digest.update(chunk)
                    size_bytes += len(chunk)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            self._unlink_if_exists(stage_path)
            raise
        return stage_path, size_bytes, f"sha256:{digest.hexdigest()}"

    def _stage_metadata(self, destination: Path, stat: ArtifactStat) -> Path:
        handle = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
        )
        stage_path = Path(handle.name)
        try:
            with handle:
                json.dump(
                    {
                        "version": _METADATA_VERSION,
                        "size_bytes": stat.size_bytes,
                        "checksum": stat.checksum,
                        "content_type": stat.content_type,
                    },
                    handle,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            self._unlink_if_exists(stage_path)
            raise
        return stage_path

    def _artifact_exists(self, ref: ArtifactRef) -> bool:
        return self._path_exists(self._payload_path(ref)) or self._path_exists(
            self._metadata_path(ref)
        )

    def _payload_path(self, ref: ArtifactRef) -> Path:
        return self._safe_path(self._payload_roots[ref.state], ref.key)

    def _metadata_path(self, ref: ArtifactRef) -> Path:
        relative = Path(*ref.key.split("/"))
        metadata_key = relative.parent / f"{relative.name}.json"
        return self._safe_path(self._metadata_roots[ref.state], metadata_key.as_posix())

    def _safe_path(self, base: Path, key: str) -> Path:
        candidate = base.joinpath(*key.split("/"))
        try:
            resolved = candidate.resolve(strict=False)
        except OSError as exc:
            raise ArtifactContractError(f"artifact path cannot be resolved safely: {key}") from exc
        resolved_base = base.resolve(strict=False)
        if not resolved_base.is_relative_to(self._root):
            raise ArtifactContractError("artifact namespace escapes configured storage root")
        if not resolved.is_relative_to(resolved_base):
            raise ArtifactContractError("artifact path escapes configured storage root")
        return candidate

    def _ensure_parent(self, base: Path, parent: Path) -> None:
        relative = parent.relative_to(base)
        current = base
        for segment in relative.parts:
            current = current / segment
            if current.exists():
                if current.is_symlink() or not current.is_dir():
                    raise ArtifactContractError("artifact path contains a non-directory component")
                continue
            current.mkdir()
            if current.is_symlink():
                raise ArtifactContractError("artifact path contains a symlink component")

    def _regular_file_exists(self, path: Path) -> bool:
        if not self._path_exists(path):
            return False
        if path.is_symlink() or not path.is_file():
            raise ArtifactContractError("artifact storage entry must be a regular file")
        # Resolving here also rejects a symlink introduced into a parent after path construction.
        self._assert_within_root(path)
        return True

    def _path_exists(self, path: Path) -> bool:
        return path.exists() or path.is_symlink()

    def _assert_within_root(self, path: Path) -> None:
        try:
            resolved = path.resolve(strict=False)
        except OSError as exc:
            raise ArtifactContractError("artifact path cannot be resolved safely") from exc
        if not resolved.is_relative_to(self._root):
            raise ArtifactContractError("artifact path escapes configured storage root")

    @staticmethod
    def _unlink_if_exists(path: Path | None) -> None:
        if path is None:
            return
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    @staticmethod
    def _prune_empty_parents(start: Path, stop: Path) -> None:
        current = start
        while current != stop:
            try:
                current.rmdir()
            except OSError:
                return
            current = current.parent


assert issubclass(LocalArtifactStore, ArtifactStore)
