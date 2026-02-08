"""ResearchPlanner: plan-execute mode (规划执行模式).

Generate a full plan for the goal, then execute each step via DirectSolver.
State persisted to planner.state_file; supports resume and optional replanning.
"""

import json
import logging
import re
import shutil
from pathlib import Path
from typing import Any

from evomaster.core.exp import BaseExp
from evomaster.utils.types import Dialog, SystemMessage, UserMessage

from .direct_solver import DirectSolver


def _get_mat_master_config(config) -> dict:
    try:
        if hasattr(config, "model_dump"):
            d = config.model_dump()
        else:
            d = dict(config) if config else {}
        return d.get("mat_master") or {}
    except Exception:
        return {}


class ResearchPlanner(BaseExp):
    """规划模式：生成计划 -> 按步执行 DirectSolver -> 更新状态。

    类似 Cursor Composer：先给出较长计划，再逐步执行；每步均可触发 CALC/EVO/默认能力。
    """

    def __init__(self, agent, config):
        super().__init__(agent, config)
        self.logger = logging.getLogger(self.__class__.__name__)
        mat = _get_mat_master_config(config)
        planner_cfg = mat.get("planner") or {}
        self.state_file = planner_cfg.get("state_file", "research_state.json")
        self.max_steps = planner_cfg.get("max_steps", 20)

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
        return {"goal": "", "plan": [], "history": []}

    def _save_state(self, task_id: str, state: dict[str, Any]) -> None:
        path = self._state_path(task_id)
        tmp = path.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
            shutil.move(tmp, path)
        except Exception as e:
            self.logger.error("Failed to save state: %s", e)

    def _generate_plan(self, goal: str, task_id: str) -> list[dict[str, Any]]:
        """One-shot LLM: generate numbered list of sub-tasks."""
        system = (
            "You are a research planner. Given a high-level goal, output a numbered list "
            "of concrete sub-tasks, one per line. Each line must start with a number and a period, e.g. '1. ...'"
        )
        user = (
            f"Goal: {goal}\n\n"
            "Output only a numbered list of sub-tasks (1. ... 2. ...). No other text."
        )
        dialog = Dialog(
            messages=[
                SystemMessage(content=system),
                UserMessage(content=user),
            ],
            tools=[],
        )
        try:
            reply = self.agent.llm.query(dialog)
            content = (reply.content or "").strip()
        except Exception as e:
            self.logger.warning("Plan generation failed: %s", e)
            return [{"id": 1, "desc": goal, "status": "pending"}]

        steps = []
        for line in content.splitlines():
            line = line.strip()
            m = re.match(r"^\d+\.\s*(.+)$", line)
            if m:
                steps.append({"id": len(steps) + 1, "desc": m.group(1).strip(), "status": "pending"})
        if not steps:
            steps = [{"id": 1, "desc": goal, "status": "pending"}]
        return steps[: self.max_steps]

    def run(self, task_description: str, task_id: str = "planner_task") -> dict[str, Any]:
        run_dir = self._run_dir_path()
        workspaces = run_dir / "workspaces" / task_id
        workspaces.mkdir(parents=True, exist_ok=True)
        state = self._load_state(task_id)
        state["goal"] = state.get("goal") or task_description

        if not state.get("plan"):
            self.logger.info("[Planner] Generating plan for: %s", task_description[:80])
            state["plan"] = self._generate_plan(task_description, task_id)
            self._save_state(task_id, state)

        solver = DirectSolver(self.agent, self.config)
        if self.run_dir is not None:
            solver.set_run_dir(self.run_dir)

        for step in state["plan"]:
            if step.get("status") == "done":
                continue
            step_id = step.get("id", 0)
            desc = step.get("desc", "")
            self.logger.info("[Planner] Step %s: %s", step_id, desc[:80])
            step_dir = workspaces / f"step_{step_id}"
            step_dir.mkdir(parents=True, exist_ok=True)
            solver.set_run_dir(step_dir)
            try:
                result = solver.run(desc, task_id=f"{task_id}_step_{step_id}")
                step["status"] = "done"
                state["history"].append({
                    "step": step_id,
                    "desc": desc[:200],
                    "result_summary": str(result)[:200],
                })
                self._save_state(task_id, state)
            except Exception as e:
                self.logger.error("[Planner] Step %s failed: %s", step_id, e)
                state["history"].append({"step": step_id, "error": str(e)})
                self._save_state(task_id, state)

        return {"status": "completed", "state": state}
