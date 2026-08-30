"""Unified policy for async tool exposure and runtime permission.

This policy centralizes:
1) Which tool specs are exposed to LLM (submit-only async surface).
   Async tools whose remote name starts with ``submit_`` are kept; others are
   dropped so the model only sees submit-style MCP calls.
2) Which tool calls are allowed while async jobs are pending.
"""

import json


class AsyncExecutionPolicy:
    """Single policy entry for async-execution behavior."""

    _HIDDEN_LIFECYCLE_NAMES = frozenset(
        {'query_job_status', 'get_job_results', 'terminate_job', 'get_job_status'}
    )
    _HIDDEN_LIFECYCLE_SUFFIXES = tuple(f"_{n}" for n in _HIDDEN_LIFECYCLE_NAMES)
    _NATIVE_LIFECYCLE_NAMES = frozenset({'query_job_status', 'get_job_results'})
    _ALWAYS_ALLOWED_DURING_PENDING = frozenset({'mem_save', 'mem_recall'})

    def __init__(self, registry) -> None:
        self._registry = registry

    @staticmethod
    def _annotate_compdart_constraint_schema(spec) -> None:
        """Expose the service's single-comparison condition grammar to the LLM."""
        fn = getattr(spec, 'function', None)
        if getattr(fn, 'name', '') != 'mat_compdart_submit_run_dart_ga':
            return
        parameters = getattr(fn, 'parameters', None)
        if not isinstance(parameters, dict):
            return
        condition = (
            ((parameters.get('properties') or {}).get('constraints') or {})
            .get('items', {})
            .get('properties', {})
            .get('condition')
        )
        if not isinstance(condition, dict):
            return
        guidance = (
            'Use one comparison operator followed by one numeric value, such as '
            '>=0.1 or <0.5. Express a lower and upper bound as two constraint '
            'entries with the same target; chained expressions are invalid. '
            'The numeric value is a mole fraction in [0, 1], so 0.3 means '
            '30 at.%.'
        )
        existing = str(condition.get('description') or '').strip()
        if guidance not in existing:
            condition['description'] = (
                f'{existing} {guidance}'.strip() if existing else guidance
            )

    def filter_tool_specs_for_llm(self, specs: list) -> list:
        """Apply submit-only async surface and hide lifecycle tools.

        Async tools whose remote name starts with ``submit_`` are kept; others
        are dropped so the model only sees submit-style MCP calls.
        """
        if not specs:
            return specs

        prefixes = sorted(
            {entry.server_prefix for entry in self._registry.entries},
            key=len,
            reverse=True,
        )

        filtered = []
        for spec in specs:
            fn = getattr(spec, 'function', None)
            name = getattr(fn, 'name', '') if fn else ''
            if not isinstance(name, str) or not name:
                filtered.append(spec)
                continue
            self._annotate_compdart_constraint_schema(spec)

            matched_prefix = None
            remote_name = ''
            for prefix in prefixes:
                marker = f"{prefix}_"
                if name.startswith(marker):
                    matched_prefix = prefix
                    remote_name = name[len(marker) :]
                    break

            if not matched_prefix:
                # Hide generic lifecycle tools globally for mat_* servers that
                # are not represented by an async registry entry.
                if name.startswith('mat_') and name.endswith(
                    self._HIDDEN_LIFECYCLE_SUFFIXES
                ):
                    continue
                filtered.append(spec)
                continue

            if remote_name in self._HIDDEN_LIFECYCLE_NAMES:
                if (
                    remote_name in self._NATIVE_LIFECYCLE_NAMES
                    and self._registry.uses_native_lifecycle(matched_prefix)
                ):
                    filtered.append(spec)
                continue

            if not self._registry.is_async_tool(matched_prefix, remote_name):
                if not remote_name.startswith('submit_'):
                    filtered.append(spec)
                continue

            if remote_name.startswith('submit_'):
                filtered.append(spec)

        return filtered

    def is_call_allowed_while_pending(self, tool_call) -> bool:
        """Restrict tool calls when async jobs are still running."""
        name = tool_call.function.name or ''
        if name in self._ALWAYS_ALLOWED_DURING_PENDING:
            return True
        if name.startswith('mat_') and '_submit_' in name:
            return True
        for entry in self._registry.entries:
            marker = f"{entry.server_prefix}_"
            if not name.startswith(marker):
                continue
            remote_name = name[len(marker) :]
            if (
                remote_name in self._NATIVE_LIFECYCLE_NAMES
                and self._registry.uses_native_lifecycle(entry.server_prefix)
            ):
                return True
            break
        if name == 'monitor_job':
            return True
        if name == 'use_skill':
            # Only allow bohrium-job skill (for poll_job.py re-invocation) during pending
            try:
                args = json.loads(tool_call.function.arguments or '{}')
                skill_name = (args.get('skill_name') or '').strip().lower()
                if skill_name == 'bohrium-job':
                    return True
            except Exception:
                pass
            return False
        return False

    @staticmethod
    def pending_gate_message() -> str:
        return (
            '⚠️ PENDING ASYNC JOB GATE: async calculations are still running. '
            'Unrelated tools are blocked (e.g., literature/web/doc/manual searches). '
            'Keep monitoring pending jobs until completion.'
        )
