"""PrincipalInvestigatorExp: strategy layer (mode='pi').

Maintains project_state.json (persistent memory), runs Hypothesis -> Experiment -> Analysis
loop, and dispatches sub-tasks by type: CALC -> ResilientCalcExp, EVO -> SkillEvolutionExp,
default -> WorkerExp.
"""

import json
import logging
import re
import shutil
from pathlib import Path
from typing import Any, Tuple

from evomaster.core.exp import BaseExp
from evomaster.utils.types import TaskInstance

from .resilient_calc_exp import ResilientCalcExp
from .skill_evolution_exp import SkillEvolutionExp
from .worker_exp import WorkerExp


def _get_mat_master_config(config) -> dict:
    """Get mat_master section as dict."""
    try:
        if hasattr(config, "model_dump"):
            d = config.model_dump()
        else:
            d = dict(config) if config else {}
        return d.get("mat_master") or {}
    except Exception:
        return {}


class PrincipalInvestigatorExp(BaseExp):
    """PI mode: strategy layer.

    Maintains a research hypothesis graph (project_state.json), decomposes the
    high-level goal into sub-tasks, dispatches WorkerExp for each, and updates
    project state. Uses atomic writes for state persistence.
    """

    def __init__(self, agent, config):
        super().__init__(agent, config)
        self.logger = logging.getLogger(self.__class__.__name__)
        mat = _get_mat_master_config(config)
        pi_cfg = mat.get("pi") or {}
        self.project_state_file = pi_cfg.get("project_state_file", "project_state.json")
        self.max_iterations = 5

    def _load_state(self, run_dir: Path) -> dict[str, Any]:
        state_path = run_dir / self.project_state_file
        if state_path.exists():
            try:
                with open(state_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                self.logger.warning("Failed to load state: %s", e)
        return {
            "hypotheses": [],
            "tested_materials": [],
            "current_best": None,
            "history": [],
        }

    def _save_state(self, run_dir: Path, state: dict[str, Any]) -> None:
        """Atomic write: write to .tmp then rename."""
        state_path = run_dir / self.project_state_file
        tmp_path = state_path.with_suffix(".tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
            shutil.move(tmp_path, state_path)
        except Exception as e:
            self.logger.error("Failed to save state: %s", e)

    def _last_assistant_content(self, trajectory) -> str:
        """Extract last assistant message content from trajectory."""
        if not trajectory or not getattr(trajectory, "steps", None):
            return ""
        for step in reversed(trajectory.steps):
            msg = getattr(step, "assistant_message", None)
            if msg and getattr(msg, "content", None):
                return (msg.content or "").strip()
        return ""

    def _parse_decision(self, decision: str) -> Tuple[str, str]:
        """Parse agent output into sub_type and task description.

        Accepts: 'TYPE | Task', '[TYPE] | Task', 'TYPE: Task', 'TYPE : Task', or plain description.
        TYPE: CALC -> calc, EVO -> evo, else -> default (worker).
        """
        text = (decision or "").strip()
        first_line = text.split("\n")[0].strip()
        # Pipe or colon: "CALC | ..." / "[CALC] | ..." / "CALC: ..."
        calc_match = re.match(
            r"^(?:\[?CALC\]?)\s*[:\|]\s*(.+)$", first_line, re.IGNORECASE | re.DOTALL
        )
        if calc_match:
            return "calc", calc_match.group(1).strip()
        evo_match = re.match(
            r"^(?:\[?EVO\]?)\s*[:\|]\s*(.+)$", first_line, re.IGNORECASE | re.DOTALL
        )
        if evo_match:
            return "evo", evo_match.group(1).strip()
        return "default", text

    def _create_sub_exp(self, sub_type: str):
        """Create sub-experiment by type: calc -> ResilientCalcExp, evo -> SkillEvolutionExp, default -> WorkerExp."""
        if sub_type == "calc":
            return ResilientCalcExp(self.agent, self.config)
        if sub_type == "evo":
            return SkillEvolutionExp(self.agent, self.config)
        return WorkerExp(self.agent, self.config)

    def run(self, task_description: str, task_id: str = "pi_task") -> dict[str, Any]:
        run_dir = Path(self.run_dir) if self.run_dir else Path(".")
        workspaces = run_dir / "workspaces" / task_id
        workspaces.mkdir(parents=True, exist_ok=True)
        state = self._load_state(workspaces)

        self.logger.info("[PI] Starting research loop for: %s", task_description[:80])

        for i in range(self.max_iterations):
            self.logger.info("=== PI Loop Iteration %s/%s ===", i + 1, self.max_iterations)

            plan_prompt = (
                f"Project Goal: {task_description}\n"
                f"Current State: {json.dumps(state, ensure_ascii=False)}\n"
                "Task: Analyze the state and propose the NEXT single concrete sub-task.\n"
                "Reply with exactly one line in the form: <TYPE> | <Task Description>\n"
                "TYPE must be one of: CALC (calculation job, e.g. VASP/LAMMPS), EVO (evolve a new skill/tool), or omit TYPE for a generic task (default worker).\n"
                "Examples: 'CALC | Run VASP relaxation for structure X' | 'EVO | Add a script to parse OUTCAR' | 'Optimize POSCAR'\n"
                "If the goal is achieved, reply with 'TERMINATE'."
            )
            plan_task = TaskInstance(
                task_id=f"{task_id}_plan_{i}",
                task_type="discovery",
                description=plan_prompt,
            )
            trajectory = self.agent.run(plan_task)
            decision = self._last_assistant_content(trajectory)

            if "TERMINATE" in (decision or "").upper():
                self.logger.info("[PI] Goal achieved or Agent decided to stop.")
                break

            sub_type, sub_task_desc = self._parse_decision(decision)
            if not sub_task_desc:
                sub_task_desc = f"Sub-task step {i}"
            self.logger.info("[PI] Dispatching sub-task [%s]: %s", sub_type, sub_task_desc[:80])

            sub_exp = self._create_sub_exp(sub_type)
            step_dir = workspaces / f"step_{i}"
            step_dir.mkdir(parents=True, exist_ok=True)
            sub_exp.set_run_dir(step_dir)

            try:
                result = sub_exp.run(sub_task_desc, task_id=f"{task_id}_step_{i}")
                state["history"].append({
                    "step": i,
                    "task": sub_task_desc[:200],
                    "result_summary": str(result)[:200],
                })
                self._save_state(workspaces, state)
            except Exception as e:
                self.logger.error("[PI] Sub-task failed: %s", e)
                state["history"].append({"step": i, "error": str(e)})
                self._save_state(workspaces, state)

        return {"status": "completed", "state": state}
