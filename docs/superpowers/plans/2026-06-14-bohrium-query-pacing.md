# Bohrium query 内置 pacing 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把短作业等待时 agent 对 `Bohrium(action="query")` 的高频重复调用从模型循环下沉到工具代码做节流，减少 token 浪费，同时保持第一次 query 和终态 query 立即返回。

**Architecture:** 在 `BohriumTool` 本文件 override 基类 `execute_with_context`。`query` 走 pacing 分支：用 `runner_state` 记录同一 run 内每个 job 上次真实查询的时间和运行态；对仍处运行态的 job 的重复 query，在 async 层 `await asyncio.sleep(...)` 到最小间隔后再查平台。非 `query` action 透传给基类，保持 submit / download / kill / list 原行为。运行态判断由同步 `_query()` 通过 `ToolResult.meta` 带出机器可读信号，pacing 层只读 meta、不解析 content。

**Tech Stack:** Python 3.10+，asyncio，Pydantic（`ToolResult`），pytest（`asyncio.run` 包裹 + monkeypatch，无 `@pytest.mark.asyncio`）。

---

## 与 spec 的偏离（已与用户确认）

本 plan 在 [spec](../specs/2026-06-14-bohrium-query-pacing-design.md) 基础上做了一处经用户确认的简化，执行者务必按本 plan 为准：

1. **删除两个环境变量**：不引入 `BOHRIUM_QUERY_MIN_INTERVAL_SECONDS` 和 `BOHRIUM_QUERY_MAX_WAIT_SECONDS`。用户判断这两个可配置 env 属于过度设计。
2. **`min_interval` 落为类常量**：用 `BohriumTool._QUERY_MIN_INTERVAL_SECONDS: ClassVar[float] = 30.0`。spec 测试计划本就认可“通过类常量注入较小间隔”。
3. **删除 `max_wait` 概念及其 cap 逻辑**：spec 配置表自己承认默认配置下 `max_wait` 永不生效（`wait = min(min_interval - elapsed, max_wait)` 中 `min_interval - elapsed` 恒 ≤ 30 < 60）。它存在的唯一理由是给 `min_interval` 的 env override 兜底。一旦不做 env 可配置，`max_wait` 失去意义。新公式直接为 `wait = _QUERY_MIN_INTERVAL_SECONDS - elapsed`，天然 ≤ 30，无需封顶。
4. **不引入 `from src.utils.constant import env_int`**：核实发现 `matmaster/` 包内零个 `from src.` 先例，且 `src/utils/constant.py` 顶部 `import pymysql` + `from utils.env import ...` + 模块级 `DB_CONFIG`。借这个 6 行函数会把 MySQL 驱动和 DB 配置整条 import 链拉进 agent 运行时核心包，并开 matmaster 首个反向依赖 src 的口子。删 env 后此依赖一并消失。
5. **状态结构省略 `last_status`**：spec 的状态结构列出了 `last_status: str`，但 pacing 算法只消费 `running` 与时间戳，按 YAGNI 省略。

> 受影响的 spec 章节：第 4 节配置表、第 5 节状态结构、第 6.1/6.3 节 `max_wait`、第 10 节测试中的 env 项、第 11 节、第 13 节实施顺序第 4 步、第 12 节风险二/三关于 env 的部分。

---

## 文件结构

| 文件 | 责任 | 操作 |
|---|---|---|
| `matmaster/tools/builtin/bohrium_tool/tool.py` | 在 `_query` 成功返回处带出 `meta` 运行态信号；override `execute_with_context` 做 query pacing；更新 prompt 的 quick jobs 文案 | Modify |
| `tests/matmaster/tools/builtin/test_bohrium_tool_pacing.py` | 聚焦覆盖 meta 信号、pacing 行为、prompt 文案 | Create |

不新增运行时模块、不改 DB schema、不动后台 monitor、不恢复 `poll`/`wait` 入口。所有改动集中在上面两个文件。

