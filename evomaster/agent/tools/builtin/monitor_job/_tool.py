"""MonitorJob built-in tool class and parameter schema."""

from __future__ import annotations

import json
import os
from typing import Any, ClassVar

from pydantic import Field

from evomaster.agent.session import BaseSession
from evomaster.agent.session.ssh import SSHSession

from ...base import BaseTool, BaseToolParams
from ._constants import _DEFAULT_MONITOR_LLM_TIMEOUT_SECONDS
from ._lifecycle import _run_lifecycle


class MonitorJobParams(BaseToolParams):
    """Monitor a submitted remote calculation job (DPA, ABACUS, LAMMPS, CP2K, QE, ABINIT, ORCA,
    Gaussian, etc.) until it reaches a terminal state.

    Polls the Bohrium OpenAPI for job status and downloads result files on success.
    Transient API/network errors are confirmed over multiple checks before being
    treated as a real failure.

    On success: returns status + downloaded file paths.
    On failure: returns status='failed' + log_tail (last section of the job log).
    The agent should read log_tail to diagnose the root cause, fix input files,
    re-submit via MCP, and call monitor_job again with the new job_id.
    If the cause cannot be identified, call ask_human(mode="timeout").
    """

    name: ClassVar[str] = 'monitor_job'

    job_id: str = Field(description='Job ID returned by the MCP submit tool.')
    software: str = Field(
        description=(
            'Software name (case-insensitive): dpa, abacus, lammps, cp2k, qe, abinit, orca, '
            'gaussian, or any registered async software.'
        )
    )
    workspace: str = Field(
        default='.',
        description=(
            'Workspace directory for result downloads. '
            'Defaults to the session workspace (session-isolated run directory). '
            'Only override if you need results in a specific path.'
        ),
    )
    bohr_job_id: str | None = Field(
        default=None,
        description=(
            'Explicit Bohrium OpenAPI job ID (from extra_info.bohr_job_id in submit response). '
            'Required for dpdispatcher-based jobs (ABACUS, etc.) whose MCP job_id contains a hex hash.'
        ),
    )
    poll_interval: int = Field(default=30, description='Seconds between status checks.')
    access_key: str | None = Field(
        default=None,
        description='Bohrium access key. Falls back to BOHRIUM_ACCESS_KEY env var.',
    )
    download_tag: str | None = Field(
        default=None,
        description='Folder tag for downloaded results (timestamp subfolder always added).',
    )
    llm_decision_mode: str = Field(
        default='auto_terminate',
        description=(
            'LLM 决策模式：off(关闭) | advise(仅给建议，不终止) | auto_terminate(判定异常时尝试终止 Bohrium 作业)'
        ),
    )
    llm_model_alias: str | None = Field(
        default=None,
        description='LLM 配置别名（来自 configs/mat_master/config.yaml 的 llm 节点），为空则用 default。',
    )
    llm_timeout_seconds: int = Field(
        default=_DEFAULT_MONITOR_LLM_TIMEOUT_SECONDS,
        description='LLM 决策请求超时（秒）。',
    )
    decision_check_interval: int = Field(
        default=10,
        description=(
            'LLM 决策间隔（渐进式）：前 ~5 分钟每 2 次轮询一次，接下来 ~10 分钟每 5 次，之后每 N 次（本参数，默认 10）。'
        ),
    )
    task_intent: str | None = Field(
        default=None,
        description=(
            '任务目标/意图描述，用于帮助 LLM 判断任务是否完成或异常。'
            '**MANDATORY**: 直接传入用户的原始查询（ORIGINAL TASK）。'
            '例如：用户输入"石墨烯建模并进行5ps nvt md"，则传入 task_intent="石墨烯建模并进行5ps nvt md"。'
        ),
    )
    max_polls_per_call: int | None = Field(
        default=None,
        description=(
            '单次调用最多轮询次数。None 表示不限制，轮询直到作业终态或总轮数上限（默认）。'
            '设为正整数可限制单次调用的轮询次数（用于调试或缩短单次占用）。'
        ),
    )


class MonitorJobTool(BaseTool):
    """Built-in tool: monitor a remote Bohrium calculation job."""

    name: ClassVar[str] = 'monitor_job'
    params_class: ClassVar[type[BaseToolParams]] = MonitorJobParams

    def execute(
        self, session: BaseSession, args_json: str
    ) -> tuple[str, dict[str, Any]]:
        try:
            params = self.parse_params(args_json)
        except Exception as exc:
            return f'Parameter validation error: {exc}', {'error': str(exc)}

        assert isinstance(params, MonitorJobParams)

        # Resolve workspace: fall back to the session's configured workspace so that
        # downloads are isolated to the session's run directory, not the process CWD.
        workspace = params.workspace
        if not workspace or workspace == '.':
            if isinstance(session, SSHSession):
                workspace = (
                    session.config.working_dir
                    or getattr(session.config, 'workspace_path', None)
                    or '/share/workspace'
                )
            else:
                workspace = getattr(session.config, 'workspace_path', None) or '.'

        # Inject access_key from session._bohrium_credentials if not explicitly provided
        access_key = params.access_key
        if not access_key:
            creds = getattr(session, '_bohrium_credentials', None)
            if isinstance(creds, dict):
                access_key = creds.get('access_key')
            if not access_key:
                access_key = os.environ.get('BOHRIUM_ACCESS_KEY')

        max_ppc = params.max_polls_per_call
        if max_ppc is not None and max_ppc <= 0:
            max_ppc = None
        stop_ev = getattr(session, '_stop_event', None)
        result = _run_lifecycle(
            job_id=params.job_id,
            software=params.software,
            workspace=workspace,
            session=session,
            poll_interval=params.poll_interval,
            bohr_job_id=params.bohr_job_id,
            download_tag=params.download_tag,
            access_key=access_key,
            task_intent=params.task_intent,
            llm_decision_mode=(params.llm_decision_mode or 'off').strip().lower(),
            llm_model_alias=params.llm_model_alias,
            llm_timeout_seconds=max(
                5,
                int(params.llm_timeout_seconds or _DEFAULT_MONITOR_LLM_TIMEOUT_SECONDS),
            ),
            decision_check_interval=max(1, int(params.decision_check_interval or 1)),
            max_polls_per_call=max_ppc,
            stop_event=stop_ev,
        )

        output = json.dumps(result, indent=2, ensure_ascii=False)
        info = {
            'status': result.get('status'),
            'job_id': result.get('job_id'),
            'bohr_job_id': result.get('bohr_job_id'),
        }
        return output, info
