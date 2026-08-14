"""
eon.tools.pdf_tool
====================
PDFTool — lectura/escritura de PDFs.

Dependencias opcionales: pypdf (lectura), reportlab (escritura)
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .base_tool import Tool, ToolResult

logger = logging.getLogger("eon.tools.pdf")


class PDFTool(Tool):
    """Tool para leer y escribir archivos PDF.

    Lectura requiere pypdf. Escritura requiere reportlab.
    Ambas son dependencias opcionales.
    """

    name = "pdf"

    async def execute(
        self,
        action: str = "read",
        path: str = "",
        text: str = "",
        output_path: str = "",
        **kwargs: Any,
    ) -> ToolResult:
        """Lee o escribe un PDF.

        Args:
            action: "read" o "write".
            path: Ruta del PDF a leer.
            text: Texto a escribir en el PDF (para action="write").
            output_path: Ruta de salida (para action="write").
        """
        if action == "read":
            return await self._read(path)
        elif action == "write":
            return await self._write(text, output_path)
        else:
            return ToolResult(ok=False, error=f"Acción desconocida: {action}")

    async def _read(self, path: str) -> ToolResult:
        try:
            from pypdf import PdfReader
        except ImportError:
            return ToolResult(
                ok=False,
                error="pypdf no está instalado. Instala con: pip install pypdf",
            )

        try:
            reader = PdfReader(path)
            pages = []
            for page in reader.pages:
                pages.append(page.extract_text() or "")

            return ToolResult(
                ok=True,
                data={
                    "pages": len(reader.pages),
                    "text": "\n\n".join(pages)[:50000],
                    "path": str(path),
                },
            )
        except Exception as exc:
            return ToolResult(ok=False, error=f"Error leyendo PDF: {exc}")

    async def _write(self, text: str, output_path: str) -> ToolResult:
        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.pdfgen import canvas
        except ImportError:
            return ToolResult(
                ok=False,
                error="reportlab no está instalado. Instala con: pip install reportlab",
            )

        try:
            c = canvas.Canvas(output_path, pagesize=A4)
            width, height = A4
            y = height - 50
            for line in text.split("\n"):
                if y < 50:
                    c.showPage()
                    y = height - 50
                c.drawString(50, y, line[:100])
                y -= 15
            c.save()

            file_size = Path(output_path).stat().st_size if Path(output_path).exists() else 0
            return ToolResult(
                ok=True,
                data={
                    "path": str(output_path),
                    "size_bytes": file_size,
                },
            )
        except Exception as exc:
            return ToolResult(ok=False, error=f"Error escribiendo PDF: {exc}")
