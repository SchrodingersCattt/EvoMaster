"""Unified policy for async tool exposure and runtime permission.

This policy centralizes:
1) Which tool specs are exposed to LLM (submit-only async surface).
   Non-submit async tools (e.g. legacy ``run_gromacs``) are hidden; use
   ``submit_run_gromacs`` so the MCP tool name matches ``submit_*`` and is exposed.
2) Which tool calls are allowed while async jobs are pending.
"""


class AsyncExecutionPolicy:
    """Single policy entry for async-execution behavior."""

    _HIDDEN_LIFECYCLE_NAMES = frozenset(
        {'query_job_status', 'get_job_results', 'terminate_job', 'get_job_status'}
    )
    _HIDDEN_LIFECYCLE_SUFFIXES = tuple(f"_{n}" for n in _HIDDEN_LIFECYCLE_NAMES)
    _ALWAYS_ALLOWED_DURING_PENDING = frozenset({'think', 'mem_save', 'mem_recall'})

    def __init__(self, registry) -> None:
        self._registry = registry

    def filter_tool_specs_for_llm(self, specs: list) -> list:
        """Apply submit-only async surface and hide lifecycle tools.

        Async tools whose remote name starts with ``submit_`` (e.g. ``submit_run_gromacs``)
        are kept; others are dropped so the model only sees submit-style MCP calls.
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

            # Hide generic lifecycle tools globally for every mat_* server,
            # not only servers discovered as async in registry.
            if name.startswith('mat_') and name.endswith(
                self._HIDDEN_LIFECYCLE_SUFFIXES
            ):
                continue

            matched_prefix = None
            remote_name = ''
            for prefix in prefixes:
                marker = f"{prefix}_"
                if name.startswith(marker):
                    matched_prefix = prefix
                    remote_name = name[len(marker) :]
                    break

            if not matched_prefix:
                filtered.append(spec)
                continue

            if remote_name in self._HIDDEN_LIFECYCLE_NAMES:
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
        if name == 'monitor_job':
            return True
        return False

    @staticmethod
    def pending_gate_message() -> str:
        return (
            '⚠️ PENDING ASYNC JOB GATE: async calculations are still running. '
            'Unrelated tools are blocked (e.g., literature/web/doc/manual searches). '
            'Keep monitoring pending jobs until completion.'
        )
