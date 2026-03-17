"""MatMasterAgent: finish only when task_completed=true.

System prompt uses file-first loading with runtime composition fallback.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
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

from .async_execution_policy import AsyncExecutionPolicy
from .callback import MatToolCallbacks, ToolCallbackPipeline
from .execution import BatchExecutor, ExecutionTask
from .execution_journal import ExecutionJournal
from .job_registry import JobRegistry
from .tool_guard import ToolGuard


class MatMasterAgent(Agent):
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
            'tool_output_auto_save_patterns', ['mat_sn_', 'mat_doc_']
        )
        if not isinstance(self._tool_output_auto_save_patterns, list):
            self._tool_output_auto_save_patterns = ['mat_sn_', 'mat_doc_']
        self._tool_output_save_counter = 0
        self._execution_journal = ExecutionJournal()
        # Feature C: 超大工具结果摘要化
        self._tool_obs_summarize_patterns: list[str] = _exec.get(
            'tool_obs_summarize_patterns',
            ['mat_sn_search-papers-enhanced', 'mat_sn_search-papers', 'mat_sg_', 'mat_doc_'],
        )
        if not isinstance(self._tool_obs_summarize_patterns, list):
            self._tool_obs_summarize_patterns = ['mat_sn_search-papers-enhanced', 'mat_sn_search-papers', 'mat_sg_', 'mat_doc_']
        self._tool_obs_summarize_threshold: int = int(
            _exec.get('tool_obs_summarize_threshold', 10000)
        )

        # C.7 — 初始化 ContextCompactor（如果 compaction.enabled）
        _ctx_cfg = (config_dict or {}).get('agents', {}).get('general', {}).get('context', {})
        _compaction_raw = _ctx_cfg.get('compaction', {})
        _compaction_cfg = CompactionConfig(**_compaction_raw) if _compaction_raw else CompactionConfig()
        self._compaction_enabled: bool = _compaction_cfg.enabled
        if _compaction_cfg.enabled:
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
                'context_window=%d, ratio=%.0f%%)',
                _compaction_cfg.effective_trigger_tokens(),
                _compaction_cfg.context_window_tokens,
                _compaction_cfg.trigger_ratio * 100,
            )

        # C.7 — 初始化 ContextCompactor（如果 compaction.enabled）
        _ctx_cfg = (config_dict or {}).get('agents', {}).get('general', {}).get('context', {})
        _compaction_raw = _ctx_cfg.get('compaction', {})
        _compaction_cfg = CompactionConfig(**_compaction_raw) if _compaction_raw else CompactionConfig()
        self._compaction_enabled: bool = _compaction_cfg.enabled
        if _compaction_cfg.enabled:
            def _llm_caller(dialog):
                return self.llm.query(dialog)
            _compactor = ContextCompactor(
                config=_compaction_cfg,
                llm_caller=_llm_caller,
                execution_journal=self._execution_journal,
            )
            self.context_manager.set_compactor(_compactor)
            self.logger.info(
                '[Agent] ContextCompactor enabled (trigger_tokens=%d)',
                _compaction_cfg.trigger_tokens,
            )

    def _initialize(self, task) -> None:
        """Override: reset counters and set up execution journal for each new task."""
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
            self._current_task_description = getattr(task, 'description', '') or ''
            self.current_dialog = Dialog(
                messages=[
                    SystemMessage(content=system_prompt),
                    *history_messages,
                    UserMessage(content=task.description or ''),
                ],
                tools=self._get_tool_specs(),
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

    def _precheck_finish_gates(
        self, requested_task_completed: str
    ) -> tuple[list[str], dict[str, Any]]:
        """Validate finish gates before executing the finish tool.

        Quality gates (manuscript, survey) are subject to a safety cap: after
        ``finish_block_max`` consecutive blocks the gates are force-passed so
        the agent never loops indefinitely.  Async-job gates are **not** capped
        because orphan jobs can leak resources.
        """
        blocked_msgs: list[str] = []
        gate_info: dict[str, Any] = {}
        if requested_task_completed not in ('true', 'partial'):
            return blocked_msgs, gate_info

        self._job_registry.refresh_pending()
        can_finish, gate_info = self._job_registry.can_finish()

        # Async-job gate is never force-passed (risk of orphan jobs).
        if not can_finish:
            blocked_msgs.append(
                '[finish_attempt_gate] Blocked: pending async jobs still running. '
                'Continue monitoring until pending_jobs_check passes.'
            )

        # Quality gates: skip checking once the safety cap is reached.
        force_pass = self._finish_block_count >= self._finish_block_max
        if force_pass:
            self.logger.warning(
                '[finish_attempt_gate] Quality gates force-passed after %d '
                'consecutive blocks (cap=%d).',
                self._finish_block_count,
                self._finish_block_max,
            )
            gate_info['finish_force_passed'] = True
        else:
            workspace = getattr(self.session.config, 'workspace_path', '') or ''
            task_description = getattr(self, '_current_task_description', '')
            
            gate_eval = self._llm_finish_gate_check(
                task_description=task_description,
                requested_task_completed=requested_task_completed,
                workspace_path=workspace,
            )
            
            if not gate_eval.get('approved', False):
                reason = gate_eval.get('reason', 'Task completion requirements not met')
                blocked_msgs.append(
                    f'[finish_attempt_gate] Blocked: {reason}\n'
                    f'Task: {task_description[:100]}{"..." if len(task_description) > 100 else ""}'
                )
        if blocked_msgs:
            self._finish_block_count += 1
            gate_info['finish_block_count'] = self._finish_block_count
            gate_info['finish_block_max'] = self._finish_block_max
        else:
            self._finish_block_count = 0

        # Guidance: if quality gates keep blocking and the run is likely stuck
        # due to external web access limits (403/404/paywall), encourage the model
        # to finish with task_completed='partial' and clear caveats instead of looping.
        # NOTE: This does not force-pass any gate. It only adds info to help the LLM replan.
        if blocked_msgs and requested_task_completed == 'true':
            gate_info.setdefault(
                'finish_hint',
                (
                    "If you are blocked by unavailable/paywalled web sources (403/404/etc.), "
                    "switch to alternative open sources or finish with task_completed='partial' "
                    "and include explicit limitations/caveats."
                ),
            )

        return blocked_msgs, gate_info

    def _llm_finish_gate_check(
        self,
        task_description: str,
        requested_task_completed: str,
        workspace_path: str,
    ) -> dict[str, Any]:
        """Use LLM to decide whether finish is appropriate given the actual task requirements.
        
        Evaluates whether the user's requested task has been accomplished, rather than
        checking hardcoded gates that may not match the actual requirements.
        
        Returns dict with 'approved' (bool) and 'reason' (str).
        """
        if not task_description or not task_description.strip():
            # No task description available, allow finish
            return {'approved': True, 'reason': ''}
        
        prompt = f"""TASK DESCRIPTION:
{task_description}