设计要点（为什么 pacing 放在 async 层而非 `_query`）：
- `runner_state` 的线程安全契约（见 `matmaster/types/tool_runner_state.py`）要求只能在 asyncio event loop 线程访问，**不能**在同步 `_execute()` / `_query()` 或线程池里访问。
- pacing 需要 `await asyncio.sleep`，必须在 async 的 `execute_with_context`。
- 保持 `_query()` 仍是单次平台查询，便于测试和被 direct `_execute()` 无 pacing 复用。

---

## Task 1：`_query` 成功返回时带出 meta 运行态信号

让 `_query()` 在成功路径把机器可读的运行态信号放进 `ToolResult.meta`，供 pacing 层判断，避免解析 content 文案。这是 Task 2 的前置依赖。

**Files:**
- Create: `tests/matmaster/tools/builtin/test_bohrium_tool_pacing.py`
- Modify: `matmaster/tools/builtin/bohrium_tool/tool.py:543-546`（`_query` 的成功 `return`）

- [ ] **Step 1: 写失败测试（meta 信号）**

新建 `tests/matmaster/tools/builtin/test_bohrium_tool_pacing.py`，写入：

```python
"""Pacing + meta-signal tests for Bohrium query (in-tool query pacing)."""

from __future__ import annotations

import matmaster.bohrium.client as bohrium_client_module
from matmaster.tools.builtin.bohrium_tool import BohriumTool
from tests.matmaster.tools.builtin.test_bohrium_tool_helpers import _patch_bridge


class TestBohriumQueryMeta:
    def test_query_running_emits_meta(self, tmp_path, monkeypatch):
        tool = BohriumTool(workdir=tmp_path)
        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(
            bohrium_client_module, "_get", lambda *a, **k: {"data": {"status": 1}}
        )  # Running

        result = tool._query({"job_id": "job-1"})

        assert result.status == "success"
        assert result.meta["bohrium_running"] is True
        assert result.meta["bohrium_status_code"] == 1

    def test_query_finished_emits_meta(self, tmp_path, monkeypatch):
        tool = BohriumTool(workdir=tmp_path)
        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(
            bohrium_client_module, "_get", lambda *a, **k: {"data": {"status": 2}}
        )  # Finished (SUCCESS_CODE)

        result = tool._query({"job_id": "job-1"})

        assert result.status == "success"
        assert result.meta["bohrium_running"] is False
        assert result.meta["bohrium_status_code"] == 2
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/matmaster/tools/builtin/test_bohrium_tool_pacing.py::TestBohriumQueryMeta -v`
Expected: FAIL，`KeyError: 'bohrium_running'`（当前 `_query` 的 `ToolResult` 未设置 `meta`，默认是空 dict）。

- [ ] **Step 3: 在 `_query` 成功返回处加 meta**

修改 `matmaster/tools/builtin/bohrium_tool/tool.py`，把当前的成功返回（543-546 行）：

```python
            return ToolResult(
                status="success",
                content=json.dumps(result_payload, ensure_ascii=False),
            )
```

改为：

```python
            return ToolResult(
                status="success",
                content=json.dumps(result_payload, ensure_ascii=False),
                meta={
                    "bohrium_running": code in RUNNING_CODES,
                    "bohrium_status_code": int(code),
                },
            )
```

说明：`code` 在该 `try` 块作用域内已是 reconfirm 后的最终状态码（见 485-494 行），`RUNNING_CODES` 已在文件顶部导入（41 行）。`content` 不变，仍只承载用户可见结果。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/matmaster/tools/builtin/test_bohrium_tool_pacing.py::TestBohriumQueryMeta -v`
Expected: PASS（2 passed）。

- [ ] **Step 5: 确认不回归现有 query / ledger 测试**

现有 `test_bohrium_tool_query.py` 只断言 `result.content` 的 JSON，`test_bohrium_tool_ledger.py` 只断言 `status` 和 ledger 调用，都不读 `meta`，新增 meta 不应影响它们。

Run: `uv run pytest tests/matmaster/tools/builtin/test_bohrium_tool_query.py tests/matmaster/tools/builtin/test_bohrium_tool_ledger.py -v`
Expected: PASS（全部沿用旧断言）。

- [ ] **Step 6: Commit**

```bash
git add matmaster/tools/builtin/bohrium_tool/tool.py tests/matmaster/tools/builtin/test_bohrium_tool_pacing.py
git commit -m "feat: emit machine-readable running signal in Bohrium query meta

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

