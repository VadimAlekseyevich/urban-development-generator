import uuid

from pydantic import BaseModel, Field

from core.urban_generator.domain import ArtifactState


class ArtifactUploadRead(BaseModel):
    artifact_id: uuid.UUID
    key: str
    filename: str
    state: ArtifactState
    size_bytes: int = Field(ge=0)
    checksum: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    content_type: str | None = None
