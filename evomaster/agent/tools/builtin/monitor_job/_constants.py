"""Shared constants for monitor_job (lifecycle polling, logs, LLM prompts)."""

from __future__ import annotations

from pathlib import Path

# Repo root: evomaster/agent/tools/builtin/monitor_job/_constants.py → parents[5]
REPO_ROOT = Path(__file__).resolve().parents[5]

# ---------------------------------------------------------------------------
# Constants for lifecycle states
# ---------------------------------------------------------------------------

TERMINAL_SUCCESS = frozenset(
    {
        'Done',
        'Success',
        'Finished',
        'Completed',
        'done',
        'success',
        'finished',
        'completed',
    }
)
TERMINAL_FAILURE = frozenset(
    {'Failed', 'Error', 'Cancelled', 'failed', 'error', 'cancelled'}
)
UNKNOWN_STATUSES = frozenset({'Unknown', 'unknown'})

# Number of consecutive failure/error status responses required before treating
# a job as truly failed.  Filters out transient network blips and API errors.
_MAX_FAILURE_CONFIRMS = 3

# Maximum characters to include in log_tail returned to the agent.
_LOG_TAIL_MAX_CHARS = 5000
_LOG_PER_FILE_MAX_CHARS = 3000

LOG_PATTERNS: dict[str, list[str]] = {
    'vasp': ['OUTCAR', 'vasp.out', '*.out'],
    'abacus': ['OUT.ABACUS', 'running_*.log', '*.log'],
    'lammps': ['log.lammps', '*.log'],
    'cp2k': ['*.out', 'cp2k.out', '*.log'],
    'gaussian': ['*.log', '*.out'],
    'qe': ['*.out', '*.log'],
    'abinit': ['*.out', '*.log'],
    'orca': ['*.out', '*.log'],
    'dpa': ['*.log', '*.out', '*.json'],
    'gromacs': ['log', 'md.log', '*.log'],
}

_AUTO_DOWNLOAD_MAX_BYTES = 100 * 1024 * 1024  # 100 MB
_MONITOR_LLM_DECISION_PROMPT = """你是科学计算作业监控专家，分析材料计算、量子化学、分子动力学等任务的运行日志。

**任务目标**：{task_intent}

判断作业是否应该继续运行或立即终止，返回 JSON（不要 markdown 标记）：
{{
  "decision": "continue" | "terminate",
  "reason": "简洁的中文原因（<30字）",
  "severity": "low" | "medium" | "high",
  "confidence": 0.0-1.0,
  "suggested_poll_interval_seconds": 30
}}

suggested_poll_interval_seconds（可选）：挂起恢复场景下建议的下次轮询间隔（秒），仅当 decision 为 continue 时生效。范围 30-300。长时间稳定运行（如 MD 已跑很久、SCF 迭代平稳）可建议 120-300 以降低轮询频率；刚启动或关键阶段可建议 30-60。不填或无效则使用默认 30。

关键判断点：
- 数值异常（NaN、Inf）→ terminate
- 致命错误（Fatal Error、Segmentation Fault）→ terminate
- 死循环（长时间无进展）→ terminate
- 任务已完成（达到目标步数/时间）→ terminate（正常完成）
- 正常迭代收敛中 → continue
- 日志不完整或无法判断 → continue（保守策略）

注意：如果任务已经达到预期目标（如 MD 跑完指定时间、优化收敛），应判断为 terminate（正常完成），reason 中说明"任务已完成"。

"""
_DEFAULT_MONITOR_LLM_TIMEOUT_SECONDS = 45
