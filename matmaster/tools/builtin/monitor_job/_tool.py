"""MonitorJob built-in tool class -- matmaster native BuiltinTool subclass."""

from __future__ import annotations

import json
import os
from typing import Any, ClassVar

from matmaster.tools.builtin.base import BuiltinTool
from matmaster.tools.tool_result import ToolResult

from ._constants import _DEFAULT_MONITOR_LLM_TIMEOUT_SECONDS
from ._lifecycle import _run_lifecycle


class MonitorJobTool(BuiltinTool):
    """Built-in tool: monitor a remote Bohrium calculation job."""

    name: ClassVar[str] = 'monitor_job'
    description: ClassVar[str] = (
        'Monitor a submitted remote calculation job (DPA, ABACUS, LAMMPS, CP2K, QE, ABINIT, ORCA, '
        'Gaussian, etc.) until it reaches a terminal state.\n\n'
        'Polls the Bohrium OpenAPI for job status and downloads result files on success.\n'
        'On success: returns status + downloaded file paths.\n'
        'On failure: returns status=\'failed\' + log_tail (last section of the job log).\n'
        'The agent should read log_tail to diagnose the root cause, fix input files, '
        're-submit via MCP, and call monitor_job again with the new job_id.\n'
        'If the cause cannot be identified, call ask_human(mode="timeout").'
    )
    json_schema: ClassVar[dict[str, Any]] = {
        'type': 'object',
        'properties': {
            'job_id': {
                'type': 'string',
                'description': 'Job ID returned by the MCP submit tool.',
            },
            'software': {
                'type': 'string',
                'description': (
                    'Software name (case-insensitive): dpa, abacus, lammps, cp2k, qe, abinit, orca, '
                    'gaussian, or any registered async software.'
                ),
            },
            'workspace': {
                'type': 'string',
                'description': (
                    'Workspace directory for result downloads. '
                    'Defaults to the session workspace (session-isolated run directory).'
                ),
                'default': '.',
            },
            'bohr_job_id': {
                'type': 'string',
                'description': (
                    'Explicit Bohrium OpenAPI job ID (from extra_info.bohr_job_id in submit response). '
                    'Required for dpdispatcher-based jobs whose MCP job_id contains a hex hash.'
                ),
            },
            'poll_interval': {
                'type': 'integer',
                'description': 'Seconds between status checks.',
                'default': 30,
            },
            'access_key': {
                'type': 'string',
                'description': 'Bohrium access key. Falls back to BOHRIUM_ACCESS_KEY env var.',
            },
            'download_tag': {
                'type': 'string',
                'description': 'Folder tag for downloaded results (timestamp subfolder always added).',
            },
            'llm_decision_mode': {
                'type': 'string',
                'description': 'LLM decision mode: off | advise | auto_terminate',
                'default': 'auto_terminate',
            },
            'llm_model_alias': {
                'type': 'string',
                'description': 'LLM config alias (from config/config.yaml llm section).',
            },
            'llm_timeout_seconds': {
                'type': 'integer',
                'description': 'LLM decision request timeout (seconds).',
                'default': _DEFAULT_MONITOR_LLM_TIMEOUT_SECONDS,
            },
            'decision_check_interval': {
                'type': 'integer',
                'description': 'LLM decision interval (progressive schedule).',
                'default': 10,
            },
            'task_intent': {
                'type': 'string',
                'description': (
                    'Task goal description for LLM decision. '
                    'MANDATORY: pass in the user original query directly.'
                ),
            },
            'max_polls_per_call': {
                'type': 'integer',
                'description': 'Max poll count per call. None = unlimited.',
            },
            'timeout_minutes': {
                'type': 'number',
                'description': 'Limit call to at most timeout_minutes of wall time.',
            },
        },
        'required': ['job_id', 'software'],
    }

    def _execute(self, arguments: dict[str, Any]) -> ToolResult:
        session = self._require_session()

        job_id = arguments.get('job_id', '')
        software = arguments.get('software', '')
        workspace = arguments.get('workspace', '.')
        bohr_job_id = arguments.get('bohr_job_id')
        poll_interval = int(arguments.get('poll_interval', 30))
        access_key = arguments.get('access_key')
        download_tag = arguments.get('download_tag')
        llm_decision_mode = (
            (arguments.get('llm_decision_mode', 'auto_terminate') or 'off')
            .strip()
            .lower()
        )
        llm_model_alias = arguments.get('llm_model_alias')
        llm_timeout_seconds = max(
            5,
            int(
                arguments.get(
                    'llm_timeout_seconds', _DEFAULT_MONITOR_LLM_TIMEOUT_SECONDS
                )
            ),
        )
        decision_check_interval = max(
            1, int(arguments.get('decision_check_interval', 10))
        )
        max_ppc = arguments.get('max_polls_per_call')
        timeout_minutes = arguments.get('timeout_minutes')
        task_intent = arguments.get('task_intent')

        # Resolve workspace (duck-type session instead of isinstance SSHSession)
        if not workspace or workspace == '.':
            is_ssh = hasattr(session, 'upload_file') and callable(
                getattr(session, 'upload_file', None)
            )
            if is_ssh:
                config = getattr(session, 'config', None)
                workspace = (
                    getattr(config, 'working_dir', None)
                    or getattr(config, 'workspace_path', None)
                    or '/share'
                )
            else:
                config = getattr(session, 'config', None)
                workspace = getattr(config, 'workspace_path', None) or '.'

        # Inject access_key from session credentials
        if not access_key:
            creds = getattr(session, '_bohrium_credentials', None)
            if isinstance(creds, dict):
                access_key = creds.get('access_key')
            if not access_key:
                access_key = os.environ.get('BOHRIUM_ACCESS_KEY')

        # max_polls_per_call resolution
        if max_ppc is not None:
            max_ppc = int(max_ppc)
            if max_ppc <= 0:
                max_ppc = None
        if max_ppc is None and timeout_minutes is not None:
            max_ppc = max(1, int(float(timeout_minutes) * 60 // poll_interval))

        stop_ev = self._stop_event_for_exec()

        result = _run_lifecycle(
            job_id=job_id,
            software=software,
            workspace=workspace,
            session=session,
            poll_interval=poll_interval,
            bohr_job_id=bohr_job_id,
            download_tag=download_tag,
            access_key=access_key,
            task_intent=task_intent,
            llm_decision_mode=llm_decision_mode,
            llm_model_alias=llm_model_alias,
            llm_timeout_seconds=llm_timeout_seconds,
            decision_check_interval=decision_check_interval,
            max_polls_per_call=max_ppc,
            stop_event=stop_ev,
        )

        output = json.dumps(result, indent=2, ensure_ascii=False)
        status = (
            'success'
            if result.get('status')
            in (
                'Done',
                'Success',
                'Finished',
                'Completed',
                'done',
                'success',
                'finished',
                'completed',
            )
            else 'error'
        )
        return ToolResult(
            status=status,
            content=output,
            info={
                'status': result.get('status'),
                'job_id': result.get('job_id'),
                'bohr_job_id': result.get('bohr_job_id'),
            },
        )
