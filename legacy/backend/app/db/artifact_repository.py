from sqlalchemy.orm import Session

from backend.app.models.artifact import Artifact


class SqlAlchemyArtifactRepository:
    """SQLAlchemy adapter for uploaded artifact metadata."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, artifact: Artifact) -> Artifact:
        self._session.add(artifact)
        try:
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise
        return artifact
