"""
eon.tools.image_tool
======================
ImageTool — manipulación básica de imágenes.

Dependencia opcional: Pillow (PIL)
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .base_tool import Tool, ToolResult

logger = logging.getLogger("eon.tools.image")


class ImageTool(Tool):
    """Tool para manipulación básica de imágenes.

    Soporta redimensionar, convertir formato, y obtener info.
    Requiere Pillow instalado.
    """

    name = "image"

    async def execute(
        self,
        action: str = "info",
        path: str = "",
        output_path: str = "",
        width: int | None = None,
        height: int | None = None,
        format: str = "PNG",
        **kwargs: Any,
    ) -> ToolResult:
        """Manipula una imagen.

        Args:
            action: "info", "resize", o "convert".
            path: Ruta de la imagen de entrada.
            output_path: Ruta de salida.
            width: Nuevo ancho (para resize).
            height: Nuevo alto (para resize).
            format: Formato de salida (PNG, JPEG, etc.).
        """
        try:
            from PIL import Image
        except ImportError:
            return ToolResult(
                ok=False,
                error="Pillow no está instalado. Instala con: pip install Pillow",
            )

        try:
            img = Image.open(path)

            if action == "info":
                return ToolResult(
                    ok=True,
                    data={
                        "format": img.format,
                        "mode": img.mode,
                        "size": img.size,
                        "width": img.width,
                        "height": img.height,
                    },
                )

            elif action == "resize":
                if width and height:
                    img = img.resize((width, height))
                elif width:
                    ratio = width / img.width
                    img = img.resize((width, int(img.height * ratio)))
                elif height:
                    ratio = height / img.height
                    img = img.resize((int(img.width * ratio), height))
                else:
                    return ToolResult(ok=False, error="resize requiere width o height")

                img.save(output_path, format=format)
                return ToolResult(
                    ok=True,
                    data={"path": str(output_path), "size": img.size},
                )

            elif action == "convert":
                img.save(output_path, format=format)
                return ToolResult(
                    ok=True,
                    data={"path": str(output_path), "format": format},
                )

            else:
                return ToolResult(ok=False, error=f"Acción desconocida: {action}")

        except Exception as exc:
            return ToolResult(ok=False, error=f"Error procesando imagen: {exc}")
