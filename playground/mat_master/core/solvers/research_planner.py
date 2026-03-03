"""ResearchPlanner: pre-check → deterministic flight-plan execution under CRP (Computational Resource Protocol).

- Pre-check phase: assesses task readiness, runs prerequisites (PDF parsing, info gathering) before planning.
- Loads industrial-grade system prompt from prompts/planner_system_prompt.txt.
- Injects hard-coded CRP (license firewall, tool stack); validates plan JSON; enforces human-in-the-loop for high-cost steps.
- Persists state to research_state.json; supports resume.
"""

import json
import logging
import re
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Optional

from evomaster.core.exp import BaseExp
from evomaster.utils.types import Dialog, SystemMessage, TaskInstance, UserMessage

from ...prompts.build_prompt import LANGUAGE_RULE
from ..async_tool_registry import AsyncToolRegistry
from ..constants import MANUSCRIPT_FAIL_MARKERS
from ..execution import BatchExecutor, ExecutionTask
from .direct_solver import DirectSolver, _get_available_tool_names

try:
    from ..exp import SkillEvolutionExp

    _HAS_EVOLUTION = True
except ImportError:
    SkillEvolutionExp = None
    _HAS_EVOLUTION = False


def _get_async_registry(config) -> AsyncToolRegistry:
    """Create AsyncToolRegistry from config (handles model_dump)."""
    try:
        if hasattr(config, 'model_dump'):
            d = config.model_dump()
        else:
            d = dict(config) if config else {}
    except Exception as exc:
        logging.getLogger(__name__).warning(
            'Failed to parse config for AsyncToolRegistry: %s', exc
        )
        d = {}
    return AsyncToolRegistry(d)


