"""ResilientCalcExp: tactical resilience layer (mode='resilient_calc').

Submit -> Monitor -> Diagnose -> Fix -> Retry. Reads error_handlers from
config.mat_master.resilient_calc. Uses calculation skills (e.g. LogDiagnosticsSkill)
when wired; log monitoring is reserved and currently silent.
All calculation inputs must be OSS links (local paths go through path_adaptor upload);
result files from OSS are downloaded to workspace.
"""

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from evomaster.core.exp import BaseExp
from evomaster.utils.types import TaskInstance

try:
    from evomaster.adaptors.calculation import download_oss_to_local
except ImportError:
    download_oss_to_local = None


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
    diagnose (log monitoring reserved, currently silent), apply config-driven
    fix actions, resubmit. Retries until success or max_retries.
    Calculation skills are used by the agent (skill_registry); path_adaptor
    ensures inputs are OSS links (local paths uploaded); result OSS files
    are downloaded to workspace.
    """

    def __init__(self, agent, config):
        super().__init__(agent, config)
        self.logger = logging.getLogger(self.__class__.__name__)
        mat = _get_mat_master_config(config)
        self.calc_config = mat.get("resilient_calc") or {}
        self.max_retries = self.calc_config.get("max_retries", 3)
        self.poll_interval = self.calc_config.get("poll_interval_seconds", 30)
        self.error_handlers = self.calc_config.get("error_handlers") or {}
        self._task_id = None  # set from run() for workspace resolution

    def _collect_result_urls_from_trajectory(self, trajectory) -> dict[str, Any]:
        """Extract result payload (output_files, result_urls, urls, etc.) from tool responses in trajectory."""
        result = {}
        if not trajectory or not getattr(trajectory, "steps", None):
            return result
        for step in trajectory.steps:
            for tr in getattr(step, "tool_responses", []) or []:
                content = getattr(tr, "content", None) or ""
                if not content:
                    continue
                try:
                    data = json.loads(content) if isinstance(content, str) else content
                    if not isinstance(data, dict):
                        continue
                    for key in ("output_files", "result_urls", "artifacts", "urls", "output_urls"):
                        val = data.get(key)
                        if not val:
                            continue
                        if isinstance(val, str):
                            val = [val]
                        if isinstance(val, list) and val:
                            result.setdefault(key, []).extend(val)
                except (json.JSONDecodeError, TypeError):
                    pass
        return result

    def run(self, task_description: str, task_id: str = "calc_task") -> dict[str, Any]:
        self._task_id = task_id
        self.logger.info("[Resilient] Starting calculation: %s", task_description[:80])
        task = TaskInstance(task_id=task_id, task_type="discovery", description=task_description)
        trajectory = self.agent.run(task)

        # Trigger OSS download when trajectory contains result URLs (e.g. from MCP submit/get_result)
        result_from_trajectory = self._collect_result_urls_from_trajectory(trajectory)
        if result_from_trajectory:
            self._download_result_oss_to_workspace(result_from_trajectory)

        job_id = self._extract_job_id(trajectory)
        if not job_id:
            self.logger.warning("No Job ID found; treating as synchronous task.")
            return {"trajectory": trajectory, "status": trajectory.status, "steps": len(trajectory.steps)}

        # Log monitoring: reserved for future implementation; silent for now.
        self._start_log_monitor_silent(job_id)

        retries = 0
        while retries < self.max_retries:
            status = self._check_job_status(job_id)
            self.logger.info("[Resilient] Job %s status: %s", job_id, status)

            if status in ("Done", "Success", "Finished"):
                self.logger.info("Calculation succeeded.")
                result = self._get_results(job_id)
                result = self._download_result_oss_to_workspace(result)
                return {"job_id": job_id, "status": status, "results": result}

            if status == "Unknown":
                self.logger.error(
                    "_check_job_status returned 'Unknown'; implement job status polling (e.g. via MCP tool output)."
                )
                return {
                    "status": "failed",
                    "job_id": job_id,
                    "retries": retries,
                    "message": "Job status unknown. Implement _check_job_status to return Done/Failed/Error.",
                }

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

    def _start_log_monitor_silent(self, job_id: str) -> None:
        """Reserved for log monitoring. Not implemented; silent (no-op)."""
        pass

    def _check_job_status(self, job_id: str) -> str:
        """Query job status (e.g. via MCP tool). Reserved: returns Unknown until wired to real polling."""
        # TODO: wire to MCP job-status tool and return Done/Success/Finished | Failed/Error/Cancelled | Unknown
        return "Unknown"

    def _diagnose_error(self, job_id: str) -> str:
        """Return error code for error_handlers lookup. Log monitoring reserved; silent (returns 'unknown')."""
        # TODO: wire to LogDiagnosticsSkill / extract_error when log monitoring is implemented
        return "unknown"

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
        """Extract job id from trajectory (e.g. from MCP submit-* tool response). Returns None for sync/discovery-only tasks."""
        if not trajectory or not getattr(trajectory, "steps", None):
            return None
        for step in trajectory.steps:
            for tr in getattr(step, "tool_responses", []) or []:
                name = getattr(tr, "name", "") or ""
                if "submit" not in name.lower():
                    continue
                content = getattr(tr, "content", None) or ""
                if not content:
                    continue
                try:
                    data = json.loads(content) if isinstance(content, str) else content
                    if not isinstance(data, dict):
                        continue
                    job_id = data.get("job_id") or data.get("id")
                    if isinstance(job_id, str) and job_id.strip():
                        return job_id.strip()
                    if isinstance(job_id, (int, float)):
                        return str(int(job_id))
                except (json.JSONDecodeError, TypeError):
                    pass
        return None

    # Subdir under workspace for OSS result downloads (keeps workspace root tidy)
    DOWNLOAD_SUBDIR = "calculation_results"

    def _get_workspace_root(self) -> Path | None:
        """Resolve workspace root; create run_dir/workspaces/task_id or run_dir/workspace if missing."""
        if not self.run_dir:
            return None
        run_path = Path(self.run_dir).resolve()
        if self._task_id:
            ws = run_path / "workspaces" / self._task_id
            ws.mkdir(parents=True, exist_ok=True)
            return ws
        ws = run_path / "workspace"
        ws.mkdir(parents=True, exist_ok=True)
        return ws

    def _get_download_dir(self) -> Path | None:
        """Workspace subdir for OSS downloads; created on demand."""
        workspace = self._get_workspace_root()
        if not workspace:
            return None
        download_dir = workspace / self.DOWNLOAD_SUBDIR
        download_dir.mkdir(parents=True, exist_ok=True)
        return download_dir

    def _download_result_oss_to_workspace(self, result: dict[str, Any]) -> dict[str, Any]:
        """If result contains OSS/HTTP URLs (output_files, result_urls, artifacts, etc.), download to workspace subdir."""
        if not download_oss_to_local:
            return result
        download_dir = self._get_download_dir()
        if not download_dir:
            return result
        out = dict(result)
        downloaded: list[str] = []
        for key in ("output_files", "result_urls", "artifacts", "urls", "output_urls"):
            urls = out.get(key)
            if not urls:
                continue
            if isinstance(urls, str):
                urls = [urls]
            if not isinstance(urls, list):
                continue
            local_paths = []
            for i, url in enumerate(urls):
                if not isinstance(url, str) or not (url.startswith("http://") or url.startswith("https://")):
                    continue
                try:
                    seg = (url.split("?")[0].rstrip("/") or "").split("/")[-1] or "file"
                    seg = re.sub(r"[^\w.\-]", "_", seg) or "file"
                    rel = f"result_{key}_{i}_{seg}"
                    path = download_oss_to_local(url, download_dir, dest_relative_path=rel)
                    local_paths.append(str(path))
                    downloaded.append(str(path))
                except Exception as e:
                    self.logger.warning("Failed to download result URL %s: %s", url, e)
            if local_paths:
                out[f"{key}_local"] = local_paths
        if downloaded:
            out["downloaded"] = downloaded
            self.logger.info("Downloaded %d result file(s) to %s", len(downloaded), download_dir)
        return out

    def _get_results(self, job_id: str) -> Any:
        """Fetch results for job. Override or extend to call MCP get_job_result(job_id); URLs are then downloaded via _download_result_oss_to_workspace."""
        return {}
