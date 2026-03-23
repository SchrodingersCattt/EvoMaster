"""BasePlayground 的 MCP 初始化与解析（Mixin）。"""

from __future__ import annotations

import asyncio
import json
import threading
import traceback
from pathlib import Path
from typing import Any

from evomaster.agent import create_registry
from evomaster.skills import SkillRegistry


class PlaygroundMcpMixin:
    """提供 MCP 工具管理、服务器解析与后台 asyncio loop。"""

    logger: Any
    config: Any
    config_manager: Any
    run_dir: Any
    _mcp_loop: asyncio.AbstractEventLoop | None
    _mcp_thread: threading.Thread | None

    def _start_loop_in_thread(self) -> threading.Thread:

        def _runner():
            asyncio.set_event_loop(self._mcp_loop)
            self._mcp_loop.run_forever()

        t = threading.Thread(target=_runner, daemon=True)
        t.start()
        return t

    def _create_tools_for_agent(
        self,
        skill_registry: SkillRegistry | None,
        tool_config: dict,
    ):
        """为该 agent 创建独立的工具注册表（每 agent 独立 tools，与上游一致）。

        Args:
            skill_registry: 本 agent 的 Skill 注册中心（子集或全量）
            tool_config: 本 agent 的 tools 配置，形如 {"builtin": list[str], "mcp": str}

        Returns:
            ToolRegistry 实例，供该 agent 独占使用
        """

        builtin = tool_config.get('builtin', ['*'])
        registry = create_registry(builtin, skill_registry)
        if tool_config.get('mcp') and getattr(self, 'mcp_manager', None):
            self.mcp_manager.register_tools_into(registry)
        return registry

    def _setup_mcp_tools(self):
        """初始化 MCP 连接与工具加载，不注册到任何全局 registry（每 agent 在 _create_tools_for_agent 中通过 register_tools_into 注入）。

        Returns:
            MCPToolManager 实例，若未配置 MCP 则返回 None
        """
        self.mcp_manager = None
        if not (hasattr(self.config, 'mcp') or hasattr(self.config, 'mcp_servers')):
            return None
        manager = self._init_mcp_manager()
        self.mcp_manager = manager
        return manager

    def _init_mcp_manager(self):
        """创建 MCP 管理器并异步初始化所有服务器；不向任何 ToolRegistry 注册（由各 agent 的 _create_tools_for_agent 按需 register_tools_into）。"""
        from evomaster.agent.tools import MCPToolManager

        mcp_config = getattr(self.config, 'mcp', None)
        if not mcp_config or not isinstance(mcp_config, dict):
            return None
        if not mcp_config.get('enabled', True):
            self.logger.info('MCP is disabled in config')
            return None

        config_file = mcp_config.get('config_file', 'mcp_config.json')
        config_path = Path(config_file)
        if not config_path.is_absolute():
            config_path = self.config_manager.config_dir / config_path
        if not config_path.exists():
            self.logger.warning(f"MCP config file not found: {config_path}")
            return None

        self.logger.info(f"Loading MCP config from: {config_path}")
        try:
            with open(config_path, encoding='utf-8') as f:
                mcp_servers_config = json.load(f)
        except Exception as e:
            self.logger.error(f"Failed to load MCP config: {e}")
            return None

        PLACEHOLDER = '__EVOMASTER_WORKSPACES__'

        def _deep_replace(obj, old: str, new: str):
            if isinstance(obj, str):
                return obj.replace(old, new)
            if isinstance(obj, list):
                return [_deep_replace(x, old, new) for x in obj]
            if isinstance(obj, dict):
                return {k: _deep_replace(v, old, new) for k, v in obj.items()}
            return obj

        try:
            if self.run_dir is not None:
                ws_root = str((Path(self.run_dir) / 'workspaces').resolve())
                mcp_servers_config = _deep_replace(
                    mcp_servers_config, PLACEHOLDER, ws_root
                )
                self.logger.info(f"[MCP] Replaced {PLACEHOLDER} -> {ws_root}")
        except Exception as e:
            self.logger.warning(f"[MCP] Failed to replace placeholder paths: {e}")

        servers = self._parse_mcp_servers(mcp_servers_config)
        if not servers:
            self.logger.warning('No valid MCP servers found in config')
            return None

        self.logger.info('Setting up MCP tools...')
        manager = MCPToolManager()
        progress_cb = getattr(self, '_mcp_progress_callback', None)
        if callable(progress_cb):
            manager.set_progress_callback(progress_cb)
        if mcp_config.get('path_adaptor') == 'calculation':
            from evomaster.adaptors.calculation import get_calculation_path_adaptor

            calc_servers = mcp_config.get('calculation_servers')
            if calc_servers:
                manager.path_adaptor_servers = set(calc_servers)
            else:
                manager.path_adaptor_servers = {
                    s.get('name') for s in servers if s.get('name')
                }
            manager.path_adaptor_factory = lambda: get_calculation_path_adaptor(
                mcp_config
            )
            self.logger.info(
                'Path adaptor enabled for servers: %s', manager.path_adaptor_servers
            )

        async def init_mcp_servers():
            for server_config in servers:
                try:
                    await manager.add_server(**server_config)
                except Exception as e:
                    server_name = server_config.get('name', 'unknown')
                    self.logger.error(
                        f"Failed to add MCP server {server_name}: {e}",
                        exc_info=True,
                    )
                    sub_exceptions = getattr(e, 'exceptions', None)
                    if sub_exceptions is not None:
                        for i, sub in enumerate(sub_exceptions):
                            tb_str = ''.join(
                                traceback.format_exception(
                                    type(sub), sub, getattr(sub, '__traceback__', None)
                                )
                            ).strip()
                            self.logger.error(
                                f"  MCP sub-exception [{i}]: {type(sub).__name__}: {sub}\n{tb_str}",
                            )
                    elif getattr(e, '__cause__', None) is not None:
                        cause = e.__cause__
                        tb_str = ''.join(
                            traceback.format_exception(
                                type(cause),
                                cause,
                                getattr(cause, '__traceback__', None),
                            )
                        ).strip()
                        self.logger.error(
                            f"  MCP exception cause: {type(cause).__name__}: {cause}\n{tb_str}",
                        )

        if self._mcp_loop is None or self._mcp_loop.is_closed():
            self._mcp_loop = asyncio.new_event_loop()
            self._mcp_thread = self._start_loop_in_thread()

        manager.loop = self._mcp_loop
        future = asyncio.run_coroutine_threadsafe(init_mcp_servers(), self._mcp_loop)
        future.result()

        tool_count = len(manager.get_tool_names())
        server_count = len(manager.get_server_names())
        self.logger.info(
            f"MCP manager initialized: {tool_count} tools from {server_count} servers"
        )
        return manager

    def _parse_mcp_servers(self, mcp_config: dict) -> list[dict]:
        """解析 MCP 服务器配置

        支持标准 MCP 格式和扩展格式。

        Args:
            mcp_config: MCP 配置字典

        Returns:
            服务器配置列表
        """
        servers = []
        mcp_servers = mcp_config.get('mcpServers', {})

        for name, config in mcp_servers.items():
            if 'command' in config:
                # 标准格式（stdio）
                servers.append(
                    {
                        'name': name,
                        'transport': 'stdio',
                        'command': config['command'],
                        'args': config.get('args', []),
                        'env': config.get('env', {}),
                    }
                )
            elif 'transport' in config:
                # 扩展格式（http/sse）
                transport = config['transport'].lower()
                if transport in ['http', 'sse', 'streamable_http', 'streamable-http']:
                    servers.append(
                        {
                            'name': name,
                            'transport': transport,
                            'url': config['url'],
                            'headers': config.get('headers', {}),
                        }
                    )
                else:
                    self.logger.warning(
                        f"Unsupported transport for server {name}: {transport}"
                    )
            else:
                self.logger.warning(
                    f"Invalid config for server {name}: missing 'command' or 'transport'"
                )

        return servers
