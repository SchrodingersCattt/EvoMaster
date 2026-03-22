"""Tool execution with MAT callbacks, auto-save, and observation compaction."""

from __future__ import annotations

import json
import traceback as _tb
from typing import Any

from .agent_tool_observation import (
    auto_save_tool_output,
    compact_extract_webpage_observation,
    compact_mat_sn_papers_observation,
    format_tool_observation,
    summarize_large_tool_observation,
)
from .execution import BatchExecutor, ExecutionTask


class MatMasterToolExecutionMixin:
    """Mixin: ``_execute_tool`` and ``_execute_tools_parallel``."""

    def _execute_tool(self, tool_call) -> tuple[str, dict[str, Any]]:
        """Execute tool with MAT callbacks.

        Overrides the base class so that:
        1. **All** errors include the full Python traceback.
        2. **All** observations are returned as JSON text.
        3. ``execute_bash`` results include ``status`` + command metadata.
        """
        try:
            self._tool_callback_pipeline.run_before(tool_call)
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
                return format_tool_observation(self.logger, tool_name, obs, inf), inf

            try:
                observation, info = tool.execute(self.session, tool_args)
                self._log_tool_end(tool_name, observation, info)
            except Exception as e:
                tb_str = _tb.format_exc()
                error_msg = f"Tool execution error: {e}\n\nTraceback:\n{tb_str}"
                self.logger.error('Tool execution failed:\n%s', tb_str)
                self._log_tool_end(tool_name, error_msg, {'error': str(e)})
                observation, info = error_msg, {'error': str(e)}

            if tool_name == 'str_replace_editor' and 'error' not in info:
                try:
                    _editor_args = (
                        json.loads(tool_args)
                        if isinstance(tool_args, str)
                        else tool_args
                    )
                    if (
                        isinstance(_editor_args, dict)
                        and _editor_args.get('command') == 'create'
                        and _editor_args.get('path')
                    ):
                        info = {**info, 'auto_saved_path': _editor_args['path']}
                except Exception:
                    pass

            if tool_name != 'finish':
                self._execution_journal.record(
                    step=self._step_count,
                    tool=tool_name,
                    status='error' if 'error' in info else 'success',
                    info=info,
                    observation=observation if isinstance(observation, str) else '',
                )

            if tool_name == 'finish' and isinstance(observation, dict):
                return observation, info

            observation, info = self._tool_callback_pipeline.run_after(
                tool_call,
                observation,
                info,
            )

            saved_path: str | None = None
            if isinstance(info, dict) and 'error' not in info:
                saved_path, self._tool_output_save_counter = auto_save_tool_output(
                    self.logger,
                    self.session,
                    tool_name,
                    observation,
                    save_patterns=self._tool_output_auto_save_patterns,
                    save_counter=self._tool_output_save_counter,
                    step_count=getattr(self, '_step_count', 0),
                )
                if saved_path:
                    if isinstance(observation, str):
                        observation = (
                            observation.rstrip() + f"\n\n[Auto-saved to: {saved_path}]"
                        )
                    info = {**info, 'auto_saved_path': saved_path}

            if isinstance(info, dict) and 'error' not in info and saved_path:
                _compact = compact_mat_sn_papers_observation(
                    tool_name, observation, saved_path
                )
                if _compact is not None:
                    observation = _compact
                    info = {**info, 'obs_summarized': True}
                    self.logger.info(
                        '[FeatureC] Compact mat_sn papers observation for %s → data_count=%s',
                        tool_name,
                        _compact.get('data_count'),
                    )

                _compact_web = compact_extract_webpage_observation(
                    tool_name, observation, saved_path
                )
                if _compact_web is not None:
                    observation = _compact_web
                    info = {**info, 'obs_summarized': True}
                    self.logger.info(
                        '[FeatureC] Compact extract_info_from_webpage observation → data_count=%s',
                        _compact_web.get('data_count'),
                    )

            if isinstance(info, dict) and 'error' not in info:
                _summary = summarize_large_tool_observation(
                    tool_name,
                    observation,
                    saved_path,
                    summarize_patterns=self._tool_obs_summarize_patterns,
                    summarize_threshold=self._tool_obs_summarize_threshold,
                )
                if _summary is not None:
                    self.logger.info(
                        '[FeatureC] Summarized large observation for %s (%d→%d chars)',
                        tool_name,
                        len(observation) if isinstance(observation, str) else 0,
                        len(_summary),
                    )
                    observation = _summary
                    info = {**info, 'obs_summarized': True}

            return (
                format_tool_observation(self.logger, tool_name, observation, info),
                info,
            )

        except Exception as exc:
            tb_str = _tb.format_exc()
            error_msg = f"Tool execution error: {exc}\n\nTraceback:\n{tb_str}"
            self.logger.error('_execute_tool failed:\n%s', tb_str)
            return format_tool_observation(
                self.logger,
                'internal_error',
                error_msg,
                {'error': str(exc)},
            ), {'error': str(exc)}

    def _execute_tools_parallel(
        self,
        tool_calls: list,
        *,
        max_workers: int = 4,
    ) -> list[tuple[Any, str, dict[str, Any]]]:
        """Execute multiple tool calls concurrently via BatchExecutor."""
        if not tool_calls:
            return []

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

        executor = BatchExecutor(max_workers=max_workers, rate_limit=self._rate_limit)
        results = executor.execute_batch(batch_tasks)

        ordered: list[tuple[Any, str, dict[str, Any]]] = []
        for idx, res in enumerate(results):
            tc = tool_calls[idx]
            if res.status == 'success':
                ordered.append((tc, res.output, res.info))
            else:
                ordered.append(
                    (tc, res.output or res.error or 'Unknown error', res.info)
                )
        return ordered
