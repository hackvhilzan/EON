"""
eon.verification.models
========================
Modelos para verificación multi-capa.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class VerificationStatus(str, Enum):
    """Estado de una verificación individual."""

    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"      # capa no aplicable
    ERROR = "error"           # la capa falló al ejecutarse


@dataclass(frozen=True)
class LayerResult:
    """Resultado de una capa de verificación."""

    layer_name: str
    status: VerificationStatus
    confidence: float = 0.0       # 0.0 - 1.0
    motivo: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.status == VerificationStatus.PASSED

    @property
    def applicable(self) -> bool:
        return self.status != VerificationStatus.SKIPPED


@dataclass(frozen=True)
class VerificationResult:
    """Resultado agregado de todas las capas de verificación."""

    cumple: bool
    confianza: float               # 0.0 - 1.0, score calibrable
    motivo: str = ""
    layers: list[LayerResult] = field(default_factory=list)
    evidence_graph: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "cumple": self.cumple,
            "confianza": self.confianza,
            "motivo": self.motivo,
            "layers": [
                {
                    "layer_name": l.layer_name,
                    "status": l.status.value,
                    "confidence": l.confidence,
                    "motivo": l.motivo,
                    "details": l.details,
                }
                for l in self.layers
            ],
            "evidence_graph": self.evidence_graph,
        }


@dataclass
class EvidenceNode:
    """Nodo en el grafo de evidencias."""

    node_id: str
    node_type: str            # "task", "tool_result", "artifact", "criteria"
    value: Any = None
    children: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvidenceGraph:
    """Grafo de evidencias: Task → ToolResult → artefacto → criterio verificado."""

    nodes: dict[str, EvidenceNode] = field(default_factory=dict)

    def add_node(self, node: EvidenceNode) -> None:
        self.nodes[node.node_id] = node

    def add_edge(self, from_id: str, to_id: str) -> None:
        if from_id in self.nodes:
            self.nodes[from_id].children.append(to_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": {
                nid: {
                    "node_type": n.node_type,
                    "value": str(n.value) if n.value is not None else None,
                    "children": list(n.children),
                    "metadata": dict(n.metadata),
                }
                for nid, n in self.nodes.items()
            }
        }