> 注意：commit 只包含代码与测试文件，绝不 `git add` 任何 `docs/` 路径（项目规则禁止向 docs 提交）。

---

## Task 2：override `execute_with_context` 实现 query pacing

在 `BohriumTool` 本文件 override 基类的 async 入口：`query` 走 pacing 分支，非 query 透传基类。状态存在 `runner_state` 的专用 key 里，生命周期为一次 agent run。

**Files:**
- Modify: `matmaster/tools/builtin/bohrium_tool/tool.py`（import 区 19-22 / 48 / 50；类常量区 244 行之后；新增方法插入到 `def _execute`（358 行）之前）
- Modify: `tests/matmaster/tools/builtin/test_bohrium_tool_pacing.py`（追加 import 与一个测试类）

- [ ] **Step 1: 追加测试 import**

在 `tests/matmaster/tools/builtin/test_bohrium_tool_pacing.py` 顶部 import 区，把：

```python
from __future__ import annotations

import matmaster.bohrium.client as bohrium_client_module
from matmaster.tools.builtin.bohrium_tool import BohriumTool
from tests.matmaster.tools.builtin.test_bohrium_tool_helpers import _patch_bridge
```

改为：

```python
from __future__ import annotations

import asyncio

import matmaster.bohrium.client as bohrium_client_module
import matmaster.tools.builtin.bohrium_tool.tool as tmod
from matmaster.tools.builtin.bohrium_tool import BohriumTool
from matmaster.types.tool_runner_state import ToolRunnerState
from matmaster.types.tool_spec import ToolExecutionContext
from tests.matmaster.tools.builtin.test_bohrium_tool_helpers import _patch_bridge
```

- [ ] **Step 2: 写失败测试（pacing 行为）**

在同一文件末尾追加：

```python
class TestBohriumQueryPacing:
    def test_first_query_runs_immediately_repeat_running_waits(
        self, tmp_path, monkeypatch
    ):
        tool = BohriumTool(workdir=tmp_path)
        calls: list[str] = []

        def fake_get(base_url, path, access_key, params=None, timeout=30):
            calls.append(path)
            return {"data": {"status": 1}}  # Running

        slept: list[float] = []

        async def fake_sleep(delay):
            slept.append(delay)

        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(bohrium_client_module, "_get", fake_get)
        monkeypatch.setattr(tmod.asyncio, "sleep", fake_sleep)

        state = ToolRunnerState()
        ctx = ToolExecutionContext(runner_state=state)

        # First query: immediate, no sleep, one platform hit.
        asyncio.run(tool.execute_with_context({"action": "query", "job_id": "J"}, ctx))
        assert slept == []
        assert len(calls) == 1

        # Second query while still Running: waits up to min interval, then hits again.
        asyncio.run(tool.execute_with_context({"action": "query", "job_id": "J"}, ctx))
        assert len(slept) == 1
        assert 0 < slept[0] <= 30.0
        assert len(calls) == 2

    def test_repeat_query_after_terminal_does_not_wait(self, tmp_path, monkeypatch):
        tool = BohriumTool(workdir=tmp_path)

        slept: list[float] = []

        async def fake_sleep(delay):
            slept.append(delay)

        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(
            bohrium_client_module, "_get", lambda *a, **k: {"data": {"status": 2}}
        )  # Finished
        monkeypatch.setattr(tmod.asyncio, "sleep", fake_sleep)

        state = ToolRunnerState()
        ctx = ToolExecutionContext(runner_state=state)

        asyncio.run(tool.execute_with_context({"action": "query", "job_id": "J"}, ctx))
        asyncio.run(tool.execute_with_context({"action": "query", "job_id": "J"}, ctx))
        assert slept == []

    def test_query_error_does_not_record_running_pacing(self, tmp_path, monkeypatch):
        tool = BohriumTool(workdir=tmp_path)

        slept: list[float] = []

        async def fake_sleep(delay):
            slept.append(delay)

        def boom(base_url, path, access_key, params=None, timeout=30):
            raise RuntimeError("api down")

        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(bohrium_client_module, "_get", boom)
        monkeypatch.setattr(tmod.asyncio, "sleep", fake_sleep)

        state = ToolRunnerState()
        ctx = ToolExecutionContext(runner_state=state)

        res1 = asyncio.run(
            tool.execute_with_context({"action": "query", "job_id": "J"}, ctx)
        )
        res2 = asyncio.run(
            tool.execute_with_context({"action": "query", "job_id": "J"}, ctx)
        )

        assert res1.status == "error"
        assert res2.status == "error"
        assert slept == []
        pacing = state.get(BohriumTool._QUERY_PACING_STATE_KEY, {})
        assert "J" not in pacing

    def test_query_without_runner_state_is_single_shot(self, tmp_path, monkeypatch):
        tool = BohriumTool(workdir=tmp_path)
        calls: list[str] = []

        def fake_get(base_url, path, access_key, params=None, timeout=30):
            calls.append(path)
            return {"data": {"status": 1}}  # Running

        slept: list[float] = []

        async def fake_sleep(delay):
            slept.append(delay)

        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(bohrium_client_module, "_get", fake_get)
        monkeypatch.setattr(tmod.asyncio, "sleep", fake_sleep)

        # exec_ctx=None → no runner_state → degrade to base single-shot behavior.
        asyncio.run(tool.execute_with_context({"action": "query", "job_id": "J"}, None))
        asyncio.run(tool.execute_with_context({"action": "query", "job_id": "J"}, None))

        assert slept == []
        assert len(calls) == 2
```

