"""ResearchPlanner: deterministic flight-plan execution under CRP (Computational Resource Protocol).

- Loads industrial-grade system prompt from prompts/planner_system_prompt.txt.
- Injects hard-coded CRP (license firewall, tool stack); validates plan JSON; enforces human-in-the-loop for high-cost steps.
- Persists state to research_state.json; supports resume.
"""

import json
import logging
import shutil
import sys
from pathlib import Path
from typing import Any, Callable

from evomaster.core.exp import BaseExp
from evomaster.utils.types import Dialog, SystemMessage, UserMessage

from .direct_solver import DirectSolver, _get_available_tool_names

try:
    from ..exp import SkillEvolutionExp
    _HAS_EVOLUTION = True
except ImportError:
    SkillEvolutionExp = None
    _HAS_EVOLUTION = False

# === CRP: immutable protocol (no config override) ===
INTERNAL_CRP_CONTEXT = {
    "Protocol_Name": "MatMaster_CRP_v1.0",
    "License_Registry": {
        "Allow_List": ["ABACUS", "LAMMPS", "DPA", "CP2K", "OpenBabel", "mat_abacus", "mat_dpa", "mat_sg", "mat_doc", "mat_sn"],
        "Block_List": ["VASP", "Gaussian", "CASTEP", "Wien2k"],
        "Policy": "Strict_Block_Execution",
    },
    "Tool_Stack": {
        "Preferred_DFT": "ABACUS",
        "Preferred_MLP": "DPA",
        "Preferred_MD": "LAMMPS",
    },
}


def _get_mat_master_config(config) -> dict:
    try:
        if hasattr(config, "model_dump"):
            d = config.model_dump()
        else:
            d = dict(config) if config else {}
        return d.get("mat_master") or {}
    except Exception:
        return {}


def _is_deg_plan(plan: Any) -> bool:
    """True if plan is a DEG (has 'steps' or 'execution_graph' with step_id)."""
    if not isinstance(plan, dict):
        return False
    steps = plan.get("steps") or plan.get("execution_graph")
    return isinstance(steps, list) and len(steps) > 0 and isinstance(steps[0].get("step_id"), int)


def _normalize_step(step: dict[str, Any]) -> dict[str, Any]:
    """Map execution_graph schema to internal steps schema (goal-oriented)."""
    intensity = (step.get("compute_intensity") or step.get("compute_cost") or "MEDIUM").upper()
    if intensity == "LOW":
        cost = "Low"
    elif intensity == "HIGH":
        cost = "High"
    else:
        cost = "Medium"
    step_type = (step.get("step_type") or "normal").lower()
    if step_type not in ("normal", "skill_evolution"):
        step_type = "skill_evolution" if step.get("tool_name") == "skill_evolution" else "normal"
    intent = step.get("goal") or step.get("scientific_intent") or step.get("intent", "")
    return {
        "step_id": step.get("step_id"),
        "step_type": step_type,
        "tool_name": "skill_evolution" if step_type == "skill_evolution" else "",  # only for executor branch
        "intent": intent,
        "compute_cost": cost,
        "requires_human_confirm": step.get("requires_confirmation", step.get("requires_human_confirm", False)),
        "fallback_logic": step.get("fallback_strategy") or step.get("fallback_logic", "None"),
        "status": step.get("status", "pending"),
        "conditional_branch": step.get("conditional_branch"),  # optional: {"if_success": <step_id>, "if_fail": <step_id>}
        "depends_on": step.get("depends_on", []),              # optional: [step_id, ...] that must complete first
    }


def _normalize_plan(plan: dict[str, Any], max_steps: int = 999) -> dict[str, Any]:
    """Ensure plan has 'steps' with internal field names; cap length."""
    graph = plan.get("execution_graph") or plan.get("steps") or []
    plan["steps"] = [_normalize_step(s) for s in graph][:max_steps]
    for s in plan["steps"]:
        s.setdefault("status", "pending")
    return plan


def _plan_to_external_schema(plan: dict[str, Any]) -> dict[str, Any]:
    """Convert internal plan (steps) to prompt schema (execution_graph) for revision."""
    steps = plan.get("steps", [])
    intensity_map = {"Low": "LOW", "Medium": "MEDIUM", "High": "HIGH"}
    execution_graph = []
    for s in steps:
        entry = {
            "step_id": s.get("step_id"),
            "step_type": s.get("step_type", "normal"),
            "goal": s.get("intent", ""),
            "compute_intensity": intensity_map.get(s.get("compute_cost"), "MEDIUM"),
            "requires_confirmation": s.get("requires_human_confirm", False),
            "fallback_strategy": s.get("fallback_logic", "None"),
            "status": s.get("status", "pending"),
        }
        if s.get("conditional_branch"):
            entry["conditional_branch"] = s["conditional_branch"]
        if s.get("depends_on"):
            entry["depends_on"] = s["depends_on"]
        execution_graph.append(entry)
    out = {
        "plan_id": plan.get("plan_id"),
        "status": plan.get("status"),
        "refusal_reason": plan.get("refusal_reason"),
        "strategy_name": plan.get("strategy_name"),
        "fidelity_level": plan.get("fidelity_level", "Production"),
        "execution_graph": execution_graph,
    }
    if plan.get("plan_report"):
        out["plan_report"] = plan["plan_report"]
    return out


