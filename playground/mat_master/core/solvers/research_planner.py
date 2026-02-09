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
    execution_graph = [
        {
            "step_id": s.get("step_id"),
            "step_type": s.get("step_type", "normal"),
            "goal": s.get("intent", ""),
            "compute_intensity": intensity_map.get(s.get("compute_cost"), "MEDIUM"),
            "requires_confirmation": s.get("requires_human_confirm", False),
            "fallback_strategy": s.get("fallback_logic", "None"),
        }
        for s in steps
    ]
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
        self._input_fn = input_fn  # optional; if set, used instead of stdin in _ask_human (e.g. for WebSocket UI)
        self._output_callback: Callable[[str, str, Any], None] | None = output_callback  # (source, type, content) → frontend

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
        return {"goal": "", "plan": None, "history": []}

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

    def run(self, task_description: str, task_id: str = "planner_task") -> dict[str, Any]:
        run_dir = self._run_dir_path()
        workspaces = run_dir / "workspaces" / task_id
        workspaces.mkdir(parents=True, exist_ok=True)
        state = self._load_state(task_id)
        state["goal"] = state.get("goal") or task_description
        state.setdefault("history", [])

        plan = state.get("plan")
        if not _is_deg_plan(plan) or state.get("goal") != task_description:
            self.logger.info("[Planner] Designing flight plan for: %s", task_description[:80])
            plan = self._generate_plan(task_description)
            state["plan"] = plan

        if plan.get("status") == "REFUSED":
            reason = plan.get("refusal_reason", "Unknown")
            self.logger.warning("[CRP] Mission refused: %s", reason)
            return {"status": "failed", "reason": reason, "state": state}

        # Detailed plan report (cost, risks, alternatives)
        self._print_plan_report(plan)

        # Pre-flight: loop until user types 'go' or 'abort'; otherwise treat input as revision feedback
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
                    line = f"  {s.get('step_id')}. {cost:10}{stype} {s.get('intent')}"
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
                    return {"status": "aborted", "state": state}
                if not ans.strip():
                    continue
                self.logger.info("[Planner] Revising plan from user feedback: %s", ans[:100])
                plan = self._revise_plan(task_description, plan, ans)
                state["plan"] = plan
                self._save_state(task_id, state)
                if plan.get("status") == "REFUSED":
                    self.logger.warning("[CRP] Revised plan refused: %s", plan.get("refusal_reason"))
                    return {"status": "failed", "reason": plan.get("refusal_reason"), "state": state}
        else:
            # human_check 关闭时也把步骤列表推送到前端
            fid = plan.get("fidelity_level", "")
            header = f"[Planner] {plan.get('strategy_name', '')}" + (f" (fidelity: {fid})" if fid else "")
            step_lines = [header, "-" * 50]
            for s in plan.get("steps", []):
                cost = f"[{s.get('compute_cost', '?')}]"
                stype = f" ({s.get('step_type', 'normal')})" if s.get("step_type") == "skill_evolution" else ""
                step_lines.append(f"  {s.get('step_id')}. {cost:10}{stype} {s.get('intent')}")
            step_lines.append("-" * 50)
            self._emit("Planner", "thought", "\n".join(step_lines))

        solver = DirectSolver(self.agent, self.config)
        if self.run_dir is not None:
            solver.set_run_dir(self.run_dir)

        for step in plan.get("steps", []):
            if step.get("status") == "done":
                continue
            step_id = step.get("step_id", 0)
            tool_name = step.get("tool_name", "")
            intent = step.get("intent", "")
            fallback = step.get("fallback_logic", "None")
            if step.get("requires_human_confirm") or step.get("compute_cost") == "High":
                ans = self._ask_human(f"Step {step_id} is HIGH COST. Proceed? (y/n)")
                if ans.strip().lower() != "y":
                    continue
            self.logger.info("[Planner] Step %s (goal): %s", step_id, intent[:80])
            step_dir = workspaces / f"step_{step_id}"
            step_dir.mkdir(parents=True, exist_ok=True)
            steps_list = plan.get("steps", [])
            self._emit("Planner", "status_stages", {"total": len(steps_list), "current": step_id, "step_id": step_id, "intent": intent[:120] if intent else ""})

            # Branch A: skill_evolution (Evo-Fallback autonomy)
            if tool_name == "skill_evolution":
                if not _HAS_EVOLUTION:
                    self.logger.warning("[Planner] skill_evolution requested but SkillEvolutionExp not available.")
                    print("\033[91m[Planner] Skill Evolution not available. Attempting fallback.\033[0m")
                    if self._execute_fallback(step, solver, workspaces):
                        step["status"] = "done"
                        state["history"].append({"step": step_id, "tool_name": tool_name, "intent": intent[:200], "result_summary": "fallback_after_evo_unavailable"})
                        self._save_state(task_id, state)
                    else:
                        state["history"].append({"step": step_id, "error": "skill_evolution_unavailable_no_fallback"})
                        self._save_state(task_id, state)
                    continue
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
                        step["status"] = "done"
                        state["history"].append({"step": step_id, "tool_name": tool_name, "intent": intent[:200], "result_summary": str(skill_path or evo_result)[:200]})
                        self._save_state(task_id, state)
                    else:
                        print("\033[93m[Autonomy] Evolution failed. Triggering fallback.\033[0m")
                        if self._execute_fallback(step, solver, workspaces):
                            step["status"] = "done"
                            state["history"].append({"step": step_id, "tool_name": tool_name, "intent": intent[:200], "result_summary": "fallback_after_evo_failed"})
                            self._save_state(task_id, state)
                        else:
                            state["history"].append({"step": step_id, "error": "evo_failed_no_fallback", "detail": str(evo_result)})
                            self._save_state(task_id, state)
                            if self._ask_human("Abort mission? (y/n)").strip().lower() == "y":
                                break
                except Exception as e:
                    self.logger.error("[Planner] Skill evolution step %s failed: %s", step_id, e)
                    if self._execute_fallback(step, solver, workspaces):
                        step["status"] = "done"
                        state["history"].append({"step": step_id, "tool_name": tool_name, "result_summary": "fallback_after_evo_exception"})
                        self._save_state(task_id, state)
                    else:
                        state["history"].append({"step": step_id, "error": str(e)})
                        self._save_state(task_id, state)
                        if self._ask_human("Abort mission? (y/n)").strip().lower() == "y":
                            break
                continue

            # Branch B: goal-oriented execution (executor chooses how to achieve)
            self._emit("Planner", "exp_run", "DirectSolver")
            step_prompt = f"Achieve: {intent}. If that fails: {fallback}"
            try:
                solver.set_run_dir(step_dir)
                result = solver.run(step_prompt, task_id=f"{task_id}_step_{step_id}")
                step["status"] = "done"
                state["history"].append({"step": step_id, "tool_name": tool_name, "intent": intent[:200], "result_summary": str(result)[:200]})
                self._save_state(task_id, state)
            except Exception as e:
                self.logger.error("[Planner] Step %s failed: %s", step_id, e)
                print("\033[93m[Planner] Step failed. Attempting fallback...\033[0m")
                if self._execute_fallback(step, solver, workspaces):
                    step["status"] = "done"
                    state["history"].append({"step": step_id, "tool_name": tool_name, "intent": intent[:200], "result_summary": "completed_via_fallback"})
                    self._save_state(task_id, state)
                else:
                    state["history"].append({"step": step_id, "error": str(e)})
                    self._save_state(task_id, state)
                    print("\033[91m[Planner] Step and fallback failed.\033[0m")
                    if self._ask_human("Abort mission? (y/n)").strip().lower() == "y":
                        break
        return {"status": "completed", "plan": plan, "state": state}
