from sqlalchemy import text
from sqlalchemy.orm import Session


def probe_database(session: Session) -> None:
    """Run the bounded SQL readiness probe outside the HTTP controller."""

    session.execute(text("SELECT 1"))
