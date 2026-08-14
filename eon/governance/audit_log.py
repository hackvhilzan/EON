"""AuditLog: log append-only de todas las decisiones de gobernanza.

Cada entrada incluye un hash de la anterior, haciendo el log verificable y
resistente a manipulación. El log se persiste como JSONL append-only.

El hash se calcula con JSON canónico (``sort_keys=True``, separadores
compactos) + SHA-256, no con ``__hash__()`` de dataclass — este último no
es estable entre procesos.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import AuditEntry


def _ahora_iso() -> str:
    return datetime.now(UTC).isoformat()


def _calcular_hash(entry_dict: dict[str, Any]) -> str:
    """Hash SHA-256 del JSON canónico de la entrada (sin entry_hash)."""
    d = {k: v for k, v in entry_dict.items() if k != "entry_hash"}
    canon = json.dumps(d, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


class AuditLog:
    """Log append-only de todas las decisiones de gobernanza.

    Cada entrada incluye ``previous_hash`` (hash de la entrada anterior) y
    ``entry_hash`` (hash de sí misma). Modificar una entrada pasada
    invalida todos los hashes posteriores — el log es verificable e
    inmutable por diseño.
    """

    def __init__(self, root: Path | str | None = None) -> None:
        self._root = Path(root) / "governance" if root else None
        if self._root:
            self._root.mkdir(parents=True, exist_ok=True)
        self._entries: list[AuditEntry] = []
        self._load()

    def _load(self) -> None:
        """Carga entradas existentes desde el archivo JSONL."""
        if not self._root:
            return
        audit_file = self._root / "audit.jsonl"
        if not audit_file.exists():
            return
        with open(audit_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                self._entries.append(AuditEntry(**d))

    def _persist(self, entry: AuditEntry) -> None:
        """Persiste una entrada al archivo JSONL (append)."""
        if not self._root:
            return
        audit_file = self._root / "audit.jsonl"
        with open(audit_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(entry), default=str) + "\n")

    def registrar(
        self,
        execution_id: str,
        event_type: str,
        decision: str,
        policy_id: str,
        reason: str,
        task_id: str | None = None,
        capability_id: str | None = None,
        sandbox_profile: str | None = None,
    ) -> AuditEntry:
        """Añade una entrada al log. No se puede modificar ni eliminar
        entradas existentes."""
        prev_hash = self._entries[-1].entry_hash if self._entries else "genesis"
        entry = AuditEntry(
            timestamp=_ahora_iso(),
            execution_id=execution_id,
            event_type=event_type,
            task_id=task_id,
            capability_id=capability_id,
            decision=decision,
            policy_id=policy_id,
            reason=reason,
            sandbox_profile=sandbox_profile,
            previous_hash=prev_hash,
            entry_hash="",  # se calcula abajo
        )
        entry_dict = asdict(entry)
        entry_hash = _calcular_hash(entry_dict)
        # Crear nueva entrada con el hash calculado
        entry = AuditEntry(**{**entry_dict, "entry_hash": entry_hash})
        self._entries.append(entry)
        self._persist(entry)
        return entry

    def replay(self, execution_id: str) -> list[AuditEntry]:
        """Reconstruye todas las decisiones de gobernanza para una
        ejecución específica, en orden cronológico."""
        return [e for e in self._entries if e.execution_id == execution_id]

    def verificar_integridad(self) -> bool:
        """Verifica que la cadena de hashes sea válida — ninguna entrada
        fue modificada."""
        prev_hash = "genesis"
        for entry in self._entries:
            if entry.previous_hash != prev_hash:
                return False
            # Recalcular el hash de la entrada
            entry_dict = asdict(entry)
            expected_hash = _calcular_hash(entry_dict)
            if entry.entry_hash != expected_hash:
                return False
            prev_hash = entry.entry_hash
        return True

    @property
    def entries(self) -> list[AuditEntry]:
        return list(self._entries)

    def __len__(self) -> int:
        return len(self._entries)
