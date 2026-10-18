"""SQLAlchemy iş birimi: bir oturum, bir hareket."""

from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from .repositories import (
    SqlAnalysisRunRepository,
    SqlArtifactRepository,
    SqlCandidateRepository,
    SqlDiseaseRepository,
    SqlFileRepository,
    SqlUserRepository,
)


class SqlUnitOfWork:
    def __init__(self, session_factory: sessionmaker):
        self._factory = session_factory
        self._session: Session = None

    def __enter__(self) -> SqlUnitOfWork:
        self._session = self._factory()
        self.users = SqlUserRepository(self._session)
        self.diseases = SqlDiseaseRepository(self._session)
        self.runs = SqlAnalysisRunRepository(self._session)
        self.candidates = SqlCandidateRepository(self._session)
        self.artifacts = SqlArtifactRepository(self._session)
        self.files = SqlFileRepository(self._session)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if exc_type is not None:
                self._session.rollback()
            else:
                self._session.rollback()  # commit edilmemiş değişiklikler hiçbir zaman sessizce yazılmaz
        finally:
            self._session.close()

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()


def make_uow_factory(session_factory: sessionmaker):
    return lambda: SqlUnitOfWork(session_factory)
