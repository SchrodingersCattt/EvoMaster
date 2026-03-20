"""monitor_job — built-in tool for resilient remote calculation job lifecycle.

Runs entirely inside the agent backend process so it can import
evomaster.adaptors.calculation.job_service without shipping source code to
the remote Bohrium node.

Workflow
--------
1. Poll Bohrium OpenAPI until the job reaches a terminal state.
   Transient failures (network errors, API blips) are confirmed over
   ``_MAX_FAILURE_CONFIRMS`` consecutive checks before being treated as real.
2. On success: download result files via the NAS file-token API.
   - Local session  → write directly to ``workspace/calculation_results/``.
   - SSH session    → download to a temp dir on the backend, then SFTP-push
                      each file to the container's ``workspace/``, then clean up.
3. On confirmed failure: read the log tail and return it with the result so
   the LLM agent can diagnose the root cause and decide next steps.

Files larger than ``_AUTO_DOWNLOAD_MAX_BYTES`` (100 MB) are skipped; their
paths are listed in ``download_skipped`` so the user can fetch them manually
using the ``bohr_job_id``.
"""

from __future__ import annotations

from ._logs import run_monitor_decision_once
from ._tool import MonitorJobParams, MonitorJobTool

__all__ = [
    'MonitorJobParams',
    'MonitorJobTool',
    'run_monitor_decision_once',
]