- [ ] **Step 3: 跑测试确认失败**

Run: `uv run pytest tests/matmaster/tools/builtin/test_bohrium_tool_pacing.py::TestBohriumQueryPacing -v`
Expected: FAIL。`BohriumTool` 尚未 override `execute_with_context`，基类直接 `asyncio.to_thread(_execute)`：第二次 running query 不会 sleep（`test_first_query_runs_immediately_repeat_running_waits` 断言 `len(slept) == 1` 失败）；`BohriumTool._QUERY_PACING_STATE_KEY` 尚不存在（`test_query_error_does_not_record_running_pacing` 触发 `AttributeError`）。

- [ ] **Step 4: 追加 import（tool.py）**

修改 `matmaster/tools/builtin/bohrium_tool/tool.py`。

(a) stdlib import 区（19-22 行）：

```python
import json
import logging
from pathlib import Path
from typing import Any, ClassVar
```

改为：

```python
import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, ClassVar
```

(b) tool_result import（48 行）：

```python
from matmaster.tools.tool_result import ToolResult
```

改为：

```python
from matmaster.tools.tool_result import ToolResult, normalize_tool_result
```

(c) tool_spec import（50 行）：

```python
from matmaster.types.tool_spec import ResourceClaim
```

改为：

```python
from matmaster.types.tool_spec import ResourceClaim, ToolExecutionContext
```

- [ ] **Step 5: 加类常量**

用 Edit 以 `capabilities` 类属性块作为锚点（其后、`__init__` 方法之前插入两个类常量）。将：

```python
    capabilities: ClassVar[frozenset[str]] = frozenset(
        {
            "bohrium.submit",
            "bohrium.query",
            "bohrium.download",
            "bohrium.kill",
        }
    )
```

改为：

```python
    capabilities: ClassVar[frozenset[str]] = frozenset(
        {
            "bohrium.submit",
            "bohrium.query",
            "bohrium.download",
            "bohrium.kill",
        }
    )

    # In-turn query pacing. Minimum seconds between two real platform queries
    # for the SAME running job within ONE agent run. The first query and any
    # terminal-state query are never delayed. Kept as a class attribute (not an
    # env var) so it stays a single fixed knob and tests can monkeypatch it.
    _QUERY_MIN_INTERVAL_SECONDS: ClassVar[float] = 30.0
    _QUERY_PACING_STATE_KEY: ClassVar[str] = "bohrium_query_pacing"
```

