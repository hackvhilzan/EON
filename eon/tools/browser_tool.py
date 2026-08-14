"""
eon.tools.browser_tool
========================
BrowserTool — navegación headless con Playwright.

Dependencia opcional: playwright
"""
from __future__ import annotations

import logging
from typing import Any

from .base_tool import Tool, ToolResult

logger = logging.getLogger("eon.tools.browser")


class BrowserTool(Tool):
    """Tool para navegación web headless.

    Usa Playwright para automatizar navegación, captura de screenshots,
    extracción de contenido y interacción con páginas web.

    Requiere: pip install playwright && playwright install chromium
    """

    name = "browser"

    def __init__(self, headless: bool = True) -> None:
        self._headless = headless
        self._playwright: Any = None
        self._browser: Any = None

    async def _ensure_browser(self) -> Any:
        if self._browser is None:
            try:
                from playwright.async_api import async_playwright
            except ImportError as exc:
                raise ImportError(
                    "playwright no está instalado. "
                    "Instala con: pip install playwright && playwright install chromium"
                ) from exc

            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(headless=self._headless)
        return self._browser

    async def execute(
        self,
        url: str = "",
        action: str = "navigate",
        selector: str = "",
        text: str = "",
        screenshot_path: str = "",
        wait_for: str = "",
        **kwargs: Any,
    ) -> ToolResult:
        """Navega una página web.

        Args:
            url: URL a navegar.
            action: "navigate", "screenshot", "click", "fill", "extract".
            selector: Selector CSS para click/fill/extract.
            text: Texto a introducir (para fill).
            screenshot_path: Ruta para guardar screenshot.
            wait_for: Selector a esperar antes de continuar.
        """
        try:
            browser = await self._ensure_browser()
            page = await browser.new_page()

            if url:
                await page.goto(url)

            if wait_for:
                await page.wait_for_selector(wait_for)

            if action == "navigate":
                title = await page.title()
                content = await page.content()
                return ToolResult(
                    ok=True,
                    data={"title": title, "url": page.url, "content_length": len(content)},
                )

            elif action == "screenshot":
                await page.screenshot(path=screenshot_path, full_page=True)
                return ToolResult(ok=True, data={"path": screenshot_path})

            elif action == "click":
                await page.click(selector)
                return ToolResult(ok=True, data={"clicked": selector})

            elif action == "fill":
                await page.fill(selector, text)
                return ToolResult(ok=True, data={"filled": selector, "text": text})

            elif action == "extract":
                elements = await page.query_selector_all(selector)
                texts = []
                for el in elements:
                    texts.append(await el.text_content())
                return ToolResult(ok=True, data={"texts": texts, "count": len(texts)})

            else:
                return ToolResult(ok=False, error=f"Acción desconocida: {action}")

        except ImportError as exc:
            return ToolResult(ok=False, error=str(exc))
        except Exception as exc:
            return ToolResult(ok=False, error=f"Error en navegador: {exc}")

    async def close(self) -> None:
        """Cierra el browser."""
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
