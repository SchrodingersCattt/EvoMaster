"""Pre-check (readiness assessment and prerequisites) for ``ResearchPlanner``."""

import json
from typing import Any

from evomaster.utils.types import Dialog, SystemMessage, UserMessage

from ..direct_solver import DirectSolver
from ..plan_utils import _extract_json_from_content


class ResearchPlannerPrecheckMixin:
    """Mixin for pre-check phase: workspace scan, readiness assessment, prerequisites."""

    def _scan_workspace_files(self) -> list[str]:
        """List top-level workspace files for pre-check context."""
        workspace = self._run_dir_path()
        files: list[str] = []
        try:
            for path in workspace.iterdir():
                if path.name.startswith('.') or path.name == '__pycache__':
                    continue
                if path.is_file():
                    files.append(str(path.relative_to(workspace)))
                elif path.is_dir():
                    for child in path.iterdir():
                        if child.is_file():
                            files.append(str(child.relative_to(workspace)))
        except Exception as e:
            self.logger.debug('Workspace scan failed (non-critical): %s', e)
        return files[:100]

    def _assess_readiness(
        self, task_description: str, state: dict[str, Any], task_id: str
    ) -> dict[str, Any]:
        """Use LLM to assess whether the task is ready to plan."""
        workspace_files = self._scan_workspace_files()
        files_str = (
            '\n'.join(f"  - {file}" for file in workspace_files)
            if workspace_files
            else '  (empty workspace)'
        )

        user_content = f"""TASK DESCRIPTION:
{task_description}

WORKSPACE FILES:
{files_str}

Assess whether this task can be planned immediately or needs preliminary work. Output JSON only."""

        dialog = Dialog(
            messages=[
                SystemMessage(content=self._pre_check_system),
                UserMessage(content=user_content),
            ],
            tools=[],
        )
        try:
            if not self._consume_turns(state, task_id, 1):
                self._fail_max_turns_exceeded(task_id, state)
                return {
                    'ready_to_plan': False,
                    'prerequisites': [],
                    'reasoning': 'max_turns_exceeded',
                }
            reply = self._stream_llm(dialog, 'Planner', 'pre_check')
            raw = _extract_json_from_content(reply.content or '')
            if raw:
                return json.loads(raw)
            self._emit('Planner', 'thought', f"[Pre-check] {reply.content or ''}")
        except Exception as e:
            self.logger.warning(
                '[Pre-check] Assessment failed, proceeding to plan: %s',
                e,
            )
        return {
            'ready_to_plan': False,
            'prerequisites': [],
            'reasoning': 'Assessment failed; defaulting to not ready.',
        }

    def _execute_prerequisites(
        self,
        prerequisites: list[dict[str, Any]],
        task_description: str,
        task_id: str,
        state: dict[str, Any],
    ) -> str:
        """Run prerequisite tasks via ``DirectSolver`` and collect context."""
        if not prerequisites:
            return ''

        workspaces = self._run_dir_path() / 'workspaces' / task_id
        pre_check_dir = workspaces / 'pre_check'
        pre_check_dir.mkdir(parents=True, exist_ok=True)

        solver = DirectSolver(self.agent, self.config)
        solver.set_run_dir(pre_check_dir)

        collected_context: list[str] = []
        for index, prereq in enumerate(prerequisites):
            prereq_type = prereq.get('type', 'unknown')
            description = prereq.get('description', '')
            target = prereq.get('target', '')

            if prereq_type == 'clarify_task':
                self.logger.info(
                    '[Pre-check] clarify_task (fast path, no agent): %s',
                    description[:120],
                )
                self._emit(
                    'Planner',
                    'thought',
                    f"[Pre-check] Clarification noted (no agent needed): {description}",
                )
                collected_context.append(
                    f"[Prerequisite {index + 1}: clarify_task] {description}"
                    + (f"\nTarget: {target}" if target else '')
                )
                self._append_journal(
                    state,
                    task_id,
                    phase='pre_check',
                    title=(
                        f"Prerequisite {index + 1}/{len(prerequisites)} "
                        '(clarify_task, fast path)'
                    ),
                    body=f"{description}" + (f"\nTarget: {target}" if target else ''),
                )
                continue

            if not self._consume_turns(state, task_id, 1):
                self._fail_max_turns_exceeded(task_id, state)
                break

            self.logger.info(
                '[Pre-check] Running prerequisite %d/%d: [%s] %s',
                index + 1,
                len(prerequisites),
                prereq_type,
                description[:80],
            )
            self._emit(
                'Planner',
                'thought',
                f"[Pre-check] Prerequisite {index + 1}/{len(prerequisites)}: {description}",
            )
            self._emit('Planner', 'exp_run', 'DirectSolver (pre-check)')

            scope_prefix = (
                'SCOPE: You are in the PRE-CHECK phase. Your ONLY job is to collect '
                'the specific information described below to help the planner create '
                'a better plan. Do NOT attempt to execute the full user task, build '
                'datasets, generate reports, or perform any work beyond what is '
                'explicitly described in this prerequisite. When you have collected '
                'the requested information, call finish immediately.\n\n'
            )

            if prereq_type == 'parse_pdf':
                body = (
                    'Parse the following PDF file and extract all relevant information '
                    'for planning a research task. Use '
                    'mat_doc_extract_material_data_from_pdf (MCP tool) as the primary '
                    'method. Extract: crystal structures, computational methods, '
                    'software used, key parameters (k-mesh, cutoff, functional, '
                    f'pseudopotentials), target properties/results. File: {target}. '
                    'After extraction, summarize all findings clearly.'
                )
            elif prereq_type == 'parse_files':
                body = (
                    'Read and parse the following files to extract information needed '
                    f'for planning: {target}. For PDFs, use mat_doc MCP tools first. '
                    'Summarize key findings.'
                )
            else:
                body = (
                    f"Complete this prerequisite task: {description}. Target: {target}."
                )

            prompt = scope_prefix + body

            try:
                precheck_task = self._build_task_with_dialog_history(
                    prompt,
                    f"{task_id}_precheck_{index}",
                )
                if precheck_task is not None:
                    result = solver.run(task=precheck_task)
                else:
                    result = solver.run(prompt, task_id=f"{task_id}_precheck_{index}")
                summary = self._summarize_solver_result(result, max_len=2000)
                collected_context.append(
                    f"[Prerequisite {index + 1}: {prereq_type}] {description}\nResult: {summary}"
                )
                self._append_journal(
                    state,
                    task_id,
                    phase='pre_check',
                    title=f"Prerequisite {index + 1}/{len(prerequisites)} completed",
                    body=f"type={prereq_type}\n{description}\n\nResult summary:\n{summary}",
                )
                self._append_literature_index(
                    state,
                    task_id,
                    source=f"precheck:{prereq_type}",
                    text=summary,
                )
            except Exception as e:
                self.logger.warning(
                    '[Pre-check] Prerequisite %d failed: %s', index + 1, e
                )
                collected_context.append(
                    f"[Prerequisite {index + 1}: {prereq_type}] FAILED: {e}"
                )
                self._append_journal(
                    state,
                    task_id,
                    phase='pre_check',
                    title=f"Prerequisite {index + 1}/{len(prerequisites)} failed",
                    body=f"type={prereq_type}\n{description}\n\nError: {e}",
                )

        return '\n\n'.join(collected_context)
