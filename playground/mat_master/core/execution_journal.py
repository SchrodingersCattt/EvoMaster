"""ExecutionJournal: per-task, thread-safe log of tool-call outcomes.

Persists to ``_tmp/execution_journal_{task_id}.jsonl`` (one JSON line per
entry).  Provides compact summaries for periodic reminders and a full
Markdown ``## Execution Details`` section for finish-message augmentation.
"""

import json
import os
import threading
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any


class ExecutionJournal:
    """Records tool-call outcomes for a single agent task run.

    Thread-safe: ``record()`` is guarded by a ``threading.Lock`` because
    ``_execute_tools_parallel`` may call ``_execute_tool`` concurrently.
    """

    def __init__(self) -> None:
        self._entries: list[dict[str, Any]] = []
        self._path: str | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def entries(self) -> list[dict[str, Any]]:
        """Read-only view of accumulated entries (snapshot under lock)."""
        with self._lock:
            return list(self._entries)

    def set_path(self, path: str) -> None:
        """Set the JSONL file path and load any existing entries (resume).

        Creates parent directories as needed.  Must be called before the
        first ``record()`` when a workspace path is known.
        """
        self._path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._load()

    def record(
        self,
        step: int,
        tool: str,
        status: str,
        info: dict[str, Any],
        observation: str,
    ) -> None:
        """Append one journal entry (in-memory + file).

        Args:
            step: Current ``_step_count`` value.
            tool: Tool name.
            status: ``"success"`` or ``"error"``.
            info: The ``info`` dict returned by the tool.
            observation: String observation (truncated to 300 chars for
                the summary field).
        """
        entry: dict[str, Any] = {
            'step': step,
            'ts': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'tool': tool,
            'status': status,
            'saved_path': info.get('auto_saved_path') or None,
            'downloaded_files': info.get('downloaded_files') or [],
            'error': info.get('error') or None,
            'summary': (observation or '')[:300].strip(),
        }
        with self._lock:
            self._entries.append(entry)
            self._flush_one(entry)

    def get_compact_summary(self, include_details: bool = False) -> str:
        """Return a short progress text (~200-300 tokens) for periodic reminders.

        Args:
            include_details: If True, include per-step summaries for compact message
                generation (used by ContextCompactor). Default False keeps the existing
                ~200-300 token format and is fully backward-compatible.
        """
        with self._lock:
            entries = list(self._entries)

        if not entries:
            return 'No tool calls recorded yet.'

        tool_counts: dict[str, int] = defaultdict(int)
        tool_errors: dict[str, int] = defaultdict(int)
        all_files: list[str] = []
        last_error: str | None = None

        for e in entries:
            t = e['tool']
            tool_counts[t] += 1
            if e['status'] == 'error':
                tool_errors[t] += 1
                last_error = f"Step {e['step']} {t} — {str(e.get('error') or e.get('summary') or '')[:120]}"
            if e.get('saved_path'):
                all_files.append(e['saved_path'])
            for f in e.get('downloaded_files') or []:
                all_files.append(f)

        tool_lines = []
        for tool, count in tool_counts.items():
            errs = tool_errors.get(tool, 0)
            if errs:
                tool_lines.append(f'{tool} (x{count}, {errs} err)')
            else:
                tool_lines.append(f'{tool} (x{count}, all ok)')

        lines = [
            f'Steps completed: {len(entries)}',
            f'Tools: {", ".join(tool_lines)}',
        ]
        if all_files:
            display_files = all_files[-6:]
            lines.append('Files: ' + ', '.join(display_files))
        if last_error:
            lines.append(f'Last error: {last_error}')
        # include_details: per-step summaries for ContextCompactor
        if include_details:
            detail_lines = [
                f'  Step {e["step"]} [{e["tool"]}] {e["status"]}: {e["summary"][:150]}'
                for e in entries[-30:]  # 最近 30 条
            ]
            lines.append('Recent steps:\n' + '\n'.join(detail_lines))
        return '\n'.join(lines)

    def get_execution_details_md(self) -> str:
        """Return a full ``## Execution Details`` Markdown section."""
        with self._lock:
            entries = list(self._entries)

        if not entries:
            return ''

        # Group by tool, preserving first-seen order
        tool_order: list[str] = []
        by_tool: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for e in entries:
            t = e['tool']
            if t not in by_tool:
                tool_order.append(t)
            by_tool[t].append(e)

        sections = ['## Execution Details']
        for tool in tool_order:
            sections.append(f'\n### {tool}')
            for e in by_tool[tool]:
                step = e['step']
                status = e['status']
                parts = [f'- Step {step}: {status}.']
                if e.get('saved_path'):
                    p = e['saved_path']
                    fname = os.path.basename(p)
                    os.path.splitext(fname)[1].upper().lstrip('.') or 'File'
                    uri = (
                        p
                        if p.startswith('file://') or p.startswith('http')
                        else f'file://{p}'
                    )
                    parts.append(f' [{fname}]({uri})')
                for f in e.get('downloaded_files') or []:
                    fname2 = os.path.basename(f)
                    uri2 = (
                        f
                        if f.startswith('file://') or f.startswith('http')
                        else f'file://{f}'
                    )
                    parts.append(f' [{fname2}]({uri2})')
                if e['status'] == 'error' and e.get('error'):
                    parts.append(f" — {str(e['error'])[:200]}")
                elif e.get('summary'):
                    parts.append(f" {e['summary'][:120]}")
                sections.append(''.join(parts))

        return '\n'.join(sections)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Load existing entries from the JSONL file (for resume support).

        Corrupt lines are silently skipped so a single bad write cannot
        prevent the journal from loading the rest of the history.
        """
        if not self._path or not os.path.exists(self._path):
            return
        loaded: list[dict[str, Any]] = []
        try:
            with open(self._path, encoding='utf-8') as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        loaded.append(json.loads(line))
                    except Exception:
                        pass
        except Exception:
            pass
        with self._lock:
            self._entries = loaded

    def _flush_one(self, entry: dict[str, Any]) -> None:
        """Append a single entry to the JSONL file. Caller must hold lock."""
        if not self._path:
            return
        try:
            with open(self._path, 'a', encoding='utf-8') as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + '\n')
        except Exception:
            pass
