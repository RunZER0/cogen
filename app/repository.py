from __future__ import annotations

import sqlite3
from abc import ABC, abstractmethod
from contextlib import contextmanager
from pathlib import Path
from threading import RLock

from app.domain import AnalysisJob, Venture, utc_now


class VentureRepository(ABC):
    @abstractmethod
    def save_venture(self, venture: Venture) -> Venture: ...

    @abstractmethod
    def get_venture(self, venture_id: str) -> Venture | None: ...

    @abstractmethod
    def list_ventures(self) -> list[Venture]: ...

    @abstractmethod
    def delete_venture(self, venture_id: str) -> None: ...

    @abstractmethod
    def save_job(self, job: AnalysisJob) -> AnalysisJob: ...

    @abstractmethod
    def get_job(self, job_id: str) -> AnalysisJob | None: ...

    @abstractmethod
    def save_state_record(
        self,
        kind: str,
        record_id: str,
        venture_id: str,
        payload: str,
        *,
        idempotency_key: str | None = None,
    ) -> None: ...

    @abstractmethod
    def get_state_record(self, kind: str, record_id: str) -> str | None: ...

    @abstractmethod
    def get_state_record_by_idempotency(
        self,
        kind: str,
        venture_id: str,
        idempotency_key: str,
    ) -> str | None: ...

    @abstractmethod
    def list_state_records(self, kind: str, venture_id: str) -> list[str]: ...

    @abstractmethod
    def list_all_state_records(self, kind: str) -> list[str]:
        """Return all state records of a given kind across all ventures."""
        ...

    def ping(self) -> bool:
        return True


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
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS state_records (
                    kind TEXT NOT NULL,
                    id TEXT NOT NULL,
                    venture_id TEXT NOT NULL,
                    idempotency_key TEXT,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(kind, id)
                )
                """
            )
            con.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS state_records_idempotency
                ON state_records(kind, venture_id, idempotency_key)
                WHERE idempotency_key IS NOT NULL
                """
            )
            con.execute(
                """
                CREATE INDEX IF NOT EXISTS state_records_venture
                ON state_records(kind, venture_id, updated_at)
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

    def delete_venture(self, venture_id: str) -> None:
        with self._lock, self._connection() as con:
            con.execute("DELETE FROM ventures WHERE id=?", (venture_id,))
            con.execute("DELETE FROM state_records WHERE venture_id=?", (venture_id,))

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

    def save_state_record(
        self,
        kind: str,
        record_id: str,
        venture_id: str,
        payload: str,
        *,
        idempotency_key: str | None = None,
    ) -> None:
        with self._lock, self._connection() as con:
            con.execute(
                """
                INSERT INTO state_records(kind, id, venture_id, idempotency_key, payload, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(kind, id) DO UPDATE SET
                    payload=excluded.payload,
                    idempotency_key=COALESCE(excluded.idempotency_key, state_records.idempotency_key),
                    updated_at=excluded.updated_at
                """,
                (
                    kind,
                    record_id,
                    venture_id,
                    idempotency_key,
                    payload,
                    utc_now().isoformat(),
                ),
            )

    def get_state_record(self, kind: str, record_id: str) -> str | None:
        with self._connection() as con:
            row = con.execute(
                "SELECT payload FROM state_records WHERE kind=? AND id=?",
                (kind, record_id),
            ).fetchone()
        return row["payload"] if row else None

    def get_state_record_by_idempotency(
        self,
        kind: str,
        venture_id: str,
        idempotency_key: str,
    ) -> str | None:
        with self._connection() as con:
            row = con.execute(
                """
                SELECT payload FROM state_records
                WHERE kind=? AND venture_id=? AND idempotency_key=?
                """,
                (kind, venture_id, idempotency_key),
            ).fetchone()
        return row["payload"] if row else None

    def list_state_records(self, kind: str, venture_id: str) -> list[str]:
        with self._connection() as con:
            rows = con.execute(
                """
                SELECT payload FROM state_records
                WHERE kind=? AND venture_id=?
                ORDER BY updated_at ASC, id ASC
                """,
                (kind, venture_id),
            ).fetchall()
        return [row["payload"] for row in rows]

    def list_all_state_records(self, kind: str) -> list[str]:
        with self._connection() as con:
            rows = con.execute(
                """
                SELECT payload FROM state_records
                WHERE kind=?
                ORDER BY updated_at ASC, id ASC
                """,
                (kind,),
            ).fetchall()
        return [row["payload"] for row in rows]

    def ping(self) -> bool:
        with self._connection() as con:
            return con.execute("SELECT 1").fetchone()[0] == 1


class PostgresRepository(VentureRepository):
    """Neon/Postgres persistence, via a small client-side connection pool.

    The original design opened a fresh psycopg connection per query, on the assumption that
    Neon's own pooled connection string (the `-pooler` endpoint) made a "new" connection cheap
    since the server side already keeps warm backend connections ready. Measured live, repeatedly,
    that assumption doesn't hold: every psycopg.connect() call still pays a full client-side
    TCP+TLS handshake to Neon's servers regardless of pooling on the far end, and that cost alone
    was making a single intake question round-trip take ~5 seconds. A small pool of
    already-authenticated connections, held open for the life of this process, removes that cost
    for the common case. Kept deliberately small — not one per potential concurrent request — so a
    Cloud Run instance isn't holding open a large number of idle connections against Neon's own
    connection limit; Neon's pooler still absorbs bursts beyond this pool's size.
    """

    def __init__(self, database_url: str):
        if not database_url:
            raise ValueError("DATABASE_URL is required for DATABASE_BACKEND=postgres")
        self.database_url = database_url
        self._lock = RLock()
        try:
            from psycopg_pool import ConnectionPool
        except ModuleNotFoundError as exc:  # pragma: no cover - dependency installed in CI/runtime
            raise RuntimeError("Postgres backend requires psycopg_pool") from exc
        # Neon closes idle connections server-side well inside the pool's own default max_idle —
        # reproduced live: a pooled connection handed back out failed with "server closed the
        # connection unexpectedly" on the very next query. check=check_connection makes the pool
        # verify a connection is actually alive before handing it out, transparently discarding and
        # replacing a stale one instead of returning it to the caller to fail on.
        self._pool = ConnectionPool(
            database_url, min_size=1, max_size=5, open=True, check=ConnectionPool.check_connection,
        )
        self._init_db()

    @contextmanager
    def _connection(self):
        with self._pool.connection() as con:
            yield con

    def _init_db(self) -> None:
        with self._connection() as con, con.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS ventures (
                    id TEXT PRIMARY KEY,
                    payload JSONB NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    payload JSONB NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS state_records (
                    kind TEXT NOT NULL,
                    id TEXT NOT NULL,
                    venture_id TEXT NOT NULL,
                    idempotency_key TEXT,
                    payload JSONB NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL,
                    PRIMARY KEY(kind, id)
                )
                """
            )
            cur.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS state_records_idempotency
                ON state_records(kind, venture_id, idempotency_key)
                WHERE idempotency_key IS NOT NULL
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS state_records_venture
                ON state_records(kind, venture_id, updated_at)
                """
            )

    def save_venture(self, venture: Venture) -> Venture:
        payload = venture.model_dump_json()
        with self._lock, self._connection() as con, con.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ventures(id, payload, updated_at) VALUES (%s, %s::jsonb, %s)
                ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload, updated_at=EXCLUDED.updated_at
                """,
                (venture.id, payload, venture.updated_at),
            )
        return venture

    def get_venture(self, venture_id: str) -> Venture | None:
        with self._connection() as con, con.cursor() as cur:
            cur.execute("SELECT payload::text FROM ventures WHERE id=%s", (venture_id,))
            row = cur.fetchone()
        return Venture.model_validate_json(row[0]) if row else None

    def list_ventures(self) -> list[Venture]:
        with self._connection() as con, con.cursor() as cur:
            cur.execute("SELECT payload::text FROM ventures ORDER BY updated_at DESC")
            rows = cur.fetchall()
        return [Venture.model_validate_json(row[0]) for row in rows]

    def delete_venture(self, venture_id: str) -> None:
        with self._lock, self._connection() as con, con.cursor() as cur:
            cur.execute("DELETE FROM ventures WHERE id=%s", (venture_id,))
            cur.execute("DELETE FROM state_records WHERE venture_id=%s", (venture_id,))

    def save_job(self, job: AnalysisJob) -> AnalysisJob:
        payload = job.model_dump_json()
        with self._lock, self._connection() as con, con.cursor() as cur:
            cur.execute(
                """
                INSERT INTO jobs(id, payload, updated_at) VALUES (%s, %s::jsonb, %s)
                ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload, updated_at=EXCLUDED.updated_at
                """,
                (job.id, payload, job.updated_at),
            )
        return job

    def get_job(self, job_id: str) -> AnalysisJob | None:
        with self._connection() as con, con.cursor() as cur:
            cur.execute("SELECT payload::text FROM jobs WHERE id=%s", (job_id,))
            row = cur.fetchone()
        return AnalysisJob.model_validate_json(row[0]) if row else None

    def save_state_record(
        self,
        kind: str,
        record_id: str,
        venture_id: str,
        payload: str,
        *,
        idempotency_key: str | None = None,
    ) -> None:
        with self._lock, self._connection() as con, con.cursor() as cur:
            cur.execute(
                """
                INSERT INTO state_records(kind, id, venture_id, idempotency_key, payload, updated_at)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s)
                ON CONFLICT(kind, id) DO UPDATE SET
                    payload=EXCLUDED.payload,
                    idempotency_key=COALESCE(EXCLUDED.idempotency_key, state_records.idempotency_key),
                    updated_at=EXCLUDED.updated_at
                """,
                (kind, record_id, venture_id, idempotency_key, payload, utc_now()),
            )

    def get_state_record(self, kind: str, record_id: str) -> str | None:
        with self._connection() as con, con.cursor() as cur:
            cur.execute(
                "SELECT payload::text FROM state_records WHERE kind=%s AND id=%s",
                (kind, record_id),
            )
            row = cur.fetchone()
        return row[0] if row else None

    def get_state_record_by_idempotency(
        self,
        kind: str,
        venture_id: str,
        idempotency_key: str,
    ) -> str | None:
        with self._connection() as con, con.cursor() as cur:
            cur.execute(
                """
                SELECT payload::text FROM state_records
                WHERE kind=%s AND venture_id=%s AND idempotency_key=%s
                """,
                (kind, venture_id, idempotency_key),
            )
            row = cur.fetchone()
        return row[0] if row else None

    def list_state_records(self, kind: str, venture_id: str) -> list[str]:
        with self._connection() as con, con.cursor() as cur:
            cur.execute(
                """
                SELECT payload::text FROM state_records
                WHERE kind=%s AND venture_id=%s
                ORDER BY updated_at ASC, id ASC
                """,
                (kind, venture_id),
            )
            rows = cur.fetchall()
        return [row[0] for row in rows]

    def list_all_state_records(self, kind: str) -> list[str]:
        with self._connection() as con, con.cursor() as cur:
            cur.execute(
                """
                SELECT payload::text FROM state_records
                WHERE kind=%s
                ORDER BY updated_at ASC, id ASC
                """,
                (kind,),
            )
            rows = cur.fetchall()
        return [row[0] for row in rows]

    def ping(self) -> bool:
        with self._connection() as con, con.cursor() as cur:
            cur.execute("SELECT 1")
            return cur.fetchone()[0] == 1
