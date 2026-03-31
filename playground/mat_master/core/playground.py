"""Mat Master Playground 实现

材料科学 / 计算材料方向的 EvoMaster agent，接入 Mat 的 MCP 工具
（Structure Generator、Science Navigator、Document Parser、DPA Calculator）。
使用 MatMasterAgent：支持 functions.finish 归一化、仅 task_completed==true 时结束。
mat_master 在此复写 _setup_mcp_tools、setup、_create_exp，不修改基类 core/playground.py。
"""

import asyncio
import json
import logging
from pathlib import Path

import yaml

from evomaster.core import BasePlayground, register_playground

from ..memory import MemoryService, get_memory_tools
from ..tools import get_peek_file_tool, get_web_search_tool
from .agent import MatMasterAgent
from .registry import MatMasterSkillRegistry
from .solvers import DirectSolver, ResearchPlanner
from .workspace_resolver import resolve_workspace_path


def _project_root() -> Path:
    """Project root (EvoMaster repo)."""
    return Path(__file__).resolve().parent.parent.parent.parent


@register_playground('mat_master')
class MatMasterPlayground(BasePlayground):
    """Mat Master Playground

    材料科学向的 playground，使用 Mat 的 MCP 服务（结构生成、科学导航、
    文档解析、DPA 计算），支持 LiteLLM 与 Azure 的 LLM 配置格式。
    使用 MatMasterAgent：异步任务未完成时不会因 partial 结束，需 task_completed=true 才结束。
    工作流模式通过 --mode 切换：direct（即时）| planner（规划执行）。
    异步计算的弹性监控（submit→poll→diagnose→retry）由内置工具 monitor_job 处理，
    不再作为独立的 Exp 模式。

    使用方式：
        python run.py --agent mat_master --task "材料相关任务"
    """

    def __init__(self, config_dir: Path = None, config_path: Path = None):
        """初始化 MatMasterPlayground

        Args:
            config_dir: 配置目录路径，默认为 configs/mat_master/
            config_path: 配置文件完整路径（如果提供，会覆盖 config_dir）
        """
        if config_path is None and config_dir is None:
            config_dir = _project_root() / 'configs' / 'mat_master'

        super().__init__(config_dir=config_dir, config_path=config_path)
        self.logger = logging.getLogger(self.__class__.__name__)
        self._skill_registry = None
        self._run_mode = None  # CLI --mode overrides config when set
        self.memory_service = MemoryService(run_dir=None)

    def set_mode(self, mode: str) -> None:
        """Set workflow mode from CLI: direct | planner (use --mode to switch)."""
        raw = (mode or '').strip().lower() or None
        self._run_mode = raw
        if self._run_mode:
            self.logger.info('Mat Master mode (from --mode): %s', self._run_mode)

    def set_run_dir(self, run_dir, task_id=None, session_id=None):
        """Override: keep memory_service.run_dir in sync and centralize workspace resolution."""
        super().set_run_dir(run_dir, task_id=task_id)
        # Ensure root logger level allows file handler to receive INFO (evo only adds handler, root default is WARNING)
        if run_dir and getattr(self, 'log_file_handler', None) is not None:
            root = logging.getLogger()
            level = getattr(
                logging, getattr(self.config.logging, 'level', 'INFO'), logging.INFO
            )
            root.setLevel(level)
        self.memory_service.run_dir = Path(run_dir) if run_dir else None
        if task_id:
            self.memory_service.set_session_id(task_id)
        if run_dir is not None:
            resolution = resolve_workspace_path(
                run_dir,
                task_id=task_id,
                session_id=session_id,
                create=True,
            )
            ws_path = resolution.path
            ws_str = str(ws_path)
            # Keep config in sync before setup() creates the session.
            self._update_workspace_path(ws_path)
            self.logger.info(
                'Workspace resolved: mode=%s source=%s session_id=%s task_id=%s path=%s',
                resolution.mode,
                resolution.source,
                session_id or '',
                task_id or '',
                ws_str,
            )
        # When reusing cached pg, update the active session as well.
        if self.session is not None and run_dir is not None:
            if hasattr(self.session.config, 'workspace_path'):
                self.session.config.workspace_path = ws_str
            if hasattr(self.session.config, 'working_dir'):
                self.session.config.working_dir = ws_str
            self.logger.debug('Session workspace updated to: %s', ws_str)

    def _create_tools_for_agent(self, skill_registry, tool_config):
        """Override: 在基类 registry 上增加 memory、peek_file、extract_webpage、monitor_job、aissq（每 agent 独立 tools）。"""
        registry = super()._create_tools_for_agent(skill_registry, tool_config)
        if self.run_dir is not None:
            self.memory_service.run_dir = Path(self.run_dir)
        memory_tools = get_memory_tools(self.memory_service)
        registry.register_many(memory_tools)
        registry.register(get_peek_file_tool())
        from ..tools import (
            get_aissq_download_tool,
            get_aissq_search_tool,
            get_extract_webpage_tool,
        )

        # P1-b: derive workspace-scoped cache dir (not run_dir, to isolate batch tasks)
        _cache_dir = None
        if self.run_dir is not None:
            _run_path = Path(self.run_dir)
            if self.task_id:
                _ws = _run_path / 'workspaces' / self.task_id
            else:
                _ws = _run_path / 'workspace'
            _cache_dir = _ws / '_tmp' / 'web_cache'
        registry.register(get_extract_webpage_tool(cache_dir=_cache_dir))
        registry.register(get_web_search_tool())
        from evomaster.agent.tools.builtin.monitor_job import MonitorJobTool

        registry.register(MonitorJobTool())
        registry.register(get_aissq_search_tool())
        registry.register(get_aissq_download_tool())
        self.logger.info(
            'Registered %d memory tools, peek_file, extract_info_from_webpage, web-search, monitor_job, aissq_search, aissq_download (per-agent registry)',
            len(memory_tools),
        )
        return registry

    def sync_skills_to_remote(
        self,
        remote_base: str = '/personal/workspace/.evomaster',
    ) -> None:
        """Upload skills (core + mat_master + user) to the remote node.

        User skills land at remote_user_skills_root (default /personal/.evomaster-skills),
        which is separate from remote_base so they persist as a user-level directory across
        different project syncs.  session.remote_user_skills_root and
        session.local_user_skills_root are set so that skill.py can remap user skill
        script paths for SSH execution.
        """
        from evomaster.agent.session import SSHSession

        if not isinstance(self.session, SSHSession):
            self.logger.debug('sync_skills_to_remote: skipped (not an SSH session)')
            return

        env = self.session._env
        exclude = {
            '__pycache__',
            '.git',
            'node_modules',
            '.mypy_cache',
            '.pytest_cache',
        }

        config_dict = self.config.model_dump()
        skills_config = config_dict.get('skills', {})
        skills_root_rel = skills_config.get('skills_root', 'evomaster/skills')
        skills_root = Path(skills_root_rel)
        if not skills_root.is_absolute():
            skills_root = _project_root() / skills_root
        if skills_root.is_dir():
            env.upload_directory_tarball(
                str(skills_root), f"{remote_base}/{skills_root_rel}", exclude=exclude
            )

        mat_skills_root = _project_root() / 'playground' / 'mat_master' / 'skills'
        if mat_skills_root.is_dir():
            env.upload_directory_tarball(
                str(mat_skills_root),
                f"{remote_base}/playground/mat_master/skills",
                exclude=exclude,
            )

        # Upload user skills to their own persistent remote directory
        skill_evolution_cfg = (config_dict.get('mat_master') or {}).get(
            'skill_evolution'
        ) or {}
        local_user_root_raw = skill_evolution_cfg.get(
            'local_user_skills_root', '~/.evomaster-skills'
        )
        local_user_root = Path(local_user_root_raw).expanduser()
        remote_user_root = skill_evolution_cfg.get(
            'remote_user_skills_root', '/personal/.evomaster-skills'
        )
        if local_user_root.is_dir():
            env.upload_directory_tarball(
                str(local_user_root), remote_user_root, exclude=exclude
            )
            self.logger.info(
                'sync_skills_to_remote: user skills synced to %s', remote_user_root
            )

        # Store mapping attributes on session for SSH path remapping in skill.py
        self.session.remote_project_root = remote_base
        self.session.remote_user_skills_root = remote_user_root
        self.session.local_user_skills_root = str(local_user_root)
        self.logger.info(
            'sync_skills_to_remote: done, remote_project_root=%s, remote_user_skills_root=%s',
            remote_base,
            remote_user_root,
        )

    def setup(self) -> None:
        """Override: build MatMasterSkillRegistry and pass to tools/agents."""
        self.logger.info('Setting up Mat Master playground...')

        llm_config_dict = self._setup_llm_config()
        self._llm_config_dict = llm_config_dict

        self._setup_session()

        config_dict = self.config.model_dump()
        skills_config = config_dict.get('skills', {})
        mat_master_config = config_dict.get('mat_master') or {}
        mat_skills_root = _project_root() / 'playground' / 'mat_master' / 'skills'
        skill_registry = None

        # Resolve user skills root (shared by both enabled/disabled branches)
        skill_evolution_cfg = mat_master_config.get('skill_evolution') or {}
        local_user_skills_root_raw = skill_evolution_cfg.get(
            'local_user_skills_root', '~/.evomaster-skills'
        )
        local_user_skills_root = Path(local_user_skills_root_raw).expanduser()

        if skills_config.get('enabled', False):
            from evomaster.skills import SkillRegistry

            skills_root = Path(skills_config.get('skills_root', 'evomaster/skills'))
            if not skills_root.is_absolute():
                skills_root = _project_root() / skills_root
            core_registry = SkillRegistry(skills_root)
            self.logger.info(
                'Loaded %d core skills', len(core_registry.get_all_skills())
            )

            dynamic_root = None
            dynamic_rel = skill_evolution_cfg.get('dynamic_skills_root')
            if dynamic_rel:
                dynamic_root = _project_root() / dynamic_rel
            skill_registry = MatMasterSkillRegistry(
                core_registry,
                dynamic_root=dynamic_root,
                mat_skills_root=mat_skills_root,
                user_skills_root=local_user_skills_root,
            )
            self.logger.info(
                'MatMasterSkillRegistry ready (dynamic_root=%s, mat_skills_root=%s, user_skills_root=%s)',
                dynamic_root,
                mat_skills_root,
                local_user_skills_root,
            )
        else:
            # Always load local mat_master skills (ask-human, etc.) even when skills.enabled is False
            from evomaster.skills import SkillRegistry

            empty_root = Path(__file__).resolve().parent.parent / 'memory'
            core_registry = SkillRegistry(empty_root)
            skill_registry = MatMasterSkillRegistry(
                core_registry,
                dynamic_root=None,
                mat_skills_root=mat_skills_root,
                user_skills_root=local_user_skills_root,
            )
            self.logger.info(
                'MatMasterSkillRegistry (local only, mat_skills_root=%s, user_skills_root=%s)',
                mat_skills_root,
                local_user_skills_root,
            )

        self._skill_registry = skill_registry
        # 初始化 MCP（每 agent 在 _create_agent 内通过 _create_tools_for_agent 获得独立 registry）
        self._setup_mcp_tools()
        if self.mcp_manager is not None:
            self.logger.info('MCP manager ready (tools will be attached per-agent)')

        agents_config = getattr(self.config, 'agents', None)
        if agents_config and isinstance(agents_config, dict) and agents_config:
            self._setup_agents(skill_registry=skill_registry)
        else:
            raise ValueError(
                'No agents configuration found. '
                'Please add "agents" section to config.yaml (e.g. agents: { default: ... })'
            )

    def _create_exp(self):
        """Return solver by --mode: direct | planner.

        Resilient calculation logic (submit/monitor/diagnose/retry) is now handled
        by the monitor_job built-in tool, not a top-level Exp. The agent invokes it
        naturally within either Direct or Planner mode.
        """
        mode = getattr(self, '_run_mode', None) or 'direct'
        if mode == 'planner':
            output_callback = getattr(self, '_planner_output_callback', None)
            exp = ResearchPlanner(
                self.agent,
                self.config,
                output_callback=output_callback,
                config_dir=self.config_dir,
            )
        else:
            exp = DirectSolver(self.agent, self.config)

        if self.run_dir:
            exp.set_run_dir(self.run_dir)
        return exp

    def run(self, task_description: str, output_file: str | None = None) -> dict:
        """Override: pass task_id to exp.run() when set (e.g. batch mode)."""
        try:
            self.setup()
            self._setup_trajectory_file(output_file)
            exp = self._create_exp()
            self.logger.info('Running experiment...')
            task_id = getattr(self, 'task_id', None)
            if task_id:
                return exp.run(task_description, task_id=task_id)
            return exp.run(task_description)
        finally:
            self.cleanup()

    def _create_agent(
        self,
        name: str,
        agent_config: dict,
        skill_registry=None,
        tool_config: dict | None = None,
        llm_config: dict | None = None,
        skill_config: dict | None = None,
    ):
        """创建 Mat Master 专用 Agent（MatMasterAgent）；签名与基类 v0.0.2 一致。"""
        from evomaster.agent import AgentConfig
        from evomaster.agent.context import ContextConfig
        from evomaster.utils import LLMConfig, create_llm

        if llm_config is None:
            llm_config = self.config_manager.get_agent_llm_config(name)
        if tool_config is None:
            tool_config = self.config_manager.get_agent_tools_config(name)
        builtin = tool_config.get('builtin', ['*'])
        enable_tools = bool(builtin)
        enabled_tool_names = None
        if '*' in builtin or not builtin:
            enabled_tool_names = None
        else:
            enabled_tool_names = list(builtin)

        base_max_turns = agent_config.get('max_turns', 20)
        full_cfg = self.config.model_dump() or {}
        mode = (self._run_mode or 'direct').strip().lower()
        mode_profiles = (full_cfg.get('mat_master') or {}).get('mode_profiles') or {}
        mode_cfg = mode_profiles.get(mode) if isinstance(mode_profiles, dict) else {}
        if not isinstance(mode_cfg, dict):
            mode_cfg = {}
        max_turns = int(mode_cfg.get('agent_max_turns', base_max_turns))
        context_config_dict = agent_config.get('context', {})
        context_config = ContextConfig(**context_config_dict)
        agent_cfg = AgentConfig(max_turns=max_turns, context_config=context_config)
        output_config = self._get_output_config()

        llm = create_llm(LLMConfig(**llm_config), output_config=output_config)
        self.logger.debug(f"Created independent LLM instance for {name} agent")

        system_prompt_file = agent_config.get('system_prompt_file')
        user_prompt_file = agent_config.get('user_prompt_file')
        playground_base = Path(str(self.config_dir).replace('configs', 'playground'))
        if system_prompt_file:
            prompt_path = Path(system_prompt_file)
            if not prompt_path.is_absolute():
                system_prompt_file = str((playground_base / prompt_path).resolve())
        if user_prompt_file:
            prompt_path = Path(user_prompt_file)
            if not prompt_path.is_absolute():
                user_prompt_file = str((playground_base / prompt_path).resolve())
        prompt_format_kwargs = agent_config.get('prompt_format_kwargs', {})

        # Read execution-layer config for the unified BatchExecutor
        _full_config_dict = self.config.model_dump() or {}
        _mat_master_cfg = _full_config_dict.get('mat_master') or {}
        _exec_cfg = _mat_master_cfg.get('execution') or {}

        tools = self._create_tools_for_agent(skill_registry, tool_config)
        agent = MatMasterAgent(
            llm=llm,
            session=self.session,
            tools=tools,
            system_prompt_file=system_prompt_file,
            user_prompt_file=user_prompt_file,
            prompt_format_kwargs=prompt_format_kwargs,
            config=agent_cfg,
            skill_registry=skill_registry,
            output_config=output_config,
            config_dir=self.config_dir,
            enable_tools=enable_tools,
            enabled_tool_names=enabled_tool_names,
            direct_max_workers=_exec_cfg.get('direct_max_workers', 4),
            rate_limit=_exec_cfg.get('rate_limit'),
            config_dict=_full_config_dict,
            mode_profile=(self._run_mode or 'direct'),
        )
        agent.set_agent_name(name)
        return agent

    def _setup_mcp_tools(self):
        """初始化 MCP 工具（mat_master 复写：在添加服务器前设置 tool_include_only）。

        与基类逻辑一致，仅在步骤 7 与 8 之间从 config.mcp.tool_include_only 写入 manager，
        使 mat_sn 等仅注册指定工具（如 web-search、search-papers-enhanced）。
        """
        from evomaster.agent.tools import MCPToolManager

        # 从当前使用的 config 文件直接读 mcp，确保 tool_include_only 一定来自 YAML
        yaml_path = self.config_dir / (
            getattr(self.config_manager, 'config_file', None) or 'config.yaml'
        )
        mcp_config = None
        if yaml_path.exists():
            try:
                with open(yaml_path, encoding='utf-8') as f:
                    raw = yaml.safe_load(f)
                if isinstance(raw, dict):
                    mcp_config = raw.get('mcp')
            except Exception as e:
                self.logger.debug('Read mcp from YAML: %s', e)
        if not mcp_config or not isinstance(mcp_config, dict):
            mcp_config = (self.config.model_dump() or {}).get('mcp')
        if not mcp_config or not isinstance(mcp_config, dict):
            if not mcp_config:
                self.logger.debug('MCP not configured, skipping')
            else:
                self.logger.error('Invalid MCP config format, expected dict')
            return None
        if not mcp_config.get('enabled', True):
            self.logger.info('MCP is disabled in config')
            return None

        config_file = mcp_config.get('config_file', 'mcp_config.json')
        config_path = Path(config_file)
        if not config_path.is_absolute():
            config_path = self.config_manager.config_dir / config_path

        # 环境感知：根据 SERVICE_ENV 切换 MCP 配置文件（test → mcp_config.test.json）
        if mcp_config.get('path_adaptor') == 'calculation':
            from evomaster.adaptors.calculation import resolve_mcp_config_path

            config_path = resolve_mcp_config_path(config_path)

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
            else:
                self.logger.debug(
                    f"[MCP] run_dir is None, skip placeholder replace: {PLACEHOLDER}"
                )
                if PLACEHOLDER in json.dumps(mcp_servers_config):
                    self.logger.warning(
                        '[MCP] run_dir is None but config contains %s; MCP servers may fail or write to wrong paths.',
                        PLACEHOLDER,
                    )
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
        from matmaster.tools.lazy_mcp import configure_mcp_manager

        all_names = {s.get('name') for s in servers if s.get('name')}
        configure_mcp_manager(manager, mcp_config, all_server_names=all_names)

        if manager.path_adaptor_servers:
            self.logger.info(
                'Path adaptor enabled for servers: %s', manager.path_adaptor_servers
            )
        if manager.sync_tools_by_server:
            self.logger.info(
                'MCP sync_tools_by_server set: %s',
                list(manager.sync_tools_by_server.keys()),
            )

        async def init_mcp_servers():
            for server_config in servers:
                try:
                    await manager.add_server(**server_config)
                except Exception as e:
                    self.logger.error(
                        f"Failed to add MCP server {server_config.get('name')}: {e}"
                    )

        if self._mcp_loop is None or self._mcp_loop.is_closed():
            self._mcp_loop = asyncio.new_event_loop()
            self._mcp_thread = self._start_loop_in_thread()

        manager.loop = self._mcp_loop
        future = asyncio.run_coroutine_threadsafe(init_mcp_servers(), self._mcp_loop)
        future.result()

        # 每 agent 独立 tools：不在此处 register_tools；各 agent 在 _create_tools_for_agent 中通过 register_tools_into 注入

        # ── 异步工具不限超时 ──────────────────────────────────────────────
        # 使用 AsyncToolRegistry（从 calculation_executors 推导）标记异步工具，
        # 将其 _call_timeout 设为 None（fut.result(timeout=None) = 无限等待），
        # 避免 submit_*/calculate_*/run_* 等远程提交因 MCP 服务端处理慢而误报超时。
        try:
            from .async_tool_registry import AsyncToolRegistry

            config_dict = self.config.model_dump()
            registry = AsyncToolRegistry(config_dict)
            async_count = 0
            for server_name, server_tools in manager.tools_by_server.items():
                for _, mcp_tool in server_tools.items():
                    remote_name = getattr(mcp_tool, '_remote_tool_name', '') or ''
                    if registry.is_async_tool(server_name, remote_name):
                        mcp_tool._call_timeout = None
                        async_count += 1
            if async_count:
                self.logger.info(
                    'Async timeout override: %d MCP tools set to no-timeout (derived from calculation_executors)',
                    async_count,
                )
        except Exception as e:
            self.logger.warning('Failed to apply async timeout overrides: %s', e)

        tool_count = len(manager.get_tool_names())
        server_count = len(manager.get_server_names())
        self.logger.info(
            f"MCP manager initialized: {tool_count} tools from {server_count} servers (per-agent attach)"
        )
        self.mcp_manager = manager
        return manager
