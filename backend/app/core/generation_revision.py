"""Identity of the backend image, not a revision supplied by a browser.

The build pipeline must pass a clean checkout's commit as a build argument.
The marker is baked into the image; it is not a cryptographic attestation.
"""

import re
from pathlib import Path

from backend.app.core.config import settings

_REVISION_FILE = Path(__file__).resolve().parents[3] / ".generation-revision"
_SHA = re.compile(r"^[0-9a-f]{40}$")


class GenerationRevisionError(ValueError):
    """Generation cannot truthfully be attributed to the requested revision."""


def build_revision() -> str | None:
    try:
        value = _REVISION_FILE.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError) as exc:
        raise GenerationRevisionError("cannot read backend build revision") from exc
    if not value:
        return None
    if _SHA.fullmatch(value) is None:
        raise GenerationRevisionError("invalid backend build revision marker")
    return value


def require_generation_revision(requested: str | None) -> None:
    actual = build_revision()
    if actual is None:
        if settings.app_env.casefold() == "production":
            raise GenerationRevisionError("production generation requires a backend build revision")
        # Local unlabelled development remains compatible with legacy callers.
        return
    if requested != actual:
        raise GenerationRevisionError(
            f"run revision does not match executing backend build: {actual}"
        )
