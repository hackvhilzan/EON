"""
eon.package.models
====================
Modelos de dominio de Package (Fase 12).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


class PackageState(str, Enum):
    """Estado de un Package."""

    PENDING = "pending"
    BUILDING = "building"
    READY = "ready"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def es_terminal(self) -> bool:
        return self in (PackageState.READY, PackageState.FAILED, PackageState.CANCELLED)


@dataclass(frozen=True)
class WorkspaceRef:
    """Referencia de solo lectura a un Workspace.

    Mismos cuatro campos que `WorkspaceRefPort` en `coordinator/ports.py`:
    `workspace_id`, `objective_id`, `estado`, `artifacts_path`.
    """

    workspace_id: str
    objective_id: str
    estado: str
    artifacts_path: str


@dataclass
class Package:
    """Entidad de dominio de Package (PACKAGE.md §1).

    Inmutable salvo por `estado` y `actualizado_en`.
    """

    workspace_id: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    estado: PackageState = PackageState.PENDING
    artifacts_path: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    creado_en: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    actualizado_en: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "workspace_id": self.workspace_id,
            "estado": self.estado.value if hasattr(self.estado, "value") else str(self.estado),
            "artifacts_path": self.artifacts_path,
            "metadata": dict(self.metadata),
            "creado_en": self.creado_en,
            "actualizado_en": self.actualizado_en,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Package:
        return cls(
            workspace_id=d["workspace_id"],
            id=d.get("id") or str(uuid.uuid4()),
            estado=PackageState(d.get("estado", "pending")),
            artifacts_path=d.get("artifacts_path", ""),
            metadata=dict(d.get("metadata", {})),
            creado_en=d.get("creado_en", datetime.now(UTC).isoformat()),
            actualizado_en=d.get("actualizado_en", datetime.now(UTC).isoformat()),
        )
