"""
eon.persistence.sqlite_engine
================================
Motor SQLite compartido por todos los stores.

Patrón: un solo archivo .db con una tabla por entidad de dominio.
Cada tabla almacena (id TEXT PRIMARY KEY, data TEXT [JSON], created_at, updated_at).
Las queries de dominio específicas (by_objective, active_for_objective, etc.)
se implementan con JSON extract functions de SQLite.

Thread-safety: SQLite con check_same_thread=False + un Lock por store.
WAL mode para permitir lecturas concurrentes sin bloquear escrituras.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any


class SQLiteEngine:
    """Conexión SQLite compartida con WAL mode y thread-safety."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            str(self._db_path),
            check_same_thread=False,
            isolation_level=None,  # autocommit mode
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")

    @property
    def db_path(self) -> Path:
        return self._db_path

    @property
    def lock(self) -> threading.Lock:
        return self._lock

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def executescript(self, sql: str) -> None:
        with self._lock:
            self._conn.executescript(sql)

    def query_one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        with self._lock:
            cursor = self._conn.execute(sql, params)
            return cursor.fetchone()

    def query_all(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            cursor = self._conn.execute(sql, params)
            return cursor.fetchall()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ─── Schema management ──────────────────────────────

    def init_schema(self) -> None:
        """Crea todas las tablas si no existen."""
        self.executescript(
            """
            CREATE TABLE IF NOT EXISTS coordinator_executions (
                id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS objectives (
                id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                padre_id TEXT,
                estado TEXT
            );

            CREATE TABLE IF NOT EXISTS plans (
                id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                objective_id TEXT,
                estado TEXT,
                version INTEGER
            );

            CREATE TABLE IF NOT EXISTS scheduler_runs (
                plan_id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS scheduler_task_index (
                task_id TEXT PRIMARY KEY,
                plan_id TEXT NOT NULL,
                FOREIGN KEY (plan_id) REFERENCES scheduler_runs(plan_id)
            );

            CREATE TABLE IF NOT EXISTS workspaces (
                id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                objective_id TEXT,
                estado TEXT
            );

            CREATE TABLE IF NOT EXISTS packages (
                id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                workspace_id TEXT
            );

            CREATE TABLE IF NOT EXISTS workers (
                id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                estado TEXT
            );

            CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT NOT NULL,
                execution_id TEXT,
                event_type TEXT NOT NULL,
                payload TEXT NOT NULL,
                timestamp TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_events_execution ON events(execution_id);
            CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
            CREATE INDEX IF NOT EXISTS idx_objectives_padre ON objectives(padre_id);
            CREATE INDEX IF NOT EXISTS idx_plans_objective ON plans(objective_id);
            CREATE INDEX IF NOT EXISTS idx_workspaces_objective ON workspaces(objective_id);
            CREATE INDEX IF NOT EXISTS idx_packages_workspace ON packages(workspace_id);
            """
        )

    # ─── Generic JSON CRUD ──────────────────────────────

    def insert_entity(
        self,
        table: str,
        entity_id: str,
        data: dict[str, Any],
        extra_cols: dict[str, Any] | None = None,
        id_col: str = "id",
    ) -> None:
        """Inserta una entidad como JSON. Falla si ya existe (INSERT OR IGNORE opcional)."""
        data_json = json.dumps(data, ensure_ascii=False, default=str)
        now = data.get("creado_en", data.get("created_at", ""))
        updated = data.get("actualizado_en", data.get("updated_at", now))

        cols = [id_col, "data", "created_at", "updated_at"]
        vals: list[Any] = [entity_id, data_json, now, updated]

        if extra_cols:
            for k, v in extra_cols.items():
                cols.append(k)
                vals.append(v)

        placeholders = ", ".join("?" for _ in cols)
        col_names = ", ".join(cols)
        sql = f"INSERT INTO {table} ({col_names}) VALUES ({placeholders})"
        with self._lock:
            self._conn.execute(sql, tuple(vals))

    def upsert_entity(
        self,
        table: str,
        entity_id: str,
        data: dict[str, Any],
        extra_cols: dict[str, Any] | None = None,
        id_col: str = "id",
    ) -> None:
        """Inserta o reemplaza una entidad."""
        data_json = json.dumps(data, ensure_ascii=False, default=str)
        now = data.get("creado_en", data.get("created_at", ""))
        updated = data.get("actualizado_en", data.get("updated_at", now))

        cols = [id_col, "data", "created_at", "updated_at"]
        vals: list[Any] = [entity_id, data_json, now, updated]

        if extra_cols:
            for k, v in extra_cols.items():
                cols.append(k)
                vals.append(v)

        placeholders = ", ".join("?" for _ in cols)
        col_names = ", ".join(cols)
        sql = f"INSERT OR REPLACE INTO {table} ({col_names}) VALUES ({placeholders})"
        with self._lock:
            self._conn.execute(sql, tuple(vals))

    def get_entity(self, table: str, entity_id: str) -> dict[str, Any] | None:
        """Lee una entidad por id, devuelve el dict parsed o None."""
        row = self.query_one(f"SELECT data FROM {table} WHERE id = ?", (entity_id,))
        if row is None:
            return None
        return json.loads(row["data"])

    def get_entity_by_col(self, table: str, col: str, value: Any) -> dict[str, Any] | None:
        """Lee una entidad por una columna arbitraria."""
        row = self.query_one(f"SELECT data FROM {table} WHERE {col} = ?", (value,))
        if row is None:
            return None
        return json.loads(row["data"])

    def list_entities(self, table: str) -> list[dict[str, Any]]:
        """Lista todas las entidades de una tabla."""
        rows = self.query_all(f"SELECT data FROM {table} ORDER BY created_at")
        return [json.loads(r["data"]) for r in rows]

    def list_entities_by_col(self, table: str, col: str, value: Any) -> list[dict[str, Any]]:
        """Lista entidades filtrando por una columna."""
        rows = self.query_all(
            f"SELECT data FROM {table} WHERE {col} = ? ORDER BY created_at", (value,)
        )
        return [json.loads(r["data"]) for r in rows]

    def delete_entity(self, table: str, entity_id: str) -> bool:
        """Borra una entidad. Devuelve True si existía."""
        with self._lock:
            cursor = self._conn.execute(f"DELETE FROM {table} WHERE id = ?", (entity_id,))
            return cursor.rowcount > 0

    def exists(self, table: str, entity_id: str) -> bool:
        row = self.query_one(f"SELECT 1 FROM {table} WHERE id = ?", (entity_id,))
        return row is not None
