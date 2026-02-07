"""ResilientCalcExp: tactical resilience layer (mode='resilient_calc').

Submit -> Monitor -> Diagnose -> Fix -> Retry. Reads error_handlers from
config.mat_master.resilient_calc. Uses LogDiagnosticsSkill for diagnosis.
"""

import json
import logging
import time
from typing import Any

from evomaster.core.exp import BaseExp
from evomaster.utils.types import TaskInstance


def _get_mat_master_config(config) -> dict:
    try:
        if hasattr(config, "model_dump"):
            d = config.model_dump()
        else:
            d = dict(config) if config else {}
        return d.get("mat_master") or {}
    except Exception:
        return {}


class ResilientCalcExp(BaseExp):
    """Resilient calculation mode: tactical layer.

    Handles long-running calc jobs: submit, then monitor; on failure,
    diagnose via LogDiagnosticsSkill, apply config-driven fix actions,
    resubmit. Retries until success or max_retries.
    """

    def __init__(self, agent, config):
        super().__init__(agent, config)
        self.logger = logging.getLogger(self.__class__.__name__)
        mat = _get_mat_master_config(config)
        self.calc_config = mat.get("resilient_calc") or {}
        self.max_retries = self.calc_config.get("max_retries", 3)
        self.poll_interval = self.calc_config.get("poll_interval_seconds", 30)
        self.error_handlers = self.calc_config.get("error_handlers") or {}

    def run(self, task_description: str, task_id: str = "calc_task") -> dict[str, Any]:
        self.logger.info("[Resilient] Starting calculation: %s", task_description[:80])
        task = TaskInstance(task_id=task_id, task_type="discovery", description=task_description)
        trajectory = self.agent.run(task)

        job_id = self._extract_job_id(trajectory)
        if not job_id:
            self.logger.warning("No Job ID found; treating as synchronous task.")
            return {"trajectory": trajectory, "status": trajectory.status, "steps": len(trajectory.steps)}

        retries = 0
        while retries < self.max_retries:
            status = self._check_job_status(job_id)
            self.logger.info("[Resilient] Job %s status: %s", job_id, status)

            if status in ("Done", "Success", "Finished"):
                self.logger.info("Calculation succeeded.")
                return {"job_id": job_id, "status": status, "results": self._get_results(job_id)}

            if status in ("Failed", "Error", "Cancelled"):
                self.logger.warning("Job %s failed. Diagnosing...", job_id)
                error_code = self._diagnose_error(job_id)
                self.logger.info("Diagnosis result: %s", error_code)

                fix_actions = self.error_handlers.get(error_code)
                if not fix_actions:
                    self.logger.error("No handler for error: %s. Giving up.", error_code)
                    break

                self.logger.info("Applying fixes: %s", fix_actions)
                new_job_id = self._apply_fix_and_resubmit(job_id, fix_actions)
                if new_job_id:
                    job_id = new_job_id
                    retries += 1
                else:
                    break

            time.sleep(self.poll_interval)

        return {
            "status": "failed",
            "job_id": job_id,
            "retries": retries,
            "message": f"Calculation failed after {retries} retries.",
        }

    def _check_job_status(self, job_id: str) -> str:
        """Query job status (e.g. via MCP tool). Stub: override or wire real tool."""
        return "Unknown"

    def _diagnose_error(self, job_id: str) -> str:
        """Call LogDiagnosticsSkill to get error code. Stub: wire to extract_error script."""
        return "unknown_error"

    def _apply_fix_and_resubmit(self, failed_job_id: str, actions: list[dict]) -> str | None:
        """Ask agent to apply fix actions and resubmit; return new job_id or None."""
        fix_prompt = (
            f"Job {failed_job_id} failed.\n"
            f"Required actions: {json.dumps(actions)}\n"
            "Please apply these changes to the input files and resubmit the job."
        )
        task = TaskInstance(task_id="fix_task", task_type="discovery", description=fix_prompt)
        trajectory = self.agent.run(task)
        return self._extract_job_id(trajectory)

    def _extract_job_id(self, trajectory) -> str | None:
        """Extract job id from trajectory (e.g. from last tool output). Placeholder."""
        return None

    def _get_results(self, job_id: str) -> Any:
        """Fetch results for job. Placeholder."""
        return None
