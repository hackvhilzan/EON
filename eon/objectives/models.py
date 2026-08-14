"""
eon.objectives.models
========================
Modelo único de Objetivo (Fase 7). Implementa exactamente lo que define
OBJECTIVES.md §1 (contrato congelado v1.0) -- sin añadir campos, sin quitar
campos. Mismo espíritu que eon.core.models.Cell: un único dataclass del que
habla todo el resto del sistema, con su propio historial append-only.

Inmutabilidad (§1): un Objetivo es inmutable salvo por `estado`, `propietario`,
`historial` y `actualizado_en`. Este módulo no impone esa regla con magia de
Python (frozen dataclasses harían el `estado`/`propietario` también inmutables,
que es justo lo contrario de lo que hace falta) -- la impone ObjectiveManager,
que es el único punto de escritura (§8, Invariante 6). Aquí solo vive el dato.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum

from .exceptions import InvalidObjectiveError


def _ahora() -> str:
    return datetime.now(UTC).isoformat()


class ObjectiveState(str, Enum):
    """Los diez estados de la máquina de OBJECTIVES.md §5: siete no-terminales y
    tres terminales de verdad (Invariante 2: nunca revierten)."""

    PENDIENTE = "pendiente"
    PLANIFICANDO = "planificando"
    BLOQUEADO = "bloqueado"
    EN_PROGRESO = "en_progreso"
    PAUSADO = "pausado"
    VERIFICANDO = "verificando"
    FALLANDO = "fallando"
    COMPLETADO = "completado"
    FALLIDO = "fallido"
    CANCELADO = "cancelado"

    @property
    def es_terminal(self) -> bool:
        return self in (ObjectiveState.COMPLETADO, ObjectiveState.FALLIDO, ObjectiveState.CANCELADO)


class ObjectiveOrigin(str, Enum):
    """§1: quién/qué creó el Objetivo. No es lo mismo que `propietario`."""

    USUARIO = "usuario"
    DESCOMPOSICION = "descomposicion"
    SISTEMA = "sistema"


@dataclass
class ObjectiveHistoryEntry:
    """Una entrada del `historial` append-only de un Objetivo. Cubre tanto
    transiciones de estado (`de`/`a` presentes) como eventos estructurales que no
    cambian el estado pero sí deben quedar anotados por contrato -- p.ej.
    `objetivo_reasignado` (§1: "se anota en historial... nunca un cambio
    silencioso de campo") y `objetivo_descompuesto` (§6: "no es una transición de
    estado por sí sola", pero igualmente debe dejar rastro)."""

    evento: str
    ts: str
    de: str | None = None
    a: str | None = None
    motivo: str | None = None
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Objective:
    """Exactamente los campos de OBJECTIVES.md §1. No añadir ni quitar ninguno."""

    descripcion: str
    criterio_de_exito: str
    origen: ObjectiveOrigin
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    estado: ObjectiveState = ObjectiveState.PENDIENTE
    propietario: str | None = None
    padre_id: str | None = None
    depende_de: list[str] = field(default_factory=list)
    confianza_minima: float = 0.7
    valor: float = 0.5
    creado_en: str = field(default_factory=_ahora)
    actualizado_en: str = field(default_factory=_ahora)
    historial: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Invariante 1: todo Objetivo tiene criterio_de_exito desde que nace; no
        # existe un Objetivo a medio definir en `pendiente`.
        if not self.descripcion or not self.descripcion.strip():
            raise InvalidObjectiveError("Un Objetivo necesita `descripcion`.")
        if not self.criterio_de_exito or not self.criterio_de_exito.strip():
            raise InvalidObjectiveError(
                "Un Objetivo sin `criterio_de_exito` no es un Objetivo válido (OBJECTIVES.md §1)."
            )
        if isinstance(self.origen, str):
            self.origen = ObjectiveOrigin(self.origen)
        if isinstance(self.estado, str):
            self.estado = ObjectiveState(self.estado)
        if not (0.0 <= self.confianza_minima <= 1.0):
            raise InvalidObjectiveError("`confianza_minima` debe estar entre 0.0 y 1.0.")
        if not (0.0 <= self.valor <= 1.0):
            raise InvalidObjectiveError("`valor` debe estar entre 0.0 y 1.0.")
        # §1: "Si no se asigna explícitamente, [el propietario] nace con propietario = origen."
        if self.propietario is None:
            self.propietario = self.origen.value

    # ---- historial (única forma de anotar algo; nunca se edita `historial` a mano) ----

    def registrar_transicion(self, de: str, a: str, evento: str, motivo: str | None = None, **extra) -> None:
        entrada = ObjectiveHistoryEntry(evento=evento, ts=_ahora(), de=de, a=a, motivo=motivo, extra=extra)
        self.historial.append(entrada.to_dict())
        self.actualizado_en = entrada.ts

    def registrar_evento(self, evento: str, motivo: str | None = None, **extra) -> None:
        """Para eventos estructurales que no son una transición de estado
        (`objetivo_descompuesto`, `objetivo_reasignado`)."""
        entrada = ObjectiveHistoryEntry(evento=evento, ts=_ahora(), motivo=motivo, extra=extra)
        self.historial.append(entrada.to_dict())
        self.actualizado_en = entrada.ts

    def to_dict(self) -> dict:
        d = asdict(self)
        d["estado"] = self.estado.value
        d["origen"] = self.origen.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> Objective:
        return cls(
            descripcion=d["descripcion"],
            criterio_de_exito=d["criterio_de_exito"],
            origen=ObjectiveOrigin(d.get("origen", "humano")),
            id=d["id"],
            estado=ObjectiveState(d.get("estado", "pendiente")),
            propietario=d.get("propietario"),
            padre_id=d.get("padre_id"),
            depende_de=list(d.get("depende_de", [])),
            confianza_minima=d.get("confianza_minima", 0.7),
            valor=d.get("valor", 0.5),
            creado_en=d.get("creado_en", _ahora()),
            actualizado_en=d.get("actualizado_en", _ahora()),
            historial=list(d.get("historial", [])),
        )