- [ ] **Step 6: 加 `execute_with_context` override**

用 Edit 把下面的新方法插到 `_execute` 定义的正前方——锚点是 `    def _execute(self, arguments: dict[str, Any]) -> str | ToolResult:` 这一行（Step 4/5 已使其行号偏移，按 old_string 锚点定位而非行号）。新方法与 `_execute` 同为 `BohriumTool` 的方法，4 空格缩进，插入后原 `_execute` 保持不变：

```python
    async def execute_with_context(
        self,
        arguments: dict[str, Any],
        exec_ctx: ToolExecutionContext | None,
    ) -> str | ToolResult:
        """Pace repeated query calls for the same running job within one run.

        Non-query actions and a missing runner_state fall back to the base
        single-shot behavior. The first query for a job runs immediately; a
        repeat query while the job was last seen Running waits up to
        ``_QUERY_MIN_INTERVAL_SECONDS`` before hitting the platform again.
        Terminal-state repeats are never delayed.

        runner_state is only touched here, in the event-loop thread (before
        and after ``asyncio.to_thread``), honoring the ToolRunnerState thread
        safety contract; the sync ``_query`` never reads it.
        """
        if arguments.get("action") != "query":
            return await super().execute_with_context(arguments, exec_ctx)

        raw_job_id = arguments.get("job_id")
        runner_state = exec_ctx.runner_state if exec_ctx is not None else None
        if raw_job_id is None or runner_state is None:
            # No job_id → let _query return the existing error. No runner_state
            # → degrade to single-shot. Either way: no pacing.
            return await super().execute_with_context(arguments, exec_ctx)

        pacing = runner_state.get(self._QUERY_PACING_STATE_KEY)
        if pacing is None:
            pacing = {}
            runner_state.set(self._QUERY_PACING_STATE_KEY, pacing)

        normalized_job_id = str(raw_job_id).strip()
        record = pacing.get(normalized_job_id)
        if record is not None and record["running"]:
            wait = self._QUERY_MIN_INTERVAL_SECONDS - (
                time.monotonic() - record["last_checked_monotonic"]
            )
            if wait > 0:
                await asyncio.sleep(wait)

        result = await asyncio.to_thread(self._execute, arguments)

        normalized = normalize_tool_result(result)
        if normalized.status == "success":
            pacing[normalized_job_id] = {
                "last_checked_monotonic": time.monotonic(),
                "running": bool(normalized.meta.get("bohrium_running")),
            }
        return result
```

实现要点（对照 spec 第 6 节）：
- 非 query 与缺 job_id / 无 runner_state：`super().execute_with_context(...)` 复用基类的 `asyncio.to_thread(_execute)` + 异常处理，避免重复代码。
- 仅当上次记录为运行态才计算间隔并 sleep；`wait = min_interval - elapsed` 天然 ≤ 30，无需 `max_wait` 封顶（见“与 spec 的偏离”第 3 条）。
- `await asyncio.sleep(...)` 不被 try/except 包裹，run 取消时 `CancelledError` 自然向上传播，工具不吞取消（spec 6.3）。
- 查询后只读 `normalized.meta`、不解析 `content`；`status != "success"` 时不写 running 记录（spec 6.2 / 第 8 节错误语义）；meta 缺失自然退化为 `running=False`。
- 返回原始 `result`（保持与基类返回类型一致；`FullToolRunner` 后续会再 `normalize_tool_result` 一次）。

- [ ] **Step 7: 跑测试确认通过**

Run: `uv run pytest tests/matmaster/tools/builtin/test_bohrium_tool_pacing.py::TestBohriumQueryPacing -v`
Expected: PASS（4 passed）。

- [ ] **Step 8: 确认非 query action 与既有 query 测试不回归**

既有测试均以 `exec_ctx=None` 调用 `execute_with_context`，走透传分支，行为与改动前一致；submit/download/kill 测试 `action != "query"` 同样透传。

