"""CLI tool to generate cached MCP tool schemas.

Connects to all MCP servers defined in config, applies full filtering
(tool_include_only, sync_tools, dedup via MCPToolManager._build_tools),
and dumps the resulting tool schemas to matmaster/cache/<server>.json.

Usage:
    uv run python -m matmaster.tools.cache_mcp_schemas
    uv run python -m matmaster.tools.cache_mcp_schemas --config-dir config
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate MCP tool schema cache")
    parser.add_argument(
        "--config-dir",
        default="config",
        help="Directory containing mcp.yaml and mcp_config*.json",
    )
    parser.add_argument(
        "--output-dir",
        default="matmaster/cache",
        help="Output directory for cached schema JSON files",
    )
    return parser.parse_args()


async def generate_cache(config_dir: Path, output_dir: Path) -> None:
    import yaml

    from matmaster.mcp.manager import MCPToolManager
    from matmaster.tools.lazy_mcp import configure_mcp_manager

    mcp_yaml = config_dir / "mcp.yaml"
    if not mcp_yaml.exists():
        logger.error("mcp.yaml not found at %s", mcp_yaml)
        sys.exit(1)

    with open(mcp_yaml, encoding="utf-8") as f:
        mcp_config = yaml.safe_load(f)

    if not mcp_config:
        logger.error("mcp.yaml is empty")
        sys.exit(1)

    mcp_config_file = mcp_config.get("config_file", "mcp_config.json")
    mcp_config_path = config_dir / mcp_config_file

    if mcp_config.get("path_adaptor") == "calculation":
        try:
            from matmaster.adaptors.calculation import resolve_mcp_config_path

            mcp_config_path = resolve_mcp_config_path(mcp_config_path)
        except ImportError:
            pass

    if not mcp_config_path.exists():
        logger.error("MCP config not found: %s", mcp_config_path)
        sys.exit(1)

    with open(mcp_config_path, encoding="utf-8") as f:
        server_defs = json.load(f).get("mcpServers", {})

    output_dir.mkdir(parents=True, exist_ok=True)

    manager = MCPToolManager()
    manager.loop = asyncio.get_running_loop()
    configure_mcp_manager(manager, mcp_config)

    for name, server_cfg in server_defs.items():
        try:
            logger.info("Connecting to %s (%s)...", name, server_cfg.get("transport"))
            await manager.add_server(name=name, **server_cfg)
        except Exception as e:
            logger.error("Failed to connect %s: %s", name, e)
            continue

    for server_name, tools in manager.tools_by_server.items():
        schemas = []
        for tool_info in tools.values():
            schemas.append(
                {
                    "name": tool_info.get("remote_tool_name") or tool_info["name"],
                    "description": tool_info.get("description", ""),
                    "input_schema": tool_info.get("input_schema", {}),
                }
            )
        out_path = output_dir / f"{server_name}.json"
        out_path.write_text(json.dumps(schemas, indent=2, ensure_ascii=False))
        logger.info("Wrote %d tools to %s", len(schemas), out_path)

    await manager.cleanup()
    logger.info("Done. Cache written to %s", output_dir)


def main() -> None:
    args = parse_args()
    asyncio.run(generate_cache(Path(args.config_dir), Path(args.output_dir)))


if __name__ == "__main__":
    main()
