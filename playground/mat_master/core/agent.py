"""MatMasterAgent: finish only when task_completed=true.

System prompt uses file-first loading with runtime composition fallback.
"""

import json
import os
from pathlib import Path
from typing import Any

from evomaster.agent.agent import Agent
from evomaster.agent.context import CompactionConfig, ContextCompactor
from evomaster.utils.types import (
    AssistantMessage,
    Dialog,
    StepRecord,
    SystemMessage,
    ToolMessage,
    Trajectory,
    UserMessage,
)

from .agent_dialog import sanitize_dialog_history
from .agent_finish_gates import MatMasterFinishGatesMixin
from .agent_finish_message import generate_finish_report
from .agent_tool_execution import MatMasterToolExecutionMixin
from .async_execution_policy import AsyncExecutionPolicy
from .callback import MatToolCallbacks, ToolCallbackPipeline
from .execution_journal import ExecutionJournal
from .job_registry import JobRegistry
from .tool_guard import ToolGuard


class MatMasterAgent(MatMasterFinishGatesMixin, MatMasterToolExecutionMixin, Agent):
    """Agent that ends the run when the finish tool is called with task_completed=true or partial.

    If the agent calls finish with task_completed=false, we add the
    tool response and continue (do not set should_finish).
    """

    def __init__(
        self,
        *args,
        direct_max_workers: int = 4,
        rate_limit: float | None = None,
        config_dict: dict | None = None,
        mode_profile: str = 'direct',
        **kwargs,
    ):
        self._system_prompt_file_configured = bool(kwargs.get('system_prompt_file'))
        super().__init__(*args, **kwargs)
        # Stateful guard for loop prevention + validation gate.
        self._tool_guard = ToolGuard(self.logger, config_dict)
        # Concurrency config for Direct mode (BatchExecutor)
        self._direct_max_workers: int = max(1, direct_max_workers)
        self._rate_limit: float | None = rate_limit
        # Full config dict for async tool registry (prompt injection)
        self._full_config_dict: dict = config_dict or {}
        # Mode profile controls prompt-level execution contract (direct/planner).
        self._mode_profile: str = (mode_profile or 'direct').strip().lower()
        from .async_tool_registry import AsyncToolRegistry

        self._async_tool_registry = AsyncToolRegistry(self._full_config_dict)
        self._async_execution_policy = AsyncExecutionPolicy(self._async_tool_registry)
        # Runtime-tracked async jobs: source of truth for finish-attempt gate.
        self._job_registry = JobRegistry(self.logger)
        # Finish-block safety cap: after N consecutive blocks, force-pass quality gates.
        _mat = (config_dict or {}).get('mat_master') or {}
        _planner = _mat.get('planner') or {}
        _quality = _planner.get('quality_gates') or {}
        self._finish_block_max: int = max(1, int(_quality.get('finish_block_max', 3)))
        self._finish_block_count: int = 0
        # Tool callback pipeline
        self._tool_callback_pipeline = ToolCallbackPipeline(self.logger)
        self._register_default_tool_callbacks()
        # 工具执行结果自动落盘：匹配这些前缀的工具，每次执行后把 observation 写入 _tmp/tool_outputs/<tool_name>/
        _mat = (config_dict or {}).get('mat_master') or {}
        _exec = _mat.get('execution') or {}
        self._tool_output_auto_save_patterns: list[str] = _exec.get(
            'tool_output_auto_save_patterns',
            ['mat_sn_', 'mat_doc_', 'web-search', 'extract_info_from_webpage'],
        )
        if not isinstance(self._tool_output_auto_save_patterns, list):
            self._tool_output_auto_save_patterns = [
                'mat_sn_',
                'mat_doc_',
                'web-search',
                'extract_info_from_webpage',
            ]
        self._tool_output_save_counter = 0
        self._execution_journal = ExecutionJournal()
        # 超大工具结果摘要化：匹配以下前缀的工具 observation 超过阈值时自动替换为结构化摘要
        self._tool_obs_summarize_patterns: list[str] = _exec.get(
            'tool_obs_summarize_patterns',
            [
                'mat_sn_search-papers-enhanced',
                'mat_sn_search-papers',
                'mat_sg_',
                'mat_doc_',
            ],
        )
        if not isinstance(self._tool_obs_summarize_patterns, list):
            self._tool_obs_summarize_patterns = [
                'mat_sn_search-papers-enhanced',
                'mat_sn_search-papers',
                'mat_sg_',
                'mat_doc_',
            ]
        self._tool_obs_summarize_threshold: int = int(
            _exec.get('tool_obs_summarize_threshold', 10000)
        )

        # 初始化 ContextCompactor（如果 compaction.enabled）
        _ctx_cfg = (
            (config_dict or {}).get('agents', {}).get('general', {}).get('context', {})
        )
        _compaction_raw = _ctx_cfg.get('compaction', {})
        _compaction_cfg = (
            CompactionConfig(**_compaction_raw)
            if _compaction_raw
            else CompactionConfig()
        )
        self._compaction_enabled: bool = _compaction_cfg.enabled
        if _compaction_cfg.enabled:
            # 尝试为 compaction 创建独立小模型 LLM（由 config.llm.<compaction_llm> 指定）
            _compaction_llm_instance = None
            _compaction_llm_key = (
                _compaction_cfg.compaction_llm
            )  # e.g. "compaction" or None
            if _compaction_llm_key:
                _llm_dict = (config_dict or {}).get('llm', {})
                _compaction_llm_cfg_raw = _llm_dict.get(_compaction_llm_key)
                if _compaction_llm_cfg_raw and isinstance(
                    _compaction_llm_cfg_raw, dict
                ):
                    try:
                        from evomaster.utils import LLMConfig, create_llm

                        _compaction_llm_instance = create_llm(
                            LLMConfig(**_compaction_llm_cfg_raw),
                            output_config={
                                'show_in_console': False,
                                'log_to_file': False,
                            },
                        )
                        self.logger.info(
                            '[Agent] ContextCompactor using dedicated LLM: key=%s model=%s',
                            _compaction_llm_key,
                            _compaction_llm_cfg_raw.get('model', '?'),
                        )
                    except Exception as _e:
                        self.logger.warning(
                            '[Agent] Failed to create compaction LLM (key=%s): %s'
                            ' — falling back to agent LLM',
                            _compaction_llm_key,
                            _e,
                        )
                else:
                    self.logger.warning(
                        '[Agent] compaction_llm key=%r not found in config.llm'
                        ' — falling back to agent LLM',
                        _compaction_llm_key,
                    )

            if _compaction_llm_instance is not None:

                def _llm_caller(dialog, _llm=_compaction_llm_instance):
                    return _llm.query(dialog)

            else:

                def _llm_caller(dialog):
                    return self.llm.query(dialog)

            _compactor = ContextCompactor(
                config=_compaction_cfg,
                llm_caller=_llm_caller,
                execution_journal=self._execution_journal,
            )
            self.context_manager.set_compactor(_compactor)
            self.logger.info(
                '[Agent] ContextCompactor enabled (effective_trigger_tokens=%d, '
                'context_window=%d, ratio=%.0f%%, compaction_llm=%s)',
                _compaction_cfg.effective_trigger_tokens(),
                _compaction_cfg.context_window_tokens,
                _compaction_cfg.trigger_ratio * 100,
                _compaction_llm_key or 'agent_llm',
            )

    def _initialize(self, task) -> None:
        """Override: 支持 task.meta['dialog_history'] 多轮对话；否则与基类一致并重置 tool output 计数。"""
        history_raw = task.meta.get('dialog_history') if task.meta else None
        if isinstance(history_raw, list) and len(history_raw) > 0:
            self.trajectory = Trajectory(
                task_id=task.task_id,
                meta={
                    'agent_version': self.VERSION,
                    'task_type': getattr(task, 'task_type', 'general'),
                },
            )
            system_prompt = self._get_system_prompt()
            self._initial_system_prompt = system_prompt
            self._initial_user_prompt = task.description or ''
            history_messages = []
            for d in history_raw:
                if not isinstance(d, dict):
                    continue
                role = (d.get('role') or '').strip().lower()
                try:
                    if role == 'user':
                        history_messages.append(UserMessage.model_validate(d))
                    elif role == 'assistant':
                        history_messages.append(AssistantMessage.model_validate(d))
                    elif role == 'tool':
                        history_messages.append(ToolMessage.model_validate(d))
                except Exception as e:
                    self.logger.warning(
                        'dialog_history skip invalid message role=%s: %s', role, e
                    )
            # Defensive: ensure every tool_use has a matching tool_result so
            # the dialog is structurally valid for all LLM providers.
            history_messages = sanitize_dialog_history(
                history_messages, logger=self.logger
            )
            self._current_task_description = getattr(task, 'description', '') or ''
            self.current_dialog = Dialog(
                messages=[
                    SystemMessage(content=system_prompt),
                    *history_messages,
                    UserMessage(content=task.description or ''),
                ],
                tools=self._get_tool_specs(),
            )

            # Pre-compact: if the restored history already exceeds the compaction
            # trigger threshold, compact it now instead of deferring to the first
            # prepare_for_query() call.  This avoids the "compaction fires on every
            # continuation" problem where full raw DB history is replayed each time.
            if self.context_manager.should_compact(self.current_dialog):
                tokens_before = self.context_manager.estimate_tokens(
                    self.current_dialog
                )
                self.logger.info(
                    '[Agent] _initialize: pre-compacting dialog_history '
                    '(tokens=%d exceeds trigger=%d)',
                    tokens_before,
                    self.context_manager.config.compaction.effective_trigger_tokens(),
                )
                self.current_dialog = self.context_manager.prepare_for_query(
                    self.current_dialog
                )
                tokens_after = self.context_manager.estimate_tokens(self.current_dialog)
                self.logger.info(
                    '[Agent] _initialize: pre-compact done tokens=%d → %d (saved %d)',
                    tokens_before,
                    tokens_after,
                    tokens_before - tokens_after,
                )

            self.trajectory.dialogs.append(self.current_dialog)
            self._step_count = 0
            self._tool_output_save_counter = 0
            return
        super()._initialize(task)
        self._tool_output_save_counter = 0
        self._current_task_description = getattr(task, 'description', '') or ''
        # Reset journal for the new task and bind it to the task-scoped file.
        self._execution_journal = ExecutionJournal()
        workspace = getattr(self.session.config, 'workspace_path', '') or ''
        if workspace:
            task_id = getattr(self.trajectory, 'task_id', None) or 'unknown'
            journal_name = f'execution_journal_{task_id}.jsonl'
            self._execution_journal.set_path(
                os.path.join(workspace, '_tmp', journal_name)
            )

    def _get_async_tool_registry(self):
        """Get async registry derived from full config dict."""
        return self._async_tool_registry

    def _register_default_tool_callbacks(self) -> None:
        """Register default MAT callbacks in execution order."""
        MatToolCallbacks(self).register(self._tool_callback_pipeline)

    def _get_system_prompt(self) -> str:
        """Use generated system prompt (tool list + date), then append working directory, tool rules, and skills.
        Date and OS/Shell are appended last so they appear at the end of the prompt (and in log tail).
        """
        from ..prompts.build_prompt import build_mat_master_system_prompt

        # Build registry from config for dynamic prompt injection
        registry = self._get_async_tool_registry()

        template_text = (
            self._system_prompt
            if self._system_prompt_file_configured
            and bool(getattr(self, '_system_prompt', ''))
            else None
        )
        base, current_date, os_type, shell_type = build_mat_master_system_prompt(
            registry=registry,
            mode_profile=self._mode_profile,
            template_text=template_text,
        )

        working_dir = self.session.config.workspace_path
        working_dir_abs = str(Path(working_dir).absolute())
        working_dir_info = f"\n\nYou must perform all operations in this working directory; do not change directory. All file operations and commands must be run under: {working_dir_abs}"
        prompt = base + working_dir_info
        if working_dir_abs == '/share' or working_dir_abs.startswith('/share/'):
            prompt += (
                '\n\nThis working directory is under /share. '
                '/share is the Bohrium project-scoped shared storage, so files here may be shared across multiple sessions in the same project. '
                'Do not assume session isolation when reading, overwriting, moving, or deleting files.'
            )

        # Mandatory citation and output format for survey/manuscript — agent MUST follow this
        _citation_format_path = (
            Path(__file__).resolve().parent.parent
            / 'skills'
            / '_common'
            / 'reference'
            / 'citation_and_output_format.md'
        )
        if _citation_format_path.exists():
            prompt += '\n\n# Citation and output format (mandatory for literature surveys and manuscripts)\n\n'
            prompt += _citation_format_path.read_text(encoding='utf-8').strip()
            prompt += '\n\nYou MUST follow the above format when writing survey reports or manuscript sections: use [n](url) for every citation, include a References section with URL for each [n], and obey General / Citation / References section / Terminology rules.'

        if self.skill_registry is not None:
            skills_info = self.skill_registry.get_meta_info_context()
            if skills_info:
                prompt += f"\n{skills_info}\n"
                prompt += """
You can use the 'use_skill' tool to:
1. Get detailed information about a skill: action='get_info'
2. Get reference documentation: action='get_reference'
3. Run scripts from Operator skills: action='run_script'
"""
        # Append date and OS/Shell last so they appear in the log tail (LLM logs truncate to head+tail)
        prompt += f"\nToday's date: {current_date} (OS: {os_type}, Shell: {shell_type})"
        return prompt

    def _get_tool_specs(self) -> list:
        """Expose MCP tools using unified async execution policy."""
        specs = super()._get_tool_specs()
        return self._async_execution_policy.filter_tool_specs_for_llm(specs)

    def _on_assistant_message(self, msg: AssistantMessage) -> None:
        """Optional hook after assistant message is added. Override in subclasses (e.g. streaming)."""

    def _on_tool_call_start(self, tool_call) -> None:
        """Optional hook called after before-callbacks have patched tool_call
        args but before the tool is actually executed.

        Override in subclasses (e.g. StreamingMatMasterAgent) to emit
        ``tool_call`` events with the callback-resolved arguments."""

    def _on_tool_message(self, msg: ToolMessage) -> None:
        """Optional hook after each tool message is added. Override in subclasses (e.g. streaming)."""

    # Marker embedded in periodic reminder system messages so they can be
    # identified and replaced on the next injection cycle.
    _REMINDER_MARKER = '\x00EXEC_STATE_REMINDER\x00'
    _REMINDER_INTERVAL = 5  # inject / refresh every N steps

    def _build_execution_reminder(self) -> str:
        """Build the periodic execution-state reminder injected into the dialog."""
        n = self._step_count
        task_text = (self._initial_user_prompt or '').strip()
        if len(task_text) > 500:
            task_text = task_text[:500] + '…'
        compact = self._execution_journal.get_compact_summary()
        # compaction 启用时注入 produced artifacts 列表（防止 LLM 遗忘已产出文件）
        artifacts_block = ''
        if getattr(self, '_compaction_enabled', False):
            files = [
                e['saved_path']
                for e in self._execution_journal.entries
                if e.get('saved_path')
            ]
            if files:
                artifacts_block = '\n\nPRODUCED ARTIFACTS:\n' + '\n'.join(
                    f'- {f}' for f in files[-20:]
                )
        return (
            f'{self._REMINDER_MARKER}'
            f'[EXECUTION STATE REMINDER — Step {n}]\n\n'
            f'ORIGINAL TASK:\n{task_text}\n\n'
            f'PROGRESS ({len(self._execution_journal.entries)} tool calls):\n{compact}'
            f'{artifacts_block}\n\n'
            'ACTIVE RULES:\n'
            '- All workspace file links must use [filename](path) format.\n'
            '- Finish message must include "## Execution Details" per-step subsections.\n'
            '- Do not use fallback strategies unless the primary approach explicitly fails.'
        )

    def _step(self) -> bool:
        """Override: for finish tool, execute it and set should_finish when task_completed is true or partial."""
        self._step_count += 1
        # Keep async monitor state fresh across turns.
        self._job_registry.refresh_pending()

        # Inject / refresh periodic execution-state reminder every N steps.
        if self._step_count > 1 and self._step_count % self._REMINDER_INTERVAL == 0:
            self.current_dialog.messages = [
                m
                for m in self.current_dialog.messages
                if not (
                    getattr(m, 'role', None) is not None
                    and getattr(m.role, 'value', m.role) == 'system'
                    and self._REMINDER_MARKER in (getattr(m, 'content', '') or '')
                )
            ]
            reminder = self._build_execution_reminder()
            self.current_dialog.add_message(SystemMessage(content=reminder))

        dialog_for_query = self.context_manager.prepare_for_query(self.current_dialog)
        # 若 prepare_for_query 发生了压缩/截断，将结果回写到 current_dialog，
        # 避免下一个 step 再次对未压缩的原始 dialog 重复触发压缩。
        if dialog_for_query is not self.current_dialog:
            self.current_dialog = dialog_for_query
        assistant_message = self._query_with_context_recovery(dialog_for_query)
        self.current_dialog.add_message(assistant_message)
        self._on_assistant_message(assistant_message)
        step_record = StepRecord(
            step_id=self._step_count,
            assistant_message=assistant_message,
        )

        if not assistant_message.tool_calls:
            if hasattr(self, 'enable_tools') and not self.enable_tools:
                self.trajectory.add_step(step_record)
                self._append_trajectory_entry(dialog_for_query, step_record)
                return True
            self._handle_no_tool_call()
            self.trajectory.add_step(step_record)
            self._append_trajectory_entry(dialog_for_query, step_record)
            return False

        should_finish = False

        # Separate finish calls from regular tool calls
        finish_call = None
        regular_calls = []
        for tool_call in assistant_message.tool_calls:
            if tool_call.function.name == 'finish':
                finish_call = tool_call
            else:
                regular_calls.append(tool_call)

        # Execute regular tool calls in parallel (with loop detection)
        if regular_calls:
            # Split into executable vs loop-blocked
            exec_calls = []
            loop_blocked: list[tuple[Any, str]] = []  # (tool_call, warning_msg)
            pending_jobs_exist = bool(self._job_registry.pending_jobs())
            for tc in regular_calls:
                if (
                    pending_jobs_exist
                    and not self._async_execution_policy.is_call_allowed_while_pending(
                        tc
                    )
                ):
                    loop_blocked.append(
                        (
                            tc,
                            self._async_execution_policy.pending_gate_message(),
                        )
                    )
                    self._on_tool_call_start(tc)
                    self._tool_guard.record_tool_call(tc)
                    continue
                decision = self._tool_guard.evaluate(tc)
                if decision.blocked:
                    loop_blocked.append((tc, decision.message))
                    # Emit tool_call event for blocked calls too (with original args).
                    self._on_tool_call_start(tc)
                else:
                    exec_calls.append(tc)
                self._tool_guard.record_tool_call(tc)

            # Execute non-blocked calls in parallel
            results = (
                self._execute_tools_parallel(
                    exec_calls, max_workers=self._direct_max_workers
                )
                if exec_calls
                else []
            )

            # Merge results: first the executed ones, then the loop-blocked ones (in original order)
            all_results: list[tuple[Any, str, dict[str, Any]]] = []
            exec_iter = iter(results)
            block_iter = iter(loop_blocked)
            for tc in regular_calls:
                if any(tc is btc for btc, _ in loop_blocked):
                    btc, warn_msg = next(block_iter)
                    all_results.append((btc, warn_msg, {'loop_blocked': True}))
                else:
                    all_results.append(next(exec_iter))

            for tool_call, observation, info in all_results:
                self._tool_guard.update_after_tool(tool_call, observation, info)
                # 从源头就传结构化 result：解析 JSON 作为 content，tool_result 事件的 result/observation 即为字典
                if isinstance(observation, dict):
                    result_content: Any = observation
                else:
                    obs_str = (
                        observation
                        if isinstance(observation, str)
                        else str(observation)
                    )
                    try:
                        result_content = json.loads(obs_str)
                        if not isinstance(result_content, dict):
                            result_content = {'message': obs_str}
                    except (json.JSONDecodeError, TypeError):
                        result_content = {'message': obs_str}
                tool_message = ToolMessage(
                    content=result_content,
                    tool_call_id=tool_call.id,
                    name=tool_call.function.name,
                    meta={'info': info},
                )
                self.logger.info(
                    '[flow] _step: about to _on_tool_message tool_name=%s',
                    tool_call.function.name,
                )
                self._on_tool_message(tool_message)
                self.logger.info(
                    '[flow] _step: after _on_tool_message tool_name=%s',
                    tool_call.function.name,
                )
                step_record.tool_responses.append(tool_message)

                # For LLM dialog: 始终用字符串，过长时截断
                observation_str = (
                    observation
                    if isinstance(observation, str)
                    else json.dumps(observation, ensure_ascii=False, default=str)
                )
                MAX_TOOL_OUTPUT = 30000
                if len(observation_str) > MAX_TOOL_OUTPUT:
                    observation_for_llm = (
                        observation_str[: MAX_TOOL_OUTPUT // 2]
                        + '\n\n... [output truncated due to length] ...\n\n'
                        + observation_str[-MAX_TOOL_OUTPUT // 2 :]
                    )
                    dialog_message = ToolMessage(
                        content=observation_for_llm,
                        tool_call_id=tool_call.id,
                        name=tool_call.function.name,
                        meta={'info': info},
                    )
                    self.current_dialog.add_message(dialog_message)
                else:
                    dialog_message = ToolMessage(
                        content=observation_str,
                        tool_call_id=tool_call.id,
                        name=tool_call.function.name,
                        meta={'info': info},
                    )
                    self.current_dialog.add_message(dialog_message)

        # Handle finish tool (always last, sequential)
        if finish_call:
            self.logger.debug('Processing tool call: finish')
            finish_args: dict[str, Any] = {}
            try:
                finish_args = json.loads(finish_call.function.arguments)
                self.logger.info('=' * 80)
                self.logger.info(
                    'Finish Tool Arguments: task_completed=%s',
                    finish_args.get('task_completed'),
                )
                self.logger.info('=' * 80)
            except Exception:
                pass
            requested_task_completed = str(finish_args.get('task_completed', 'false'))
            blocked_msgs, gate_info = self._precheck_finish_gates(
                requested_task_completed
            )

            if blocked_msgs:
                observation = {
                    'status': 'success',
                    'message': '\n\n'.join(blocked_msgs),
                    'task_completed': requested_task_completed,
                    'finish_blocked': True,
                    **gate_info,
                }
                info = {
                    'task_completed': requested_task_completed,
                    'finish_blocked': True,
                }
                info.update(gate_info)
                should_finish = False
            else:
                observation, info = self._execute_tool(finish_call)
                task_completed = info.get('task_completed', 'false')
                if task_completed in ('true', 'partial'):
                    should_finish = True
                    report_url, normalised_message = generate_finish_report(
                        self.logger,
                        self._execution_journal,
                        finish_message=info.get('message', ''),
                        task_completed=task_completed,
                        workspace_path=getattr(
                            self.session.config, 'workspace_path', ''
                        )
                        or '',
                    )
                    info['message'] = normalised_message
                    observation['message'] = normalised_message
                    if report_url:
                        info['report_url'] = report_url
                        observation['report_url'] = report_url

            # Full content for streaming (yield) and trajectory recording：统一为 dict
            tool_message = ToolMessage(
                content=observation,
                tool_call_id=finish_call.id,
                name=finish_call.function.name,
                meta={'info': info},
            )
            self._on_tool_message(tool_message)
            step_record.tool_responses.append(tool_message)

            # For LLM dialog: 序列化为字符串，过长时截断
            MAX_TOOL_OUTPUT = 30000
            content_for_llm = (
                json.dumps(observation, ensure_ascii=False)
                if isinstance(observation, dict)
                else observation
            )
            if len(content_for_llm) > MAX_TOOL_OUTPUT:
                content_for_llm = (
                    content_for_llm[: MAX_TOOL_OUTPUT // 2]
                    + '\n\n... [output truncated due to length] ...\n\n'
                    + content_for_llm[-MAX_TOOL_OUTPUT // 2 :]
                )
            dialog_message = ToolMessage(
                content=content_for_llm,
                tool_call_id=finish_call.id,
                name=finish_call.function.name,
                meta={'info': info},
            )
            self.current_dialog.add_message(dialog_message)

        self.trajectory.add_step(step_record)
        self._append_trajectory_entry(dialog_for_query, step_record)
        return should_finish
