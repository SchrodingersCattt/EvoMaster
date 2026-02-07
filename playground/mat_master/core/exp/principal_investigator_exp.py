"""PrincipalInvestigatorExp: strategy layer (mode='pi').

Maintains project_state.json (persistent memory), runs Hypothesis -> Experiment -> Analysis
loop, and dispatches sub-tasks to WorkerExp.
"""

import json
import logging
import shutil
from pathlib import Path
from typing import Any

from evomaster.core.exp import BaseExp
from evomaster.utils.types import TaskInstance

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
                "Task: Analyze the state and propose the NEXT single concrete sub-task description.\n"
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

            sub_task_desc = decision or f"Sub-task step {i}"
            self.logger.info("[PI] Dispatching sub-task: %s", sub_task_desc[:80])

            worker = WorkerExp(self.agent, self.config)
            step_dir = workspaces / f"step_{i}"
            step_dir.mkdir(parents=True, exist_ok=True)
            worker.set_run_dir(step_dir)

            try:
                result = worker.run(sub_task_desc, task_id=f"{task_id}_step_{i}")
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
