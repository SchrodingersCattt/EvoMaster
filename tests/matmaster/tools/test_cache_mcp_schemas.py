"""Gap 3 (27-01-04 / MCP-01): cache_mcp_schemas uses matmaster.mcp.MCPToolManager + dict-based tool access.

Behavioral contract:
- generate_cache function is importable.
- Tool schema building uses dict-based access (tool_info["remote_tool_name"]) not MCPTool attributes.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestCacheMcpSchemasImports:
    def test_generate_cache_importable(self):
        from matmaster.tools.cache_mcp_schemas import generate_cache

        assert callable(generate_cache)


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
                    "has_calculation_preflight": False,
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