Run: `uv run pytest tests/matmaster/tools/builtin/ -v`
Expected: PASS（含 query / ledger / download / submit / session_credentials 全部）。

- [ ] **Step 9: Commit**

```bash
git add matmaster/tools/builtin/bohrium_tool/tool.py tests/matmaster/tools/builtin/test_bohrium_tool_pacing.py
git commit -m "feat: pace repeated Bohrium query for running jobs within a run

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3：更新 prompt 的 quick jobs 文案

把“鼓励 agent 自己 sleep 30-60s 轮询”改为“工具会自动 pacing；不要用 Bash sleep 管理 Bohrium 查询节奏”，让 prompt 与代码边界一致（spec 第 7 节）。

**Files:**
- Modify: `matmaster/tools/builtin/bohrium_tool/tool.py:320-324`（`prompt()` 内的 quick jobs 段落）
- Modify: `tests/matmaster/tools/builtin/test_bohrium_tool_pacing.py`（追加 prompt 断言测试）

- [ ] **Step 1: 写失败测试（prompt 文案）**

在 `tests/matmaster/tools/builtin/test_bohrium_tool_pacing.py` 末尾追加：

```python
class TestBohriumPrompt:
    def test_prompt_drops_manual_sleep_guidance(self, tmp_path):
        tool = BohriumTool(workdir=tmp_path)
        text = tool.prompt()

        assert text is not None
        # Old guidance that told the agent to sleep between polls is gone.
        assert "sleep 30-60" not in text
        # New guidance states the tool paces repeated queries automatically.
        assert "automatically paces" in text
        assert "Bash sleep" in text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/matmaster/tools/builtin/test_bohrium_tool_pacing.py::TestBohriumPrompt -v`
Expected: FAIL。当前 prompt 仍含 `sleep 30-60`，且不含 `automatically paces` / `Bash sleep`。

- [ ] **Step 3: 改 prompt 文案**

修改 `matmaster/tools/builtin/bohrium_tool/tool.py`，把当前 quick jobs 段落（320-324 行）：

```python
            '- Exception — quick jobs: if a job is expected to finish within '
            'a few minutes, you MAY wait for it in-turn: sleep 30-60 s '
            'between polls or do other pending work, for at most 5 minutes '
            'in total. Still running after that → stop polling, fall back to '
            'the default handoff and end your turn.\n'
```

改为：

```python
            '- Exception — quick jobs: if a job is expected to finish within '
            'a few minutes, you MAY keep querying it in-turn. The Bohrium tool '
            'automatically paces repeated query calls for the same running '
            'job, so do NOT manage query cadence yourself with Bash sleep. If '
            'you have other pending work, do that FIRST instead of firing a '
            'query and waiting — once a paced query is issued, this turn '
            'blocks until that tool call returns and you cannot do other work '
            'meanwhile. Still wait at most ~5 minutes in total; after that '
            'hand off to background monitoring and end your turn.\n'
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/matmaster/tools/builtin/test_bohrium_tool_pacing.py::TestBohriumPrompt -v`
Expected: PASS（1 passed）。

- [ ] **Step 5: Commit**

```bash
git add matmaster/tools/builtin/bohrium_tool/tool.py tests/matmaster/tools/builtin/test_bohrium_tool_pacing.py
git commit -m "docs: tell agent the Bohrium tool paces queries, drop manual sleep advice

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4：整体验证

- [ ] **Step 1: 跑本次新增测试全集**

Run: `uv run pytest tests/matmaster/tools/builtin/test_bohrium_tool_pacing.py -v`
Expected: PASS（meta 2 + pacing 4 + prompt 1 = 7 passed）。

- [ ] **Step 2: 跑全部 builtin 工具测试，确认无回归**

Run: `uv run pytest tests/matmaster/tools/builtin/ -v`
Expected: PASS（含 bohrium 全部子文件 + 其它 builtin 工具）。

- [ ] **Step 3: import 健全性检查**

确认改动没有引入循环 import 或语法问题（本 plan 刻意不依赖 `src.utils.constant`，import 图保持单向）。

