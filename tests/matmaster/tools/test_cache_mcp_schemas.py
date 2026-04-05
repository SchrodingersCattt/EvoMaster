"""Gap 3 (27-01-04 / MCP-01): cache_mcp_schemas uses matmaster.mcp.MCPToolManager + dict-based tool access.

Behavioral contract:
- cache_mcp_schemas.py imports MCPToolManager from matmaster.mcp.manager (not evomaster).
- generate_cache function is importable.
- Tool schema building uses dict-based access (tool_info["remote_tool_name"]) not MCPTool attributes.
- No evomaster imports in the module source.
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestCacheMcpSchemasImports:
    def test_generate_cache_importable(self):
        from matmaster.tools.cache_mcp_schemas import generate_cache

        assert callable(generate_cache)

    def test_no_evomaster_in_source(self):
        import matmaster.tools.cache_mcp_schemas as mod

        source = inspect.getsource(mod)
        # Only top-level/non-comment evomaster references are a problem
        lines = [
            line.strip()
            for line in source.split('\n')
            if ('from evomaster' in line or 'import evomaster' in line)
            and not line.strip().startswith('#')
        ]
        assert lines == [], f"Found evomaster imports in cache_mcp_schemas.py: {lines}"

    def test_module_imports_matmaster_mcp_manager(self):
        """generate_cache uses matmaster.mcp.manager.MCPToolManager internally."""
        import matmaster.tools.cache_mcp_schemas as mod

        source = inspect.getsource(mod)
        assert (
            "matmaster.mcp.manager" in source or "from matmaster.mcp" in source
        ), "cache_mcp_schemas.py does not use matmaster.mcp.manager"

    def test_module_imports_matmaster_adaptors_calculation(self):
        """generate_cache uses matmaster.adaptors.calculation for resolve_mcp_config_path."""
        import matmaster.tools.cache_mcp_schemas as mod

        source = inspect.getsource(mod)
        assert (
            "matmaster.adaptors.calculation" in source
        ), "cache_mcp_schemas.py does not use matmaster.adaptors.calculation"


class TestCacheMcpSchemasGenerateCache:
    """Test generate_cache behavior using dict-based tool_info access."""

    async def test_generate_cache_uses_dict_tool_info(self, tmp_path):
        """generate_cache reads tool info via dict keys, not MCPTool attributes."""
        import yaml

        # Set up fake config dir
        mcp_yaml = tmp_path / "mcp.yaml"
        mcp_yaml.write_text(yaml.dump({"config_file": "mcp_config.json"}))

        mcp_config_json = tmp_path / "mcp_config.json"
        mcp_config_json.write_text(
            '{"mcpServers": {"test_srv": {"transport": "http", "url": "http://x"}}}'
        )

        output_dir = tmp_path / "cache"

        # Patch MCPToolManager so no real connection is made
        mock_manager = MagicMock()
        mock_manager.loop = None
        mock_manager.add_server = AsyncMock()
        mock_manager.cleanup = AsyncMock()

        # tools_by_server returns dict-based tool_info (not MCPTool instances)
        mock_manager.tools_by_server = {
            "test_srv": {
                "test_srv_run": {
                    "name": "test_srv_run",
                    "remote_tool_name": "run",
                    "description": "Run something",
                    "input_schema": {"type": "object", "properties": {}},
                    "has_path_adaptor": False,
                    "connection": MagicMock(),
                }
            }
        }

        with patch("matmaster.mcp.manager.MCPToolManager", return_value=mock_manager):
            from matmaster.tools.cache_mcp_schemas import generate_cache

            await generate_cache(tmp_path, output_dir)

        # Output file should exist
        out_file = output_dir / "test_srv.json"
        assert out_file.exists(), f"Expected cache file {out_file} not created"

        import json

        data = json.loads(out_file.read_text())
        assert len(data) == 1
        assert data[0]["name"] == "run"
        assert data[0]["description"] == "Run something"

    async def test_generate_cache_handles_missing_mcp_yaml(self, tmp_path, capsys):
        """generate_cache exits with error code when mcp.yaml is absent."""
        output_dir = tmp_path / "cache"
        from matmaster.tools.cache_mcp_schemas import generate_cache

        with pytest.raises(SystemExit) as exc_info:
            await generate_cache(tmp_path, output_dir)
        assert exc_info.value.code == 1
