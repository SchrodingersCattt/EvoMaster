"""matmaster/tools/bash_runner.py

Shared bash execution core extracted from BashTool. Pure command
execution only: plan, env injection, exec, observation assembly.
No figure, upload, or path-validation concerns. Timeout-cap policy
stays in each calling tool.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from matmaster.bohrium.runtime import get_runtime
from matmaster.tools.filesystem_semantics.shell_planner import plan_shell_command
from matmaster.tools.script_env import (
    prepare_inline_command,
    prepare_script_command,
)
from matmaster.types.cancellation import CancellationToken


@dataclass(slots=True)
class BashRunResult:
    output: str
    exit_code: int
    working_dir: str
    observation: str


def run_bash_command(
    *,
    session: Any,
    command: str,
    timeout_s: float,
    cancel_token: CancellationToken | None,
    extra_env: dict[str, str] | None = None,
) -> BashRunResult:
    runtime = get_runtime(session)
    env = runtime.build_env() if runtime is not None else {}
    if extra_env:
        env = {**env, **extra_env}

    plan = plan_shell_command(command)
    if plan.mode == "script":
        prepared = prepare_script_command(command, env, session, shell_path="bash")
    else:
        prepared = prepare_inline_command(command, env, session)

    result = session.exec_bash(
        command=prepared,
        timeout=timeout_s,
        cancel_token=cancel_token,
    )

    output = result.get("output", "") or result.get("stdout", "")
    exit_code = result.get("exit_code", 0)
    working_dir = result.get("working_dir", "")

    observation = output
    if working_dir:
        observation += f"\n[Session working directory: {working_dir}]"
    observation += f"\n[Command finished with exit code {exit_code}]"

    return BashRunResult(
        output=output,
        exit_code=exit_code,
        working_dir=working_dir,
        observation=observation,
    )
