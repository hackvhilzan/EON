"""
Tests de Plugins y Tools (Fase 6).

Criterios de aceptación:
- PluginLoader.load_directory() carga plugins desde .py files
- PluginLoader soporta PLUGIN, create_plugin() y create_tool()
- ToolRegistry.discover() descubre plugins y los registra
- CapabilityMap.register_capability() y register_plugin() funcionan
- Nuevas Tools: camino feliz, error, edge case (mínimo 3 tests por tool)
- Tools con dependencias ausentes fallan graciosamente
- SDK: Tool, ToolResult, SandboxProfile re-exportados
- Hot-reload: reload_directory recarga plugins
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from eon.capabilities.capability_map import (
    CAPABILITY_MAP,
    register_capability,
    register_plugin,
    resolver,
)
from eon.plugins import PluginLoader
from eon.plugins.base import ToolPlugin
from eon.tools.base_tool import Tool, ToolResult
from eon.tools.registry import ToolRegistry

# ─── Plugin Loader Tests ──────────────────────────────────


class TestPluginLoader:
    def test_load_directory_with_plugin_variable(self, tmp_path):
        """Carga un plugin que define PLUGIN."""
        plugin_file = tmp_path / "my_plugin.py"
        plugin_file.write_text(
            "from eon.sdk import Tool, ToolResult, ToolPlugin\n"
            "class MyTool(Tool):\n"
            "    name = 'my_tool'\n"
            "    async def execute(self, **kwargs):\n"
            "        return ToolResult(ok=True)\n"
            "PLUGIN = ToolPlugin(name='my_plug', tool=MyTool(), description='test')\n"
        )

        loader = PluginLoader()
        plugins = loader.load_directory(tmp_path)

        assert len(plugins) == 1
        assert plugins[0].name == "my_plug"
        assert plugins[0].tool.name == "my_tool"
        assert plugins[0].capability_id == "tool.my_tool"

    def test_load_directory_with_create_plugin(self, tmp_path):
        """Carga un plugin que define create_plugin()."""
        plugin_file = tmp_path / "factory_plugin.py"
        plugin_file.write_text(
            "from eon.sdk import Tool, ToolResult, ToolPlugin\n"
            "class FactoryTool(Tool):\n"
            "    name = 'factory_tool'\n"
            "    async def execute(self, **kwargs):\n"
            "        return ToolResult(ok=True)\n"
            "def create_plugin():\n"
            "    return ToolPlugin(name='factory_plug', tool=FactoryTool(), description='factory')\n"
        )

        loader = PluginLoader()
        plugins = loader.load_directory(tmp_path)

        assert len(plugins) == 1
        assert plugins[0].name == "factory_plug"
        assert plugins[0].tool.name == "factory_tool"

    def test_load_directory_with_create_tool(self, tmp_path):
        """Carga un plugin que define create_tool()."""
        plugin_file = tmp_path / "tool_plugin.py"
        plugin_file.write_text(
            "from eon.sdk import Tool, ToolResult\n"
            "class SimpleTool(Tool):\n"
            "    name = 'simple_tool'\n"
            "    async def execute(self, **kwargs):\n"
            "        return ToolResult(ok=True)\n"
            "def create_tool():\n"
            "    return SimpleTool()\n"
        )

        loader = PluginLoader()
        plugins = loader.load_directory(tmp_path)

        assert len(plugins) == 1
        assert plugins[0].tool.name == "simple_tool"
        assert plugins[0].capability_id == "tool.simple_tool"

    def test_load_directory_nonexistent(self, tmp_path):
        """Directorio inexistente → lista vacía."""
        loader = PluginLoader()
        plugins = loader.load_directory(tmp_path / "nonexistent")
        assert plugins == []

    def test_load_directory_skips_underscore_files(self, tmp_path):
        """Archivos que empiezan con _ se omiten."""
        (tmp_path / "__init__.py").write_text("# should be skipped")
        (tmp_path / "_private.py").write_text("# should be skipped")
        (tmp_path / "valid.py").write_text(
            "from eon.sdk import Tool, ToolResult, ToolPlugin\n"
            "class V(Tool):\n"
            "    name = 'v'\n"
            "    async def execute(self, **kwargs):\n"
            "        return ToolResult(ok=True)\n"
            "PLUGIN = ToolPlugin(name='v_plug', tool=V())\n"
        )

        loader = PluginLoader()
        plugins = loader.load_directory(tmp_path)
        assert len(plugins) == 1

    def test_load_directory_registers_in_registry(self, tmp_path):
        """Los plugins se registran en el ToolRegistry."""
        plugin_file = tmp_path / "reg_test.py"
        plugin_file.write_text(
            "from eon.sdk import Tool, ToolResult, ToolPlugin\n"
            "class RegTool(Tool):\n"
            "    name = 'reg_test_tool'\n"
            "    async def execute(self, **kwargs):\n"
            "        return ToolResult(ok=True)\n"
            "PLUGIN = ToolPlugin(name='reg_plug', tool=RegTool())\n"
        )

        registry = ToolRegistry()
        loader = PluginLoader(registry)
        loader.load_directory(tmp_path)

        assert "reg_test_tool" in registry.list()

    def test_load_multiple_plugins(self, tmp_path):
        """Carga múltiples plugins del mismo directorio."""
        for i in range(3):
            (tmp_path / f"plugin_{i}.py").write_text(
                f"from eon.sdk import Tool, ToolResult, ToolPlugin\n"
                f"class T{i}(Tool):\n"
                f"    name = 'tool_{i}'\n"
                f"    async def execute(self, **kwargs):\n"
                f"        return ToolResult(ok=True)\n"
                f"PLUGIN = ToolPlugin(name='plug_{i}', tool=T{i}())\n"
            )

        loader = PluginLoader()
        plugins = loader.load_directory(tmp_path)
        assert len(plugins) == 3

    def test_load_directory_registers_capability_in_map(self, tmp_path):
        """Al cargar un plugin, su capability_id se registra en CapabilityMap."""

        from eon.capabilities.capability_map import CAPABILITY_MAP

        plugin_file = tmp_path / "cap_test.py"
        plugin_file.write_text(
            "from eon.sdk import Tool, ToolResult, ToolPlugin\n"
            "class CapTool(Tool):\n"
            "    name = 'cap_test_tool'\n"
            "    async def execute(self, **kwargs):\n"
            "        return ToolResult(ok=True)\n"
            "PLUGIN = ToolPlugin(name='cap_plug', tool=CapTool())\n"
        )

        loader = PluginLoader()
        loader.load_directory(tmp_path)

        # El capability_id debe estar en el mapa global
        assert "tool.cap_test_tool" in CAPABILITY_MAP


# ─── ToolRegistry.discover() Tests ────────────────────────


class TestToolRegistryDiscover:
    def test_discover_loads_plugins(self, tmp_path):
        """discover() carga plugins desde directorio."""
        plugin_file = tmp_path / "disc.py"
        plugin_file.write_text(
            "from eon.sdk import Tool, ToolResult, ToolPlugin\n"
            "class DiscTool(Tool):\n"
            "    name = 'disc_tool'\n"
            "    async def execute(self, **kwargs):\n"
            "        return ToolResult(ok=True)\n"
            "PLUGIN = ToolPlugin(name='disc_plug', tool=DiscTool())\n"
        )

        registry = ToolRegistry()
        registry.discover(plugins_dir=str(tmp_path))

        assert "disc_tool" in registry.list()

    def test_autodiscover_registers_filesystem(self):
        """autodiscover() registra al menos filesystem."""
        registry = ToolRegistry()
        registry.autodiscover()
        assert "filesystem" in registry.list()

    def test_autodiscover_registers_database(self):
        """autodiscover() registra database (sin deps externas)."""
        registry = ToolRegistry()
        registry.autodiscover()
        assert "database" in registry.list()

    def test_autodiscover_registers_terminal(self):
        """autodiscover() registra terminal."""
        registry = ToolRegistry()
        registry.autodiscover()
        assert "terminal" in registry.list()

    def test_autodiscover_registers_python(self):
        """autodiscover() registra python."""
        registry = ToolRegistry()
        registry.autodiscover()
        assert "python" in registry.list()


# ─── CapabilityMap Tests ──────────────────────────────────


class TestCapabilityMapRegister:
    def test_register_capability(self):
        """register_capability añade una capability nueva."""
        import copy

        test_map = copy.deepcopy(CAPABILITY_MAP)
        register_capability("tool.custom", "custom_tool", capability_map=test_map)

        spec = resolver("tool.custom", capability_map=test_map)
        assert spec.tool_name == "custom_tool"

    def test_register_capability_overwrites(self):
        """register_capability sobrescribe si ya existe."""
        import copy

        test_map = copy.deepcopy(CAPABILITY_MAP)
        test_map["tool.test"] = type(next(iter(CAPABILITY_MAP.values())))(tool_name="old_tool")

        register_capability("tool.test", "new_tool", capability_map=test_map)
        spec = resolver("tool.test", capability_map=test_map)
        assert spec.tool_name == "new_tool"

    def test_register_plugin(self):
        """register_plugin registra una ToolPlugin en el mapa."""
        import copy

        class DummyTool(Tool):
            name = "dummy"

            async def execute(self, **kwargs):
                return ToolResult(ok=True)

        plugin = ToolPlugin(name="dummy_plug", tool=DummyTool())
        test_map = copy.deepcopy(CAPABILITY_MAP)
        register_plugin(plugin, capability_map=test_map)

        spec = resolver("tool.dummy", capability_map=test_map)
        assert spec.tool_name == "dummy"


# ─── SDK Tests ────────────────────────────────────────────


class TestSDK:
    def test_imports(self):
        """SDK re-exporta las interfaces necesarias."""
        from eon.sdk import PolicyDecision, SandboxProfile, Tool, ToolPlugin, ToolResult

        assert Tool is not None
        assert ToolResult is not None
        assert SandboxProfile is not None
        assert PolicyDecision is not None
        assert ToolPlugin is not None

    def test_create_tool_with_sdk(self):
        """Se puede crear una Tool usando solo el SDK."""
        from eon.sdk import Tool, ToolResult

        class MyCustomTool(Tool):
            name = "custom_sdk_tool"

            async def execute(self, **kwargs):
                return ToolResult(ok=True, data={"custom": True})

        tool = MyCustomTool()
        assert tool.name == "custom_sdk_tool"

        # Ejecutar
        result = asyncio.run(tool.execute())
        assert result.ok is True
        assert result.data["custom"] is True


# ─── Tool Tests ───────────────────────────────────────────


class TestDatabaseTool:
    """Tests para DatabaseTool."""

    def test_select_query(self, tmp_path):
        """Camino feliz: SELECT read-only."""
        # Crear BD de prueba
        import sqlite3

        from eon.tools.database_tool import DatabaseTool

        db_path = str(tmp_path / "test.db")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE users (id INTEGER, name TEXT)")
        conn.execute("INSERT INTO users VALUES (1, 'Alice')")
        conn.execute("INSERT INTO users VALUES (2, 'Bob')")
        conn.commit()
        conn.close()

        tool = DatabaseTool(db_path=db_path)
        result = asyncio.run(tool.execute(query="SELECT * FROM users"))

        assert result.ok is True
        assert result.data["row_count"] == 2
        assert result.data["rows"][0]["name"] == "Alice"

    def test_write_blocked(self, tmp_path):
        """Edge case: INSERT bloqueado (read-only)."""
        from eon.tools.database_tool import DatabaseTool

        tool = DatabaseTool(db_path=str(tmp_path / "test.db"))
        result = asyncio.run(tool.execute(query="INSERT INTO users VALUES (1, 'test')"))

        assert result.ok is False
        assert "read-only" in result.error.lower()

    def test_drop_blocked(self):
        """Edge case: DROP bloqueado."""
        from eon.tools.database_tool import DatabaseTool

        tool = DatabaseTool()
        result = asyncio.run(tool.execute(query="DROP TABLE users"))

        assert result.ok is False
        assert "read-only" in result.error.lower()

    def test_sql_error(self):
        """Error: consulta SQL inválida."""
        from eon.tools.database_tool import DatabaseTool

        tool = DatabaseTool()
        result = asyncio.run(tool.execute(query="SELECT * FROM nonexistent_table"))

        assert result.ok is False


class TestTerminalTool:
    """Tests para TerminalTool."""

    def test_echo_command(self):
        """Camino feliz: comando simple."""
        from eon.tools.terminal_tool import TerminalTool

        tool = TerminalTool()
        result = asyncio.run(tool.execute(command="echo 'hello world'"))

        assert result.ok is True
        assert "hello world" in result.data["stdout"]

    def test_failing_command(self):
        """Error: comando que falla."""
        from eon.tools.terminal_tool import TerminalTool

        tool = TerminalTool()
        result = asyncio.run(tool.execute(command="exit 1"))

        assert result.ok is False
        assert result.data["returncode"] == 1

    def test_timeout(self):
        """Edge case: timeout."""
        from eon.tools.terminal_tool import TerminalTool

        tool = TerminalTool(default_timeout=1.0)
        result = asyncio.run(tool.execute(command="sleep 10", timeout=1.0))

        assert result.ok is False
        assert "Timeout" in result.error


class TestPythonTool:
    """Tests para PythonTool."""

    def test_simple_code(self):
        """Camino feliz: código Python simple."""
        from eon.tools.python_tool import PythonTool

        tool = PythonTool()
        result = asyncio.run(tool.execute(code="print('hello from python')"))

        assert result.ok is True
        assert "hello from python" in result.data["stdout"]

    def test_error_code(self):
        """Error: código con error."""
        from eon.tools.python_tool import PythonTool

        tool = PythonTool()
        result = asyncio.run(tool.execute(code="raise ValueError('test error')"))

        assert result.ok is False

    def test_timeout(self):
        """Edge case: timeout."""
        from eon.tools.python_tool import PythonTool

        tool = PythonTool(default_timeout=1.0)
        result = asyncio.run(tool.execute(code="import time; time.sleep(10)", timeout=1.0))

        assert result.ok is False
        assert "Timeout" in result.error


class TestPDFTool:
    """Tests para PDFTool."""

    def test_read_without_pypdf(self, tmp_path):
        """Error: pypdf no instalado (o lectura de archivo inexistente)."""
        from eon.tools.pdf_tool import PDFTool

        tool = PDFTool()
        result = asyncio.run(tool.execute(action="read", path=str(tmp_path / "nonexistent.pdf")))

        # Si pypdf no está instalado → error claro
        # Si está instalado → error de archivo no encontrado
        assert result.ok is False

    def test_write_without_reportlab(self, tmp_path):
        """Error: reportlab no instalado."""
        from eon.tools.pdf_tool import PDFTool

        tool = PDFTool()
        result = asyncio.run(tool.execute(action="write", text="test", output_path=str(tmp_path / "out.pdf")))

        # Si reportlab no está instalado → error claro
        # Si está instalado → ok
        if result.ok:
            assert (tmp_path / "out.pdf").exists()
        else:
            assert "reportlab" in result.error.lower() or "error" in result.error.lower()

    def test_unknown_action(self):
        """Edge case: acción desconocida."""
        from eon.tools.pdf_tool import PDFTool

        tool = PDFTool()
        result = asyncio.run(tool.execute(action="invalid"))

        assert result.ok is False
        assert "invalid" in result.error.lower() or "desconocida" in result.error.lower()


class TestImageTool:
    """Tests para ImageTool."""

    def test_info_without_pillow(self):
        """Error: Pillow no instalado."""
        from eon.tools.image_tool import ImageTool

        tool = ImageTool()
        result = asyncio.run(tool.execute(action="info", path="nonexistent.png"))

        assert result.ok is False

    def test_unknown_action(self):
        """Edge case: acción desconocida."""
        from eon.tools.image_tool import ImageTool

        tool = ImageTool()
        result = asyncio.run(tool.execute(action="invalid"))

        assert result.ok is False


class TestEmailTool:
    """Tests para EmailTool."""

    def test_send_without_config(self):
        """Error: SMTP no configurado."""
        from eon.tools.email_tool import EmailTool

        tool = EmailTool()
        result = asyncio.run(tool.execute(to="test@example.com", subject="test", body="hello"))

        assert result.ok is False
        assert "SMTP" in result.error or "smtp" in result.error.lower()


class TestInternetTool:
    """Tests para InternetTool."""

    def test_execute_without_httpx(self):
        """Error: httpx no instalado."""
        from eon.tools.internet_tool import InternetTool

        tool = InternetTool()
        try:
            result = asyncio.run(tool.execute(url="http://example.com"))

            # Si httpx está instalado, puede fallar por red
            # Lo importante es que no crashea
            assert isinstance(result, ToolResult)
        finally:
            tool.close()


class TestAPITool:
    """Tests para APITool."""

    def test_execute_without_httpx(self):
        """Error: httpx no instalado."""
        from eon.tools.api_tool import APITool

        tool = APITool()
        try:
            result = asyncio.run(tool.execute(url="http://api.example.com"))

            assert isinstance(result, ToolResult)
        finally:
            tool.close()


# ─── Hot Reload Tests ─────────────────────────────────────


class TestHotReload:
    def test_reload_directory(self, tmp_path):
        """reload_directory recarga plugins."""
        plugin_file = tmp_path / "reload_test.py"
        plugin_file.write_text(
            "from eon.sdk import Tool, ToolResult, ToolPlugin\n"
            "class ReloadTool(Tool):\n"
            "    name = 'reload_tool'\n"
            "    async def execute(self, **kwargs):\n"
            "        return ToolResult(ok=True)\n"
            "PLUGIN = ToolPlugin(name='reload_plug', tool=ReloadTool())\n"
        )

        registry = ToolRegistry()
        loader = PluginLoader(registry)
        plugins1 = loader.load_directory(tmp_path)
        assert len(plugins1) == 1

        # Reload
        plugins2 = loader.reload_directory(tmp_path)
        assert len(plugins2) == 1
        assert plugins2[0].name == "reload_plug"


# ─── Example Plugins Tests ────────────────────────────────


class TestExamplePlugins:
    """Tests con los plugins de ejemplo en plugins/examples/."""

    @pytest.fixture
    def examples_dir(self) -> Path:
        return Path(__file__).parent.parent.parent / "plugins" / "examples"

    def test_load_echo_plugin(self, examples_dir):
        """Carga el plugin echo."""
        if not examples_dir.exists():
            pytest.skip("plugins/examples no existe")

        loader = PluginLoader()
        plugins = loader.load_directory(examples_dir)

        # Debe cargar al menos 3 plugins
        assert len(plugins) >= 3

        names = [p.name for p in plugins]
        assert "echo_plugin" in names
        assert "timestamp_plugin" in names

    def test_echo_tool_works(self, examples_dir):
        """La tool echo funciona correctamente."""
        if not examples_dir.exists():
            pytest.skip("plugins/examples no existe")

        registry = ToolRegistry()
        loader = PluginLoader(registry)
        loader.load_directory(examples_dir)

        if "echo" in registry.list():
            result = asyncio.run(registry.execute("echo", message="test message"))
            assert result.ok is True
            assert result.data["echo"] == "test message"


# ─── End-to-End Plugin Tests ──────────────────────────────


class TestPluginEndToEnd:
    """Test end-to-end: PluginLoader → CapabilityMap → CapabilityExecutor."""

    def test_plugin_invocable_via_capability_executor(self, tmp_path):
        """Un plugin cargado es invocable vía CapabilityExecutor."""
        from eon.capabilities.capability_executor import CapabilityExecutor
        from eon.capabilities.capability_map import CAPABILITY_MAP

        # Crear plugin
        plugin_file = tmp_path / "e2e_plugin.py"
        plugin_file.write_text(
            "from eon.sdk import Tool, ToolResult, ToolPlugin\n"
            "class E2ETool(Tool):\n"
            "    name = 'e2e_tool'\n"
            "    async def execute(self, message='', **kwargs):\n"
            "        return ToolResult(ok=True, data={'echo': message})\n"
            "PLUGIN = ToolPlugin(name='e2e_plug', tool=E2ETool())\n"
        )

        # Guardar estado del CAPABILITY_MAP para restaurar después
        saved_keys = set(CAPABILITY_MAP.keys())

        try:
            registry = ToolRegistry()
            loader = PluginLoader(registry)
            loader.load_directory(tmp_path)

            # El capability_id debe estar en el mapa
            assert "tool.e2e_tool" in CAPABILITY_MAP

            # Crear CapabilityExecutor y ejecutar
            with CapabilityExecutor(tool_registry=registry) as executor:
                ok = executor("tool.e2e_tool", {"message": "hello e2e"})
                assert ok is True
                assert executor.ultimo_resultado is not None
                assert executor.ultimo_resultado.ok is True
                assert executor.ultimo_resultado.data["echo"] == "hello e2e"
        finally:
            # Limpiar CAPABILITY_MAP
            for key in list(CAPABILITY_MAP.keys()):
                if key not in saved_keys:
                    del CAPABILITY_MAP[key]