def _load_pre_check_system_prompt(config_dir=None) -> str:
    """Load pre-check system prompt from file, falling back to minimal inline template."""
    candidates = []
    if config_dir is not None:
        playground_base = Path(str(config_dir).replace('configs', 'playground', 1))
        candidates.append(
            (playground_base / 'prompts' / 'pre_check_system_prompt.txt').resolve()
        )
        candidates.append(
            (Path(config_dir) / 'prompts' / 'pre_check_system_prompt.txt').resolve()
        )
    local_base = Path(__file__).resolve().parent.parent.parent
    candidates.append(
        (local_base / 'prompts' / 'pre_check_system_prompt.txt').resolve()
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate.read_text(encoding='utf-8')
    # Fallback: minimal inline template
    return (
        'You are a pre-planning readiness assessor. Output JSON with keys: '
        'ready_to_plan (bool), prerequisites (list), reasoning (str). '
        'Do NOT generate the plan itself. Only assess readiness.'
    )


# Pre-check prompt is loaded per-instance in ResearchPlanner.__init__ via
# _load_pre_check_system_prompt(self._config_dir) and stored as self._pre_check_system.


def _get_mat_master_config(config) -> dict:
    try:
        if hasattr(config, 'model_dump'):
            d = config.model_dump()
        else:
            d = dict(config) if config else {}
        return d.get('mat_master') or {}
    except Exception as exc:
        logging.getLogger(__name__).warning(
            'Failed to parse mat_master config: %s', exc
        )
        return {}


def _is_deg_plan(plan: Any) -> bool:
    """True if plan is a DEG (has 'steps' or 'execution_graph' with step_id)."""
    if not isinstance(plan, dict):
        return False
    steps = plan.get('steps') or plan.get('execution_graph')
    return (
        isinstance(steps, list)
        and len(steps) > 0
        and isinstance(steps[0].get('step_id'), int)
    )


def _normalize_step(step: dict[str, Any]) -> dict[str, Any]:
    """Map execution_graph schema to internal steps schema (goal-oriented)."""
    intensity = (
        step.get('compute_intensity') or step.get('compute_cost') or 'MEDIUM'
    ).upper()
    if intensity == 'LOW':
        cost = 'Low'
    elif intensity == 'HIGH':
        cost = 'High'
    else:
        cost = 'Medium'
    step_type = (step.get('step_type') or 'normal').lower()
    if step_type not in ('normal', 'skill_evolution'):
        step_type = (
            'skill_evolution'
            if step.get('tool_name') == 'skill_evolution'
            else 'normal'
        )
    intent = step.get('goal') or step.get('scientific_intent') or step.get('intent', '')
    return {
        'step_id': step.get('step_id'),
        'step_type': step_type,
        'tool_name': (
            'skill_evolution' if step_type == 'skill_evolution' else ''
        ),  # only for executor branch
        'intent': intent,
        'compute_cost': cost,
        'requires_human_confirm': step.get(
            'requires_confirmation', step.get('requires_human_confirm', False)
        ),
        'fallback_logic': step.get('fallback_strategy')
        or step.get('fallback_logic', 'None'),
        'status': step.get('status', 'pending'),
        'conditional_branch': step.get(
            'conditional_branch'
        ),  # optional: {"if_success": <step_id>, "if_fail": <step_id>}
        'depends_on': step.get(
            'depends_on', []
        ),  # optional: [step_id, ...] that must complete first
    }


def _normalize_plan(plan: dict[str, Any], max_steps: int = 999) -> dict[str, Any]:
    """Ensure plan has 'steps' with internal field names; cap length."""
    graph = plan.get('execution_graph') or plan.get('steps') or []
    plan['steps'] = [_normalize_step(s) for s in graph][:max_steps]
    for s in plan['steps']:
        s.setdefault('status', 'pending')
    return plan


def _plan_to_external_schema(plan: dict[str, Any]) -> dict[str, Any]:
    """Convert internal plan (steps) to prompt schema (execution_graph) for revision."""
    steps = plan.get('steps', [])
    intensity_map = {'Low': 'LOW', 'Medium': 'MEDIUM', 'High': 'HIGH'}
    execution_graph = []
    for s in steps:
        entry = {
            'step_id': s.get('step_id'),
            'step_type': s.get('step_type', 'normal'),
            'goal': s.get('intent', ''),
            'compute_intensity': intensity_map.get(s.get('compute_cost'), 'MEDIUM'),
            'requires_confirmation': s.get('requires_human_confirm', False),
            'fallback_strategy': s.get('fallback_logic', 'None'),
            'status': s.get('status', 'pending'),
        }
        if s.get('conditional_branch'):
            entry['conditional_branch'] = s['conditional_branch']
        if s.get('depends_on'):
            entry['depends_on'] = s['depends_on']
        execution_graph.append(entry)
    out = {
        'plan_id': plan.get('plan_id'),
        'status': plan.get('status'),
        'refusal_reason': plan.get('refusal_reason'),
        'strategy_name': plan.get('strategy_name'),
        'fidelity_level': plan.get('fidelity_level', 'Production'),
        'execution_graph': execution_graph,
    }
    if plan.get('plan_report'):
        out['plan_report'] = plan['plan_report']
    return out


def _extract_json_from_content(content: str) -> str | None:
    """Extract first {...} or ```json ... ``` from LLM output."""
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


def _to_thought_tag(label: str) -> str:
    """Normalize bracket labels like 'Pre-check' into stable snake_case tags."""
    t = (label or '').strip().lower()
    t = re.sub(r'[^a-z0-9]+', '_', t)
    return t.strip('_')


def _normalize_planner_thought(content: Any) -> dict[str, Any]:
    """Convert planner thought payloads to structured JSON for frontend readability."""
    if isinstance(content, dict):
        return content
    if content is None:
        return {'message': ''}
    if not isinstance(content, str):
        return {'data': content}

    raw_text = content
    text = raw_text.strip()
    payload: dict[str, Any] = {'message': raw_text}
    if not text:
        return payload

    # Parse leading bracket tags, e.g. "[Pre-check] ...", "[CRP] ..."
    tag_match = re.match(r'^\[([^\]]+)\]\s*([\s\S]*)$', text)
    if tag_match:
        payload['tag'] = _to_thought_tag(tag_match.group(1))
        payload['message'] = tag_match.group(2).strip()

    # If thought includes embedded JSON, expose it explicitly under `data`.
    embedded = _extract_json_from_content(text)
    if embedded:
        try:
            parsed = json.loads(embedded)
            payload['data'] = parsed
            # Avoid redundant payloads like {"message":"{...}", "data": {...}}
            # If message is exactly the same JSON structure, keep only `data`.
            msg_text = str(payload.get('message', '')).strip()
            if msg_text:
                try:
                    if json.loads(msg_text) == parsed:
                        payload.pop('message', None)
                except Exception:
                    pass
        except Exception:
            pass
    return payload


class ResearchPlanner(BaseExp):
    """Pre-check → Plan → PreFlight → Execute under CRP: readiness assessment → flight plan (JSON DEG) → validate → optional confirm → execute steps via DirectSolver."""

    def __init__(
        self,
        agent,
        config,
        input_fn=None,
        output_callback=None,
        config_dir: str | Path | None = None,
    ):
        super().__init__(agent, config)
        self.logger = logging.getLogger('MatMaster.Planner')
        mat = _get_mat_master_config(config)
        planner_cfg = mat.get('planner') or {}
        self._config_dir = Path(config_dir).resolve() if config_dir else None
        self._planner_prompt_file = str(
            planner_cfg.get('system_prompt_file', 'prompts/planner_system_prompt.txt')
        )
        self._pre_check_system = _load_pre_check_system_prompt(
            self._config_dir
        ).replace('{language_rule}', LANGUAGE_RULE)
        self.state_file = planner_cfg.get('state_file', 'research_state.json')
        self.max_steps = planner_cfg.get('max_steps', 20)
        self.human_check = planner_cfg.get('human_check_step', True)
        # Dynamic closed-loop planning config
        self.max_replans = planner_cfg.get('max_replans', 5)
        self.window_size = planner_cfg.get('window_size', 1)
        self.auto_replan = planner_cfg.get('auto_replan', True)
        self.replan_on_failure = planner_cfg.get('replan_on_failure', True)
        self.replan_on_new_skill = planner_cfg.get('replan_on_new_skill', True)
        # Unified execution layer config (shared BatchExecutor)
        exec_cfg = mat.get('execution') or {}
        self._planner_max_workers: int = max(
            1, exec_cfg.get('planner_max_workers', self.window_size)
        )
        self._rate_limit: float | None = exec_cfg.get('rate_limit')
        self._input_fn = input_fn  # optional; if set, used instead of stdin in _ask_human (e.g. for WebSocket UI)
        self._output_callback: Callable[[str, str, Any], None] | None = (
            output_callback  # (source, type, content) → frontend
        )
        self._solver: DirectSolver | None = None  # lazily created in run()
        # Async tool registry (config-driven) — used for CRP context and prompt placeholder replacement
        self._registry: AsyncToolRegistry = _get_async_registry(config)
        quality_cfg = planner_cfg.get('quality_gates') or {}
        self._quality_gate_cfg: dict[str, int] = {
            'survey_min_references': max(
                1, int(quality_cfg.get('survey_min_references', 5))
            ),
            'survey_min_unique_dois': max(
                1, int(quality_cfg.get('survey_min_unique_dois', 3))
            ),
            'survey_min_substantial_lines': max(
                1, int(quality_cfg.get('survey_min_substantial_lines', 8))
            ),
            'survey_min_evidence_delta': max(
                1, int(quality_cfg.get('survey_min_evidence_delta', 1))
            ),
            'survey_min_line_length': max(
                1, int(quality_cfg.get('survey_min_line_length', 60))
            ),
        }

    def _emit(self, source: str, event_type: str, content: Any) -> None:
        if source == 'Planner' and event_type == 'thought':
            content = _normalize_planner_thought(content)
            event_type = 'planner_reply'
        if self._output_callback:
            self._output_callback(source, event_type, content)

    def _run_dir_path(self) -> Path:
        return Path(self.run_dir) if self.run_dir else Path('.')

    def _state_path(self, task_id: str) -> Path:
        base = self._run_dir_path()
        workspaces = base / 'workspaces' / task_id
        workspaces.mkdir(parents=True, exist_ok=True)
        return workspaces / self.state_file

    def _task_workspace_dir(self, task_id: str) -> Path:
        base = self._run_dir_path()
        workspaces = base / 'workspaces' / task_id
        workspaces.mkdir(parents=True, exist_ok=True)
        return workspaces

    def _load_state(self, task_id: str) -> dict[str, Any]:
        path = self._state_path(task_id)
        if path.exists():
            try:
                with open(path, encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                self.logger.warning('Failed to load state: %s', e)
        return {
            'goal': '',
            'plan': None,
            'history': [],
            'phase': 'pre_check',
            'replan_count': 0,
            'execution_window': 0,
            'pre_check_context': '',
        }

    def _save_state(self, task_id: str, state: dict[str, Any]) -> None:
        path = self._state_path(task_id)
        tmp = path.with_suffix('.tmp')
        try:
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
            shutil.move(tmp, path)
        except Exception as e:
            self.logger.error('Failed to save state: %s', e)

    def _planner_artifact_dir(self, task_id: str) -> Path:
        base = self._task_workspace_dir(task_id)
        artifact_dir = base / '_tmp' / 'planner'
        artifact_dir.mkdir(parents=True, exist_ok=True)
        return artifact_dir

    def _ensure_longtask_artifacts(
        self, state: dict[str, Any], task_id: str, goal: str
    ) -> dict[str, Any]:
        """Create planner artifacts used by quality gates and resume safety."""
        artifact_dir = self._planner_artifact_dir(task_id)
        journal_path = artifact_dir / 'research_journal.md'
        literature_path = artifact_dir / 'literature_index.jsonl'
        if not journal_path.exists():
            journal_path.write_text(
                '# Planner Research Journal\n\n'
                f"- task_id: {task_id}\n"
                f"- created_at: {datetime.now(UTC).isoformat()}\n"
                f"- goal: {goal}\n\n",
                encoding='utf-8',
            )
        if not literature_path.exists():
            literature_path.write_text('', encoding='utf-8')
        artifacts = state.setdefault('artifacts', {})
        if not isinstance(artifacts, dict):
            artifacts = {}
            state['artifacts'] = artifacts
        artifacts['research_journal'] = str(journal_path)
        artifacts['literature_index'] = str(literature_path)
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
            Path(journal_raw)
            if journal_raw
            else (self._planner_artifact_dir(task_id) / 'research_journal.md')
        )
        ts = datetime.now(UTC).isoformat()
        block = [f"## [{ts}] {phase}: {title}"]
        if body:
            block.append(body.strip())
        block.append('')
        with journal_path.open('a', encoding='utf-8') as f:
            f.write('\n'.join(block))
            f.write('\n')

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
        for u in urls:
            if u in seen:
                continue
            seen.add(u)
            deduped.append(u)
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
            Path(lit_raw)
            if lit_raw
            else (self._planner_artifact_dir(task_id) / 'literature_index.jsonl')
        )
        seen = state.get('literature_seen_urls') or []
        if not isinstance(seen, list):
            seen = []
        seen_set = {str(x) for x in seen}
        added = 0
        new_urls = [u for u in self._extract_urls_from_text(text) if u not in seen_set]
        if new_urls:
            with lit_path.open('a', encoding='utf-8') as f:
                for url in new_urls:
                    seen_set.add(url)
                    rec = {
                        'ts': datetime.now(UTC).isoformat(),
                        'source': source,
                        'url': url,
                    }
                    f.write(json.dumps(rec, ensure_ascii=False))
                    f.write('\n')
                    added += 1
        if added:
            state['literature_seen_urls'] = list(seen_set)
            state['literature_entry_count'] = (
                int(state.get('literature_entry_count', 0) or 0) + added
            )
        return added

    def _build_context_prompt(self, task_description: str) -> str:
        """Build RUNTIME_CONTEXT + REQUEST_CONFIG + USER_INTENT for the planner; includes hardware and license awareness."""
        try:
            import torch

            has_gpu = torch.cuda.is_available()
        except Exception:
            has_gpu = False
        mat = _get_mat_master_config(self.config)
        crp_cfg = mat.get('crp', {})
        active_licenses = crp_cfg.get('licenses', [])
        task_lower = task_description.lower()
        _SCREENING_KW = [
            'quick',
            'fast',
            'screen',
            'rough',
            'coarse',
            'preliminary',
            '快速',
            '粗略',
        ]
        _EXPLORATORY_KW = ['初步', '探索', 'exploratory', '试试', 'try out', 'pilot']
        if any(w in task_lower for w in _SCREENING_KW):
            fidelity = 'Screening'
        elif any(w in task_lower for w in _EXPLORATORY_KW):
            fidelity = 'Exploratory'
        else:
            fidelity = 'Production'
        context_data = {
            'RUNTIME_CONTEXT': {
                'Hardware': {
                    'Has_GPU': has_gpu,
                    'Compute_Tier': 'HPC_Cluster' if has_gpu else 'Local_CPU',
                },
                'License_Keys': active_licenses,
                'Internet_Access': True,
            },
            'REQUEST_CONFIG': {
                'Target_Fidelity': fidelity,
                'Max_Steps': self.max_steps,
            },
            'USER_INTENT': task_description,
        }
        tools_preview = _get_available_tool_names(self.agent)
        tools_str = ', '.join(tools_preview[:100]) if tools_preview else '(none)'
        return f"""# CURRENT RUNTIME STATE (JSON)
{json.dumps(context_data, indent=2, ensure_ascii=False)}

# AVAILABLE TOOLS (use exact names in tool_name)
{tools_str}

# INSTRUCTION
Analyze USER_INTENT against RUNTIME_CONTEXT and REQUEST_CONFIG. Generate the research plan in strict JSON format: plan_id, status, strategy_name, fidelity_level, execution_graph (each step has goal and step_type, no tool_name), and plan_report (summary, cost_assessment, risks, alternatives). No other text."""

    def _resolve_planner_prompt_path(self) -> Path:
        configured = Path(self._planner_prompt_file)
        if configured.is_absolute():
            return configured

        candidates: list[Path] = []
        if self._config_dir is not None:
            # Keep parity with Evo playground prompt path resolution:
            # relative prompt paths are resolved against a mirrored playground base.
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
        """Load planner system prompt file from config, then apply runtime replacements."""
        prompt_file = self._resolve_planner_prompt_path()
        if prompt_file.exists():
            raw = prompt_file.read_text(encoding='utf-8')
        else:
            self.logger.warning(
                'Planner prompt file not found (%s), using minimal fallback',
                prompt_file,
            )
            raw = 'You are a Research Planner. Output a single JSON object with plan_id, status, strategy_name, steps.'
        # Replace {{placeholders}} with registry values (async software list, CRP, etc.)
        raw = self._registry.replace_placeholders(raw)
        # Also replace the license-firewall placeholder with the full generated section
        raw = raw.replace(
            '{{CRP_LICENSE_FIREWALL}}', self._registry.format_planner_license_firewall()
        )
        crp_context = self._registry.crp_context_dict()
        crp_str = json.dumps(crp_context, indent=2)
        return f"{raw}\n\n{LANGUAGE_RULE}\n\n# EMBEDDED SYSTEM PROTOCOL (IMMUTABLE)\n{crp_str}"

    # Regex patterns that indicate the blocked software name is used in a
    # *mapping / comparison / reference* context, NOT as an execution target.
    # e.g. "VASP -> ABACUS", "map VASP to ABACUS", "replace VASP with", "originally in VASP"
    # NOTE: The allowed-software alternation (ABACUS|LAMMPS|DPA|...) is built dynamically
    # from the registry in _build_mapping_patterns().
    _MAPPING_PATTERN_TEMPLATES: list[str] = [
        r'{sw}\s*(?:→|->|-->|=>)\s*\w',
        r'(?:map|convert|replace|switch|redirect|translate|migrate)\s+{sw}',
        r'{sw}\s+(?:to|into|with)\s+(?:{{ALLOW_ALT}}|open[\s-]?source)',
        r'(?:originally|formerly|previously|instead of|rather than|not)\s+(?:in\s+|using\s+)?{sw}',
        r'(?:mapped|equivalent)\s+.*{sw}',
    ]

    def _build_mapping_patterns(self) -> list[re.Pattern[str]]:
        """Build mapping patterns with allowed-software names from registry."""
        allow_alt = '|'.join(re.escape(n) for n in self._registry.software_names)
        patterns = []
        for tmpl in self._MAPPING_PATTERN_TEMPLATES:
            filled = tmpl.replace('{{ALLOW_ALT}}', allow_alt)
            patterns.append(re.compile(filled, re.IGNORECASE))
        return patterns

    def _is_mapping_context(self, text: str, sw: str) -> bool:
        """Return True if the blocked software name appears only in mapping/reference context."""
        for pat in self._build_mapping_patterns():
            concrete = re.compile(
                pat.pattern.replace('{sw}', re.escape(sw)), re.IGNORECASE
            )
            if concrete.search(text):
                return True
        return False

    def _validate_plan_safety(self, plan: dict[str, Any]) -> dict[str, Any]:
        """CRP watchdog: auto-redirect steps that mention blocked software.

        Strategy:
        1. Skip mentions that are clearly mapping/comparison context (e.g. "VASP -> ABACUS").
        2. For genuine violations, auto-rewrite the step intent to replace blocked names
           with CRP-allowed alternatives (ABACUS/DPA/LAMMPS).
        3. Only REFUSE as absolute last resort (if rewritten text still contains blocked names).
        """
        if plan.get('status') == 'REFUSED':
            return plan
        crp_ctx = self._registry.crp_context_dict()
        block = crp_ctx['License_Registry']['Block_List']
        preferred = crp_ctx['Tool_Stack']
        # Mapping table: blocked software -> preferred replacement
        redirect_map: dict[str, str] = {}
        for sw in block:
            sw_lower = sw.lower()
            if sw_lower in ('vasp', 'castep', 'wien2k'):
                redirect_map[sw] = preferred['Preferred_DFT']  # ABACUS
            elif sw_lower == 'gaussian':
                redirect_map[sw] = preferred[
                    'Preferred_DFT'
                ]  # ABACUS (or CP2K for molecular)

        redirected_steps: list[int] = []
        for step in plan.get('steps', []):
            text = step.get('intent', '') or step.get('goal', '') or ''
            for sw in block:
                if sw.lower() not in text.lower():
                    continue
                # Check if it's just a mapping/reference context
                if self._is_mapping_context(text, sw):
                    continue
                # Genuine violation: auto-redirect
                replacement = redirect_map.get(sw, preferred['Preferred_DFT'])
                step_id = step.get('step_id', '?')
                self.logger.info(
                    "[CRP] Auto-redirect step %s: '%s' → '%s'", step_id, sw, replacement
                )
                # Case-insensitive replacement in the intent text
                step['intent'] = re.sub(
                    re.escape(sw), replacement, step['intent'], flags=re.IGNORECASE
                )
                redirected_steps.append(step_id)

        if redirected_steps:
            self.logger.info(
                '[CRP] Redirected %d step(s): %s',
                len(redirected_steps),
                redirected_steps,
            )
            self._emit(
                'Planner',
                'thought',
                f"[CRP] Auto-redirected blocked software in step(s) {redirected_steps} to CRP-allowed alternatives.",
            )

            # Safety check: verify no blocked names remain after rewriting
            for step in plan.get('steps', []):
                text = (step.get('intent', '') or '').lower()
                for sw in block:
                    if sw.lower() in text and not self._is_mapping_context(text, sw):
                        # Still there after rewrite — log warning but do NOT refuse
                        self.logger.warning(
                            "[CRP] Step %s still references '%s' after redirect (may be in context description); proceeding anyway.",
                            step.get('step_id'),
                            sw,
                        )
        return plan

    def _generate_plan(self, goal: str) -> dict[str, Any]:
        """Produce DEG via LLM with runtime context, normalize to steps, validate against CRP."""
        system = self._load_system_prompt()
        user = self._build_context_prompt(goal)
        dialog = Dialog(
            messages=[SystemMessage(content=system), UserMessage(content=user)],
            tools=[],
        )
        try:
            reply = self.agent.llm.query(dialog)
            # 将 Planner LLM 原始输出推送到前端
            self._emit('Planner', 'thought', reply.content or '')
            raw = _extract_json_from_content(reply.content or '')
            if not raw:
                return {
                    'status': 'REFUSED',
                    'refusal_reason': 'Planner output contained no valid JSON.',
                }
            plan = json.loads(raw)
        except json.JSONDecodeError as e:
            self.logger.error('Plan JSON parse failed: %s', e)
            return {'status': 'REFUSED', 'refusal_reason': f"Invalid JSON: {e}"}
        except Exception as e:
            self.logger.error('Plan generation failed: %s', e)
            return {'status': 'REFUSED', 'refusal_reason': str(e)}
        plan = _normalize_plan(plan, self.max_steps)
        if not plan.get('steps'):
            plan['status'] = 'REFUSED'
            plan['refusal_reason'] = (
                plan.get('refusal_reason') or 'Plan must have at least one step.'
            )
        plan = self._validate_plan_safety(plan)
        plan = self._enforce_manuscript_plan_consistency(plan, goal)
        return plan

    def _revise_plan(
        self, goal: str, current_plan: dict[str, Any], user_feedback: str
    ) -> dict[str, Any]:
        """Revise plan from user feedback; same schema and validation as _generate_plan."""
        system = self._load_system_prompt()
        external = _plan_to_external_schema(current_plan)
        plan_json = json.dumps(external, ensure_ascii=False, indent=2)
        user = f"REVISION REQUEST\nOriginal goal: {goal}\n\nCurrent plan (JSON):\n{plan_json}\n\nUser feedback: {user_feedback}\n\nOutput the revised plan as a single JSON object (same schema: execution_graph, fidelity_level). No other text."
        dialog = Dialog(
            messages=[SystemMessage(content=system), UserMessage(content=user)],
            tools=[],
        )
        try:
            reply = self.agent.llm.query(dialog)
            self._emit('Planner', 'thought', reply.content or '')
            raw = _extract_json_from_content(reply.content or '')
            if not raw:
                return {
                    **current_plan,
                    'status': 'REFUSED',
                    'refusal_reason': 'Revision output contained no valid JSON.',
                }
            plan = json.loads(raw)
        except json.JSONDecodeError as e:
            self.logger.error('Revision JSON parse failed: %s', e)
            return {
                **current_plan,
                'status': 'REFUSED',
                'refusal_reason': f"Invalid JSON: {e}",
            }
        except Exception as e:
            self.logger.error('Plan revision failed: %s', e)
            return {**current_plan, 'status': 'REFUSED', 'refusal_reason': str(e)}
        plan = _normalize_plan(plan, self.max_steps)
        if not plan.get('steps'):
            plan['status'] = 'REFUSED'
            plan['refusal_reason'] = (
                plan.get('refusal_reason') or 'Plan must have at least one step.'
            )
        plan = self._validate_plan_safety(plan)
        plan = self._enforce_manuscript_plan_consistency(plan, goal)
        return plan

    def _ask_human(
        self,
        prompt: str,
        *,
        mode: str = 'block',
        timeout_sec: int = 300,
        default: str = '',
    ) -> str:
        """Ask the human a question via ConfirmationManager.

        Args:
            prompt: The question to display.
            mode: "block" waits indefinitely; "timeout" waits up to timeout_sec
                  then returns default.
            timeout_sec: Seconds to wait in timeout mode.
            default: Value returned when timeout mode expires without a reply.
        """
        from playground.mat_master.service.confirm import ConfirmMode

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
        if confirm_mgr is not None:
            try:
                from playground.mat_master.service.confirm import REPLY_CANCELLED
            except Exception:
                REPLY_CANCELLED = None  # type: ignore[assignment]
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
                if reply is REPLY_CANCELLED:
                    # User clicked stop/cancel while waiting — treat as abort signal
                    return 'abort'
                if reply is not None:
                    return reply.strip()
                # TIMEOUT mode expired and default_reply was None/empty
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
            except Exception:
                pass

        # Fallback: stdin or input_fn
        if self._input_fn is not None:
            return (self._input_fn(prompt) or default).strip()
        print(f"\033[93m[Planner] {prompt}\033[0m")
        return sys.stdin.readline().strip() or default

    def _plan_report_text(self, plan: dict[str, Any]) -> str:
        """Build plain-text plan report (no ANSI) for frontend."""
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
            for a in alts:
                lines.append(f"  • {a}")
        return '\n'.join(lines) if lines else ''

    def _print_plan_report(self, plan: dict[str, Any]) -> None:
        """Print detailed plan report (cost, risks, alternatives) to console and emit to frontend."""
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
            for a in alts:
                print(f"  • {a}")
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
        """Run fallback_strategy for this step; returns True if fallback ran successfully.

        If fallback_logic is empty/None, the plan-level alternatives from
        plan_report.alternatives are appended as context hints to the solver prompt
        so the LLM can decide whether any of them is applicable.
        """
        fallback = step.get('fallback_logic') or step.get('fallback_strategy') or ''
        step_id = step.get('step_id', 0)

        # Build the fallback prompt: prefer step-level fallback_logic; supplement with
        # plan-level alternatives when step-level logic is absent.
        if fallback and fallback.strip().lower() != 'none':
            fallback_prompt = f"Execute fallback strategy: {fallback}"
        else:
            # No step-level fallback — use plan-level alternatives as context hints
            alternatives: list[str] = []
            if plan and isinstance(plan, dict):
                alternatives = plan.get('plan_report', {}).get('alternatives', []) or []
            if not alternatives:
                return False
            alts_text = '\n'.join(f"  - {a}" for a in alternatives)
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
        try:
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

    # ------------------------------------------------------------------
    # Dynamic Closed-loop Planning: core helper methods
    # ------------------------------------------------------------------

    def _initialize_state(self, task_description: str, task_id: str) -> dict[str, Any]:
        """Load persisted state or create fresh; ensure all required keys exist."""
        state = self._load_state(task_id)
        state.setdefault('goal', task_description)
        state.setdefault('plan', None)
        state.setdefault('history', [])
        state.setdefault('phase', 'pre_check')
        state.setdefault('replan_count', 0)
        state.setdefault('execution_window', 0)
        state.setdefault('pre_check_context', '')
        state.setdefault('artifacts', {})
        state.setdefault('literature_seen_urls', [])
        state.setdefault('literature_entry_count', 0)
        # If goal changed, reset to pre_check phase
        if state.get('goal') != task_description:
            state['goal'] = task_description
            state['plan'] = None
            state['history'] = []
            state['phase'] = 'pre_check'
            state['replan_count'] = 0
            state['pre_check_context'] = ''
            state['literature_seen_urls'] = []
            state['literature_entry_count'] = 0
            state['longtask_initialized'] = False
        state = self._ensure_longtask_artifacts(state, task_id, task_description)
        return state

    def _build_task_with_dialog_history(
        self, description: str, task_id: str
    ) -> Optional[TaskInstance]:
        """Build a TaskInstance with dialog_history so the agent sees multi-turn context.

        When this run was started with task.meta['dialog_history'] (e.g. from chat API),
        we inject that history plus the current round user message, so solver.run(task=...)
        gives the agent full conversation context before the current step/prereq prompt.
        """
        t = getattr(self, '_task_with_history', None)
        if t is None or not getattr(t, 'meta', None):
            return None
        base_history = list((t.meta or {}).get('dialog_history') or [])
        current_user_msg = (t.description or '').strip()
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

    def _is_goal_achieved(self, state: dict[str, Any]) -> bool:
        """Check if the research goal has been met.

        A mission is considered achieved only when all steps are done.
        Failed steps are not treated as completed goals.
        """
        plan = state.get('plan')
        if not plan or not isinstance(plan, dict):
            return False
        steps = plan.get('steps', [])
        if not steps:
            return False
        # Only fully done plans count as achieved.
        return all(s.get('status') == 'done' for s in steps)

    @staticmethod
    def _infer_manuscript_profile(text: str) -> str | None:
        """Infer intended manuscript profile from user intent text."""
        t = (text or '').lower()
        if any(k in t for k in ('专利', 'patent')):
            return 'patent'
        if any(k in t for k in ('thesis', '论文', '学位')):
            return 'thesis_section'
        if any(k in t for k in ('computational report', '计算报告')):
            return 'computational_report'
        if any(k in t for k in ('review', '综述')):
            return 'review'
        if any(k in t for k in ('technical report', '技术报告')):
            return 'technical_report'
        if any(k in t for k in ('grant', '基金')):
            return 'grant'
        if any(
            k in t
            for k in (
                'paper',
                'article',
                'research',
                'manuscript',
                'nature',
                'generic',
                'imrad',
                '论文',
                '文章',
                '研究报告',
            )
        ):
            return 'research_paper'
        return None

    @staticmethod
    def _summarize_solver_result(result: Any, max_len: int = 1200) -> str:
        """Extract concise text summary from DirectSolver output."""
        if isinstance(result, dict):
            for key in ('result_summary', 'summary', 'message', 'final_message'):
                value = result.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()[:max_len]
            if isinstance(result.get('execution_summary'), dict):
                return json.dumps(result['execution_summary'], ensure_ascii=False)[
                    :max_len
                ]
            if 'status' in result:
                return f"status={result.get('status')}"[:max_len]
            safe_result = {k: v for k, v in result.items() if k != 'trajectory'}
            return json.dumps(safe_result, ensure_ascii=False)[:max_len]
        return str(result)[:max_len]

    # Section lists for strict profiles.  Must stay in sync with
    # skills/manuscript-scribe/scripts/format_profiles.py (the canonical
    # source).  We duplicate a minimal constant here because the planner
    # cannot import format_profiles directly (different package tree).
    _STRICT_PROFILE_SECTIONS: dict[str, list[str]] = {
        'computational_report': ['Methods', 'Results and Discussion', 'References'],
        'patent': [
            'Technical Field',
            'Background Art',
            'Summary of Invention',
            'Detailed Description',
            'Claims',
            'Abstract',
        ],
    }

    def _enforce_manuscript_plan_consistency(
        self, plan: dict[str, Any], goal: str
    ) -> dict[str, Any]:
        """Ensure manuscript plan uses the right profile and covers required sections.

        Two checks, both generic (no per-profile if/elif):
        1. Auto-align ``--template`` / ``--profile`` in step intents.
        2. For strict profiles, verify every required section is mentioned.

        Forbidden-section substring matching was intentionally removed: it caused
        false positives (e.g. "introduction" as a common English word).  Unexpected
        sections are caught at execution time by ``validate_content.py``.
        """
        target_profile = self._infer_manuscript_profile(goal)
        if not target_profile or plan.get('status') == 'REFUSED':
            return plan

        steps = plan.get('steps', [])
        manuscript_steps = [
            s
            for s in steps
            if 'manuscript-scribe' in (s.get('intent', '') or '').lower()
        ]
        if not manuscript_steps:
            return plan

        # ── 1. Auto-align --template / --profile arguments ───────────
        template_re = re.compile(
            r'--template\s+["\']?([a-zA-Z_]+)["\']?', flags=re.IGNORECASE
        )
        profile_re = re.compile(
            r'--profile\s+["\']?([a-zA-Z_]+)["\']?', flags=re.IGNORECASE
        )
        rewritten_ids: list[int] = []
        for step in manuscript_steps:
            intent = step.get('intent', '')
            new_intent = template_re.sub(f"--template {target_profile}", intent)
            new_intent = profile_re.sub(f"--profile {target_profile}", new_intent)
            if new_intent != intent:
                step['intent'] = new_intent
                rewritten_ids.append(step.get('step_id', -1))
        if rewritten_ids:
            self._emit(
                'Planner',
                'thought',
                f"[Planner] Auto-aligned profile to '{target_profile}' in step(s) {rewritten_ids}.",
            )

        # ── 2. Required-section coverage (strict profiles only) ──────
        required = self._STRICT_PROFILE_SECTIONS.get(target_profile)
        if required:
            all_intents = ' '.join(
                s.get('intent', '') for s in manuscript_steps
            ).lower()
            missing = [sec for sec in required if sec.lower() not in all_intents]
            if missing:
                plan['status'] = 'REFUSED'
                plan['refusal_reason'] = (
                    f"manuscript_consistency: {target_profile} plan missing required "
                    f"section mentions: {', '.join(missing)}"
                )

        return plan

    _MANUSCRIPT_FAIL_MARKERS = MANUSCRIPT_FAIL_MARKERS

    @staticmethod
    def _extract_longtask_result(result_text: str) -> dict[str, Any] | None:
        from ...skills._common.longtask_runtime import parse_prefixed_result_line

        return parse_prefixed_result_line(result_text or '')

    @classmethod
    def _detect_manuscript_validation_failure(
        cls, intent: str, result_text: str
    ) -> tuple[bool, str]:
        """Detect manuscript-scribe failures encoded in textual tool output."""
        i = (intent or '').lower()
        if 'manuscript-scribe' not in i:
            return False, ''

        # Structured status has precedence over textual markers when available.
        structured = cls._extract_longtask_result(result_text)
        if structured:
            status = str(structured.get('status', '')).strip().lower()
            message = str(structured.get('message', '')).strip()
            if status in {'retryable_error', 'fatal_error'}:
                return True, message or f"manuscript status={status}"
            if status == 'completed':
                return False, ''

        t = (result_text or '').lower()
        if 'assemble_manuscript' in i or 'validate_content' in i:
            for marker in cls._MANUSCRIPT_FAIL_MARKERS:
                if marker in t:
                    return True, f"manuscript validation failed ({marker})"
        return False, ''

    _QUALITY_CRITICAL_HINTS: tuple[str, ...] = (
        'deep-survey',
        'run_survey',
        'literature review',
        'literature survey',
        '综述报告',
        '文献调研',
    )

    @classmethod
    def _is_quality_critical_step(cls, intent: str) -> bool:
        text = (intent or '').lower()
        return any(h in text for h in cls._QUALITY_CRITICAL_HINTS)

    @staticmethod
    def _extract_markdown_paths_from_text(text: str, workspace_dir: Path) -> list[Path]:
        candidates: list[Path] = []
        if not text:
            return candidates
        for raw in re.findall(r'([A-Za-z0-9_./\\:\-]+\.md)', text):
            cleaned = raw.strip().strip("`'\"")
            if not cleaned:
                continue
            path = Path(cleaned)
            if not path.is_absolute():
                path = workspace_dir / path
            if path.exists() and path.is_file():
                candidates.append(path)
        return candidates

    @staticmethod
    def _count_substantial_lines(content: str, min_length: int = 60) -> int:
        count = 0
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith('#'):
                continue
            if stripped.startswith('<!--'):
                continue
            if len(stripped) < min_length:
                continue
            count += 1
        return count

    @staticmethod
    def _reference_metrics(content: str) -> tuple[int, int]:
        lower = content.lower()
        match = re.search(
            r'^\s{0,3}#{1,6}\s+references?\s*$', lower, flags=re.MULTILINE
        )
        ref_text = content[match.end() :] if match else content
        ref_entries = re.findall(r'(?m)^\s*\[(\d+)\]\s+', ref_text)
        if ref_entries:
            ref_count = len(set(ref_entries))
        else:
            cite_entries = re.findall(r'\[(\d+)\]\(https?://[^\)]+\)', ref_text)
            ref_count = len(set(cite_entries))
        doi_urls = set(re.findall(r'https?://(?:dx\.)?doi\.org/([^\s\)]+)', lower))
        bare_dois = set(re.findall(r'\b10\.\d{4,9}/[-._;()/:a-z0-9]+\b', lower))
        unique_doi_count = len({d.rstrip('.,;:)') for d in doi_urls | bare_dois})
        return ref_count, unique_doi_count

    def _collect_quality_files(
        self, step_dir: Path, workspace_dir: Path, result_text: str
    ) -> list[Path]:
        files: list[Path] = []
        seen: set[str] = set()

        def _add(path: Path) -> None:
            key = str(path.resolve()) if path.exists() else str(path)
            if key in seen:
                return
            seen.add(key)
            files.append(path)

        for p in step_dir.glob('*.md'):
            _add(p)

        survey_dir = workspace_dir / '_tmp' / 'surveys'
        if survey_dir.exists():
            for p in survey_dir.glob('*.md'):
                _add(p)
        for p in workspace_dir.glob('*_review_*.md'):
            _add(p)
        for p in workspace_dir.glob('*_survey_*.md'):
            _add(p)
        for p in self._extract_markdown_paths_from_text(result_text, workspace_dir):
            _add(p)
        return files

    def _detect_survey_quality_failure(
        self,
        *,
        intent: str,
        quality_files: list[Path],
        evidence_delta: int,
    ) -> tuple[bool, str]:
        if not self._is_quality_critical_step(intent):
            return False, ''
        if not quality_files:
            return (
                True,
                'No markdown artifact found for quality-critical survey/literature step.',
            )

        min_line_len = self._quality_gate_cfg.get('survey_min_line_length', 60)
        best: tuple[int, int, int, Path] | None = None
        best_content: str = ''
        for path in quality_files:
            try:
                content = path.read_text(encoding='utf-8')
            except Exception:
                continue
            refs, dois = self._reference_metrics(content)
            substantial = self._count_substantial_lines(
                content, min_length=min_line_len
            )
            score = (refs, dois, substantial, path)
            if best is None or score[:3] > best[:3]:
                best = score
                best_content = content

        if best is None:
            return True, 'Survey quality check found no readable markdown artifacts.'

        refs, dois, substantial, best_path = best
        if refs < self._quality_gate_cfg['survey_min_references']:
            return True, (
                f"Insufficient references in {best_path.name}: {refs} "
                f"(min {self._quality_gate_cfg['survey_min_references']})."
            )
        if dois < self._quality_gate_cfg['survey_min_unique_dois']:
            return True, (
                f"Insufficient unique DOIs in {best_path.name}: {dois} "
                f"(min {self._quality_gate_cfg['survey_min_unique_dois']})."
            )
        if substantial < self._quality_gate_cfg['survey_min_substantial_lines']:
            return True, (
                f"Insufficient substantive content in {best_path.name}: {substantial} lines "
                f"(min {self._quality_gate_cfg['survey_min_substantial_lines']})."
            )
        if evidence_delta < self._quality_gate_cfg['survey_min_evidence_delta']:
            return True, (
                f"No evidence growth in literature index: delta={evidence_delta} "
                f"(min {self._quality_gate_cfg['survey_min_evidence_delta']})."
            )
        if best_content and re.search(r'\btbd\b', best_content.lower()):
            return (
                True,
                f"Survey artifact still contains TBD placeholder: {best_path.name}",
            )
        return False, ''

    def _get_next_execution_window(self, plan: dict[str, Any]) -> list[dict[str, Any]]:
        """Return next batch of pending steps respecting dependencies and window_size."""
        steps = plan.get('steps', [])
        # Both "done" and "failed" steps count as resolved for dependency purposes
        resolved_ids = {
            s['step_id'] for s in steps if s.get('status') in ('done', 'failed')
        }
        window: list[dict[str, Any]] = []
        for s in steps:
            if s.get('status') != 'pending':
                continue
            deps = s.get('depends_on') or []
            if all(d in resolved_ids for d in deps):
                window.append(s)
                if len(window) >= self.window_size:
                    break
        return window

    def _summarize_history(self, history: list[dict[str, Any]]) -> str:
        """Build concise text summary of execution history for replan context."""
        if not history:
            return '(no steps executed yet)'
        lines = []
        for entry in history:
            step_id = entry.get('step', '?')
            if entry.get('error'):
                lines.append(f"  Step {step_id}: FAILED — {entry['error'][:120]}")
            else:
                summary = entry.get('result_summary', 'done')[:120]
                lines.append(f"  Step {step_id}: OK — {summary}")
        return '\n'.join(lines)

    def _get_remaining_steps_text(self, plan: dict[str, Any]) -> str:
        """Build text listing of remaining (non-done) steps for replan context."""
        steps = plan.get('steps', [])
        remaining = [s for s in steps if s.get('status') != 'done']
        if not remaining:
            return '(none)'
        lines = []
        for s in remaining:
            lines.append(
                f"  Step {s.get('step_id')}: [{s.get('compute_cost', '?')}] {s.get('intent', '')[:100]}"
            )
        return '\n'.join(lines)

    def _llm_replan_check(
        self, state: dict[str, Any], step_result: dict[str, Any]
    ) -> bool:
        """Lightweight LLM check: ask if the latest result warrants replanning."""
        if not self.auto_replan:
            return False
        history_summary = self._summarize_history(state.get('history', []))
        plan = state.get('plan', {})
        remaining = self._get_remaining_steps_text(plan)
        latest = json.dumps(step_result, ensure_ascii=False, default=str)[:500]
        prompt = f"""You are a research planner evaluating whether the current execution plan needs revision.

Goal: {state.get('goal', '')}

Execution history:
{history_summary}

Latest step result:
{latest}

Remaining planned steps:
{remaining}

Question: Based on the latest result, do the remaining steps still make sense, or should the plan be revised?
Answer with a single JSON object: {{"needs_replan": true/false, "reason": "brief explanation"}}"""
        dialog = Dialog(
            messages=[
                SystemMessage(
                    content=f"You are a concise research plan evaluator. Output only JSON.\n\n{LANGUAGE_RULE}"
                ),
                UserMessage(content=prompt),
            ],
            tools=[],
        )
        try:
            reply = self.agent.llm.query(dialog)
            raw = _extract_json_from_content(reply.content or '')
            if raw:
                result = json.loads(raw)
                if result.get('needs_replan'):
                    self.logger.info(
                        '[Planner] LLM replan check: %s', result.get('reason', '')
                    )
                    return True
        except Exception as e:
            self.logger.debug('LLM replan check failed (non-critical): %s', e)
        return False

    def _needs_replanning(
        self, state: dict[str, Any], step_result: dict[str, Any]
    ) -> tuple[bool, str]:
        """Evaluate whether execution should pause for replanning. Returns (should_replan, reason)."""
        if not self.auto_replan:
            return False, ''
        # 1. Step failed and fallback also failed
        if (
            self.replan_on_failure
            and step_result.get('status') == 'failed'
            and not step_result.get('fallback_succeeded')
        ):
            return (
                True,
                f"Step {step_result.get('step_id', '?')} failed without successful fallback",
            )
        # 2. Skill evolution produced a new tool — planner should know
        if self.replan_on_new_skill and step_result.get('new_skill_registered'):
            return (
                True,
                f"New skill registered: {step_result.get('skill_path', 'unknown')}; subsequent steps may benefit",
            )
        # 3. Result contains explicit replan signal
        if step_result.get('replan_requested'):
            return True, step_result.get(
                'replan_reason', 'explicit replan requested by executor'
            )
        # 4. LLM-based heuristic (most expensive, checked last)
        if self._llm_replan_check(state, step_result):
            return True, 'LLM heuristic detected plan deviation'
        return False, ''

    def _replan_from_results(self, state: dict[str, Any], goal: str) -> dict[str, Any]:
        """Feed execution results back to planner LLM for mid-flight revision."""
        current_plan = state['plan']
        history_summary = self._summarize_history(state.get('history', []))
        remaining = self._get_remaining_steps_text(current_plan)
        replan_reason = state.get(
            'replan_reason', 'execution results require path adjustment'
        )
        revision_prompt = (
            f"MID-EXECUTION REPLAN REQUEST\n"
            f"Original goal: {goal}\n\n"
            f"Steps completed so far:\n{history_summary}\n\n"
            f"Remaining planned steps:\n{remaining}\n\n"
            f"Reason for replan: {replan_reason}\n\n"
            f"Revise the REMAINING steps only. Do NOT modify steps already marked as done.\n"
            f"You may add new steps (with step_id after the last existing step) or remove now-unnecessary steps.\n"
            f"Output the full revised plan as a single JSON object (same schema: execution_graph, fidelity_level). No other text."
        )
        return self._revise_plan(goal, current_plan, revision_prompt)

    def _execute_single_step(
        self,
        step: dict[str, Any],
        state: dict[str, Any],
        task_id: str,
        workspaces: Path,
    ) -> dict[str, Any]:
        """Execute one step (skill_evolution or normal) and return a result dict.

        Result dict keys:
            step_id, status ("done"|"failed"), fallback_succeeded (bool),
            new_skill_registered (bool), skill_path (str), result_summary (str),
            replan_requested (bool), replan_reason (str).
        """
        step_id = step.get('step_id', 0)
        tool_name = step.get('tool_name', '')
        intent = step.get('intent', '')
        fallback = step.get('fallback_logic', 'None')
        step_dir = workspaces / f"step_{step_id}"
        step_dir.mkdir(parents=True, exist_ok=True)

        result_info: dict[str, Any] = {
            'step_id': step_id,
            'status': 'done',
            'fallback_succeeded': False,
            'new_skill_registered': False,
            'skill_path': '',
            'result_summary': '',
            'replan_requested': False,
            'replan_reason': '',
        }

        steps_list = state.get('plan', {}).get('steps', [])
        self._emit(
            'Planner',
            'status_stages',
            {
                'total': len(steps_list),
                'current': step_id,
                'step_id': step_id,
                'intent': intent[:120] if intent else '',
            },
        )

        # High-cost confirmation
        if step.get('requires_human_confirm') or step.get('compute_cost') == 'High':
            ans = self._ask_human(
                f"Step {step_id} is HIGH COST. Proceed? (y/n)", mode='block'
            )
            if ans.strip().lower() not in ('y', 'yes', 'go'):
                result_info['status'] = 'skipped'
                result_info['result_summary'] = 'skipped_by_user'
                return result_info

        self.logger.info('[Planner] Step %s (goal): %s', step_id, intent[:80])

        # Reset loop-detection state so each planner step starts with a clean
        # slate.  Without this, legitimate tool calls (e.g. ``use_skill``,
        # ``execute_bash ls``) get blocked because an earlier step happened
        # to make the same call.
        if hasattr(self.agent, '_tool_guard'):
            self.agent._tool_guard.reset_loop_history()

        assert self._solver is not None, 'DirectSolver not initialized'
        solver = self._solver

        # ---- Branch A: skill_evolution ----
        if tool_name == 'skill_evolution':
            if not _HAS_EVOLUTION:
                self.logger.warning(
                    '[Planner] skill_evolution requested but SkillEvolutionExp not available.'
                )
                print(
                    '\033[91m[Planner] Skill Evolution not available. Attempting fallback.\033[0m'
                )
                if self._execute_fallback(
                    step,
                    solver,
                    workspaces,
                    state.get('plan') if isinstance(state, dict) else None,
                ):
                    result_info['fallback_succeeded'] = True
                    result_info['result_summary'] = 'fallback_after_evo_unavailable'
                else:
                    result_info['status'] = 'failed'
                    result_info['result_summary'] = (
                        'skill_evolution_unavailable_no_fallback'
                    )
                return result_info

            print(
                '\033[95m[Autonomy] Missing capability; initiating Skill Evolution...\033[0m'
            )
            self._emit('Planner', 'exp_run', 'SkillEvolutionExp')
            evo_exp = SkillEvolutionExp(self.agent, self.config)
            evo_exp.set_run_dir(step_dir)
            try:
                evo_result = evo_exp.run(
                    intent, task_id=f"{task_id}_step_{step_id}_evo"
                )
                if evo_result.get('status') == 'completed':
                    print('\033[92m[Autonomy] New skill created. Proceeding.\033[0m')
                    skill_path = evo_result.get('skill_path', '')
                    if skill_path:
                        self._emit('Planner', 'status_skill_produced', str(skill_path))
                    result_info['new_skill_registered'] = True
                    result_info['skill_path'] = str(skill_path or '')
                    result_info['result_summary'] = str(skill_path or evo_result)[:200]

                    # --- Skill Persistence Decision ---
                    # Decide whether to copy this skill to the user's permanent library.
                    # persist_skill flag in the step dict means the user explicitly asked to save.
                    # persist_new_skills config controls the default behaviour:
                    #   "always" -> always save, "never" -> skip, "ask" -> prompt (default save).
                    try:
                        _mm_cfg = _get_mat_master_config(self.config)
                        _evo_cfg = _mm_cfg.get('skill_evolution') or {}
                        _persist_mode = _evo_cfg.get('persist_new_skills', 'ask')
                        _persist_flag = bool(step.get('persist_skill', False))
                        _should_persist = _persist_flag or _persist_mode == 'always'

                        if (
                            not _should_persist
                            and _persist_mode == 'ask'
                            and skill_path
                        ):
                            _ans = self._ask_human(
                                f"New skill created at {skill_path}.\n"
                                'Save to personal library (~/.evomaster-skills)? (y/n, default: y)',
                                mode='timeout',
                                timeout_sec=30,
                                default='y',
                            )
                            _should_persist = _ans.strip().lower() not in ('n', 'no')

                        if _should_persist and skill_path:
                            from pathlib import Path as _Path

                            _skill_p = _Path(skill_path)
                            _local_user_root = _Path(
                                _evo_cfg.get(
                                    'local_user_skills_root', '~/.evomaster-skills'
                                )
                            ).expanduser()
                            # Read skill name from SKILL.md; fall back to directory name
                            _skill_name = _skill_p.name
                            _skill_md = _skill_p / 'SKILL.md'
                            if _skill_md.exists():
                                try:
                                    import re as _re

                                    _text = _skill_md.read_text(encoding='utf-8')
                                    _m = _re.search(
                                        r'^name:\s*(.+)$', _text, _re.MULTILINE
                                    )
                                    if _m:
                                        _skill_name = _m.group(1).strip()
                                except Exception:
                                    pass
                            _persisted = evo_exp._copy_to_user_skills(
                                _skill_p, _skill_name, _local_user_root
                            )
                            result_info['persisted_path'] = _persisted
                            # Register into the live registry so the agent can use it this session
                            _registry = getattr(self.agent, 'skill_registry', None)
                            if _registry and hasattr(_registry, 'register_user_skill'):
                                _registry.register_user_skill(_Path(_persisted))
                            print(
                                f"\033[96m[Autonomy] Skill persisted to {_persisted}\033[0m"
                            )
                    except Exception as _pe:
                        self.logger.warning(
                            '[Planner] Skill persist step failed (non-fatal): %s', _pe
                        )
                else:
                    print(
                        '\033[93m[Autonomy] Evolution failed. Triggering fallback.\033[0m'
                    )
                    if self._execute_fallback(
                        step,
                        solver,
                        workspaces,
                        state.get('plan') if isinstance(state, dict) else None,
                    ):
                        result_info['fallback_succeeded'] = True
                        result_info['result_summary'] = 'fallback_after_evo_failed'
                    else:
                        result_info['status'] = 'failed'
                        result_info['result_summary'] = (
                            f"evo_failed_no_fallback: {str(evo_result)[:150]}"
                        )
            except Exception as e:
                self.logger.error(
                    '[Planner] Skill evolution step %s failed: %s', step_id, e
                )
                if self._execute_fallback(
                    step,
                    solver,
                    workspaces,
                    state.get('plan') if isinstance(state, dict) else None,
                ):
                    result_info['fallback_succeeded'] = True
                    result_info['result_summary'] = 'fallback_after_evo_exception'
                else:
                    result_info['status'] = 'failed'
                    result_info['result_summary'] = str(e)[:200]
            return result_info

        # ---- Branch B: goal-oriented execution ----
        self._emit('Planner', 'exp_run', 'DirectSolver')
        step_prompt = f"Achieve: {intent}. If that fails: {fallback}"
        try:
            solver.set_run_dir(workspaces)
            step_task = self._build_task_with_dialog_history(
                step_prompt, f"{task_id}_step_{step_id}"
            )
            if step_task is not None:
                result = solver.run(task=step_task)
            else:
                result = solver.run(step_prompt, task_id=f"{task_id}_step_{step_id}")
            summary = self._summarize_solver_result(result, max_len=1000)
            result_info['result_summary'] = summary[:200]
            if self._is_quality_critical_step(intent):
                workspace_dir = self._task_workspace_dir(task_id)
                quality_files = self._collect_quality_files(
                    step_dir, workspace_dir, summary
                )
                before_count = int(state.get('literature_entry_count', 0) or 0)
                self._append_literature_index(
                    state,
                    task_id,
                    source=f"step:{step_id}:summary",
                    text=summary,
                )
                for quality_file in quality_files:
                    try:
                        text = quality_file.read_text(encoding='utf-8')
                    except Exception:
                        continue
                    self._append_literature_index(
                        state,
                        task_id,
                        source=f"step:{step_id}:file:{quality_file.name}",
                        text=text[:200000],
                    )
                evidence_delta = (
                    int(state.get('literature_entry_count', 0) or 0) - before_count
                )
                survey_failed, survey_reason = self._detect_survey_quality_failure(
                    intent=intent,
                    quality_files=quality_files,
                    evidence_delta=max(0, evidence_delta),
                )
                if survey_failed:
                    result_info['status'] = 'failed'
                    result_info['replan_requested'] = True
                    result_info['replan_reason'] = f"Step {step_id}: {survey_reason}"
                    self._append_journal(
                        state,
                        task_id,
                        phase='executing',
                        title=f"Step {step_id} quality gate failed",
                        body=survey_reason,
                    )
                    return result_info
                self._append_journal(
                    state,
                    task_id,
                    phase='executing',
                    title=f"Step {step_id} quality gate passed",
                    body=f"evidence_delta={max(0, evidence_delta)}",
                )
            failed, fail_reason = self._detect_manuscript_validation_failure(
                intent, summary
            )
            if failed:
                result_info['status'] = 'failed'
                result_info['replan_requested'] = True
                result_info['replan_reason'] = f"Step {step_id}: {fail_reason}"
        except Exception as e:
            self.logger.error('[Planner] Step %s failed: %s', step_id, e)
            print('\033[93m[Planner] Step failed. Attempting fallback...\033[0m')
            if self._execute_fallback(
                step,
                solver,
                workspaces,
                state.get('plan') if isinstance(state, dict) else None,
            ):
                result_info['fallback_succeeded'] = True
                if self._is_quality_critical_step(intent):
                    result_info['status'] = 'failed'
                    result_info['result_summary'] = (
                        'fallback_used_for_quality_critical_step'
                    )
                    result_info['replan_requested'] = True
                    result_info['replan_reason'] = (
                        f"Step {step_id}: fallback used in a quality-critical step; "
                        're-run primary deep-survey/literature path with stronger evidence accumulation.'
                    )
                else:
                    result_info['result_summary'] = 'completed_via_fallback'
            else:
                result_info['status'] = 'failed'
                result_info['result_summary'] = str(e)[:200]
                print('\033[91m[Planner] Step and fallback failed.\033[0m')
        return result_info

    # ------------------------------------------------------------------
    # Phase methods for the state machine
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Pre-check phase: assess readiness before planning
    # ------------------------------------------------------------------

    def _scan_workspace_files(self) -> list[str]:
        """List files in the workspace (non-recursive top-level + common subdirs) for pre-check context."""
        workspace = self._run_dir_path()
        files: list[str] = []
        try:
            for p in workspace.iterdir():
                if p.name.startswith('.') or p.name == '__pycache__':
                    continue
                if p.is_file():
                    files.append(str(p.relative_to(workspace)))
                elif p.is_dir():
                    # One level deep for common subdirs
                    for child in p.iterdir():
                        if child.is_file():
                            files.append(str(child.relative_to(workspace)))
        except Exception as e:
            self.logger.debug('Workspace scan failed (non-critical): %s', e)
        return files[:100]  # cap to avoid huge lists

    def _assess_readiness(self, task_description: str) -> dict[str, Any]:
        """Use LLM to assess whether the task is ready to plan or needs prerequisite work."""
        workspace_files = self._scan_workspace_files()
        files_str = (
            '\n'.join(f"  - {f}" for f in workspace_files)
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
            reply = self.agent.llm.query(dialog)
            self._emit('Planner', 'thought', f"[Pre-check] {reply.content or ''}")
            raw = _extract_json_from_content(reply.content or '')
            if raw:
                return json.loads(raw)
        except Exception as e:
            self.logger.warning(
                '[Pre-check] Assessment failed, proceeding to plan: %s', e
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
        """Run prerequisite tasks via DirectSolver and collect extracted context."""
        if not prerequisites:
            return ''

        workspaces = self._run_dir_path() / 'workspaces' / task_id
        pre_check_dir = workspaces / 'pre_check'
        pre_check_dir.mkdir(parents=True, exist_ok=True)

        solver = DirectSolver(self.agent, self.config)
        solver.set_run_dir(pre_check_dir)

        collected_context: list[str] = []
        for i, prereq in enumerate(prerequisites):
            # Reset loop-detection per prerequisite so independent tasks don't
            # block each other.
            if hasattr(self.agent, '_tool_guard'):
                self.agent._tool_guard.reset_loop_history()

            prereq_type = prereq.get('type', 'unknown')
            description = prereq.get('description', '')
            target = prereq.get('target', '')

            self.logger.info(
                '[Pre-check] Running prerequisite %d/%d: [%s] %s',
                i + 1,
                len(prerequisites),
                prereq_type,
                description[:80],
            )
            self._emit(
                'Planner',
                'thought',
                f"[Pre-check] Prerequisite {i + 1}/{len(prerequisites)}: {description}",
            )
            self._emit('Planner', 'exp_run', 'DirectSolver (pre-check)')

            # Build a focused prompt for the prerequisite task
            if prereq_type == 'parse_pdf':
                prompt = (
                    f"Parse the following PDF file and extract all relevant information for planning a research task. "
                    f"Use mat_doc_extract_material_data_from_pdf (MCP tool) as the primary method. "
                    f"Extract: crystal structures, computational methods, software used, key parameters "
                    f"(k-mesh, cutoff, functional, pseudopotentials), target properties/results. "
                    f"File: {target}. "
                    f"After extraction, summarize all findings clearly."
                )
            elif prereq_type == 'parse_files':
                prompt = (
                    f"Read and parse the following files to extract information needed for planning: {target}. "
                    f"For PDFs, use mat_doc MCP tools first. Summarize key findings."
                )
            elif prereq_type == 'search_info':
                prompt = (
                    f"Search for the following information needed before planning: {description}. "
                    f"Target: {target}. Use mat_sn tools for literature search if needed. Summarize findings concisely."
                )
            else:
                prompt = (
                    f"Complete this prerequisite task: {description}. Target: {target}."
                )

            try:
                precheck_task = self._build_task_with_dialog_history(
                    prompt, f"{task_id}_precheck_{i}"
                )
                if precheck_task is not None:
                    result = solver.run(task=precheck_task)
                else:
                    result = solver.run(prompt, task_id=f"{task_id}_precheck_{i}")
                summary = self._summarize_solver_result(result, max_len=2000)
                collected_context.append(
                    f"[Prerequisite {i + 1}: {prereq_type}] {description}\nResult: {summary}"
                )
                self._append_journal(
                    state,
                    task_id,
                    phase='pre_check',
                    title=f"Prerequisite {i + 1}/{len(prerequisites)} completed",
                    body=f"type={prereq_type}\n{description}\n\nResult summary:\n{summary}",
                )
                self._append_literature_index(
                    state,
                    task_id,
                    source=f"precheck:{prereq_type}",
                    text=summary,
                )
            except Exception as e:
                self.logger.warning('[Pre-check] Prerequisite %d failed: %s', i + 1, e)
                collected_context.append(
                    f"[Prerequisite {i + 1}: {prereq_type}] FAILED: {e}"
                )
                self._append_journal(
                    state,
                    task_id,
                    phase='pre_check',
                    title=f"Prerequisite {i + 1}/{len(prerequisites)} failed",
                    body=f"type={prereq_type}\n{description}\n\nError: {e}",
                )

        return '\n\n'.join(collected_context)

    def _phase_pre_check(
        self, state: dict[str, Any], goal: str, task_id: str
    ) -> dict[str, Any]:
        """Assess readiness before planning. Run prerequisites if needed. Transition → planning."""
        self.logger.info('[Planner] Pre-check: assessing readiness for: %s', goal[:80])
        self._emit('Planner', 'phase_change', {'from': 'init', 'to': 'pre_check'})

        assessment = self._assess_readiness(goal)
        prerequisites = assessment.get('prerequisites') or []
        reasoning = assessment.get('reasoning', '')

        if assessment.get('ready_to_plan') and not prerequisites:
            self.logger.info('[Pre-check] Ready to plan: %s', reasoning)
            self._emit('Planner', 'thought', f"[Pre-check] Ready to plan. {reasoning}")
            self._append_journal(
                state,
                task_id,
                phase='pre_check',
                title='Ready to plan',
                body=reasoning,
            )
            state['phase'] = 'planning'
            self._emit(
                'Planner', 'phase_change', {'from': 'pre_check', 'to': 'planning'}
            )
            return state

        # Not ready — run prerequisites
        self.logger.info(
            '[Pre-check] Prerequisites needed (%d): %s', len(prerequisites), reasoning
        )
        self._emit(
            'Planner',
            'thought',
            f"[Pre-check] Not ready to plan yet. {reasoning}\n"
            f"Running {len(prerequisites)} prerequisite(s) first...",
        )
        self._append_journal(
            state,
            task_id,
            phase='pre_check',
            title='Prerequisites required',
            body=f"{reasoning}\ncount={len(prerequisites)}",
        )

        pre_check_context = self._execute_prerequisites(
            prerequisites, goal, task_id, state
        )
        state['pre_check_context'] = pre_check_context

        self.logger.info('[Pre-check] Prerequisites completed. Proceeding to planning.')
        self._emit(
            'Planner',
            'thought',
            '[Pre-check] Prerequisites completed. Now generating plan with enriched context.',
        )
        self._append_journal(
            state,
            task_id,
            phase='pre_check',
            title='Prerequisites completed',
            body=pre_check_context[:2000],
        )
        state['phase'] = 'planning'
        self._emit('Planner', 'phase_change', {'from': 'pre_check', 'to': 'planning'})
        return state

    # ------------------------------------------------------------------
    # Planning phase
    # ------------------------------------------------------------------

    def _phase_planning(
        self, state: dict[str, Any], goal: str, task_id: str
    ) -> dict[str, Any]:
        """Generate initial plan. Transition → preflight or failed.

        When the LLM returns a REFUSED plan (e.g. CRP violation), we attempt
        up to 2 auto-revisions asking the LLM to fix the issue. Only fail
        if all retries are exhausted.
        """
        plan = state.get('plan')
        if _is_deg_plan(plan) and state.get('goal') == goal:
            # Existing valid plan (e.g. resumed) — but first enforce
            # manuscript consistency to avoid resuming stale wrong-profile plans.
            checked = self._enforce_manuscript_plan_consistency(plan, goal)
            if checked.get('status') != 'REFUSED':
                state['plan'] = checked
                state['phase'] = 'preflight'
                return state
            self.logger.info(
                '[Planner] Existing plan rejected by consistency guard; regenerating. reason=%s',
                checked.get('refusal_reason', 'unknown'),
            )

        # Enrich goal with pre-check context if available
        enriched_goal = goal
        pre_check_context = state.get('pre_check_context', '')
        if pre_check_context:
            enriched_goal = (
                f"{goal}\n\n"
                f"# PRE-CHECK RESULTS (extracted context — use this to make a more precise plan)\n"
                f"{pre_check_context}"
            )

        self.logger.info('[Planner] Designing flight plan for: %s', goal[:80])
        plan = self._generate_plan(enriched_goal)

        # ── Auto-revision loop for REFUSED plans ──
        max_auto_fix = 2
        for attempt in range(max_auto_fix):
            if plan.get('status') != 'REFUSED':
                break
            reason = plan.get('refusal_reason', 'Unknown')
            self.logger.warning(
                '[CRP] Plan refused (attempt %d/%d): %s',
                attempt + 1,
                max_auto_fix,
                reason,
            )
            self._emit(
                'Planner',
                'thought',
                f"[CRP] Plan was refused: {reason}. Auto-revising (attempt {attempt + 1}/{max_auto_fix})...",
            )
            # Ask LLM to fix the plan itself. Use targeted feedback for
            # manuscript consistency failures.
            if 'manuscript_consistency' in str(reason):
                feedback = (
                    f"The plan was REFUSED for this reason: {reason}\n"
                    'Fix manuscript workflow consistency now:\n'
                    '1) Keep manuscript profile consistent with USER_INTENT (e.g., patent task -> --template patent / --profile patent).\n'
                    '2) Do not mix review sections into a patent plan.\n'
                    '3) Include per-section writing goals for all required patent sections: '
                    'Technical Field, Background Art, Summary of Invention, Detailed Description, Claims, Abstract.\n'
                    '4) Keep validate_content and assemble_manuscript goals.\n'
                    'Return the revised plan in the same JSON schema.'
                )
            else:
                allow_str = self._registry.software_list_str()
                block_str = self._registry.crp_block_str()
                feedback = (
                    f"The plan was REFUSED for this reason: {reason}\n"
                    f"Please fix the offending steps to use ONLY CRP-allowed software "
                    f"({allow_str}). Do NOT use or mention {block_str} "
                    f"as execution targets. You may reference them only in mapping descriptions "
                    f"(e.g., 'mapped from VASP → ABACUS'). "
                    f"Return the revised plan in the same JSON schema."
                )
            plan = self._revise_plan(goal, plan, feedback)

        state['plan'] = plan
        if plan.get('status') == 'REFUSED':
            reason = plan.get('refusal_reason', 'Unknown')
            self.logger.warning(
                '[CRP] Mission refused after %d auto-fix attempts: %s',
                max_auto_fix,
                reason,
            )
            state['phase'] = 'failed'
            state['fail_reason'] = reason
        else:
            state['phase'] = 'preflight'
        self._emit(
            'Planner', 'phase_change', {'from': 'planning', 'to': state['phase']}
        )
        return state

    def _phase_preflight(
        self, state: dict[str, Any], goal: str, task_id: str
    ) -> dict[str, Any]:
        """Human confirmation loop (or auto-pass). Transition → executing or aborted/failed."""
        plan = state['plan']
        # Detailed plan report (cost, risks, alternatives)
        self._print_plan_report(plan)

        if self.human_check:
            while True:
                fid = plan.get('fidelity_level', '')
                header = f"[Planner] {plan.get('strategy_name', '')}" + (
                    f" (fidelity: {fid})" if fid else ''
                )
                print(f"\033[92m{header}\033[0m")
                print('-' * 50)
                step_lines = [header, '-' * 50]
                for s in plan.get('steps', []):
                    cost = f"[{s.get('compute_cost', '?')}]"
                    stype = (
                        f" ({s.get('step_type', 'normal')})"
                        if s.get('step_type') == 'skill_evolution'
                        else ''
                    )
                    status_tag = ' [DONE]' if s.get('status') == 'done' else ''
                    line = f"  {s.get('step_id')}. {cost:10}{stype} {s.get('intent')}{status_tag}"
                    print(line)
                    step_lines.append(line)
                step_lines.append('-' * 50)
                print('-' * 50)
                self._emit('Planner', 'thought', '\n'.join(step_lines))
                ans = self._ask_human(
                    "Type 'go' to execute, 'abort' to quit, or describe changes to revise the plan.",
                    mode='block',
                )
                ans_lower = ans.strip().lower()
                if ans_lower == 'go':
                    break
                if ans_lower == 'abort':
                    state['phase'] = 'aborted'
                    self._emit(
                        'Planner',
                        'phase_change',
                        {'from': 'preflight', 'to': 'aborted'},
                    )
                    return state
                if not ans.strip():
                    continue
                self.logger.info(
                    '[Planner] Revising plan from user feedback: %s', ans[:100]
                )
                plan = self._revise_plan(goal, plan, ans)
                state['plan'] = plan
                self._save_state(task_id, state)
                if plan.get('status') == 'REFUSED':
                    # Auto-fix attempt: ask LLM to resolve the CRP issue
                    reason = plan.get('refusal_reason', 'Unknown')
                    self.logger.warning(
                        '[CRP] Revised plan refused: %s — attempting auto-fix', reason
                    )
                    self._emit(
                        'Planner',
                        'thought',
                        f"[CRP] Revised plan refused: {reason}. Auto-fixing...",
                    )
                    fix_feedback = (
                        f"The revised plan was REFUSED: {reason}\n"
                        f"Fix the offending steps to use ONLY CRP-allowed software. "
                        f"Return the corrected plan JSON."
                    )
                    plan = self._revise_plan(goal, plan, fix_feedback)
                    state['plan'] = plan
                    self._save_state(task_id, state)
                    if plan.get('status') == 'REFUSED':
                        self.logger.warning(
                            '[CRP] Auto-fix failed. Plan still refused: %s',
                            plan.get('refusal_reason'),
                        )
                        state['phase'] = 'failed'
                        state['fail_reason'] = plan.get('refusal_reason')
                        self._emit(
                            'Planner',
                            'phase_change',
                            {'from': 'preflight', 'to': 'failed'},
                        )
                        return state
        else:
            # human_check off: emit step list to frontend
            fid = plan.get('fidelity_level', '')
            header = f"[Planner] {plan.get('strategy_name', '')}" + (
                f" (fidelity: {fid})" if fid else ''
            )
            step_lines = [header, '-' * 50]
            for s in plan.get('steps', []):
                cost = f"[{s.get('compute_cost', '?')}]"
                stype = (
                    f" ({s.get('step_type', 'normal')})"
                    if s.get('step_type') == 'skill_evolution'
                    else ''
                )
                step_lines.append(
                    f"  {s.get('step_id')}. {cost:10}{stype} {s.get('intent')}"
                )
            step_lines.append('-' * 50)
            self._emit('Planner', 'thought', '\n'.join(step_lines))

        state['phase'] = 'executing'
        self._emit('Planner', 'phase_change', {'from': 'preflight', 'to': 'executing'})
        return state

    def _execute_single_step_for_batch(
        self,
        step: dict[str, Any],
        state: dict[str, Any],
        task_id: str,
        workspaces: Path,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Adapter that wraps ``_execute_single_step`` for ``BatchExecutor``.

        BatchExecutor expects ``func(**kwargs) -> tuple[output, info]``.
        ``_execute_single_step`` returns a single dict, so we wrap it as
        ``(result_dict, {})``.
        """
        result = self._execute_single_step(step, state, task_id, workspaces)
        return result, {}

    def _phase_executing(self, state: dict[str, Any], task_id: str) -> dict[str, Any]:
        """Windowed execution: run next step(s) **concurrently**, observe results, check replan triggers.

        Steps within the same execution window (whose dependencies are all
        resolved) are independent by definition.  We feed them into the shared
        ``BatchExecutor`` so that I/O-bound work (remote calculation submission,
        PDF parsing, etc.) runs in true parallel.

        Executes one window (1-N steps) per call and returns updated state.
        Transitions → replanning | completed | failed.
        """
        plan = state['plan']
        workspaces = self._run_dir_path() / 'workspaces' / task_id
        workspaces.mkdir(parents=True, exist_ok=True)

        window = self._get_next_execution_window(plan)
        if not window:
            # No more pending steps
            if self._is_goal_achieved(state):
                state['phase'] = 'completed'
                self._emit(
                    'Planner', 'phase_change', {'from': 'executing', 'to': 'completed'}
                )
            else:
                # Steps remain but are blocked (unresolvable deps or all skipped/failed)
                state['phase'] = 'failed'
                state['fail_reason'] = (
                    'No executable steps remaining (dependency deadlock or all failed)'
                )
                self._emit(
                    'Planner', 'phase_change', {'from': 'executing', 'to': 'failed'}
                )
            return state

        # ----- Concurrent execution via BatchExecutor -----
        # Build ExecutionTask list for all steps in the current window.
        # The window_size naturally caps the concurrency.
        batch_tasks: list[ExecutionTask] = []
        for step in window:
            batch_tasks.append(
                ExecutionTask(
                    task_id=str(step.get('step_id', 0)),
                    func=self._execute_single_step_for_batch,
                    kwargs={
                        'step': step,
                        'state': state,
                        'task_id': task_id,
                        'workspaces': workspaces,
                    },
                    meta={'step': step},
                )
            )

        executor = BatchExecutor(
            max_workers=self._planner_max_workers,
            rate_limit=self._rate_limit,
        )
        exec_results = executor.execute_batch(batch_tasks)

        # ----- Process results (in original window order) -----
        for res, step in zip(exec_results, window):
            if res.status == 'success':
                step_result = res.output  # dict returned by _execute_single_step
            else:
                # BatchExecutor caught an unexpected exception
                step_result = {
                    'step_id': step.get('step_id', 0),
                    'status': 'failed',
                    'fallback_succeeded': False,
                    'new_skill_registered': False,
                    'skill_path': '',
                    'result_summary': res.error or 'Executor-level failure',
                    'replan_requested': False,
                    'replan_reason': '',
                }

            # Update step status in plan
            if step_result['status'] == 'done':
                step['status'] = 'done'
            elif step_result['status'] == 'skipped':
                step['status'] = 'done'  # treat skipped as done so we don't retry
            elif step_result['status'] == 'failed':
                step['status'] = (
                    'failed'  # prevent re-picking by _get_next_execution_window
                )

            # Record in history
            history_entry: dict[str, Any] = {
                'step': step_result['step_id'],
                'tool_name': step.get('tool_name', ''),
                'intent': step.get('intent', '')[:200],
            }
            if step_result['status'] == 'failed':
                history_entry['error'] = step_result['result_summary']
            else:
                history_entry['result_summary'] = step_result['result_summary']
            if step_result.get('new_skill_registered'):
                history_entry['new_skill_registered'] = True
                history_entry['skill_path'] = step_result.get('skill_path', '')
            state['history'].append(history_entry)
            self._save_state(task_id, state)

            # --- Observe: check if replanning is needed ---
            should_replan, reason = self._needs_replanning(state, step_result)
            if should_replan:
                state['phase'] = 'replanning'
                state['replan_reason'] = reason
                self._emit(
                    'Planner',
                    'replan_triggered',
                    {'reason': reason, 'after_step': step_result['step_id']},
                )
                self._emit(
                    'Planner', 'phase_change', {'from': 'executing', 'to': 'replanning'}
                )
                return state

            # Handle unrecoverable failure: ask human for guidance
            if step_result['status'] == 'failed' and not should_replan:
                step_id = step_result.get('step_id', '?')
                summary = step_result.get('result_summary', 'unknown error')[:200]
                ans = self._ask_human(
                    f"Step {step_id} failed: {summary}\n"
                    f"Options:\n"
                    f"  'skip'    — skip this step and continue with the next\n"
                    f"  'retry'   — provide modified suggestions for retrying\n"
                    f"  'abort'   — abort the mission\n"
                    f"  Or describe modifications/suggestions.\n"
                    f"(Default: 'skip' — skip this step and continue)",
                    mode='timeout',
                    timeout_sec=120,
                    default='skip',
                )
                ans_lower = ans.strip().lower()
                if ans_lower == 'abort':
                    state['phase'] = 'aborted'
                    self._emit(
                        'Planner',
                        'phase_change',
                        {'from': 'executing', 'to': 'aborted'},
                    )
                    return state
                elif ans_lower in ('skip', ''):
                    self.logger.info(
                        '[Planner] Human chose to skip failed step %s', step_id
                    )
                    # step is already marked as "failed", execution continues
                elif ans_lower == 'retry' or (
                    ans_lower not in ('skip', 'abort', '') and len(ans) > 2
                ):
                    # User gave suggestions — trigger a replan with their feedback
                    feedback = (
                        ans
                        if ans_lower != 'retry'
                        else 'Please retry the failed step with a different approach.'
                    )
                    state['phase'] = 'replanning'
                    state['replan_reason'] = (
                        f"Human feedback after step {step_id} failure: {feedback}"
                    )
                    self._emit(
                        'Planner',
                        'replan_triggered',
                        {'reason': state['replan_reason'], 'after_step': step_id},
                    )
                    self._emit(
                        'Planner',
                        'phase_change',
                        {'from': 'executing', 'to': 'replanning'},
                    )
                    return state

            # Handle conditional branching
            branch = step.get('conditional_branch')
            if branch:
                if step_result['status'] == 'done' and branch.get('if_success'):
                    # Skip steps between current and the branch target
                    self._skip_to_step(plan, branch['if_success'])
                elif step_result['status'] == 'failed' and branch.get('if_fail'):
                    self._skip_to_step(plan, branch['if_fail'])

        # After window: check if all done
        if self._is_goal_achieved(state):
            state['phase'] = 'completed'
            self._emit(
                'Planner', 'phase_change', {'from': 'executing', 'to': 'completed'}
            )
        # else: stay in "executing" — next call will pick next window

        return state

    def _skip_to_step(self, plan: dict[str, Any], target_step_id: int) -> None:
        """Mark all pending steps before target_step_id as 'skipped' (conditional branch jump)."""
        for s in plan.get('steps', []):
            if s.get('step_id') == target_step_id:
                break
            if s.get('status') == 'pending':
                s['status'] = 'done'  # skip

    def _phase_replanning(
        self, state: dict[str, Any], goal: str, task_id: str
    ) -> dict[str, Any]:
        """Mid-flight replanning: feed results to planner LLM, revise remaining steps.

        Transition → executing (revised plan) or failed (revision refused).
        """
        self.logger.info(
            '[Planner] Mid-flight replan #%d: %s',
            state['replan_count'] + 1,
            state.get('replan_reason', ''),
        )
        old_steps = [s.copy() for s in state.get('plan', {}).get('steps', [])]

        revised_plan = self._replan_from_results(state, goal)

        if revised_plan.get('status') == 'REFUSED':
            self.logger.warning(
                '[CRP] Revised plan refused: %s', revised_plan.get('refusal_reason')
            )
            # Fall back to continuing with current plan
            state['phase'] = 'executing'
            self._emit(
                'Planner', 'phase_change', {'from': 'replanning', 'to': 'executing'}
            )
            return state

        # Preserve done status for already-completed steps
        done_ids = {s['step_id'] for s in old_steps if s.get('status') == 'done'}
        for s in revised_plan.get('steps', []):
            if s.get('step_id') in done_ids:
                s['status'] = 'done'

        state['plan'] = revised_plan
        state['replan_count'] = state.get('replan_count', 0) + 1
        state['phase'] = 'executing'

        # Emit plan revision event
        new_steps = revised_plan.get('steps', [])
        self._emit(
            'Planner',
            'plan_revised',
            {
                'replan_count': state['replan_count'],
                'reason': state.get('replan_reason', ''),
                'old_step_count': len(old_steps),
                'new_step_count': len(new_steps),
            },
        )
        self._emit('Planner', 'phase_change', {'from': 'replanning', 'to': 'executing'})

        # Print the revised plan
        self._print_plan_report(revised_plan)
        fid = revised_plan.get('fidelity_level', '')
        header = (
            f"[Planner] REVISED PLAN #{state['replan_count']}: {revised_plan.get('strategy_name', '')}"
            + (f" (fidelity: {fid})" if fid else '')
        )
        step_lines = [header, '-' * 50]
        for s in revised_plan.get('steps', []):
            cost = f"[{s.get('compute_cost', '?')}]"
            stype = (
                f" ({s.get('step_type', 'normal')})"
                if s.get('step_type') == 'skill_evolution'
                else ''
            )
            status_tag = ' [DONE]' if s.get('status') == 'done' else ''
            step_lines.append(
                f"  {s.get('step_id')}. {cost:10}{stype} {s.get('intent')}{status_tag}"
            )
        step_lines.append('-' * 50)
        self.logger.info('\n'.join(step_lines))
        self._emit('Planner', 'thought', '\n'.join(step_lines))

        return state

    # ------------------------------------------------------------------
    # Execution summary: honest reporting of all results and failures
    # ------------------------------------------------------------------

    def _build_execution_summary(self, state: dict[str, Any]) -> dict[str, Any]:
        """Build a comprehensive execution summary that honestly reports:
        - All failed steps with error details
        - All approximations and simplifications made during execution
        - All original, unprocessed results from each step
        - Replan history and reasons

        This summary is intended for the final report and must be truthful.
        """
        plan = state.get('plan') or {}
        steps = plan.get('steps', [])
        history = state.get('history', [])

        # Categorize steps
        completed_steps = []
        failed_steps = []
        skipped_steps = []
        for s in steps:
            status = s.get('status', 'pending')
            step_info = {
                'step_id': s.get('step_id'),
                'intent': s.get('intent', ''),
                'compute_cost': s.get('compute_cost', ''),
                'status': status,
            }
            if status == 'done':
                completed_steps.append(step_info)
            elif status == 'failed':
                failed_steps.append(step_info)
            else:
                skipped_steps.append(step_info)

        # Collect detailed results from history (item by item)
        step_results_detail = []
        approximations_and_simplifications = []
        for entry in history:
            detail = {
                'step_id': entry.get('step', '?'),
                'intent': entry.get('intent', ''),
                'tool_name': entry.get('tool_name', ''),
            }
            if entry.get('error'):
                detail['status'] = 'FAILED'
                detail['error'] = entry['error']
            else:
                detail['status'] = 'OK'
                detail['result_summary'] = entry.get('result_summary', '')
            if entry.get('new_skill_registered'):
                detail['new_skill_registered'] = True
                detail['skill_path'] = entry.get('skill_path', '')
            step_results_detail.append(detail)

            # Detect approximations: fallback usage, CRP redirections, coarse settings
            result_text = (entry.get('result_summary', '') or '').lower()
            if 'fallback' in result_text:
                approximations_and_simplifications.append(
                    f"Step {entry.get('step', '?')}: Executed via fallback strategy (original approach failed)."
                )
            if 'coarse' in result_text or 'screening' in result_text:
                approximations_and_simplifications.append(
                    f"Step {entry.get('step', '?')}: Used coarse/screening-level settings (not production quality)."
                )

        # Replan history
        replan_info = {
            'replan_count': state.get('replan_count', 0),
            'max_replans': self.max_replans,
        }

        summary = {
            'overall_status': state.get('phase', 'unknown'),
            'total_steps': len(steps),
            'completed_count': len(completed_steps),
            'failed_count': len(failed_steps),
            'skipped_count': len(skipped_steps),
            'completed_steps': completed_steps,
            'failed_steps': failed_steps,
            'skipped_steps': skipped_steps,
            'step_results_detail': step_results_detail,
            'approximations_and_simplifications': approximations_and_simplifications,
            'replan_info': replan_info,
            'fail_reason': state.get('fail_reason', ''),
        }

        # Emit summary to frontend
        self._emit('Planner', 'execution_summary', summary)
        return summary

    # ------------------------------------------------------------------
    # Main entry point: state-machine driven run()
    # ------------------------------------------------------------------

    def run(
        self,
        task_description: str = '',
        task_id: str = 'planner_task',
        task: Optional[TaskInstance] = None,
    ) -> dict[str, Any]:
        """State-machine driven execution: PreCheck → Planning → PreFlight → Executing ⇄ Replanning → Completed."""
        if task is not None:
            task_description = task.description or ''
            task_id = task.task_id
            self._task_with_history = task
        else:
            self._task_with_history = None
        # Initialize state (loads persisted state or creates fresh)
        state = self._initialize_state(task_description, task_id)

        # Create shared DirectSolver instance
        self._solver = DirectSolver(self.agent, self.config)
        if self.run_dir is not None:
            self._solver.set_run_dir(self.run_dir)

        self.logger.info(
            '[Planner] State machine started (phase=%s, replan_count=%d)',
            state['phase'],
            state.get('replan_count', 0),
        )

        # Main state-machine loop
        while state['phase'] not in ('completed', 'failed', 'aborted'):
            phase = state['phase']

            if phase == 'pre_check':
                state = self._phase_pre_check(state, task_description, task_id)

            elif phase == 'planning':
                state = self._phase_planning(state, task_description, task_id)

            elif phase == 'preflight':
                state = self._phase_preflight(state, task_description, task_id)

            elif phase == 'executing':
                state = self._phase_executing(state, task_id)

            elif phase == 'replanning':
                if state.get('replan_count', 0) >= self.max_replans:
                    self.logger.warning(
                        '[Planner] Max replan limit (%d) reached', self.max_replans
                    )
                    self._emit(
                        'Planner',
                        'thought',
                        f"[Planner] Replan limit ({self.max_replans}) reached. Asking human for guidance...",
                    )
                    ans = self._ask_human(
                        f"Replan limit ({self.max_replans}) reached. Options:\n"
                        f"  'continue' — force continue with current plan\n"
                        f"  'retry'    — allow one more replan attempt\n"
                        f"  'abort'    — abort the mission\n"
                        f"  Or describe changes/suggestions for the remaining plan.\n"
                        f"(Default: 'continue' — skip replanning, proceed with current plan)",
                        mode='timeout',
                        timeout_sec=120,
                        default='continue',
                    )
                    ans_lower = ans.strip().lower()
                    if ans_lower in ('skip', 'continue', ''):
                        self.logger.info(
                            '[Planner] Human chose to continue with current plan'
                        )
                        state['phase'] = 'executing'
                    elif ans_lower == 'abort':
                        state['phase'] = 'aborted'
                        self._emit(
                            'Planner',
                            'phase_change',
                            {'from': 'replanning', 'to': 'aborted'},
                        )
                    elif ans_lower == 'retry':
                        # Grant one extra replan attempt
                        self.logger.info('[Planner] Human granted one extra replan')
                        state['replan_count'] = self.max_replans - 1  # allow one more
                        state = self._phase_replanning(state, task_description, task_id)
                    else:
                        # User gave custom feedback — use it as a revision prompt
                        self.logger.info(
                            '[Planner] Human gave revision feedback: %s', ans[:100]
                        )
                        state['replan_reason'] = f"Human feedback: {ans}"
                        state['replan_count'] = self.max_replans - 1  # allow one more
                        state = self._phase_replanning(state, task_description, task_id)
                else:
                    state = self._phase_replanning(state, task_description, task_id)

            else:
                self.logger.error('[Planner] Unknown phase: %s, aborting', phase)
                state['phase'] = 'failed'
                state['fail_reason'] = f"Unknown phase: {phase}"

            self._save_state(task_id, state)

        self.logger.info(
            '[Planner] State machine finished (phase=%s, replans=%d, steps_done=%d)',
            state['phase'],
            state.get('replan_count', 0),
            sum(
                1
                for s in state.get('plan', {}).get('steps', [])
                if s.get('status') == 'done'
            ),
        )
        self._task_with_history = None
        self._solver = None  # cleanup

        # Build comprehensive execution summary
        execution_summary = self._build_execution_summary(state)
        state['execution_summary'] = execution_summary

        # Build return dict compatible with old API
        result: dict[str, Any] = {
            'status': state['phase'],
            'plan': state.get('plan'),
            'state': state,
            'execution_summary': execution_summary,
        }
        if state.get('fail_reason'):
            result['reason'] = state['fail_reason']
        return result
