"""Runtime/state helpers for ``ResearchPlanner``."""

import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional

from evomaster.agent.session.ssh import SSHSession
from evomaster.utils.types import (
    AssistantMessage,
    Dialog,
    TaskInstance,
    UserMessage,
)
from playground.mat_master.service.confirm import REPLY_CANCELLED, ConfirmMode

from ...prompts.build_prompt import LANGUAGE_RULE
from .direct_solver import DirectSolver
from .plan_utils import (
    _normalize_planner_thought,
)


class ResearchPlannerRuntimeMixin:
    def _consume_turns(self, state: dict[str, Any], task_id: str, n: int = 1) -> bool:
        """Consume planner turn budget in a thread-safe way."""
        try:
            n_i = int(n)
        except Exception:
            n_i = 1
        n_i = max(1, n_i)
        with self._turn_budget_lock:
            if self._turn_budget_remaining - n_i < 0:
                state['turn_budget_init'] = int(self._turn_budget_init)
                state['turn_budget_remaining'] = int(self._turn_budget_remaining)
                return False
            self._turn_budget_remaining -= n_i
            state['turn_budget_init'] = int(self._turn_budget_init)
            state['turn_budget_remaining'] = int(self._turn_budget_remaining)
            return True

    def _fail_max_turns_exceeded(
        self, task_id: str, state: dict[str, Any]
    ) -> dict[str, Any]:
        """Mark state failed due to planner turn budget exhaustion and persist."""
        state['phase'] = 'failed'
        state['fail_reason'] = 'max_turns_exceeded'
        state['turn_budget_init'] = int(self._turn_budget_init)
        state['turn_budget_remaining'] = int(self._turn_budget_remaining)
        self._emit(
            'Planner',
            'status',
            {
                'status': 'failed',
                'reason': 'max_turns_exceeded',
                'turn_budget_init': int(self._turn_budget_init),
                'turn_budget_remaining': int(self._turn_budget_remaining),
            },
        )
        self._save_state(task_id, state)
        return state

    def _emit(self, source: str, event_type: str, content: Any, **extra) -> None:
        if source == 'Planner' and event_type == 'thought':
            content = _normalize_planner_thought(content)
            event_type = 'planner_reply'
        if source == 'Planner' and event_type == 'llm_token':
            if extra.get('status') == 'streaming' and self._output_callback:
                self._output_callback(source, 'llm_token', content, **extra)
            return
        if self._output_callback:
            self._output_callback(source, event_type, content, **extra)

    def _stream_llm(
        self, dialog: Dialog, source: str, context: str
    ) -> AssistantMessage:
        """Wrap query_stream with llm token boundary markers."""
        stream_id = f"str_{uuid.uuid4().hex[:12]}"
        self._emit(
            source,
            'llm_token',
            '',
            status='start',
            context=context,
            stream_id=stream_id,
        )
        reply = None
        try:
            reply = self.agent.llm.query_stream(
                dialog,
                on_token=lambda delta: self._emit(
                    source, 'llm_token', delta, status='streaming'
                ),
            )
            return reply
        finally:
            token_count = len(reply.content or '') if reply is not None else 0
            self._emit(
                source,
                'llm_token',
                '',
                status='end',
                stream_id=stream_id,
                token_count=token_count,
            )

    def _run_dir_path(self) -> Path:
        return Path(self.run_dir) if self.run_dir else Path('.')

    def _planner_workspace_root(self) -> str:
        """Return the root directory used by planner artifacts/workspaces.
        """
        session = getattr(getattr(self, 'agent', None), 'session', None)
        if isinstance(session, SSHSession):
            workspace_path = (
                getattr(getattr(session, 'config', None), 'workspace_path', '') or ''
            ).strip()
            if workspace_path:
                return workspace_path.rstrip('/')
        return str(self._run_dir_path())

    def _state_path(self, task_id: str) -> str:
        # Store state inside .planner/ so the sub-agent cannot browse it.
        planner_dir = self._planner_hidden_dir(task_id)
        return f"{planner_dir}/{self.state_file}"

    def _task_workspace_dir(self, task_id: str) -> str:
        base = self._planner_workspace_root()
        session = getattr(getattr(self, 'agent', None), 'session', None)
        if isinstance(session, SSHSession):
            workspaces = f"{base.rstrip('/')}/{task_id}"
        else:
            workspaces = f"{base}/workspaces/{task_id}"
        self._file_io.mkdir(workspaces)
        return workspaces

    def _planner_hidden_dir(self, task_id: str) -> str:
        """Return the dot-prefixed hidden directory for planner-internal files.

        All planner artifacts (state, plan, journal, literature index, draft)
        are stored here so that sub-agents browsing the workspace cannot
        discover the overall plan and overstep step boundaries.
        """
        base = self._task_workspace_dir(task_id)
        hidden = f"{base}/.planner"
        self._file_io.mkdir(hidden)
        return hidden

    def _load_state(self, task_id: str) -> dict[str, Any]:
        path = self._state_path(task_id)
        if self._file_io.exists(path):
            try:
                loaded = self._file_io.read_json(path)
                if isinstance(loaded, dict):
                    tb_init = loaded.get('turn_budget_init')
                    tb_rem = loaded.get('turn_budget_remaining')
                    try:
                        if tb_init is not None:
                            self._turn_budget_init = max(1, int(tb_init))
                    except Exception:
                        pass
                    try:
                        if tb_rem is not None:
                            self._turn_budget_remaining = max(0, int(tb_rem))
                    except Exception:
                        pass
                    self.logger.info(
                        '[Planner] _load_state: RESUME from %s '
                        '(phase=%s, replan_count=%d, history_len=%d, '
                        'turn_budget_remaining=%s)',
                        path,
                        loaded.get('phase', '?'),
                        loaded.get('replan_count', 0),
                        len(loaded.get('history', [])),
                        loaded.get('turn_budget_remaining', '?'),
                    )
                return loaded
            except Exception as e:
                self.logger.warning('Failed to load state: %s', e)
        self.logger.info('[Planner] _load_state: NEW task (no state file at %s)', path)
        return {
            'task_id': task_id,
            'goal': '',
            'plan': None,
            'history': [],
            'phase': 'pre_check',
            'replan_count': 0,
            'execution_window': 0,
            'pre_check_context': '',
            'turn_budget_init': int(self._turn_budget_init),
            'turn_budget_remaining': int(self._turn_budget_remaining),
        }

    def _save_state(self, task_id: str, state: dict[str, Any]) -> None:
        path = self._state_path(task_id)
        try:
            self._file_io.write_json(path, state)
            self.logger.debug(
                '[Planner] _save_state: OK (phase=%s, path=%s)',
                state.get('phase', '?'),
                path,
            )
        except Exception as e:
            self.logger.error('Failed to save state: %s', e)

    def _planner_artifact_dir(self, task_id: str) -> str:
        # Use the hidden .planner/ directory so sub-agents cannot browse
        # the research journal or literature index.
        return self._planner_hidden_dir(task_id)

    def _ensure_longtask_artifacts(
        self, state: dict[str, Any], task_id: str, goal: str
    ) -> dict[str, Any]:
        """Create planner artifacts used by quality gates and resume safety."""
        artifact_dir = self._planner_artifact_dir(task_id)
        journal_path = f"{artifact_dir}/research_journal.md"
        literature_path = f"{artifact_dir}/literature_index.jsonl"
        if not self._file_io.exists(journal_path):
            self._file_io.write_text(
                journal_path,
                '# Planner Research Journal\n\n'
                f"- task_id: {task_id}\n"
                f"- created_at: {datetime.now(UTC).isoformat()}\n"
                f"- goal: {goal}\n\n",
            )
        if not self._file_io.exists(literature_path):
            self._file_io.write_text(literature_path, '')
        artifacts = state.setdefault('artifacts', {})
        if not isinstance(artifacts, dict):
            artifacts = {}
            state['artifacts'] = artifacts
        artifacts['research_journal'] = journal_path
        artifacts['literature_index'] = literature_path
        state.setdefault('literature_seen_urls', [])
        state.setdefault('literature_entry_count', 0)
        if not state.get('longtask_initialized'):
            self._append_journal(
                state,
                task_id,
                phase='init',
                title='Planner long-task artifacts initialized',
                body='Created research_journal.md and literature_index.jsonl.',
            )
            state['longtask_initialized'] = True
        return state

    def _append_journal(
        self,
        state: dict[str, Any],
        task_id: str,
        *,
        phase: str,
        title: str,
        body: str = '',
    ) -> None:
        artifacts = state.get('artifacts') or {}
        journal_raw = artifacts.get('research_journal', '')
        journal_path = (
            journal_raw
            if journal_raw
            else f"{self._planner_artifact_dir(task_id)}/research_journal.md"
        )
        ts = datetime.now(UTC).isoformat()
        block = [f"## [{ts}] {phase}: {title}"]
        if body:
            block.append(body.strip())
        block.append('')
        self._file_io.append_text(journal_path, '\n'.join(block) + '\n')
        self.logger.debug(
            '[Planner] _append_journal: phase=%s title=%r path=%s',
            phase,
            title,
            journal_path,
        )

    @staticmethod
    def _extract_urls_from_text(text: str) -> list[str]:
        urls: list[str] = []
        if not text:
            return urls
        seen_dois: set[str] = set()
        for m in re.findall(r"https?://[^\s\)\]\"'>]+", text):
            url = m.rstrip('.,;:)')
            if not url:
                continue
            urls.append(url)
            doi_m = re.search(r'doi\.org/(.+)', url)
            if doi_m:
                seen_dois.add(doi_m.group(1).rstrip('.,;:)').lower())
        for doi in re.findall(r'\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b', text):
            clean = doi.rstrip('.,;:)')
            if clean and clean.lower() not in seen_dois:
                urls.append(f"https://doi.org/{clean}")
                seen_dois.add(clean.lower())
        deduped: list[str] = []
        seen: set[str] = set()
        for url in urls:
            if url in seen:
                continue
            seen.add(url)
            deduped.append(url)
        return deduped

    def _append_literature_index(
        self,
        state: dict[str, Any],
        task_id: str,
        *,
        source: str,
        text: str,
    ) -> int:
        artifacts = state.get('artifacts') or {}
        lit_raw = artifacts.get('literature_index', '')
        lit_path = (
            lit_raw
            if lit_raw
            else f"{self._planner_artifact_dir(task_id)}/literature_index.jsonl"
        )
        seen = state.get('literature_seen_urls') or []
        if not isinstance(seen, list):
            seen = []
        seen_set = {str(x) for x in seen}
        added = 0
        new_urls = [u for u in self._extract_urls_from_text(text) if u not in seen_set]
        if new_urls:
            lines: list[str] = []
            for url in new_urls:
                seen_set.add(url)
                rec = {
                    'ts': datetime.now(UTC).isoformat(),
                    'source': source,
                    'url': url,
                }
                lines.append(json.dumps(rec, ensure_ascii=False))
                lines.append('\n')
                added += 1
            self._file_io.append_text(lit_path, ''.join(lines))
        if added:
            state['literature_seen_urls'] = list(seen_set)
            state['literature_entry_count'] = (
                int(state.get('literature_entry_count', 0) or 0) + added
            )
        self.logger.debug(
            '[Planner] _append_literature_index: source=%r added=%d total=%d path=%s',
            source,
            added,
            int(state.get('literature_entry_count', 0) or 0),
            lit_path,
        )
        return added

    def _resolve_planner_prompt_path(self) -> Path:
        configured = Path(self._planner_prompt_file)
        if configured.is_absolute():
            return configured

        candidates: list[Path] = []
        if self._config_dir is not None:
            playground_base = Path(
                str(self._config_dir).replace('configs', 'playground', 1)
            )
            candidates.append((playground_base / configured).resolve())
            candidates.append((self._config_dir / configured).resolve())

        local_prompts_base = Path(__file__).resolve().parent.parent.parent
        candidates.append((local_prompts_base / configured).resolve())
        candidates.append(
            (local_prompts_base / 'prompts' / 'planner_system_prompt.txt').resolve()
        )
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return candidates[0]

    def _load_system_prompt(self) -> str:
        """Load planner prompt file and apply runtime replacements."""
        prompt_file = self._resolve_planner_prompt_path()
        if prompt_file.exists():
            raw = prompt_file.read_text(encoding='utf-8')
        else:
            self.logger.warning(
                'Planner prompt file not found (%s), using minimal fallback',
                prompt_file,
            )
            raw = (
                'You are a Research Planner. Output a single JSON object with '
                'plan_id, status, strategy_name, steps.'
            )
        raw = self._registry.replace_placeholders(raw)
        raw = raw.replace(
            '{{CRP_LICENSE_FIREWALL}}', self._registry.format_planner_license_firewall()
        )
        crp_context = self._registry.crp_context_dict()
        crp_str = json.dumps(crp_context, indent=2)
        return f"{raw}\n\n{LANGUAGE_RULE}\n\n# EMBEDDED SYSTEM PROTOCOL (IMMUTABLE)\n{crp_str}"

    def _ask_human(
        self,
        prompt: str,
        *,
        mode: str = 'block',
        timeout_sec: int = 300,
        default: str = '',
    ) -> str:
        """Ask the human a question via ``ConfirmationManager``."""
        try:
            confirm_mode = ConfirmMode(mode)
        except ValueError:
            confirm_mode = ConfirmMode.BLOCK

        self._emit(
            'Planner',
            'thought',
            f"[Ask Human] (mode={mode}"
            + (
                f", timeout={timeout_sec}s, default='{default}'"
                if confirm_mode == ConfirmMode.TIMEOUT
                else ''
            )
            + f"): {prompt}",
        )

        confirm_mgr = getattr(self.agent, '_confirm_manager', None)
        if confirm_mgr is None:
            raise RuntimeError(
                'Planner confirmation requires ConfirmationManager; '
                'fallback input paths have been removed.'
            )

        try:
            reply = confirm_mgr.request(
                question=prompt,
                mode=confirm_mode,
                timeout_sec=timeout_sec,
                default_reply=default or None,
                origin='planner',
                actions=['go', 'abort', 'revise'],
                source_override='Planner',
            )
        except Exception as exc:
            raise RuntimeError(
                'Planner confirmation failed inside ConfirmationManager.'
            ) from exc

        if reply is REPLY_CANCELLED:
            return 'abort'
        if reply is not None:
            return reply.strip()
        self.logger.info(
            "[Planner] ask_human timed out (%ds), using default='%s'",
            timeout_sec,
            default,
        )
        self._emit(
            'Planner',
            'thought',
            f"[Ask Human] No response within {timeout_sec}s — defaulting to '{default}'.",
        )
        return default

    def _plan_report_text(self, plan: dict[str, Any]) -> str:
        """Build plain-text plan report for frontend rendering."""
        report = plan.get('plan_report') or {}
        if not report:
            return ''
        lines = []
        summary = report.get('summary') or ''
        if summary:
            lines.append('[Plan Report] Summary:')
            lines.append(f"  {summary}")
        cost_block = report.get('cost_assessment') or {}
        overall = cost_block.get('overall', '')
        per_step = cost_block.get('per_step') or []
        notes = cost_block.get('notes') or ''
        if overall or per_step or notes:
            lines.append('[Plan Report] Cost assessment:')
            if overall:
                lines.append(f"  Overall: {overall}")
            for ps in per_step:
                lines.append(
                    f"  Step {ps.get('step_id')}: {ps.get('cost')} — {ps.get('reason', '')}"
                )
            if notes:
                lines.append(f"  Notes: {notes}")
        risks = report.get('risks') or []
        if risks:
            lines.append('[Plan Report] Risks & mitigation:')
            for r in risks:
                lines.append(f"  Step {r.get('step_id')}: {r.get('risk')}")
                lines.append(f"    → {r.get('mitigation', '')}")
        alts = report.get('alternatives') or []
        if alts:
            lines.append('[Plan Report] Alternatives / fallbacks:')
            for alt in alts:
                lines.append(f"  • {alt}")
        return '\n'.join(lines) if lines else ''

    def _print_plan_report(self, plan: dict[str, Any]) -> None:
        """Print detailed plan report and emit it to the frontend."""
        report = plan.get('plan_report') or {}
        if not report:
            return
        summary = report.get('summary') or ''
        if summary:
            print('\033[96m[Plan Report] Summary:\033[0m')
            print(f"  {summary}")
        cost_block = report.get('cost_assessment') or {}
        overall = cost_block.get('overall', '')
        per_step = cost_block.get('per_step') or []
        notes = cost_block.get('notes') or ''
        if overall or per_step or notes:
            print('\033[96m[Plan Report] Cost assessment:\033[0m')
            if overall:
                print(f"  Overall: {overall}")
            for ps in per_step:
                print(
                    f"  Step {ps.get('step_id')}: {ps.get('cost')} — {ps.get('reason', '')}"
                )
            if notes:
                print(f"  Notes: {notes}")
        risks = report.get('risks') or []
        if risks:
            print('\033[96m[Plan Report] Risks & mitigation:\033[0m')
            for r in risks:
                print(f"  Step {r.get('step_id')}: {r.get('risk')}")
                print(f"    → {r.get('mitigation', '')}")
        alts = report.get('alternatives') or []
        if alts:
            print('\033[96m[Plan Report] Alternatives / fallbacks:\033[0m')
            for alt in alts:
                print(f"  • {alt}")
        print()
        text = self._plan_report_text(plan)
        if text:
            self._emit('Planner', 'thought', text)

    def _execute_fallback(
        self,
        step: dict[str, Any],
        solver: DirectSolver,
        workspaces: Path,
        plan: dict[str, Any] | None = None,
    ) -> bool:
        """Run fallback strategy for a step if present."""
        fallback = step.get('fallback_logic') or step.get('fallback_strategy') or ''
        step_id = step.get('step_id', 0)

        if fallback and fallback.strip().lower() != 'none':
            fallback_prompt = f"Execute fallback strategy: {fallback}"
        else:
            alternatives: list[str] = []
            if plan and isinstance(plan, dict):
                alternatives = plan.get('plan_report', {}).get('alternatives', []) or []
            if not alternatives:
                return False
            alts_text = '\n'.join(f"  - {alt}" for alt in alternatives)
            fallback_prompt = (
                f"Step {step_id} failed and has no specific fallback strategy. "
                f"The original plan listed these high-level alternatives for the overall task:\n"
                f"{alts_text}\n\n"
                'Consider whether any of these alternatives can be applied to recover from '
                f"the failure at step {step_id}. Attempt the most appropriate recovery action."
            )

        step_dir = workspaces / f"step_{step_id}"
        step_dir.mkdir(parents=True, exist_ok=True)
        solver.set_run_dir(workspaces)

        # In sub-agent mode, skip _build_task_with_dialog_history() to prevent
        # the original user goal from leaking into the fallback agent's context
        # (the same isolation principle as the primary step path).
        use_sub_agent = (
            getattr(self, '_sub_agent_factory', None) is not None
            and self._sub_agent_factory.enabled
        )

        try:
            if use_sub_agent:
                # Sub-agent mode: no dialog_history injection
                solver.run(fallback_prompt, task_id=f"fallback_{step_id}")
            else:
                # Legacy mode: include dialog_history
                fallback_task = self._build_task_with_dialog_history(
                    fallback_prompt, f"fallback_{step_id}"
                )
                if fallback_task is not None:
                    solver.run(task=fallback_task)
                else:
                    solver.run(fallback_prompt, task_id=f"fallback_{step_id}")
            return True
        except Exception as e:
            self.logger.warning('Fallback failed for step %s: %s', step_id, e)
            return False

    def _initialize_state(self, task_description: str, task_id: str) -> dict[str, Any]:
        """Load persisted state or create a fresh planner state."""
        state = self._load_state(task_id)
        state.setdefault('goal', task_description)
        state.setdefault('plan', None)
        state.setdefault('history', [])
        state.setdefault('phase', 'pre_check')
        state.setdefault('replan_count', 0)
        # Separate counters for failure vs adaptive replans (Fix 3).
        state.setdefault('failure_replan_count', 0)
        state.setdefault('adaptive_replan_count', 0)
        state.setdefault('execution_window', 0)
        state.setdefault('pre_check_context', '')
        state.setdefault('artifacts', {})
        state.setdefault('literature_seen_urls', [])
        state.setdefault('literature_entry_count', 0)
        if state.get('goal') != task_description:
            state['goal'] = task_description
            state['plan'] = None
            state['history'] = []
            state['phase'] = 'pre_check'
            state['replan_count'] = 0
            state['failure_replan_count'] = 0
            state['adaptive_replan_count'] = 0
            state['pre_check_context'] = ''
            state['literature_seen_urls'] = []
            state['literature_entry_count'] = 0
            state['longtask_initialized'] = False
        state = self._ensure_longtask_artifacts(state, task_id, task_description)
        return state

    def _build_task_with_dialog_history(
        self, description: str, task_id: str
    ) -> Optional[TaskInstance]:
        """Build a ``TaskInstance`` with dialog history when available."""
        task_with_history = getattr(self, '_task_with_history', None)
        if task_with_history is None or not getattr(task_with_history, 'meta', None):
            return None
        base_history = list((task_with_history.meta or {}).get('dialog_history') or [])
        current_user_msg = (task_with_history.description or '').strip()
        if current_user_msg:
            base_history = base_history + [
                UserMessage(content=current_user_msg).model_dump()
            ]
        return TaskInstance(
            task_id=task_id,
            task_type='discovery',
            description=description,
            meta={'dialog_history': base_history},
        )