USER REQUESTED: task_completed={requested_task_completed}

Question: Has the user's requested task been accomplished? 
- Do NOT gate on mandatory manuscript validation or survey markdown quality.
- Only block if the core deliverable requested by the user is clearly missing or incomplete.
- Be permissive: if the task is substantially done, return approved=true.

Return exactly one JSON object:
{{
  "approved": true,
  "reason": ""
}}

If NOT approved, reason should be specific (e.g. "Requested CSV file not found in workspace").
"""
        
        dialog = Dialog(
            messages=[
                SystemMessage(
                    content="You are a strict task completion validator. Output only JSON. Do not require manuscript quality gates or survey markdown gates unless the user explicitly asked for a written document."
                ),
                UserMessage(content=prompt),
            ],
            tools=[],
        )
        
        default = {'approved': True, 'reason': ''}
        try:
            reply = self.llm.query(dialog)
            raw = self._extract_json_from_reply(reply.content or '')
            if not raw:
                return default
            result = json.loads(raw)
            approved = bool(result.get('approved', True))
            reason = str(result.get('reason', '') or '')
            return {'approved': approved, 'reason': reason}
        except Exception as e:
            self.logger.debug('LLM finish gate check failed: %s', e)
            return default

    @staticmethod
    def _extract_json_from_reply(content: str) -> str | None:
        """Extract JSON object from LLM reply.
        
        Handles both raw JSON and fenced code blocks (```json ... ```).
        Returns the first valid JSON object found, or None if not found.
        """
        text = (content or '').strip()
        if '```json' in text:
            start = text.find('```json') + 7
            end = text.find('```', start)
            if end > start:
                return text[start:end].strip()
        if '```' in text:
            start = text.find('```') + 3
            end = text.find('```', start)
            if end > start:
                return text[start:end].strip()
        start = text.find('{')
        if start < 0:
            return None
        depth = 0
        for i in range(start, len(text)):
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
        return None

    # ------------------------------------------------------------------
    # Finish report generation
    # ------------------------------------------------------------------

    def _normalise_finish_message(self, raw: str) -> str:
        """Make finish message canonical: fix literal \\n, ensure URLs get a line break before them."""
        if not raw:
            return raw
        text = raw.strip()
        # Literal backslash-n etc. -> real newline so message is well-formed
        text = text.replace('\\n', '\n').replace('\\r', '\r')
        # Ensure each bare URL line has a blank line before it so markdown doesn't glue them
        out: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith('http://') or stripped.startswith('https://'):
                if out and out[-1].strip():
                    out.append('')
                out.append(line)
            else:
                out.append(line)
        workspace = getattr(self.session.config, 'workspace_path', '') or ''
        workspace_abs = str(Path(workspace).absolute()) if workspace else ''
        return self._add_file_uri_prefix('\n'.join(out), workspace_path=workspace_abs)

    @staticmethod
    def _add_file_uri_prefix(text: str, workspace_path: str = '') -> str:
        """Convert local paths to file:// URIs in non-code text.

        Handles three cases:
        - Bare absolute Unix paths (e.g. /personal/workspace/a.csv) anywhere in text.
        - Bare absolute Windows paths (e.g. C:\\Users\\foo\\a.csv) anywhere in text.
        - Relative paths inside markdown link targets (e.g. [f](_tmp/a.json)), resolved
          against workspace_path when provided.

        Skips fenced code blocks and inline code spans.  Existing http/https/ftp/file
        URLs are preserved as-is to avoid double-conversion.
        """
        _SCHEME_OR_ANCHOR = re.compile(
            r'^(?:https?|ftp|file|mailto)://|^#', re.IGNORECASE
        )
        # Matches Windows absolute paths: letter + colon + slash or backslash
        _WIN_ABS = re.compile(r'^[A-Za-z]:[/\\]')

        def _win_path_to_uri(path: str) -> str:
            """Convert a Windows absolute path to a file:/// URI."""
            return 'file:///' + path.replace('\\', '/')

        # Split on fenced code blocks (``` ... ```) and inline code (`...`).
        # Odd-indexed parts are inside code — leave them untouched.
        parts = re.split(r'(```[\s\S]*?```|`[^`\n]+`)', text)
        result: list[str] = []
        for i, part in enumerate(parts):
            if i % 2 == 1:
                result.append(part)
                continue
            # Temporarily stash existing URLs so they are never re-processed.
            stashed: list[str] = []

            def _stash(
                m: re.Match, _s: list[str] = stashed
            ) -> str:  # noqa: E731  bind loop var for B023
                _s.append(m.group(0))
                return f'\x00URL{len(_s) - 1:04d}\x00'

            proc = re.sub(r'(?:https?|ftp|file)://[^\s\)\]\,;"\'<>]+', _stash, part)
            # Convert bare absolute Unix paths (starting with /) to file:// URIs.
            # The negative lookbehind avoids touching paths already part of a URL
            # or preceded by word characters / colons (e.g. inside JSON keys).
            proc = re.sub(
                r'(?<![a-zA-Z0-9_.:-])(/[^\s\)\]\,;"\'<>*#]+)',
                r'file://\1',
                proc,
            )
            # Convert bare absolute Windows paths (e.g. C:\foo\bar.csv or C:/foo/bar.csv).
            # Lookbehind: not preceded by word chars or colon to avoid false positives.
            proc = re.sub(
                r'(?<![a-zA-Z0-9_.])([A-Za-z]:[/\\][^\s\)\]\,;"\'<>*#]+)',
                lambda m: _win_path_to_uri(m.group(1)),
                proc,
            )

            # Convert relative paths inside markdown link targets to file:// URIs.
            # Absolute targets (Unix, Windows, scheme, anchor) are already handled above
            # or detected here and skipped.
            # Targets containing \x00 are stash placeholders (already-stashed URLs)
            # and must be skipped to avoid treating them as relative paths.
            def _fix_md_link(m: re.Match) -> str:  # noqa: E731
                link_text, target = m.group(1), m.group(2)
                if (
                    '\x00' in target
                    or _SCHEME_OR_ANCHOR.match(target)
                    or target.startswith('/')
                    or _WIN_ABS.match(target)
                ):
                    return m.group(0)
                if workspace_path:
                    # Normalise workspace_path to forward slashes for the URI
                    ws = workspace_path.rstrip('/').rstrip('\\').replace('\\', '/')
                    # workspace_path may itself be a Windows path — use file:///
                    prefix = 'file:///' if re.match(r'^[A-Za-z]:/', ws) else 'file://'
                    return f'[{link_text}]({prefix}{ws}/{target})'
                return m.group(0)

            proc = re.sub(r'\[([^\]]*)\]\(([^)\s]+)\)', _fix_md_link, proc)
            # Restore stashed URLs.
            for idx, url in enumerate(stashed):
                proc = proc.replace(f'\x00URL{idx:04d}\x00', url)
            result.append(proc)
        return ''.join(result)

    def _generate_finish_report(
        self,
        finish_message: str,
        task_completed: str,
    ) -> tuple[str | None, str]:
        """Save the finish message as a Markdown report, upload to OSS, return (report_url, normalised_message).

        The report is just the normalised message (same as .message). No separate
        trajectory-based content. Returns the normalised message so the caller
        can use it for info['message'] and observation.
        """
        normalised = self._normalise_finish_message(finish_message or '')

        # Safety net: if the LLM omitted the Execution Details section and the
        # journal has entries, auto-append a structured per-step section.
        # Zero extra LLM calls.
        if '## Execution Details' not in normalised and self._execution_journal.entries:
            details_md = self._execution_journal.get_execution_details_md()
            if details_md:
                normalised = normalised.rstrip() + '\n\n' + details_md

        now_str = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
        header = (
            '# Task Finish Report\n\n'
            f'**Generated**: {now_str}  \n'
            f'**Status**: `{task_completed}`\n\n---\n\n'
        )
        md_content = header + (normalised or '*(no message)*')

        # Write to a local temp file, upload, then delete
        tmp_path = None
        try:
            from src.dao.oss_io import upload_file_to_oss  # noqa: PLC0415

            fd, tmp_path = tempfile.mkstemp(suffix='_finish_report.md')
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    f.write(md_content)
            except Exception:
                try:
                    os.close(fd)
                except Exception:
                    pass
                raise

            report_url = upload_file_to_oss(
                tmp_path,
                key_prefix='matmaster_evo/finish_reports',
            )
            self.logger.info('Finish report uploaded: %s', report_url)
            return report_url, normalised
        except Exception as e:
            self.logger.warning('_generate_finish_report: OSS upload failed: %s', e)
            return None, normalised
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Observation formatting (MatMaster-only)
    # ------------------------------------------------------------------

    @staticmethod
    def _format_bash_observation(
        observation: str, info: dict[str, Any]
    ) -> dict[str, Any]:
        """Build structured JSON object for ``execute_bash`` results.

        Includes a ``status`` field (``"success"`` / ``"error"``) so the LLM
        can reliably branch on command outcome.
        """
        exit_code = info.get('exit_code', -1)
        has_error = 'error' in info
        if has_error:
            status = 'error'
        elif exit_code != 0 and exit_code != -1:
            status = 'error'
        else:
            status = 'success'
        return {
            'status': status,
            'output': observation,
            'exit_code': exit_code,
            'working_dir': info.get('working_dir', ''),
        }

    @staticmethod
    def _to_json_value(value: Any) -> Any:
        """Convert observation payload to a JSON-compatible value when possible."""
        if not isinstance(value, str):
            return value
        text = value.strip()
        if not text:
            return ''
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return value

    def _format_tool_observation(
        self,
        tool_name: str,
        observation: Any,
        info: dict[str, Any],
    ) -> str:
        """Return JSON text for every tool observation."""
        # 调试：format 前 observation 类型与预览（便于排查 observation 被转成 str 的位置）
        obs_type = type(observation).__name__
        if isinstance(observation, dict):
            self.logger.debug(
                '[observation] before _format_tool_observation tool=%s type=%s keys=%s',
                tool_name,
                obs_type,
                list(observation.keys())[:8],
            )
        elif isinstance(observation, str):
            self.logger.debug(
                '[observation] before _format_tool_observation tool=%s type=%s len=%s head=%s',
                tool_name,
                obs_type,
                len(observation),
                (observation[:80] + '...') if len(observation) > 80 else observation,
            )
        else:
            self.logger.debug(
                '[observation] before _format_tool_observation tool=%s type=%s',
                tool_name,
                obs_type,
            )
        if tool_name == 'execute_bash':
            obs_str = observation if isinstance(observation, str) else str(observation)
            payload = self._format_bash_observation(obs_str, info)
        else:
            status = 'error' if 'error' in info else 'success'
            # For use_skill(action=run_script), propagate non-zero script exit code
            # to outer status so tool-level status matches business outcome.
            if (
                tool_name == 'use_skill'
                and info.get('action') == 'run_script'
                and isinstance(info.get('exit_code'), int)
                and info['exit_code'] != 0
            ):
                status = 'error'
            payload = {
                'status': status,
                'observation': self._to_json_value(observation),
            }
            if info:
                payload['info'] = info
        return json.dumps(payload, ensure_ascii=False, indent=2, default=str)

    # ------------------------------------------------------------------
    # Tool execution
    # ------------------------------------------------------------------

    def _auto_save_tool_output(self, tool_name: str, observation: Any) -> str | None:
        """将匹配模式的工具执行结果自动写入 session 工作区 _tmp/tool_outputs/<tool_name>/。

        支持 str/dict/list 等常见类型；非字符串 observation 会在写盘前 json.dumps，
        但返回值仍保持原始对象，前端展示不受影响。
        """
        if observation is None:
            return None
        if not any(
            tool_name.startswith(p) for p in self._tool_output_auto_save_patterns
        ):
            return None
        try:
            workspace = getattr(self.session.config, 'workspace_path', '') or getattr(
                self.session, 'working_dir', ''
            )
            if not workspace:
                return None
            base = workspace.rstrip('/')
            safe_name = re.sub(r'[^\w\-.]', '_', tool_name)
            self._tool_output_save_counter += 1
            suffix = uuid.uuid4().hex[:8]
            step = getattr(self, '_step_count', 0)
            payload: str
            if isinstance(observation, str):
                stripped = observation.strip()
                ext = (
                    '.json'
                    if stripped.startswith('{') or stripped.startswith('[')
                    else '.txt'
                )
                payload = observation
            else:
                ext = '.json'
                payload = json.dumps(
                    observation, ensure_ascii=False, indent=2, default=str
                )
            rel = f'_tmp/tool_outputs/{safe_name}/step_{step}_{suffix}{ext}'
            remote_path = f'{base}/{rel}'
            self.session.write_file(remote_path, payload, encoding='utf-8')
            self.logger.info('Auto-saved tool output to %s', remote_path)
            return remote_path
        except Exception as e:
            self.logger.warning('Auto-save tool output failed: %s', e)
            return None

    def _summarize_large_tool_observation(
        self,
        tool_name: str,
        observation: Any,
        saved_path: str | None,
    ) -> str | None:
        """Feature C: 将超大工具 observation 替换为结构化摘要 + 文件路径引用。

        仅当 observation 为字符串且长度超过阈值，且工具名匹配配置前缀时触发。
        返回替换后的摘要字符串；若不触发则返回 None（调用方保持原 observation 不变）。

        摘要策略（纯 Python，无 LLM）：
        - mat_sn_search-papers-enhanced / mat_sn_search-papers：提取 top papers（title/doi/year/score/abstract preview）
        - mat_sg_*：提取 formula/space_group/energy_above_hull
        - 通用 fallback：total_items + 前 N 条 preview + 文件路径
        """
        if not isinstance(observation, str):
            return None
        if len(observation) <= self._tool_obs_summarize_threshold:
            return None
        if not any(tool_name.startswith(p) for p in self._tool_obs_summarize_patterns):
            return None

        path_note = f'\n\n[Full result saved to: {saved_path}]' if saved_path else ''

        # ── Try to parse as JSON ──────────────────────────────────────────────
        payload: dict | list | None = None
        try:
            stripped = observation.strip()
            # observation may be "...\n\n[Auto-saved to: ...]" — strip that suffix first
            _obs_clean = re.sub(r'\n\n\[Auto-saved to:[^\]]*\]$', '', stripped).strip()
            payload = json.loads(_obs_clean)
        except Exception:
            pass

        # ── mat_sn_search-papers* ─────────────────────────────────────────────
        if tool_name.startswith('mat_sn_search-papers'):
            papers: list[dict] = []
            if isinstance(payload, dict):
                # common shapes: {"papers": [...]} or {"results": [...]} or {"data": [...]}
                for key in ('papers', 'results', 'data', 'items'):
                    if isinstance(payload.get(key), list):
                        papers = payload[key]
                        break
                if not papers and isinstance(payload.get('total'), int):
                    # flat dict with a list somewhere
                    for v in payload.values():
                        if isinstance(v, list) and v:
                            papers = v
                            break
            elif isinstance(payload, list):
                papers = payload

            if papers:
                total = len(papers)
                top_n = min(10, total)
                lines = [
                    f'[Tool: {tool_name}] Returned {total} papers. Top {top_n} shown below:',
                    '',
                ]
                for i, p in enumerate(papers[:top_n], 1):
                    if not isinstance(p, dict):
                        lines.append(f'{i}. {str(p)[:120]}')
                        continue
                    title = p.get('title') or p.get('Title') or '(no title)'
                    doi = p.get('doi') or p.get('DOI') or p.get('url') or ''
                    year = p.get('year') or p.get('Year') or p.get('published_year') or ''
                    score = p.get('score') or p.get('relevance_score') or p.get('similarity') or ''
                    abstract = p.get('abstract') or p.get('Abstract') or p.get('summary') or ''
                    abstract_preview = (abstract[:200] + '…') if len(abstract) > 200 else abstract
                    parts = [f'{i}. {title}']
                    if doi:
                        parts.append(f'   DOI/URL: {doi}')
                    meta = []
                    if year:
                        meta.append(f'year={year}')
                    if score:
                        meta.append(f'score={score}')
                    if meta:
                        parts.append(f'   {", ".join(meta)}')
                    if abstract_preview:
                        parts.append(f'   Abstract: {abstract_preview}')
                    lines.extend(parts)
                    lines.append('')
                if total > top_n:
                    lines.append(f'… and {total - top_n} more papers.{path_note}')
                else:
                    lines.append(path_note.strip() if path_note else '')
                return '\n'.join(lines).rstrip()

        # ── mat_sg_* (structure generator) ───────────────────────────────────
        if tool_name.startswith('mat_sg_'):
            structures: list[dict] = []
            if isinstance(payload, dict):
                for key in ('structures', 'results', 'data', 'items'):
                    if isinstance(payload.get(key), list):
                        structures = payload[key]
                        break
                if not structures:
                    # single structure result
                    structures = [payload]
            elif isinstance(payload, list):
                structures = payload

            if structures:
                total = len(structures)
                top_n = min(20, total)
                lines = [
                    f'[Tool: {tool_name}] Returned {total} structure(s). Top {top_n} shown:',
                    '',
                ]
                for i, s in enumerate(structures[:top_n], 1):
                    if not isinstance(s, dict):
                        lines.append(f'{i}. {str(s)[:120]}')
                        continue
                    formula = (
                        s.get('formula')
                        or s.get('Formula')
                        or s.get('reduced_formula')
                        or s.get('pretty_formula')
                        or '?'
                    )
                    sg = (
                        s.get('space_group')
                        or s.get('spacegroup')
                        or s.get('space_group_symbol')
                        or s.get('sg')
                        or '?'
                    )
                    e_hull = s.get('energy_above_hull') or s.get('e_above_hull') or s.get('stability')
                    mat_id = s.get('material_id') or s.get('id') or s.get('mp_id') or ''
                    parts = [f'{i}. {formula}  SG={sg}']
                    if e_hull is not None:
                        parts[0] += f'  e_above_hull={e_hull}'
                    if mat_id:
                        parts[0] += f'  id={mat_id}'
                    lines.extend(parts)
                if total > top_n:
                    lines.append(f'… and {total - top_n} more.{path_note}')
                else:
                    lines.append(path_note.strip() if path_note else '')
                return '\n'.join(lines).rstrip()

        # ── Generic fallback ──────────────────────────────────────────────────
        items: list = []
        if isinstance(payload, list):
            items = payload
        elif isinstance(payload, dict):
            for key in ('results', 'data', 'items', 'papers', 'structures'):
                if isinstance(payload.get(key), list):
                    items = payload[key]
                    break

        total_items = len(items) if items else None
        preview_lines: list[str] = []
        for item in items[:5]:
            preview_lines.append(f'  - {str(item)[:150]}')

        summary_parts = [f'[Tool: {tool_name}] Large observation ({len(observation):,} chars).']
        if total_items is not None:
            summary_parts.append(f'Total items: {total_items}.')
        if preview_lines:
            summary_parts.append('Preview (first 5):')
            summary_parts.extend(preview_lines)
        if path_note:
            summary_parts.append(path_note.strip())
        return '\n'.join(summary_parts)

    def _execute_tool(self, tool_call) -> tuple[str, dict[str, Any]]:
        """Execute tool with MAT callbacks.

        Overrides the base class so that:
        1. **All** errors include the full Python traceback.
        2. **All** observations are returned as JSON text.
        3. ``execute_bash`` results include ``status`` + command metadata.
        """
        import traceback as _tb

        try:
            self._tool_callback_pipeline.run_before(tool_call)
            # Emit tool_call event AFTER before-callbacks have patched the args,
            # so the frontend/log shows the resolved arguments (e.g. DPA model
            # alias -> OSS URL, patched bohr_job_id, etc.).
            self._on_tool_call_start(tool_call)

            tool_name = tool_call.function.name
            tool_args = tool_call.function.arguments
            self._log_tool_start(tool_name, tool_args)

            tool = self.tools.get_tool(tool_name)
            if tool is None:
                error_msg = f"Unknown tool: {tool_name}"
                self._log_tool_end(tool_name, error_msg, {'error': 'tool_not_found'})
                obs, inf = self._tool_callback_pipeline.run_after(
                    tool_call, error_msg, {'error': 'tool_not_found'}
                )
                return self._format_tool_observation(tool_name, obs, inf), inf

            try:
                observation, info = tool.execute(self.session, tool_args)
                self._log_tool_end(tool_name, observation, info)
            except Exception as e:
                tb_str = _tb.format_exc()
                error_msg = f"Tool execution error: {e}\n\nTraceback:\n{tb_str}"
                self.logger.error('Tool execution failed:\n%s', tb_str)
                self._log_tool_end(tool_name, error_msg, {'error': str(e)})
                observation, info = error_msg, {'error': str(e)}

            # Record tool outcome in the execution journal (skip finish — handled separately).
            # Note: recorded before run_after so the raw observation is captured; the journal
            # is used for artifact tracking, not for LLM context.
            if tool_name != 'finish':
                self._execution_journal.record(
                    step=self._step_count,
                    tool=tool_name,
                    status='error' if 'error' in info else 'success',
                    info=info,
                    observation=observation if isinstance(observation, str) else '',
                )

            # finish 工具从源头返回 dict，直接透传（不经过 run_after / _format_tool_observation，避免 callbacks 假定 observation 为 str）
            if tool_name == 'finish' and isinstance(observation, dict):
                return observation, info

            # Run after-callbacks (clean SN fields, survey reminder, etc.)
            observation, info = self._tool_callback_pipeline.run_after(
                tool_call,
                observation,
                info,
            )

            # Fix-3: 工具结果自动落盘在 callback pipeline 之后，落盘的是清洗后的数据
            saved_path: str | None = None
            if isinstance(info, dict) and 'error' not in info:
                saved_path = self._auto_save_tool_output(tool_name, observation)
                if saved_path:
                    if isinstance(observation, str):
                        observation = (
                            observation.rstrip() + f"\n\n[Auto-saved to: {saved_path}]"
                        )
                    info = {**info, 'auto_saved_path': saved_path}

            # Fix-5: Feature C 摘要化在 callback pipeline 之后，基于清洗后的 observation
            if isinstance(info, dict) and 'error' not in info:
                _summary = self._summarize_large_tool_observation(tool_name, observation, saved_path)
                if _summary is not None:
                    self.logger.info(
                        '[FeatureC] Summarized large observation for %s (%d→%d chars)',
                        tool_name, len(observation) if isinstance(observation, str) else 0, len(_summary),
                    )
                    observation = _summary
                    info = {**info, 'obs_summarized': True}

            return self._format_tool_observation(tool_name, observation, info), info

        except Exception as exc:
            # Catch-all: callback pipeline or any other unexpected error
            tb_str = _tb.format_exc()
            error_msg = f"Tool execution error: {exc}\n\nTraceback:\n{tb_str}"
            self.logger.error('_execute_tool failed:\n%s', tb_str)
            return self._format_tool_observation(
                'internal_error',
                error_msg,
                {'error': str(exc)},
            ), {'error': str(exc)}

    def _on_assistant_message(self, msg: AssistantMessage) -> None:
        """Optional hook after assistant message is added. Override in subclasses (e.g. streaming)."""

    def _on_tool_call_start(self, tool_call) -> None:
        """Optional hook called after before-callbacks have patched tool_call
        args but before the tool is actually executed.

        Override in subclasses (e.g. StreamingMatMasterAgent) to emit
        ``tool_call`` events with the callback-resolved arguments."""

    def _on_tool_message(self, msg: ToolMessage) -> None:
        """Optional hook after each tool message is added. Override in subclasses (e.g. streaming)."""

    def _execute_tools_parallel(
        self,
        tool_calls: list,
        *,
        max_workers: int = 4,
    ) -> list[tuple[Any, str, dict[str, Any]]]:
        """Execute multiple tool calls concurrently via the unified BatchExecutor.

        When the LLM returns N tool calls in a single response they are
        conceptually independent, so we run them in parallel.

        Returns a list of ``(tool_call, observation, info)`` in original order.
        """
        if not tool_calls:
            return []

        # Build ExecutionTask list — each task wraps _execute_tool for one tool_call
        batch_tasks: list[ExecutionTask] = []
        for idx, tc in enumerate(tool_calls):
            batch_tasks.append(
                ExecutionTask(
                    task_id=str(idx),
                    func=self._execute_tool,
                    kwargs={'tool_call': tc},
                    meta={'tool_call_index': idx},
                )
            )

        # Use shared BatchExecutor (true concurrency for I/O-bound tool calls)
        executor = BatchExecutor(max_workers=max_workers, rate_limit=self._rate_limit)
        results = executor.execute_batch(batch_tasks)

        # Map results back to (tool_call, observation, info) triples
        ordered: list[tuple[Any, str, dict[str, Any]]] = []
        for idx, res in enumerate(results):
            tc = tool_calls[idx]
            if res.status == 'success':
                ordered.append((tc, res.output, res.info))
            else:
                # On failure, surface the error message as the observation
                ordered.append(
                    (tc, res.output or res.error or 'Unknown error', res.info)
                )
        return ordered

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
        # C.8 — compaction 启用时注入 produced artifacts 列表（防止 LLM 遗忘已产出文件）
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
                    report_url, normalised_message = self._generate_finish_report(
                        finish_message=info.get('message', ''),
                        task_completed=task_completed,
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
