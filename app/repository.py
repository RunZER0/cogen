from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from abc import ABC, abstractmethod
from pathlib import Path
from threading import RLock

from app.domain import AnalysisJob, Venture


class VentureRepository(ABC):
    @abstractmethod
    def save_venture(self, venture: Venture) -> Venture: ...

    @abstractmethod
    def get_venture(self, venture_id: str) -> Venture | None: ...

    @abstractmethod
    def list_ventures(self) -> list[Venture]: ...

    @abstractmethod
    def save_job(self, job: AnalysisJob) -> AnalysisJob: ...

    @abstractmethod
    def get_job(self, job_id: str) -> AnalysisJob | None: ...


class SQLiteRepository(VentureRepository):
    def __init__(self, path: str):
        self.path = path
        self._lock = RLock()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _connection(self):
        con = self._connect()
        try:
            yield con
            con.commit()
        finally:
            con.close()

    def _init_db(self) -> None:
        with self._connection() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS ventures (
                    id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def save_venture(self, venture: Venture) -> Venture:
        payload = venture.model_dump_json()
        with self._lock, self._connection() as con:
            con.execute(
                """
                INSERT INTO ventures(id, payload, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at
                """,
                (venture.id, payload, venture.updated_at.isoformat()),
            )
        return venture

    def get_venture(self, venture_id: str) -> Venture | None:
        with self._connection() as con:
            row = con.execute("SELECT payload FROM ventures WHERE id=?", (venture_id,)).fetchone()
        return Venture.model_validate_json(row["payload"]) if row else None

    def list_ventures(self) -> list[Venture]:
        with self._connection() as con:
            rows = con.execute("SELECT payload FROM ventures ORDER BY updated_at DESC").fetchall()
        return [Venture.model_validate_json(row["payload"]) for row in rows]

    def save_job(self, job: AnalysisJob) -> AnalysisJob:
        payload = job.model_dump_json()
        with self._lock, self._connection() as con:
            con.execute(
                """
                INSERT INTO jobs(id, payload, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at
                """,
                (job.id, payload, job.updated_at.isoformat()),
            )
        return job

    def get_job(self, job_id: str) -> AnalysisJob | None:
        with self._connection() as con:
            row = con.execute("SELECT payload FROM jobs WHERE id=?", (job_id,)).fetchone()
        return AnalysisJob.model_validate_json(row["payload"]) if row else None


class FirestoreRepository(VentureRepository):
    def __init__(self, project: str | None = None, database: str = "(default)"):
        from google.cloud import firestore

        self._firestore = firestore
        self.client = firestore.Client(project=project, database=database)
        self.ventures = self.client.collection("ventures")
        self.jobs = self.client.collection("analysis_jobs")

    def save_venture(self, venture: Venture) -> Venture:
        self.ventures.document(venture.id).set(venture.model_dump(mode="json"))
        return venture

    def get_venture(self, venture_id: str) -> Venture | None:
        snapshot = self.ventures.document(venture_id).get()
        return Venture.model_validate(snapshot.to_dict()) if snapshot.exists else None

    def list_ventures(self) -> list[Venture]:
        docs = self.ventures.order_by(
            "updated_at", direction=self._firestore.Query.DESCENDING
        ).stream()
        return [Venture.model_validate(doc.to_dict()) for doc in docs]

    def save_job(self, job: AnalysisJob) -> AnalysisJob:
        self.jobs.document(job.id).set(job.model_dump(mode="json"))
        return job

    def get_job(self, job_id: str) -> AnalysisJob | None:
        snapshot = self.jobs.document(job_id).get()
        return AnalysisJob.model_validate(snapshot.to_dict()) if snapshot.exists else None