def _extract_json_from_content(content: str) -> str | None:
    """Extract first {...} or ```json ... ``` from LLM output."""
    text = (content or "").strip()
    if "```json" in text:
        start = text.find("```json") + 7
        end = text.find("```", start)
        if end > start:
            return text[start:end].strip()
    if "```" in text:
        start = text.find("```") + 3
        end = text.find("```", start)
        if end > start:
            return text[start:end].strip()
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


class ResearchPlanner(BaseExp):
    """Plan-execute under CRP: flight plan (JSON DEG) → validate → optional pre-flight confirm → execute steps via DirectSolver."""

    def __init__(self, agent, config, input_fn=None, output_callback=None):
        super().__init__(agent, config)
        self.logger = logging.getLogger("MatMaster.Planner")
        mat = _get_mat_master_config(config)
        planner_cfg = mat.get("planner") or {}
        self.state_file = planner_cfg.get("state_file", "research_state.json")
        self.max_steps = planner_cfg.get("max_steps", 20)
        self.human_check = planner_cfg.get("human_check_step", True)
        # Dynamic closed-loop planning config
        self.max_replans = planner_cfg.get("max_replans", 3)
        self.window_size = planner_cfg.get("window_size", 1)
        self.auto_replan = planner_cfg.get("auto_replan", True)
        self.replan_on_failure = planner_cfg.get("replan_on_failure", True)
        self.replan_on_new_skill = planner_cfg.get("replan_on_new_skill", True)
        self._input_fn = input_fn  # optional; if set, used instead of stdin in _ask_human (e.g. for WebSocket UI)
        self._output_callback: Callable[[str, str, Any], None] | None = output_callback  # (source, type, content) → frontend
        self._solver: DirectSolver | None = None  # lazily created in run()

    def _emit(self, source: str, event_type: str, content: Any) -> None:
        if self._output_callback:
            self._output_callback(source, event_type, content)

    def _run_dir_path(self) -> Path:
        return Path(self.run_dir) if self.run_dir else Path(".")

    def _state_path(self, task_id: str) -> Path:
        base = self._run_dir_path()
        workspaces = base / "workspaces" / task_id
        workspaces.mkdir(parents=True, exist_ok=True)
        return workspaces / self.state_file

    def _load_state(self, task_id: str) -> dict[str, Any]:
        path = self._state_path(task_id)
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                self.logger.warning("Failed to load state: %s", e)
        return {"goal": "", "plan": None, "history": [], "phase": "planning", "replan_count": 0, "execution_window": 0}

    def _save_state(self, task_id: str, state: dict[str, Any]) -> None:
        path = self._state_path(task_id)
        tmp = path.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
            shutil.move(tmp, path)
        except Exception as e:
            self.logger.error("Failed to save state: %s", e)

    def _build_context_prompt(self, task_description: str) -> str:
        """Build RUNTIME_CONTEXT + REQUEST_CONFIG + USER_INTENT for the planner; includes hardware and license awareness."""
        try:
            import torch
            has_gpu = torch.cuda.is_available()
        except Exception:
            has_gpu = False
        mat = _get_mat_master_config(self.config)
        crp_cfg = mat.get("crp", {})
        active_licenses = crp_cfg.get("licenses", [])
        task_lower = task_description.lower()
        fidelity = "Screening" if any(w in task_lower for w in ["quick", "fast", "screen", "rough", "粗略", "快速", "筛选"]) else "Production"
        context_data = {
            "RUNTIME_CONTEXT": {
                "Hardware": {
                    "Has_GPU": has_gpu,
                    "Compute_Tier": "HPC_Cluster" if has_gpu else "Local_CPU",
                },
                "License_Keys": active_licenses,
                "Internet_Access": True,
            },
            "REQUEST_CONFIG": {
                "Target_Fidelity": fidelity,
                "Max_Steps": self.max_steps,
            },
            "USER_INTENT": task_description,
        }
        tools_preview = _get_available_tool_names(self.agent)
        tools_str = ", ".join(tools_preview[:100]) if tools_preview else "(none)"
        return f"""# CURRENT RUNTIME STATE (JSON)
{json.dumps(context_data, indent=2, ensure_ascii=False)}

# AVAILABLE TOOLS (use exact names in tool_name)
{tools_str}

# INSTRUCTION
Analyze USER_INTENT against RUNTIME_CONTEXT and REQUEST_CONFIG. Generate the research plan in strict JSON format: plan_id, status, strategy_name, fidelity_level, execution_graph (each step has goal and step_type, no tool_name), and plan_report (summary, cost_assessment, risks, alternatives). No other text."""

    def _load_system_prompt(self) -> str:
        """Load planner_system_prompt.txt and append embedded CRP JSON."""
        base = Path(__file__).resolve().parent.parent.parent / "prompts"
        prompt_file = base / "planner_system_prompt.txt"
        if prompt_file.exists():
            raw = prompt_file.read_text(encoding="utf-8")
        else:
            self.logger.warning("planner_system_prompt.txt not found, using minimal fallback")
            raw = "You are a Research Planner. Output a single JSON object with plan_id, status, strategy_name, steps."
        crp_str = json.dumps(INTERNAL_CRP_CONTEXT, indent=2)
        return f"{raw}\n\n# EMBEDDED SYSTEM PROTOCOL (IMMUTABLE)\n{crp_str}"

    def _validate_plan_safety(self, plan: dict[str, Any]) -> dict[str, Any]:
        """Protocol watchdog: block any step whose goal implies Block_List software."""
        if plan.get("status") == "REFUSED":
            return plan
        block = INTERNAL_CRP_CONTEXT["License_Registry"]["Block_List"]
        for step in plan.get("steps", []):
            text = (step.get("intent", "") or step.get("goal", "") or "").lower()
            for sw in block:
                if sw.lower() in text:
                    msg = f"CRP violation: blocked software '{sw}' in step {step.get('step_id')}."
                    self.logger.warning(msg)
                    return {
                        "plan_id": plan.get("plan_id"),
                        "status": "REFUSED",
                        "refusal_reason": f"{msg} Use {INTERNAL_CRP_CONTEXT['Tool_Stack']['Preferred_DFT']} or {INTERNAL_CRP_CONTEXT['Tool_Stack']['Preferred_MLP']}.",
                        "strategy_name": plan.get("strategy_name"),
                        "steps": plan.get("steps", []),
                    }
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
            self._emit("Planner", "thought", reply.content or "")
            raw = _extract_json_from_content(reply.content or "")
            if not raw:
                return {"status": "REFUSED", "refusal_reason": "Planner output contained no valid JSON."}
            plan = json.loads(raw)
        except json.JSONDecodeError as e:
            self.logger.error("Plan JSON parse failed: %s", e)
            return {"status": "REFUSED", "refusal_reason": f"Invalid JSON: {e}"}
        except Exception as e:
            self.logger.error("Plan generation failed: %s", e)
            return {"status": "REFUSED", "refusal_reason": str(e)}
        plan = _normalize_plan(plan, self.max_steps)
        if not plan.get("steps"):
            plan["status"] = "REFUSED"
            plan["refusal_reason"] = plan.get("refusal_reason") or "Plan must have at least one step."
        return self._validate_plan_safety(plan)

    def _revise_plan(self, goal: str, current_plan: dict[str, Any], user_feedback: str) -> dict[str, Any]:
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
            self._emit("Planner", "thought", reply.content or "")
            raw = _extract_json_from_content(reply.content or "")
            if not raw:
                return {**current_plan, "status": "REFUSED", "refusal_reason": "Revision output contained no valid JSON."}
            plan = json.loads(raw)
        except json.JSONDecodeError as e:
            self.logger.error("Revision JSON parse failed: %s", e)
            return {**current_plan, "status": "REFUSED", "refusal_reason": f"Invalid JSON: {e}"}
        except Exception as e:
            self.logger.error("Plan revision failed: %s", e)
            return {**current_plan, "status": "REFUSED", "refusal_reason": str(e)}
        plan = _normalize_plan(plan, self.max_steps)
        if not plan.get("steps"):
            plan["status"] = "REFUSED"
            plan["refusal_reason"] = plan.get("refusal_reason") or "Plan must have at least one step."
        return self._validate_plan_safety(plan)

    def _ask_human(self, prompt: str) -> str:
        if self._input_fn is not None:
            return (self._input_fn(prompt) or "").strip()
        print(f"\033[93m[Planner] {prompt}\033[0m")
        return sys.stdin.readline().strip()

    def _plan_report_text(self, plan: dict[str, Any]) -> str:
        """Build plain-text plan report (no ANSI) for frontend."""
        report = plan.get("plan_report") or {}
        if not report:
            return ""
        lines = []
        summary = report.get("summary") or ""
        if summary:
            lines.append("[Plan Report] Summary:")
            lines.append(f"  {summary}")
        cost_block = report.get("cost_assessment") or {}
        overall = cost_block.get("overall", "")
        per_step = cost_block.get("per_step") or []
        notes = cost_block.get("notes") or ""
        if overall or per_step or notes:
            lines.append("[Plan Report] Cost assessment:")
            if overall:
                lines.append(f"  Overall: {overall}")
            for ps in per_step:
                lines.append(f"  Step {ps.get('step_id')}: {ps.get('cost')} — {ps.get('reason', '')}")
            if notes:
                lines.append(f"  Notes: {notes}")
        risks = report.get("risks") or []
        if risks:
            lines.append("[Plan Report] Risks & mitigation:")
            for r in risks:
                lines.append(f"  Step {r.get('step_id')}: {r.get('risk')}")
                lines.append(f"    → {r.get('mitigation', '')}")
        alts = report.get("alternatives") or []
        if alts:
            lines.append("[Plan Report] Alternatives / fallbacks:")
            for a in alts:
                lines.append(f"  • {a}")
        return "\n".join(lines) if lines else ""

    def _print_plan_report(self, plan: dict[str, Any]) -> None:
        """Print detailed plan report (cost, risks, alternatives) to console and emit to frontend."""
        report = plan.get("plan_report") or {}
        if not report:
            return
        summary = report.get("summary") or ""
        if summary:
            print("\033[96m[Plan Report] Summary:\033[0m")
            print(f"  {summary}")
        cost_block = report.get("cost_assessment") or {}
        overall = cost_block.get("overall", "")
        per_step = cost_block.get("per_step") or []
        notes = cost_block.get("notes") or ""
        if overall or per_step or notes:
            print("\033[96m[Plan Report] Cost assessment:\033[0m")
            if overall:
                print(f"  Overall: {overall}")
            for ps in per_step:
                print(f"  Step {ps.get('step_id')}: {ps.get('cost')} — {ps.get('reason', '')}")
            if notes:
                print(f"  Notes: {notes}")
        risks = report.get("risks") or []
        if risks:
            print("\033[96m[Plan Report] Risks & mitigation:\033[0m")
            for r in risks:
                print(f"  Step {r.get('step_id')}: {r.get('risk')}")
                print(f"    → {r.get('mitigation', '')}")
        alts = report.get("alternatives") or []
        if alts:
            print("\033[96m[Plan Report] Alternatives / fallbacks:\033[0m")
            for a in alts:
                print(f"  • {a}")
        print()
        text = self._plan_report_text(plan)
        if text:
            self._emit("Planner", "thought", text)

    def _execute_fallback(self, step: dict[str, Any], solver: DirectSolver, workspaces: Path) -> bool:
        """Run fallback_strategy for this step; returns True if fallback ran successfully."""
        fallback = step.get("fallback_logic") or step.get("fallback_strategy") or ""
        if not fallback or fallback.strip().lower() == "none":
            return False
        step_id = step.get("step_id", 0)
        step_dir = workspaces / f"step_{step_id}"
        step_dir.mkdir(parents=True, exist_ok=True)
        solver.set_run_dir(step_dir)
        try:
            solver.run(f"Execute fallback: {fallback}", task_id=f"fallback_{step_id}")
            return True
        except Exception as e:
            self.logger.warning("Fallback failed for step %s: %s", step_id, e)
            return False

    # ------------------------------------------------------------------
    # Dynamic Closed-loop Planning: core helper methods
    # ------------------------------------------------------------------

    def _initialize_state(self, task_description: str, task_id: str) -> dict[str, Any]:
        """Load persisted state or create fresh; ensure all required keys exist."""
        state = self._load_state(task_id)
        state.setdefault("goal", task_description)
        state.setdefault("history", [])
        state.setdefault("phase", "planning")
        state.setdefault("replan_count", 0)
        state.setdefault("execution_window", 0)
        # If goal changed, reset to planning phase
        if state.get("goal") != task_description:
            state["goal"] = task_description
            state["phase"] = "planning"
            state["replan_count"] = 0
        return state

    def _is_goal_achieved(self, state: dict[str, Any]) -> bool:
        """Check if the research goal has been met (all steps done or no pending steps remain)."""
        plan = state.get("plan")
        if not plan or not isinstance(plan, dict):
            return False
        steps = plan.get("steps", [])
        if not steps:
            return False
        # Goal is achieved if no pending steps remain (all are done or failed)
        return all(s.get("status") in ("done", "failed") for s in steps)

    def _get_next_execution_window(self, plan: dict[str, Any]) -> list[dict[str, Any]]:
        """Return next batch of pending steps respecting dependencies and window_size."""
        steps = plan.get("steps", [])
        # Both "done" and "failed" steps count as resolved for dependency purposes
        resolved_ids = {s["step_id"] for s in steps if s.get("status") in ("done", "failed")}
        window: list[dict[str, Any]] = []
        for s in steps:
            if s.get("status") != "pending":
                continue
            deps = s.get("depends_on") or []
            if all(d in resolved_ids for d in deps):
                window.append(s)
                if len(window) >= self.window_size:
                    break
        return window

    def _summarize_history(self, history: list[dict[str, Any]]) -> str:
        """Build concise text summary of execution history for replan context."""
        if not history:
            return "(no steps executed yet)"
        lines = []
        for entry in history:
            step_id = entry.get("step", "?")
            if entry.get("error"):
                lines.append(f"  Step {step_id}: FAILED — {entry['error'][:120]}")
            else:
                summary = entry.get("result_summary", "done")[:120]
                lines.append(f"  Step {step_id}: OK — {summary}")
        return "\n".join(lines)

    def _get_remaining_steps_text(self, plan: dict[str, Any]) -> str:
        """Build text listing of remaining (non-done) steps for replan context."""
        steps = plan.get("steps", [])
        remaining = [s for s in steps if s.get("status") != "done"]
        if not remaining:
            return "(none)"
        lines = []
        for s in remaining:
            lines.append(f"  Step {s.get('step_id')}: [{s.get('compute_cost', '?')}] {s.get('intent', '')[:100]}")
        return "\n".join(lines)

    def _llm_replan_check(self, state: dict[str, Any], step_result: dict[str, Any]) -> bool:
        """Lightweight LLM check: ask if the latest result warrants replanning."""
        if not self.auto_replan:
            return False
        history_summary = self._summarize_history(state.get("history", []))
        plan = state.get("plan", {})
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
            messages=[SystemMessage(content="You are a concise research plan evaluator. Output only JSON."), UserMessage(content=prompt)],
            tools=[],
        )
        try:
            reply = self.agent.llm.query(dialog)
            raw = _extract_json_from_content(reply.content or "")
            if raw:
                result = json.loads(raw)
                if result.get("needs_replan"):
                    self.logger.info("[Planner] LLM replan check: %s", result.get("reason", ""))
                    return True
        except Exception as e:
            self.logger.debug("LLM replan check failed (non-critical): %s", e)
        return False

    def _needs_replanning(self, state: dict[str, Any], step_result: dict[str, Any]) -> tuple[bool, str]:
        """Evaluate whether execution should pause for replanning. Returns (should_replan, reason)."""
        if not self.auto_replan:
            return False, ""
        # 1. Step failed and fallback also failed
        if self.replan_on_failure and step_result.get("status") == "failed" and not step_result.get("fallback_succeeded"):
            return True, f"Step {step_result.get('step_id', '?')} failed without successful fallback"
        # 2. Skill evolution produced a new tool — planner should know
        if self.replan_on_new_skill and step_result.get("new_skill_registered"):
            return True, f"New skill registered: {step_result.get('skill_path', 'unknown')}; subsequent steps may benefit"
        # 3. Result contains explicit replan signal
        if step_result.get("replan_requested"):
            return True, step_result.get("replan_reason", "explicit replan requested by executor")
        # 4. LLM-based heuristic (most expensive, checked last)
        if self._llm_replan_check(state, step_result):
            return True, "LLM heuristic detected plan deviation"
        return False, ""

    def _replan_from_results(self, state: dict[str, Any], goal: str) -> dict[str, Any]:
        """Feed execution results back to planner LLM for mid-flight revision."""
        current_plan = state["plan"]
        history_summary = self._summarize_history(state.get("history", []))
        remaining = self._get_remaining_steps_text(current_plan)
        replan_reason = state.get("replan_reason", "execution results require path adjustment")
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
        self, step: dict[str, Any], state: dict[str, Any], task_id: str, workspaces: Path
    ) -> dict[str, Any]:
        """Execute one step (skill_evolution or normal) and return a result dict.

        Result dict keys:
            step_id, status ("done"|"failed"), fallback_succeeded (bool),
            new_skill_registered (bool), skill_path (str), result_summary (str),
            replan_requested (bool), replan_reason (str).
        """
        step_id = step.get("step_id", 0)
        tool_name = step.get("tool_name", "")
        intent = step.get("intent", "")
        fallback = step.get("fallback_logic", "None")
        step_dir = workspaces / f"step_{step_id}"
        step_dir.mkdir(parents=True, exist_ok=True)

        result_info: dict[str, Any] = {
            "step_id": step_id,
            "status": "done",
            "fallback_succeeded": False,
            "new_skill_registered": False,
            "skill_path": "",
            "result_summary": "",
            "replan_requested": False,
            "replan_reason": "",
        }

        steps_list = state.get("plan", {}).get("steps", [])
        self._emit("Planner", "status_stages", {
            "total": len(steps_list), "current": step_id,
            "step_id": step_id, "intent": intent[:120] if intent else "",
        })

        # High-cost confirmation
        if step.get("requires_human_confirm") or step.get("compute_cost") == "High":
            ans = self._ask_human(f"Step {step_id} is HIGH COST. Proceed? (y/n)")
            if ans.strip().lower() != "y":
                result_info["status"] = "skipped"
                result_info["result_summary"] = "skipped_by_user"
                return result_info

        self.logger.info("[Planner] Step %s (goal): %s", step_id, intent[:80])

        assert self._solver is not None, "DirectSolver not initialized"
        solver = self._solver

        # ---- Branch A: skill_evolution ----
        if tool_name == "skill_evolution":
            if not _HAS_EVOLUTION:
                self.logger.warning("[Planner] skill_evolution requested but SkillEvolutionExp not available.")
                print("\033[91m[Planner] Skill Evolution not available. Attempting fallback.\033[0m")
                if self._execute_fallback(step, solver, workspaces):
                    result_info["fallback_succeeded"] = True
                    result_info["result_summary"] = "fallback_after_evo_unavailable"
                else:
                    result_info["status"] = "failed"
                    result_info["result_summary"] = "skill_evolution_unavailable_no_fallback"
                return result_info

            print("\033[95m[Autonomy] Missing capability; initiating Skill Evolution...\033[0m")
            self._emit("Planner", "exp_run", "SkillEvolutionExp")
            evo_exp = SkillEvolutionExp(self.agent, self.config)
            evo_exp.set_run_dir(step_dir)
            try:
                evo_result = evo_exp.run(intent, task_id=f"{task_id}_step_{step_id}_evo")
                if evo_result.get("status") == "completed":
                    print("\033[92m[Autonomy] New skill created. Proceeding.\033[0m")
                    skill_path = evo_result.get("skill_path", "")
                    if skill_path:
                        self._emit("Planner", "status_skill_produced", str(skill_path))
                    result_info["new_skill_registered"] = True
                    result_info["skill_path"] = str(skill_path or "")
                    result_info["result_summary"] = str(skill_path or evo_result)[:200]
                else:
                    print("\033[93m[Autonomy] Evolution failed. Triggering fallback.\033[0m")
                    if self._execute_fallback(step, solver, workspaces):
                        result_info["fallback_succeeded"] = True
                        result_info["result_summary"] = "fallback_after_evo_failed"
                    else:
                        result_info["status"] = "failed"
                        result_info["result_summary"] = f"evo_failed_no_fallback: {str(evo_result)[:150]}"
            except Exception as e:
                self.logger.error("[Planner] Skill evolution step %s failed: %s", step_id, e)
                if self._execute_fallback(step, solver, workspaces):
                    result_info["fallback_succeeded"] = True
                    result_info["result_summary"] = "fallback_after_evo_exception"
                else:
                    result_info["status"] = "failed"
                    result_info["result_summary"] = str(e)[:200]
            return result_info

        # ---- Branch B: goal-oriented execution ----
        self._emit("Planner", "exp_run", "DirectSolver")
        step_prompt = f"Achieve: {intent}. If that fails: {fallback}"
        try:
            solver.set_run_dir(step_dir)
            result = solver.run(step_prompt, task_id=f"{task_id}_step_{step_id}")
            result_info["result_summary"] = str(result)[:200]
        except Exception as e:
            self.logger.error("[Planner] Step %s failed: %s", step_id, e)
            print("\033[93m[Planner] Step failed. Attempting fallback...\033[0m")
            if self._execute_fallback(step, solver, workspaces):
                result_info["fallback_succeeded"] = True
                result_info["result_summary"] = "completed_via_fallback"
            else:
                result_info["status"] = "failed"
                result_info["result_summary"] = str(e)[:200]
                print("\033[91m[Planner] Step and fallback failed.\033[0m")
        return result_info

    # ------------------------------------------------------------------
    # Phase methods for the state machine
    # ------------------------------------------------------------------

    def _phase_planning(self, state: dict[str, Any], goal: str, task_id: str) -> dict[str, Any]:
        """Generate initial plan. Transition → preflight or failed."""
        plan = state.get("plan")
        if _is_deg_plan(plan) and state.get("goal") == goal:
            # Existing valid plan (e.g. resumed) — skip to preflight
            state["phase"] = "preflight"
            return state
        self.logger.info("[Planner] Designing flight plan for: %s", goal[:80])
        plan = self._generate_plan(goal)
        state["plan"] = plan
        if plan.get("status") == "REFUSED":
            reason = plan.get("refusal_reason", "Unknown")
            self.logger.warning("[CRP] Mission refused: %s", reason)
            state["phase"] = "failed"
            state["fail_reason"] = reason
        else:
            state["phase"] = "preflight"
        self._emit("Planner", "phase_change", {"from": "planning", "to": state["phase"]})
        return state

    def _phase_preflight(self, state: dict[str, Any], goal: str, task_id: str) -> dict[str, Any]:
        """Human confirmation loop (or auto-pass). Transition → executing or aborted/failed."""
        plan = state["plan"]
        # Detailed plan report (cost, risks, alternatives)
        self._print_plan_report(plan)

        if self.human_check:
            while True:
                fid = plan.get("fidelity_level", "")
                header = f"[Planner] {plan.get('strategy_name', '')}" + (f" (fidelity: {fid})" if fid else "")
                print(f"\033[92m{header}\033[0m")
                print("-" * 50)
                step_lines = [header, "-" * 50]
                for s in plan.get("steps", []):
                    cost = f"[{s.get('compute_cost', '?')}]"
                    stype = f" ({s.get('step_type', 'normal')})" if s.get("step_type") == "skill_evolution" else ""
                    status_tag = f" [DONE]" if s.get("status") == "done" else ""
                    line = f"  {s.get('step_id')}. {cost:10}{stype} {s.get('intent')}{status_tag}"
                    print(line)
                    step_lines.append(line)
                step_lines.append("-" * 50)
                print("-" * 50)
                self._emit("Planner", "thought", "\n".join(step_lines))
                ans = self._ask_human("Type 'go' to execute, 'abort' to quit, or describe changes to revise the plan.")
                ans_lower = ans.strip().lower()
                if ans_lower == "go":
                    break
                if ans_lower == "abort":
                    state["phase"] = "aborted"
                    self._emit("Planner", "phase_change", {"from": "preflight", "to": "aborted"})
                    return state
                if not ans.strip():
                    continue
                self.logger.info("[Planner] Revising plan from user feedback: %s", ans[:100])
                plan = self._revise_plan(goal, plan, ans)
                state["plan"] = plan
                self._save_state(task_id, state)
                if plan.get("status") == "REFUSED":
                    self.logger.warning("[CRP] Revised plan refused: %s", plan.get("refusal_reason"))
                    state["phase"] = "failed"
                    state["fail_reason"] = plan.get("refusal_reason")
                    self._emit("Planner", "phase_change", {"from": "preflight", "to": "failed"})
                    return state
        else:
            # human_check off: emit step list to frontend
            fid = plan.get("fidelity_level", "")
            header = f"[Planner] {plan.get('strategy_name', '')}" + (f" (fidelity: {fid})" if fid else "")
            step_lines = [header, "-" * 50]
            for s in plan.get("steps", []):
                cost = f"[{s.get('compute_cost', '?')}]"
                stype = f" ({s.get('step_type', 'normal')})" if s.get("step_type") == "skill_evolution" else ""
                step_lines.append(f"  {s.get('step_id')}. {cost:10}{stype} {s.get('intent')}")
            step_lines.append("-" * 50)
            self._emit("Planner", "thought", "\n".join(step_lines))

        state["phase"] = "executing"
        self._emit("Planner", "phase_change", {"from": "preflight", "to": "executing"})
        return state

    def _phase_executing(self, state: dict[str, Any], task_id: str) -> dict[str, Any]:
        """Windowed execution: run next step(s), observe results, check replan triggers.

        Executes one window (1-N steps) per call and returns updated state.
        Transitions → replanning | completed | failed.
        """
        plan = state["plan"]
        workspaces = self._run_dir_path() / "workspaces" / task_id
        workspaces.mkdir(parents=True, exist_ok=True)

        window = self._get_next_execution_window(plan)
        if not window:
            # No more pending steps
            if self._is_goal_achieved(state):
                state["phase"] = "completed"
                self._emit("Planner", "phase_change", {"from": "executing", "to": "completed"})
            else:
                # Steps remain but are blocked (unresolvable deps or all skipped/failed)
                state["phase"] = "failed"
                state["fail_reason"] = "No executable steps remaining (dependency deadlock or all failed)"
                self._emit("Planner", "phase_change", {"from": "executing", "to": "failed"})
            return state

        for step in window:
            step_result = self._execute_single_step(step, state, task_id, workspaces)

            # Update step status in plan
            if step_result["status"] == "done":
                step["status"] = "done"
            elif step_result["status"] == "skipped":
                step["status"] = "done"  # treat skipped as done so we don't retry
            elif step_result["status"] == "failed":
                step["status"] = "failed"  # prevent re-picking by _get_next_execution_window

            # Record in history
            history_entry: dict[str, Any] = {
                "step": step_result["step_id"],
                "tool_name": step.get("tool_name", ""),
                "intent": step.get("intent", "")[:200],
            }
            if step_result["status"] == "failed":
                history_entry["error"] = step_result["result_summary"]
            else:
                history_entry["result_summary"] = step_result["result_summary"]
            if step_result.get("new_skill_registered"):
                history_entry["new_skill_registered"] = True
                history_entry["skill_path"] = step_result.get("skill_path", "")
            state["history"].append(history_entry)
            self._save_state(task_id, state)

            # --- Observe: check if replanning is needed ---
            should_replan, reason = self._needs_replanning(state, step_result)
            if should_replan:
                state["phase"] = "replanning"
                state["replan_reason"] = reason
                self._emit("Planner", "replan_triggered", {"reason": reason, "after_step": step_result["step_id"]})
                self._emit("Planner", "phase_change", {"from": "executing", "to": "replanning"})
                return state

            # Handle unrecoverable failure when auto_replan is off
            if step_result["status"] == "failed" and not self.auto_replan:
                ans = self._ask_human("Step failed and auto-replan is off. Abort mission? (y/n)")
                if ans.strip().lower() == "y":
                    state["phase"] = "aborted"
                    self._emit("Planner", "phase_change", {"from": "executing", "to": "aborted"})
                    return state

            # Handle conditional branching
            branch = step.get("conditional_branch")
            if branch:
                if step_result["status"] == "done" and branch.get("if_success"):
                    # Skip steps between current and the branch target
                    self._skip_to_step(plan, branch["if_success"])
                elif step_result["status"] == "failed" and branch.get("if_fail"):
                    self._skip_to_step(plan, branch["if_fail"])

        # After window: check if all done
        if self._is_goal_achieved(state):
            state["phase"] = "completed"
            self._emit("Planner", "phase_change", {"from": "executing", "to": "completed"})
        # else: stay in "executing" — next call will pick next window

        return state

    def _skip_to_step(self, plan: dict[str, Any], target_step_id: int) -> None:
        """Mark all pending steps before target_step_id as 'skipped' (conditional branch jump)."""
        for s in plan.get("steps", []):
            if s.get("step_id") == target_step_id:
                break
            if s.get("status") == "pending":
                s["status"] = "done"  # skip

    def _phase_replanning(self, state: dict[str, Any], goal: str, task_id: str) -> dict[str, Any]:
        """Mid-flight replanning: feed results to planner LLM, revise remaining steps.

        Transition → executing (revised plan) or failed (revision refused).
        """
        self.logger.info("[Planner] Mid-flight replan #%d: %s", state["replan_count"] + 1, state.get("replan_reason", ""))
        old_steps = [s.copy() for s in state.get("plan", {}).get("steps", [])]

        revised_plan = self._replan_from_results(state, goal)

        if revised_plan.get("status") == "REFUSED":
            self.logger.warning("[CRP] Revised plan refused: %s", revised_plan.get("refusal_reason"))
            # Fall back to continuing with current plan
            state["phase"] = "executing"
            self._emit("Planner", "phase_change", {"from": "replanning", "to": "executing"})
            return state

        # Preserve done status for already-completed steps
        done_ids = {s["step_id"] for s in old_steps if s.get("status") == "done"}
        for s in revised_plan.get("steps", []):
            if s.get("step_id") in done_ids:
                s["status"] = "done"

        state["plan"] = revised_plan
        state["replan_count"] = state.get("replan_count", 0) + 1
        state["phase"] = "executing"

        # Emit plan revision event
        new_steps = revised_plan.get("steps", [])
        self._emit("Planner", "plan_revised", {
            "replan_count": state["replan_count"],
            "reason": state.get("replan_reason", ""),
            "old_step_count": len(old_steps),
            "new_step_count": len(new_steps),
        })
        self._emit("Planner", "phase_change", {"from": "replanning", "to": "executing"})

        # Print the revised plan
        self._print_plan_report(revised_plan)
        fid = revised_plan.get("fidelity_level", "")
        header = f"[Planner] REVISED PLAN #{state['replan_count']}: {revised_plan.get('strategy_name', '')}" + (f" (fidelity: {fid})" if fid else "")
        step_lines = [header, "-" * 50]
        for s in revised_plan.get("steps", []):
            cost = f"[{s.get('compute_cost', '?')}]"
            stype = f" ({s.get('step_type', 'normal')})" if s.get("step_type") == "skill_evolution" else ""
            status_tag = f" [DONE]" if s.get("status") == "done" else ""
            step_lines.append(f"  {s.get('step_id')}. {cost:10}{stype} {s.get('intent')}{status_tag}")
        step_lines.append("-" * 50)
        self.logger.info("\n".join(step_lines))
        self._emit("Planner", "thought", "\n".join(step_lines))

        return state

    # ------------------------------------------------------------------
    # Main entry point: state-machine driven run()
    # ------------------------------------------------------------------

    def run(self, task_description: str, task_id: str = "planner_task") -> dict[str, Any]:
        """State-machine driven execution: Planning → PreFlight → Executing ⇄ Replanning → Completed."""
        # Initialize state (loads persisted state or creates fresh)
        state = self._initialize_state(task_description, task_id)

        # Create shared DirectSolver instance
        self._solver = DirectSolver(self.agent, self.config)
        if self.run_dir is not None:
            self._solver.set_run_dir(self.run_dir)

        self.logger.info("[Planner] State machine started (phase=%s, replan_count=%d)", state["phase"], state.get("replan_count", 0))

        # Main state-machine loop
        while state["phase"] not in ("completed", "failed", "aborted"):
            phase = state["phase"]

            if phase == "planning":
                state = self._phase_planning(state, task_description, task_id)

            elif phase == "preflight":
                state = self._phase_preflight(state, task_description, task_id)

            elif phase == "executing":
                state = self._phase_executing(state, task_id)

            elif phase == "replanning":
                if state.get("replan_count", 0) >= self.max_replans:
                    self.logger.warning("[Planner] Max replan limit (%d) reached, forcing continue", self.max_replans)
                    state["phase"] = "executing"
                else:
                    state = self._phase_replanning(state, task_description, task_id)

            else:
                self.logger.error("[Planner] Unknown phase: %s, aborting", phase)
                state["phase"] = "failed"
                state["fail_reason"] = f"Unknown phase: {phase}"

            self._save_state(task_id, state)

        self.logger.info("[Planner] State machine finished (phase=%s, replans=%d, steps_done=%d)",
                         state["phase"], state.get("replan_count", 0),
                         sum(1 for s in state.get("plan", {}).get("steps", []) if s.get("status") == "done"))
        self._solver = None  # cleanup

        # Build return dict compatible with old API
        result: dict[str, Any] = {"status": state["phase"], "plan": state.get("plan"), "state": state}
        if state.get("fail_reason"):
            result["reason"] = state["fail_reason"]
        return result