Run: `uv run python -c "import matmaster.tools.builtin.bohrium_tool.tool; print('import ok')"`
Expected: 输出 `import ok`，无 ImportError。

- [ ] **Step 4: lint / format**

Run: `uv run ruff check matmaster/tools/builtin/bohrium_tool/tool.py tests/matmaster/tools/builtin/test_bohrium_tool_pacing.py`
Expected: 无报错（无未使用 import、无风格问题）。

如仓库使用 black：
Run: `uv run black --check matmaster/tools/builtin/bohrium_tool/tool.py tests/matmaster/tools/builtin/test_bohrium_tool_pacing.py`
Expected: `All done!`（已格式化）。若有 pre-commit 配置，可改跑 `uv run pre-commit run --files matmaster/tools/builtin/bohrium_tool/tool.py tests/matmaster/tools/builtin/test_bohrium_tool_pacing.py`。

> 命令前缀按仓库 uv 环境（`uv run ...`）；若已激活对应 venv，可去掉 `uv run` 直接执行。

---

## Spec 覆盖对照

| spec 要点 | 落点 |
|---|---|
| 第一次 query 立即返回 | Task 2：首查 `record is None` → 不 sleep（`test_first_query_runs_immediately_repeat_running_waits`） |
| 重复 running query 等到最小间隔再查 | Task 2：`record["running"]` 分支 + `asyncio.sleep` |
| 等待后终态立即返回 / 终态重复不延迟 | Task 2：`record["running"]` 为 False 不 sleep（`test_repeat_query_after_terminal_does_not_wait`） |
| 单次调用有界等待 | Task 2：`wait = _QUERY_MIN_INTERVAL_SECONDS - elapsed ≤ 30` |
| 状态只存 runner_state、run 内生命周期 | Task 2：`runner_state.set(_QUERY_PACING_STATE_KEY, ...)` |
| pacing 放 async 层、`_query` 不碰 runner_state | Task 2：override `execute_with_context`，`_query` 仍同步单查 |
| `_query` 用 meta 带出运行态信号 | Task 1：`meta["bohrium_running"] / ["bohrium_status_code"]` |
| pacing 只读 meta、不解析 content | Task 2：`normalize_tool_result(...).meta.get("bohrium_running")` |
| error 不记录 running pacing | Task 2：`status == "success"` 才写记录（`test_query_error_does_not_record_running_pacing`） |
| 无 runner_state 退化为 single-shot | Task 2：透传 `super()`（`test_query_without_runner_state_is_single_shot`） |
| 非 query action 透传 | Task 2：`action != "query"` → `super()`（`tests/.../test_bohrium_tool_*` 不回归） |
| 取消语义：sleep 不吞 CancelledError | Task 2：`await asyncio.sleep` 无 try/except 包裹 |
| prompt 删除自行 sleep 鼓励、说明工具自动 pacing | Task 3 |
| 不恢复 poll/wait、不改 DB、不动 monitor、无外部迁移 | 全程不涉及，仅改 `tool.py` + 新增测试文件 |

## 残留风险（沿用 spec，已知并接受）

- **短作业完成提示最多延迟约 30s**：默认间隔 30s 下，2 分钟 job 完成后 agent 最多晚约 30s 看到 Finished。相比约 20 次 query 的 token 成本可接受；若需更实时，改 `_QUERY_MIN_INTERVAL_SECONDS` 一个常量即可（如 15.0）。
- **paced sleep 占用 `bohrium-api` counted 槽**：`asyncio.sleep` 在 `FullToolRunner` 的 scheduler acquire/release 之间，会持有一个 `bohrium-api` 槽（`max_concurrent=3`，每 run 一个 scheduler 实例）。首查不 sleep，故批量 sanity-check 不额外占槽；仅同一轮并发重复 query 多个运行态 job 时可能互相影响。要让等待不占槽属于更大的 scheduler 改动，不在本次范围。
- **同一 batch 内并发重复 query 不去重**：两个并发 query 可能各自读到旧记录后都打平台，本设计不合并并发重复请求。触发条件是非正常调用形态，可接受。
