import uuid

from sqlalchemy.orm import Session

from backend.app.application.suitability_layers import SuitabilityArtifactRecord
from backend.app.models.artifact import Artifact


class SqlAlchemySuitabilityArtifactRepository:
    """Read artifact metadata needed by the suitability visualization service."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, *, artifact_id: uuid.UUID) -> SuitabilityArtifactRecord | None:
        artifact = self._session.get(Artifact, artifact_id)
        if artifact is None:
            return None
        return SuitabilityArtifactRecord(
            id=artifact.id,
            uri=artifact.uri,
            checksum=artifact.checksum,
            size_bytes=artifact.size_bytes,
            content_type=artifact.content_type,
            state=artifact.state,
        )
