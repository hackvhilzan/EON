"""
eon.checkpoint.store
======================
SQLiteCheckpointStore — persistencia durable de checkpoints.

Tabla: checkpoints
  - id TEXT PRIMARY KEY
  - execution_id TEXT (indexado)
  - event_seq INTEGER
  - kind TEXT
  - data TEXT (JSON completo)
  - hash TEXT
  - created_at TEXT
"""

from __future__ import annotations

import json

from ..persistence.sqlite_engine import SQLiteEngine
from .models import Checkpoint


class SQLiteCheckpointStore:
    """Almacén durable de checkpoints con backend SQLite.

    Uso:
        store = SQLiteCheckpointStore(engine)
        cp = store.create(execution_id="exec-1", ...)
        fetched = store.get(cp.id)
        latest = store.latest_for_execution("exec-1")
    """

    def __init__(self, engine: SQLiteEngine) -> None:
        self._engine = engine
        self._init_schema()

    def _init_schema(self) -> None:
        self._engine.executescript(
            """
            CREATE TABLE IF NOT EXISTS checkpoints (
                id TEXT PRIMARY KEY,
                execution_id TEXT NOT NULL,
                event_seq INTEGER NOT NULL,
                kind TEXT NOT NULL,
                data TEXT NOT NULL,
                hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_cp_execution ON checkpoints(execution_id);
            CREATE INDEX IF NOT EXISTS idx_cp_seq ON checkpoints(execution_id, event_seq);
            """
        )

    def save(self, checkpoint: Checkpoint) -> Checkpoint:
        """Persiste un checkpoint. Idempotente por id (INSERT OR REPLACE)."""
        if not checkpoint.hash:
            checkpoint.compute_hash()
        data_json = json.dumps(checkpoint.to_dict(), ensure_ascii=False, default=str)
        self._engine.execute(
            "INSERT OR REPLACE INTO checkpoints "
            "(id, execution_id, event_seq, kind, data, hash, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                checkpoint.id,
                checkpoint.execution_id,
                checkpoint.event_seq,
                checkpoint.kind.value,
                data_json,
                checkpoint.hash,
                checkpoint.created_at,
            ),
        )
        return checkpoint

    def get(self, checkpoint_id: str) -> Checkpoint | None:
        row = self._engine.query_one("SELECT data FROM checkpoints WHERE id = ?", (checkpoint_id,))
        if row is None:
            return None
        return Checkpoint.from_dict(json.loads(row["data"]))

    def latest_for_execution(self, execution_id: str) -> Checkpoint | None:
        """Devuelve el checkpoint más reciente para una ejecución."""
        row = self._engine.query_one(
            "SELECT data FROM checkpoints WHERE execution_id = ? ORDER BY event_seq DESC, created_at DESC LIMIT 1",
            (execution_id,),
        )
        if row is None:
            return None
        return Checkpoint.from_dict(json.loads(row["data"]))

    def latest_before_seq(
        self,
        execution_id: str,
        event_seq: int,
    ) -> Checkpoint | None:
        """Devuelve el checkpoint más reciente con event_seq <= al valor dado.

        Usado por TimeMachine para encontrar el checkpoint base más cercano
        a un punto temporal objetivo.
        """
        row = self._engine.query_one(
            "SELECT data FROM checkpoints "
            "WHERE execution_id = ? AND event_seq <= ? "
            "ORDER BY event_seq DESC, created_at DESC LIMIT 1",
            (execution_id, event_seq),
        )
        if row is None:
            return None
        return Checkpoint.from_dict(json.loads(row["data"]))

    def list_for_execution(self, execution_id: str) -> list[Checkpoint]:
        """Lista todos los checkpoints de una ejecución, ordenados por seq."""
        rows = self._engine.query_all(
            "SELECT data FROM checkpoints WHERE execution_id = ? ORDER BY event_seq ASC, created_at ASC",
            (execution_id,),
        )
        return [Checkpoint.from_dict(json.loads(r["data"])) for r in rows]

    def list_all(self) -> list[Checkpoint]:
        """Lista todos los checkpoints."""
        rows = self._engine.query_all("SELECT data FROM checkpoints ORDER BY created_at DESC")
        return [Checkpoint.from_dict(json.loads(r["data"])) for r in rows]

    def verify_hash(self, checkpoint_id: str) -> bool:
        """Verifica que el hash del checkpoint coincide con su contenido."""
        cp = self.get(checkpoint_id)
        if cp is None:
            return False
        expected = cp.hash
        cp.compute_hash()
        return cp.hash == expected

    def delete(self, checkpoint_id: str) -> bool:
        return self._engine.delete_entity("checkpoints", checkpoint_id)

    def count(self, execution_id: str | None = None) -> int:
        if execution_id:
            row = self._engine.query_one(
                "SELECT COUNT(*) as c FROM checkpoints WHERE execution_id = ?",
                (execution_id,),
            )
        else:
            row = self._engine.query_one("SELECT COUNT(*) as c FROM checkpoints")
        return row["c"] if row else 0
