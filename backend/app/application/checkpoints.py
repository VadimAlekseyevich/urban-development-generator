from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Protocol

from core.urban_generator.domain import (
    StageContractError,
    StageFingerprint,
    validate_stage_metadata,
)

HashPart = str | bytes


class _Digest(Protocol):
    def update(self, data: bytes, /) -> None: ...

    def hexdigest(self) -> str: ...


class CheckpointContractError(ValueError):
    """Raised when checkpoint provenance is incomplete or inconsistent."""


@dataclass(frozen=True, slots=True)
class DependencyOutputFingerprint:
    """Canonical output identity of one direct stage dependency."""

    stage_name: str
    output_fingerprint: StageFingerprint

    def __post_init__(self) -> None:
        _validate_stage_identity(self.stage_name, "checkpoint")
        if not isinstance(self.output_fingerprint, StageFingerprint):
            raise CheckpointContractError(
                "dependency output_fingerprint must be a StageFingerprint"
            )


@dataclass(frozen=True, slots=True)
class CheckpointIdentity:
    """Exact provenance key used to decide whether one stage result can be reused."""

    stage_name: str
    stage_version: str
    input_hash: str
    config_hash: str

    def __post_init__(self) -> None:
        _validate_stage_identity(self.stage_name, self.stage_version)
        _require_hash("input_hash", self.input_hash)
        _require_hash("config_hash", self.config_hash)

    def is_eligible_match(self, candidate: CheckpointIdentity) -> bool:
        """Return whether a persisted candidate has exactly matching checkpoint provenance."""

        if not isinstance(candidate, CheckpointIdentity):
            return False
        return self == candidate


def build_resolved_input_hash(
    *,
    input_parts: tuple[HashPart, ...],
    expected_dependencies: tuple[str, ...],
    dependency_outputs: tuple[DependencyOutputFingerprint, ...],
) -> str:
    """Hash resolved inputs plus ordered direct-dependency output fingerprints.

    expected_dependencies must use the exact order from canonical Stage.dependencies.
    Supplied dependency outputs must match that order one-for-one. The current stage's
    own output fingerprint is intentionally absent because it is produced only after
    execution and is persisted as separate provenance.
    """

    _require_parts_tuple("input_parts", input_parts)
    _require_dependency_names(expected_dependencies)
    if not isinstance(dependency_outputs, tuple):
        raise CheckpointContractError(
            "dependency_outputs must be an immutable tuple"
        )
    if any(
        not isinstance(item, DependencyOutputFingerprint)
        for item in dependency_outputs
    ):
        raise CheckpointContractError(
            "dependency_outputs must contain only DependencyOutputFingerprint values"
        )

    actual_dependencies = tuple(item.stage_name for item in dependency_outputs)
    if actual_dependencies != expected_dependencies:
        raise CheckpointContractError(
            "dependency output order must exactly match Stage.dependencies"
        )
    if not input_parts and not dependency_outputs:
        raise CheckpointContractError(
            "resolved input identity requires input parts or dependency outputs"
        )

    digest = _new_digest("resolved-stage-input:v1")
    _update_parts(digest, input_parts)
    digest.update(len(dependency_outputs).to_bytes(8, "big", signed=False))
    for dependency in dependency_outputs:
        _update_text(digest, dependency.stage_name)
        _update_text(digest, dependency.output_fingerprint.value)
    return f"sha256:{digest.hexdigest()}"


def build_config_hash(*parts: HashPart) -> str:
    """Hash only resolved stage configuration parts in a separate namespace."""

    digest = _new_digest("resolved-stage-config:v1")
    _update_parts(digest, parts)
    return f"sha256:{digest.hexdigest()}"


def _new_digest(namespace: str) -> _Digest:
    digest = hashlib.sha256()
    digest.update(b"urban-development-generator:checkpoint-provenance:")
    digest.update(namespace.encode("ascii"))
    digest.update(b"\0")
    return digest


def _update_parts(
    digest: _Digest,
    parts: tuple[HashPart, ...],
) -> None:
    digest.update(len(parts).to_bytes(8, "big", signed=False))
    for part in parts:
        if isinstance(part, str):
            marker = b"s"
            payload = part.encode("utf-8")
        elif isinstance(part, bytes):
            marker = b"b"
            payload = part
        else:
            raise CheckpointContractError(
                "checkpoint hash parts must be str or bytes"
            )
        digest.update(marker)
        digest.update(len(payload).to_bytes(8, "big", signed=False))
        digest.update(payload)


def _update_text(digest: _Digest, value: str) -> None:
    payload = value.encode("utf-8")
    digest.update(len(payload).to_bytes(8, "big", signed=False))
    digest.update(payload)


def _require_parts_tuple(
    field_name: str,
    parts: tuple[HashPart, ...],
) -> None:
    if not isinstance(parts, tuple):
        raise CheckpointContractError(
            f"{field_name} must be an immutable tuple"
        )
    for part in parts:
        if not isinstance(part, (str, bytes)):
            raise CheckpointContractError(
                "checkpoint hash parts must be str or bytes"
            )


def _require_dependency_names(dependencies: tuple[str, ...]) -> None:
    if not isinstance(dependencies, tuple):
        raise CheckpointContractError(
            "expected_dependencies must be an immutable tuple"
        )
    seen: set[str] = set()
    for dependency in dependencies:
        _validate_stage_identity(dependency, "checkpoint")
        if dependency in seen:
            raise CheckpointContractError(
                f"duplicate expected dependency: {dependency}"
            )
        seen.add(dependency)


def _validate_stage_identity(stage_name: str, stage_version: str) -> None:
    try:
        validate_stage_metadata(stage_name, stage_version, ())
    except StageContractError as exc:
        raise CheckpointContractError(str(exc)) from exc


def _require_hash(field_name: str, value: str) -> None:
    if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
        raise CheckpointContractError(
            f"{field_name} must use sha256:<64 lowercase hex chars>"
        )


_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
