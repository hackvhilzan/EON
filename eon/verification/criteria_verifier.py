"""
eon.verification.criteria_verifier
====================================
Layer 2: CriteriaVerifier.

Parsea el criterio_de_exito del objetivo y lo verifica si es medible.
Soporta criterios estructurados y expresiones simples.
"""

from __future__ import annotations

import re
from typing import Any

from .models import LayerResult, VerificationStatus


class CriteriaVerifier:
    """Verifica criterios de éxito medibles."""

    name = "criteria"

    def verificar(
        self,
        objective: Any,
        evidence: dict[str, Any],
        artifacts: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> LayerResult:
        criterio = ""
        if hasattr(objective, "criterio_de_exito"):
            criterio = objective.criterio_de_exito or ""
        elif isinstance(objective, str):
            criterio = objective
        elif isinstance(objective, dict):
            criterio = objective.get("criterio_de_exito", "")

        if not criterio:
            return LayerResult(
                layer_name=self.name,
                status=VerificationStatus.SKIPPED,
                motivo="No hay criterio_de_exito definido",
            )

        # Criterios estructurados
        structured = kwargs.get("criterios_estructurados", [])
        if structured:
            return self._verify_structured(structured, artifacts or {})

        # Criterios en texto libre: intentar parsear patrones comunes
        return self._verify_text_criteria(criterio, evidence, artifacts or {})

    def _verify_structured(
        self,
        criteria: list[dict[str, Any]],
        artifacts: dict[str, Any],
    ) -> LayerResult:
        results = []
        for crit in criteria:
            tipo = crit.get("tipo", "")
            if tipo == "artefacto_existe":
                path = crit.get("path", "")
                exists = path in artifacts or (artifacts and any(path in str(a) for a in artifacts.values()))
                results.append((tipo, exists, f"Artefacto {path}: {'existe' if exists else 'no existe'}"))
            elif tipo == "tamano_minimo":
                path = crit.get("path", "")
                min_bytes = crit.get("bytes", 0)
                artifact = artifacts.get(path)
                size = len(str(artifact)) if artifact else 0
                results.append((tipo, size >= min_bytes, f"{path}: {size} >= {min_bytes} bytes"))
            elif tipo == "tipo_mime":
                path = crit.get("path", "")
                results.append((tipo, True, f"Verificación MIME no implementada para {path}"))

        all_passed = all(r[1] for r in results)
        confidence = sum(1 for r in results if r[1]) / len(results) if results else 0.0

        return LayerResult(
            layer_name=self.name,
            status=VerificationStatus.PASSED if all_passed else VerificationStatus.FAILED,
            confidence=confidence,
            motivo="; ".join(r[2] for r in results) if results else "Sin criterios",
            details={"results": [{"tipo": r[0], "passed": r[1], "msg": r[2]} for r in results]},
        )

    def _verify_text_criteria(
        self,
        criterio: str,
        evidence: dict[str, Any],
        artifacts: dict[str, Any],
    ) -> LayerResult:
        criterio_lower = criterio.lower()

        # Patrón: "archivo > N bytes" o "> NKB" o "> NMB"
        size_match = re.search(r"mayor.*?(\d+)\s*(bytes?|kb|mb)", criterio_lower)
        if size_match:
            num = int(size_match.group(1))
            unit = size_match.group(2)
            multiplier = {"byte": 1, "bytes": 1, "kb": 1024, "mb": 1024 * 1024}.get(unit, 1)
            min_size = num * multiplier

            for name, artifact in artifacts.items():
                size = len(str(artifact))
                if size >= min_size:
                    return LayerResult(
                        layer_name=self.name,
                        status=VerificationStatus.PASSED,
                        confidence=0.8,
                        motivo=f"Artefacto {name} tiene {size} bytes (>= {min_size})",
                    )

            return LayerResult(
                layer_name=self.name,
                status=VerificationStatus.FAILED,
                confidence=0.2,
                motivo=f"Ningún artefacto alcanza {min_size} bytes",
            )

        # Patrón: "contiene 'texto'"
        contains_match = re.search(r"contiene\s+['\"](.+?)['\"]", criterio_lower)
        if contains_match:
            expected_text = contains_match.group(1)
            for name, artifact in artifacts.items():
                if expected_text in str(artifact).lower():
                    return LayerResult(
                        layer_name=self.name,
                        status=VerificationStatus.PASSED,
                        confidence=0.85,
                        motivo=f"Artefacto {name} contiene '{expected_text}'",
                    )
            return LayerResult(
                layer_name=self.name,
                status=VerificationStatus.FAILED,
                confidence=0.15,
                motivo=f"Ningún artefacto contiene '{expected_text}'",
            )

        # Criterio no medible automáticamente
        return LayerResult(
            layer_name=self.name,
            status=VerificationStatus.SKIPPED,
            motivo=f"Criterio no medible automáticamente: {criterio[:80]}",
        )
