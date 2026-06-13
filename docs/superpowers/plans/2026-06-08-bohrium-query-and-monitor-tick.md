# Bohrium query 改造 + monitor 巡检单元 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把长程 HPC 监控从 agent 工具里拆出去——前台 tool `poll` → `query`(单次查询立即返回,删阻塞短轮询),后台提供 `BohriumMonitor.tick()` 巡检单元供独立 monitor 进程循环调用。

**Architecture:** 两条互不依赖的链路,唯一交汇点是 `bohrium_jobs` 台账状态本身(状态驱动的隐式协调,无共享代码层)。前台改 `matmaster/tools/builtin/bohrium_tool/tool.py`;后台在 `src/services/bohrium_poller.py` 复用已有 `BohriumJobPoller` 引擎、外包一层异常兜底。只共享 `matmaster/bohrium/status.py` 里的纯函数,不抽 service 共享层。

**Tech Stack:** Python ≥3.10、pytest、`uv` 环境、asyncio(tool 侧)、MySQL `bohrium_jobs` 台账(poller 侧,测试用假对象注入,不依赖真库)。

---

## 背景:本计划基于源码核对后的事实

下列行号已对照当前 `codex/provider-stage1` 分支源码核实(spec 给出的行号大体准确,以本计划为准):

- `matmaster/tools/builtin/bohrium_tool/tool.py`
  - `description` 含 `submit / poll / download / kill`(tool.py:166-169)
  - action enum 含 `"poll"`(tool.py:177)
  - `job_id` 参数描述 `(poll, download, kill)`(tool.py:214)
  - `capabilities` **已经**声明 `bohrium.query`(tool.py:246)——本次改名是补齐这个既存命名不一致
  - 常量 `_POLL_INTERVAL` / `_POLL_MAX_WAIT`(tool.py:320-321)
  - `execute_with_context` 的 poll 分支(tool.py:338-339)
  - 注册表更新条件 `action in ("submit", "download", "kill")`(tool.py:348)
  - `_poll_with_short_loop`(tool.py:358-415),内部还调 `_update_registry(registry, "poll", ...)`(tool.py:389)
  - `_update_registry` 的 `if action == "poll":`(tool.py:449)
  - `_execute` 的 `match` 分支 `case "poll":`(tool.py:479)与未知 action 提示(tool.py:493)
  - `_poll`(tool.py:577-672):单次 `get_job_detail` + `confirm_terminal_status` + `_safe_ledger("record_poll", ...)` + download 提示 + sandbox `_fetch_log_tail`;`result_dir` 拒绝并提示用 download
  - `_log_request_context(action="poll", ...)`(tool.py:598)、错误日志 `"bohrium poll failed action=poll ..."`(tool.py:666)
  - `_kill` 返回消息里 `Bohrium(action="poll", job_id=...)`(tool.py:823)
  - `prompt()` 的 poll/download/kill 段(tool.py:297-307)
- `matmaster/tools/builtin/bohrium_tool/registry.py`:`rebuild_from_events` 的 `elif action == "poll":`(registry.py:141);内部方法 `update_poll` / `classify_poll_status`(registry.py:33,69)
- `src/services/bohrium_poller.py`:`BohriumJobPoller`(全文 158 行),`run_once(limit, claim_timeout_seconds) -> {"claimed","polled","errors"}`,模块级 `logger`(line 13);**没有** `_env_int`,**没有** `import os`
- `src/services/bohrium_jobs_wiring.py`:`record_poll` 经 `to_ledger_status` + `apply_poll`,用 `_FOREGROUND_POLL_BACKOFF_SECONDS = 30`(wiring.py:19,106-123)
- `src/dao/chat_events_table.py`:`get_bohrium_events` 把 **tool_call 的 `args["action"]`** 原样存进 rebuild 事件的 `action` 字段(chat_events_table.py:664,694)→ 这是下方 gap 1 的根因

### Spec gap 发现(spec 未明写、但迁移必须处理)

> 这两处不属于 spec §4 表格,但若不改,`poll → query` 迁移会**静默破坏注册表**。本计划已纳入对应步骤。

**Gap 1 — 注册表事件回放的动作词。** `get_bohrium_events` 把工具调用的 `action` 原样持久化;`JobRegistry.rebuild_from_events` 用 `elif action == "poll":` 分发到 `update_poll`(registry.py:141)。action 改成 `query` 后,新会话持久化的事件 `action` 是 `"query"`,回放时不再匹配 `"poll"`,`poll_count` / 状态无法恢复。**修复:** registry.py:141 的分发字符串 `"poll"` → `"query"`(内部方法名 `update_poll` 按 spec 保留不改)。本仓库无运行中数据、禁止兼容兜底,直接迁移即可。

**Gap 2 — 普通路径的注册表更新白名单。** 旧 `poll` 走 `_poll_with_short_loop`,在循环里手动调 `_update_registry(registry, "poll", ...)`(tool.py:389)。删掉该分支后 `query` 走普通路径,而普通路径只在 `action in ("submit", "download", "kill")` 时调 `_update_registry`(tool.py:348)——`query` 不在白名单,注册表将永不更新。**修复:** tool.py:348 的元组加入 `"query"` → `("submit", "query", "download", "kill")`。

---

## 文件结构(创建 / 修改一览)

**前台链路(Task 1):**
- 修改 `matmaster/tools/builtin/bohrium_tool/tool.py` — schema/description/prompt/dispatch/`_poll`→`_query`/删短轮询/日志标签/kill 消息
- 修改 `matmaster/tools/builtin/bohrium_tool/registry.py` — gap 1,回放动作词
- 重命名+重写 `tests/matmaster/tools/builtin/test_bohrium_tool_poll.py` → `test_bohrium_tool_query.py`
- 修改 `tests/matmaster/tools/builtin/test_bohrium_tool_ledger.py`、`test_bohrium_tool_prompt_rebalance.py`、`test_bohrium_tool_session_credentials.py`、`test_bohrium_tool.py`、`test_bohrium_registry.py`

**后台链路(Task 2):**
- 修改 `src/services/bohrium_poller.py` — 新增 `_env_int` helper + `BohriumMonitor` 类
- 修改 `tests/services/test_bohrium_poller.py` — 追加 `BohriumMonitor` 轻量测试(保留现有 `run_once` 测试不动)

**收尾(Task 3):** 仅验证,无新代码。

> 不创建任何新源文件。`BohriumMonitor` 紧挨引擎放进现有 `bohrium_poller.py`(spec §5.1)。**不碰** `src/monitor/`(同事负责)、不碰 DB schema、不留 `poll` alias。

---

## Task 1: 前台 tool `poll` → `query`(含 gap 1 / gap 2)

**Files:**
- Modify: `matmaster/tools/builtin/bohrium_tool/tool.py`
- Modify: `matmaster/tools/builtin/bohrium_tool/registry.py:141`
- Rename+Rewrite: `tests/matmaster/tools/builtin/test_bohrium_tool_poll.py` → `tests/matmaster/tools/builtin/test_bohrium_tool_query.py`
- Modify: `tests/matmaster/tools/builtin/test_bohrium_tool_ledger.py`
- Modify: `tests/matmaster/tools/builtin/test_bohrium_tool_prompt_rebalance.py`
- Modify: `tests/matmaster/tools/builtin/test_bohrium_tool_session_credentials.py`
- Modify: `tests/matmaster/tools/builtin/test_bohrium_tool.py`
- Modify: `tests/matmaster/tools/builtin/test_bohrium_registry.py`
- Modify: `tests/matmaster/core/test_exp_runtime_v2.py`(rebuild fixture,**真断点**)
- Modify: `tests/test_chat_events_table_spawn_id.py`(`get_bohrium_events` fixture,过时数据)
- Modify: `tests/matmaster/integration/test_runtime_credential_bridge_e2e.py`(防迁移后静默假通过)

策略:先把所有前台测试迁到 `query` 语义(全部转红),再改实现转绿,最后单次提交。

> 为什么这三个额外文件必须改(spec §4 表格未列出):它们持有以 `"action": "poll"` 形式写死的 JSON/dict fixture。`test_exp_runtime_v2.py` 用这些事件喂 `JobRegistry.rebuild_from_events` 并断言 `poll_count == 1`——gap 1 把回放动作词改成 `query` 后,`poll` 事件被忽略,状态停在 `submitted`,**该测试必爆**。另两个不会爆但会失真:`test_chat_events_table_spawn_id.py` 是 `get_bohrium_events` 的透传断言(变过时数据);`test_runtime_credential_bridge_e2e.py` 迁移后 `"poll"` 变成未知 action,测试会**静默假通过**(返回的是 "Unknown action" 错误,而非它本想验证的 result_dir/无 session 错误)。

---

- [ ] **Step 1: 用 git mv 重命名 poll 测试文件**

```bash
git mv tests/matmaster/tools/builtin/test_bohrium_tool_poll.py \
       tests/matmaster/tools/builtin/test_bohrium_tool_query.py
```

- [ ] **Step 2: 重写 `test_bohrium_tool_query.py` 为 query 单次语义**

整文件覆盖为下面内容(删掉所有 `_POLL_MAX_WAIT` / `_POLL_INTERVAL` monkeypatch 与重试循环断言,改为单次查询断言,并断言短轮询符号已删):

```python
"""Query tests for Bohrium tool (single-shot, no blocking loop)."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import matmaster.bohrium.client as bohrium_client_module
from matmaster.tools.builtin.bohrium_tool import BohriumTool
from matmaster.tools.builtin.bohrium_tool.registry import JobRegistry
from matmaster.tools.tool_result import ToolResult
from tests.matmaster.tools.builtin.test_bohrium_tool_helpers import _patch_bridge


def _make_exec_ctx(registry: JobRegistry | None = None):
    """Build a minimal exec_ctx with runner_state containing a registry."""
    state = SimpleNamespace(
        get=lambda key, default=None: (
            registry if key == "bohrium_job_registry" else default
        ),
        set=lambda key, value: None,
    )
    return SimpleNamespace(
        runner_state=state,
        cancel_token=None,
    )


class TestQuerySingleShot:
    def test_query_running_returns_once(self, tmp_path, monkeypatch):
        tool = BohriumTool(workdir=tmp_path)
        registry = JobRegistry()
        registry.register("job-1")
        calls: list[str] = []

        def fake_get(base_url, path, access_key, params=None, timeout=30):
            calls.append(path)
            return {"data": {"status": 1}}  # Running

        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(bohrium_client_module, "_get", fake_get)

        result = asyncio.run(
            tool.execute_with_context(
                {"action": "query", "job_id": "job-1"},
                _make_exec_ctx(registry),
            )
        )

        assert isinstance(result, ToolResult)
        payload = json.loads(result.content)
        assert payload["status"] == "Running"
        # single-shot: exactly one API hit, no blocking retry loop
        assert len(calls) == 1
        # registry still updated on the normal (non-loop) path
        assert registry.get("job-1").poll_count == 1
        assert registry.get("job-1").status == "running"

    def test_query_finished_returns_once(self, tmp_path, monkeypatch):
        tool = BohriumTool(workdir=tmp_path)
        registry = JobRegistry()
        registry.register("job-1")

        def fake_get(base_url, path, access_key, params=None, timeout=30):
            return {"data": {"status": 2}}  # Finished

        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(bohrium_client_module, "_get", fake_get)

        result = asyncio.run(
            tool.execute_with_context(
                {"action": "query", "job_id": "job-1"},
                _make_exec_ctx(registry),
            )
        )

        payload = json.loads(result.content)
        assert payload["status"] == "Finished"
        assert registry.get("job-1").status == "finished"

    def test_query_without_registry_still_works(self, tmp_path, monkeypatch):
        tool = BohriumTool(workdir=tmp_path)

        def fake_get(base_url, path, access_key, params=None, timeout=30):
            return {"data": {"status": 1}}

        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(bohrium_client_module, "_get", fake_get)

        result = asyncio.run(
            tool.execute_with_context(
                {"action": "query", "job_id": "job-1"},
                _make_exec_ctx(None),
            )
        )

        payload = json.loads(result.content)
        assert payload["status"] == "Running"

    def test_short_polling_loop_removed(self):
        # The blocking short-poll loop and its constants must be gone.
        assert not hasattr(BohriumTool, "_poll_with_short_loop")
        assert not hasattr(BohriumTool, "_POLL_MAX_WAIT")
        assert not hasattr(BohriumTool, "_POLL_INTERVAL")

    def test_query_in_action_enum(self):
        enum = BohriumTool.json_schema["properties"]["action"]["enum"]
        assert "query" in enum
        assert "poll" not in enum

    def test_submit_registers_job(self, tmp_path):
        tool = BohriumTool(workdir=tmp_path)
        registry = JobRegistry()

        result = tool._update_registry(
            registry,
            "submit",
            {"job_name": "test-run"},
            ToolResult(
                status="success",
                content=json.dumps({"success": True, "job_id": "job-99"}),
            ),
        )

        assert isinstance(result, ToolResult)
        rec = registry.get("job-99")
        assert rec is not None
        assert rec.status == "submitted"
        assert rec.job_name == "test-run"
```

- [ ] **Step 3: 改 `test_bohrium_tool_ledger.py` 的 `_poll` → `_query`**

把 `bt._poll({"job_id": "12345"})`(ledger 测试 line 82)改为 `bt._query(...)`。注意 `_FakeLedger.record_poll` 方法名与 `("poll", kw)` 标签是 ledger 端口动作词,**保留不改**(spec §4:ledger `record_poll` 不改名)。

```python
    res = bt._query({"job_id": "12345"})
```

(同函数内 `poll_calls = [c for c in fake.calls if c[0] == "poll"]` 保留——比对的是 ledger 动作词 `record_poll` 产生的 `"poll"` 标签,不是工具 action。)

- [ ] **Step 4: 改 `test_bohrium_tool_prompt_rebalance.py` 断言**

`test_description_is_capability_summary` 内的 `assert 'action="poll"' not in BohriumTool.description` 改成 query;`test_prompt_retains_usage_section_and_absorbs_action_details` 内 `assert "poll" in prompt` 改成 `assert "query" in prompt`:

```python
        assert 'action="query"' not in BohriumTool.description
```

```python
        assert "query" in prompt
```

- [ ] **Step 5: 改 `test_bohrium_tool_session_credentials.py` 的 action 名**

该文件 4 处 `{"action": "poll", ...}`(line 93,107,172,195)全部改 `"query"`;`test_poll_rejects_result_dir_parameter` 重命名为 `test_query_rejects_result_dir_parameter` 并把其内的 action 改 query(断言 `"no longer downloads artifacts"` 与 `'action="download"'` 保留):

```python
        result = asyncio.run(tool.execute({"action": "query", "job_id": "job-1"}))
```

```python
    def test_query_rejects_result_dir_parameter(self, tmp_path, monkeypatch):
        """query no longer accepts result_dir - directs to download action."""
        monkeypatch.delenv("BOHRIUM_ACCESS_KEY", raising=False)
        monkeypatch.delenv("BOHRIUM_PROJECT_ID", raising=False)

        _patch_bridge(monkeypatch)
        tool = BohriumTool(workdir=tmp_path)

        result = asyncio.run(
            tool.execute(
                {"action": "query", "job_id": "job-1", "result_dir": "/share/out"}
            )
        )
        assert result.status == "error"
        assert "no longer downloads artifacts" in result.content
        assert 'action="download"' in result.content
```

- [ ] **Step 6: 改 `test_bohrium_tool.py` 的 poll 引用**

- line 84 `assert "poll" in prompt` → `assert "query" in prompt`
- line 797 `tool.execute({"action": "poll", "job_id": "job-123"})` → `"query"`
- line 913 `assert "poll" in payload["message"]`(kill 消息断言)→ `assert "query" in payload["message"]`(对应 gap 修复:kill 消息将引导到 query)

```python
        assert "query" in prompt
```

```python
        result = asyncio.run(tool.execute({"action": "query", "job_id": "job-123"}))
```

```python
        assert "query" in payload["message"]
```

- [ ] **Step 7: 给 `test_bohrium_registry.py` 加 rebuild query 回放测试(gap 1)**

在文件末尾追加(验证回放动作词从 query 映射到 `update_poll`):

```python
class TestRebuildFromQueryEvents:
    def test_query_event_restores_poll_count(self):
        events = [
            {"action": "submit", "job_id": "job-1", "job_name": "n"},
            {"action": "query", "job_id": "job-1", "status": "Running"},
        ]
        reg = JobRegistry.rebuild_from_events(events)
        rec = reg.get("job-1")
        assert rec is not None
        assert rec.poll_count == 1
        assert rec.status == "running"

    def test_legacy_poll_event_ignored(self):
        # post-migration the action word is "query"; stale "poll" no longer maps
        events = [{"action": "poll", "job_id": "job-1", "status": "Running"}]
        reg = JobRegistry.rebuild_from_events(events)
        rec = reg.get("job-1")
        assert rec is None or rec.poll_count == 0
```

确认文件顶部已 `from matmaster.tools.builtin.bohrium_tool.registry import JobRegistry`(若无则补)。

- [ ] **Step 7a: 迁移 `test_exp_runtime_v2.py` 的 rebuild fixture(真断点)**

把 `tests/matmaster/core/test_exp_runtime_v2.py` 里 `bohrium_rebuild_events` 内两处 `"action": "poll"`(line 525、531)改为 `"action": "query"`。后续断言 `rec.status == "running"` 与 `rec.poll_count == 1`(line 548-552)保持不变——迁移后回放重新命中 `update_poll`,断言自然成立。

改前(两处):
```python
                                "action": "poll",
```
改后(两处):
```python
                                "action": "query",
```

- [ ] **Step 7b: 迁移 `test_chat_events_table_spawn_id.py` 的 fixture(去过时数据)**

`tests/test_chat_events_table_spawn_id.py` 输入 fixture(line 247)与期望输出(line 285)各有一处 poll,同步改 query,保持输入/输出一致(`get_bohrium_events` 是动作词透传,改完仍通过):

输入(line 247):
```python
                '"args":{"action":"query","job_id":"job-1"}}'
```
期望输出(line 285):
```python
            "action": "query",
```

- [ ] **Step 7c: 迁移 `test_runtime_credential_bridge_e2e.py`(防静默假通过)**

`tests/matmaster/integration/test_runtime_credential_bridge_e2e.py` 的 `test_bohrium_tool_poll_with_remote_share_and_no_session_errors`(line 284)把 action 改 query,并把方法名改为 query 以反映真实路径(它断言 `result.status == "error"`,改后测的是 query 的 result_dir 拒绝 / 无 session 错误,而非未知 action):

方法签名(line 284):
```python
    def test_bohrium_tool_query_with_remote_share_and_no_session_errors(
        self, tmp_path, monkeypatch
    ):
```
action 字段(line 292):
```python
                    "action": "query",
```

- [ ] **Step 8: 运行全部前台测试,确认转红**

Run:
```bash
uv run pytest tests/matmaster/tools/builtin/test_bohrium_tool_query.py \
  tests/matmaster/tools/builtin/test_bohrium_tool_ledger.py \
  tests/matmaster/tools/builtin/test_bohrium_tool_prompt_rebalance.py \
  tests/matmaster/tools/builtin/test_bohrium_tool_session_credentials.py \
  tests/matmaster/tools/builtin/test_bohrium_tool.py \
  tests/matmaster/tools/builtin/test_bohrium_registry.py \
  tests/matmaster/core/test_exp_runtime_v2.py \
  tests/test_chat_events_table_spawn_id.py \
  tests/matmaster/integration/test_runtime_credential_bridge_e2e.py -q
```
Expected: FAIL —— `query` 测试因 `AttributeError: _query` / action enum 无 `"query"` / prompt 无 `"query"` / rebuild 不识别 `"query"` 等失败(`test_exp_runtime_v2` 因 fixture 已是 query 但 registry 仍只认 poll,`poll_count` 断言失败)。这证明测试确实在驱动改动。
> 注:`test_runtime_credential_bridge_e2e.py` / `test_chat_events_table_spawn_id.py` 可能因需 MySQL 而 SKIP;若 SKIP 不算红,等 Step 22 改完实现后确认其 PASS 即可。

- [ ] **Step 9: tool.py —— description 改名**

把 tool.py:166-169:

```python
    description: ClassVar[str] = (
        "Bohrium HPC platform operations: submit / poll / download / kill "
        "jobs, list available images / machines."
    )
```

改成:

```python
    description: ClassVar[str] = (
        "Bohrium HPC platform operations: submit / query / download / kill "
        "jobs, list available images / machines."
    )
```

- [ ] **Step 10: tool.py —— action enum 改名**

把 tool.py:176-184 enum 里的 `"poll",` 改成 `"query",`:

```python
                "enum": [
                    "submit",
                    "query",
                    "download",
                    "kill",
                    "list_images",
                    "list_machines",
                ],
```

- [ ] **Step 11: tool.py —— job_id 参数描述改名**

把 tool.py:212-215:

```python
            "job_id": {
                "type": ["integer", "string"],
                "description": "Job ID returned by submit. (poll, download, kill)",
            },
```

改成:

```python
            "job_id": {
                "type": ["integer", "string"],
                "description": "Job ID returned by submit. (query, download, kill)",
            },
```

- [ ] **Step 12: tool.py —— 重写 prompt() 的 query/download/kill 段**

把 tool.py:297-307 的三段(poll/download/kill)替换为(去掉一切"阻塞等待"语义):

```python
            '- **query**: query a job\'s current status in a single call and '
            'return immediately — no blocking, no internal waiting. After '
            'submitting a job you do NOT need to repeatedly query and wait: '
            'long-running monitoring happens automatically in the background, '
            'and a job\'s completion will be surfaced in later context. Only '
            'call query when you actively need to confirm one job\'s current '
            'status, by single job_id. Does not download artifacts.\n'
            '- **download**: download artifacts for a finished or failed job into result_dir. '
            'Use only after query reports Finished or Failed. Requires result_dir; '
            'retrieves logs and artifacts for analysis.\n'
            '- **kill**: request termination of a previously submitted job. Use only when '
            'the user explicitly wants to stop a running job. The call is '
            'asynchronous; follow up with query to confirm terminal state.\n'
```

- [ ] **Step 13: tool.py —— 删短轮询常量**

删除 tool.py:319-321:

```python
    # Short-polling constants
    _POLL_INTERVAL: ClassVar[int] = 5  # seconds between API checks
    _POLL_MAX_WAIT: ClassVar[int] = 60  # max seconds to block per poll call
```

- [ ] **Step 14: tool.py —— execute_with_context 删 poll 分支 + 注册表白名单加 query(gap 2)**

删除 tool.py:338-339:

```python
        if action == "poll":
            return await self._poll_with_short_loop(arguments, registry)

```

并把 tool.py:348:

```python
        if registry is not None and action in ("submit", "download", "kill"):
```

改成:

```python
        if registry is not None and action in ("submit", "query", "download", "kill"):
```

(删 poll 分支后,docstring tool.py:328 `"""Registry-aware execution with internal short-polling for poll action."""` 改为 `"""Registry-aware execution; query/submit/download/kill share one path."""`。)

- [ ] **Step 15: tool.py —— 整段删除 `_poll_with_short_loop`**

删除 tool.py:358-415(`async def _poll_with_short_loop(...)` 整个方法体,到 `return last_result` 为止,含其上方空行)。删完后 `_update_registry` 紧跟 `execute_with_context`。

- [ ] **Step 16: tool.py —— `_update_registry` 的 poll 分支改 query**

把 tool.py:449-451:

```python
        if action == "poll":
            reg_status = classify_poll_status(str(data.get("status", "unknown")))
            registry.update_poll(job_id, status=reg_status, result=result.content)
```

改成(分发字符串改 `"query"`,内部方法 `update_poll`/`classify_poll_status` 保留):

```python
        if action == "query":
            reg_status = classify_poll_status(str(data.get("status", "unknown")))
            registry.update_poll(job_id, status=reg_status, result=result.content)
```

- [ ] **Step 17: tool.py —— `_execute` 的 match 分支与未知 action 提示改 query**

把 tool.py:479-480:

```python
            case "poll":
                return self._poll(arguments)
```

改成:

```python
            case "query":
                return self._query(arguments)
```

并把 tool.py:493 未知 action 提示:

```python
                    f"Must be one of: submit, poll, download, kill, "
```

改成:

```python
                    f"Must be one of: submit, query, download, kill, "
```

- [ ] **Step 18: tool.py —— `_poll` 重命名 `_query`,逻辑原样保留,日志标签改 query**

把 tool.py:577 方法签名:

```python
    def _poll(self, args: dict[str, Any]) -> ToolResult:
```

改成:

```python
    def _query(self, args: dict[str, Any]) -> ToolResult:
```

把方法体内 tool.py:584-592 的 `result_dir` 拒绝提示里的 `poll`:

```python
        if args.get("result_dir"):
            return ToolResult(
                status="error",
                content=(
                    "poll no longer downloads artifacts. "
                    f'Use Bohrium(action="download", job_id={raw_job_id!r}, '
                    f'result_dir="results/run_{raw_job_id}") instead.'
                ),
            )
```

改成:

```python
        if args.get("result_dir"):
            return ToolResult(
                status="error",
                content=(
                    "query no longer downloads artifacts. "
                    f'Use Bohrium(action="download", job_id={raw_job_id!r}, '
                    f'result_dir="results/run_{raw_job_id}") instead.'
                ),
            )
```

把 tool.py:598 日志标签 `action="poll"` 改 `action="query"`:

```python
            self._log_request_context(action="query", ctx=ctx, sandbox=sandbox)
```

把 tool.py:666 错误日志:

```python
                "bohrium poll failed action=poll base_url=%s sandbox=%s error=%s",
```

改成:

```python
                "bohrium query failed action=query base_url=%s sandbox=%s error=%s",
```

把 tool.py:672 错误返回 `"Poll failed: {exc}"` 改 `"Query failed: {exc}"`:

```python
            return ToolResult(status="error", content=f"Query failed: {exc}")
```

(其余逻辑——`get_job_detail` + `confirm_terminal_status` + `_safe_ledger("record_poll", ...)`(ledger 动作词保留)+ download 提示 message + sandbox `_fetch_log_tail`——**一行不动**。)

- [ ] **Step 19: tool.py —— `_kill` 返回消息把 action="poll" 改 query**

把 tool.py:823:

```python
                            f'Bohrium(action="poll", job_id={job_id!r}) '
```

改成:

```python
                            f'Bohrium(action="query", job_id={job_id!r}) '
```

- [ ] **Step 20: tool.py —— 模块 docstring 顺手去掉短轮询描述**

把 tool.py:4-12 docstring 里提到 poll 短轮询的两处:

```python
This tool handles pure communication: submit, poll (short-polling loop),
download, kill, list_images, list_machines. All software-specific knowledge
lives in software skills.

Design decisions:
- poll uses an internal short-polling loop (up to 60s, ~8s interval) so
  the agent never needs to sleep between polls
```

改成:

```python
This tool handles pure communication: submit, query (single-shot status),
download, kill, list_images, list_machines. All software-specific knowledge
lives in software skills.

Design decisions:
- query returns the job's current status in a single call; long-running
  monitoring lives in the separate monitor process, not in the agent
```

- [ ] **Step 21: registry.py —— rebuild 回放动作词 poll → query(gap 1)**

把 registry.py:141:

```python
            elif action == "poll":
```

改成:

```python
            elif action == "query":
```

- [ ] **Step 22: 运行全部前台测试,确认转绿**

Run:
```bash
uv run pytest tests/matmaster/tools/builtin/test_bohrium_tool_query.py \
  tests/matmaster/tools/builtin/test_bohrium_tool_ledger.py \
  tests/matmaster/tools/builtin/test_bohrium_tool_prompt_rebalance.py \
  tests/matmaster/tools/builtin/test_bohrium_tool_session_credentials.py \
  tests/matmaster/tools/builtin/test_bohrium_tool.py \
  tests/matmaster/tools/builtin/test_bohrium_registry.py \
  tests/matmaster/core/test_exp_runtime_v2.py \
  tests/test_chat_events_table_spawn_id.py \
  tests/matmaster/integration/test_runtime_credential_bridge_e2e.py -q
```
Expected: PASS(全部通过;需 MySQL 的用例可 SKIP,但 `test_exp_runtime_v2` 的 registry rebuild 断言不依赖 DB,必须 PASS)。

- [ ] **Step 23: 搜残留,确认无 Bohrium `poll` action 与短轮询符号(精确扫描)**

用 `rg` 而非 `grep`:排除 `__pycache__`,只锁 Bohrium action 形态(含 JSON fixture 形态),**不**把全仓任意 `def _poll` 当残留(`src/worker/agent_worker.py:217` 有个无关的 stop-request `_poll`,不在 Bohrium 范围):

Run:
```bash
rg -n 'action="poll"|"action"[[:space:]]*:[[:space:]]*"poll"|Bohrium\(action="poll"|_poll_with_short_loop|_POLL_MAX_WAIT|_POLL_INTERVAL|case "poll"|def _poll\(self, args' matmaster src tests -g '!**/__pycache__/**'
```
Expected: 无输出。
> 说明:`record_poll` / `apply_poll` / `update_poll` / `classify_poll_status` / `poll_count` / `next_poll_at` / `compute_poll_backoff` / `_poll_one` 是 ledger/registry/poller 的动作词与列名,**故意保留**,不在上述模式内;`agent_worker._poll(self)`(无 `args` 形参)也不会被 `def _poll\(self, args` 命中。

- [ ] **Step 24: 提交**

```bash
git add matmaster/tools/builtin/bohrium_tool/tool.py \
        matmaster/tools/builtin/bohrium_tool/registry.py \
        tests/matmaster/tools/builtin/test_bohrium_tool_query.py \
        tests/matmaster/tools/builtin/test_bohrium_tool_ledger.py \
        tests/matmaster/tools/builtin/test_bohrium_tool_prompt_rebalance.py \
        tests/matmaster/tools/builtin/test_bohrium_tool_session_credentials.py \
        tests/matmaster/tools/builtin/test_bohrium_tool.py \
        tests/matmaster/tools/builtin/test_bohrium_registry.py \
        tests/matmaster/core/test_exp_runtime_v2.py \
        tests/test_chat_events_table_spawn_id.py \
        tests/matmaster/integration/test_runtime_credential_bridge_e2e.py
git commit -m "feat(bohrium): migrate tool poll action to single-shot query

Delete blocking short-poll loop; query returns current status immediately.
Fix registry event replay + update whitelist for the new action word."
```

---

## Task 2: 后台 `BohriumMonitor` 巡检单元

**Files:**
- Modify: `src/services/bohrium_poller.py`(新增 `_env_int` + `BohriumMonitor`)
- Modify: `tests/services/test_bohrium_poller.py`(追加 `BohriumMonitor` 测试)

策略:`BohriumMonitor` 是新代码,纯 TDD——先写失败测试,再实现。用假 poller 注入,不依赖 MySQL。

---

- [ ] **Step 1: 在 `test_bohrium_poller.py` 末尾追加 BohriumMonitor 测试**

> 关键:`BohriumMonitor` 默认构造**不得触发 DB**——`BohriumJobPoller()`(进而 `BohriumJobsTable()` → `BaseTable.__init__` → `init_table()` 连 MySQL,见 `src/base/base_table.py:32`)必须惰性留到 `tick()` 内、被同一个 `try` 兜住。下面的测试 3/4 专门覆盖"无注入 poller 的默认路径",这是 stub 注入测试覆盖不到的契约面。

追加(放文件末尾;`BohriumMonitor` / `_env_int` 尚未实现,导入会失败):

```python
class _StubPoller:
    def __init__(self, summary=None, exc=None):
        self._summary = summary or {"claimed": 0, "polled": 0, "errors": 0}
        self._exc = exc
        self.calls: list[dict] = []

    def run_once(self, *, limit, claim_timeout_seconds):
        self.calls.append({"limit": limit, "claim_timeout_seconds": claim_timeout_seconds})
        if self._exc is not None:
            raise self._exc
        return self._summary


def test_monitor_tick_passes_through_summary() -> None:
    from src.services.bohrium_poller import BohriumMonitor

    stub = _StubPoller(summary={"claimed": 3, "polled": 2, "errors": 1})
    monitor = BohriumMonitor(poller=stub, limit=7, claim_timeout_seconds=99)

    summary = monitor.tick()

    assert summary == {"claimed": 3, "polled": 2, "errors": 1}
    assert stub.calls == [{"limit": 7, "claim_timeout_seconds": 99}]


def test_monitor_tick_swallows_injected_poller_exception() -> None:
    from src.services.bohrium_poller import BohriumMonitor

    stub = _StubPoller(exc=RuntimeError("db down"))
    monitor = BohriumMonitor(poller=stub)

    summary = monitor.tick()

    assert summary == {"claimed": 0, "polled": 0, "errors": 0, "tick_failed": 1}


def test_monitor_default_construct_is_db_free_and_lazy(monkeypatch) -> None:
    """默认构造(无注入 poller)不连库;poller 在 tick() 内惰性构造并被 try 兜住。"""
    import src.services.bohrium_poller as mod

    class _BoomPoller:
        def __init__(self):
            raise RuntimeError("no DB at construct time")

    monkeypatch.setattr(mod, "BohriumJobPoller", _BoomPoller)

    # 若 __init__ 仍 eager 构造 poller,这一行就会抛 —— 能走到 tick() 即证明惰性
    monitor = mod.BohriumMonitor()
    summary = monitor.tick()

    assert summary == {"claimed": 0, "polled": 0, "errors": 0, "tick_failed": 1}


def test_monitor_default_construct_reads_env_into_run_once(monkeypatch) -> None:
    import src.services.bohrium_poller as mod

    monkeypatch.setenv("BOHRIUM_MONITOR_LIMIT", "8")
    monkeypatch.setenv("BOHRIUM_MONITOR_CLAIM_TIMEOUT", "33")
    captured: dict[str, int] = {}

    class _StubDefaultPoller:
        def __init__(self):
            pass

        def run_once(self, *, limit, claim_timeout_seconds):
            captured["limit"] = limit
            captured["claim_timeout_seconds"] = claim_timeout_seconds
            return {"claimed": 0, "polled": 0, "errors": 0}

    monkeypatch.setattr(mod, "BohriumJobPoller", _StubDefaultPoller)

    mod.BohriumMonitor().tick()

    assert captured == {"limit": 8, "claim_timeout_seconds": 33}


def test_env_int_missing_and_invalid_fall_back(monkeypatch) -> None:
    from src.services.bohrium_poller import _env_int

    monkeypatch.delenv("BOHRIUM_X", raising=False)
    assert _env_int("BOHRIUM_X", 5) == 5
    monkeypatch.setenv("BOHRIUM_X", "not-an-int")
    assert _env_int("BOHRIUM_X", 5) == 5
    monkeypatch.setenv("BOHRIUM_X", "12")
    assert _env_int("BOHRIUM_X", 5) == 12
```

- [ ] **Step 2: 运行 BohriumMonitor 测试,确认转红**

Run:
```bash
uv run pytest tests/services/test_bohrium_poller.py -q -k "monitor or env_int"
```
Expected: FAIL —— `ImportError: cannot import name 'BohriumMonitor'` / `_env_int`。

- [ ] **Step 3: 在 `bohrium_poller.py` 顶部加 `import os`**

把文件顶部(line 5 区域)的 import:

```python
import logging
from collections.abc import Callable
from typing import Any
```

改成:

```python
import logging
import os
from collections.abc import Callable
from typing import Any
```

- [ ] **Step 4: 在 `bohrium_poller.py` 末尾新增 `_env_int` + `BohriumMonitor`**

在文件最末(`BohriumJobPoller` 之后)追加:

```python
def _env_int(name: str, default: int) -> int:
    """读环境变量为 int;缺失或非法值回退默认。不引第三方配置。"""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning("invalid int env %s=%r, using default %d", name, raw, default)
        return default


class BohriumMonitor:
    """monitor 进程的 bohrium 巡检单元:每轮 claim 到期作业、poll、写回 ledger。

    设计为可嵌入 src/monitor/monitor_worker.py 的 _run_monitor_loop:
    循环框架 / 退出信号 / 间隔 / 日志由进程外壳负责,本类只提供单轮 tick()。

    poller 惰性构造:默认 BohriumJobPoller() 会经 BohriumJobsTable ->
    BaseTable.__init__ 立即连 MySQL(src/base/base_table.py),因此把它延到
    tick() 内、和 run_once 同处一个 try——这样无库环境下 __init__ 不会爆,
    首轮 tick 连库失败也只是返回 tick_failed=1 并在下一轮自愈重试。
    """

    def __init__(
        self,
        *,
        poller: BohriumJobPoller | None = None,
        limit: int | None = None,
        claim_timeout_seconds: int | None = None,
    ) -> None:
        # 不在这里 eager 构造 poller(否则 __init__ 即连库)。None 表示惰性。
        self._poller = poller
        self._limit = limit if limit is not None else _env_int("BOHRIUM_MONITOR_LIMIT", 50)
        self._claim_timeout = (
            claim_timeout_seconds
            if claim_timeout_seconds is not None
            else _env_int("BOHRIUM_MONITOR_CLAIM_TIMEOUT", 120)
        )

    def tick(self) -> dict[str, int]:
        """单轮巡检。吞 poller 构造 / claim / DB 级异常,保证调用方循环不被打断。

        返回 BohriumJobPoller.run_once 的 summary(claimed / polled / errors);
        本轮整体失败(含首轮 poller 构造失败)时返回 tick_failed=1,其余计数为 0。
        """
        try:
            if self._poller is None:
                # 惰性构造:成功后缓存复用;构造抛错则保持 None,下一轮再试(自愈)。
                self._poller = BohriumJobPoller()
            return self._poller.run_once(
                limit=self._limit, claim_timeout_seconds=self._claim_timeout
            )
        except Exception:  # noqa: BLE001 — 进程级兜底:单轮失败不拖垮长跑进程
            logger.warning("bohrium monitor tick failed", exc_info=True)
            return {"claimed": 0, "polled": 0, "errors": 0, "tick_failed": 1}
```

- [ ] **Step 5: 运行 BohriumMonitor 测试,确认转绿**

Run:
```bash
uv run pytest tests/services/test_bohrium_poller.py -q -k "monitor or env_int"
```
Expected: PASS(5 个新测试通过,含两个默认构造/惰性路径测试)。这些测试都不依赖 MySQL。

- [ ] **Step 6: 跑整个 poller 测试文件,确认没碰坏现有 `run_once` 覆盖**

Run:
```bash
uv run pytest tests/services/test_bohrium_poller.py -q
```
Expected: PASS,或现有需要 MySQL 的用例 SKIP(`bohrium_jobs poller tests require MySQL from .env.test`)——新加的 4 个 monitor 测试不依赖 DB,必须 PASS。

- [ ] **Step 7: 提交**

```bash
git add src/services/bohrium_poller.py tests/services/test_bohrium_poller.py
git commit -m "feat(bohrium): add BohriumMonitor tick unit for monitor process

Thin wrapper over BohriumJobPoller.run_once with process-level error
guard; reads BOHRIUM_MONITOR_LIMIT / _CLAIM_TIMEOUT env. tick() never raises."
```

---

## Task 3: 回归与净代码核对(仅验证,无新代码)

**Files:** 无修改。

---

- [ ] **Step 1: 跑全部 bohrium 相关测试 + 受影响的 rebuild fixture 路径**

Run:
```bash
uv run pytest tests/matmaster/tools/builtin/ tests/services/test_bohrium_poller.py \
  tests/services/test_bohrium_jobs_wiring.py \
  tests/matmaster/core/test_exp_runtime_v2.py \
  tests/test_chat_events_table_spawn_id.py \
  tests/matmaster/integration/test_runtime_credential_bridge_e2e.py -q
```
Expected: PASS(需 MySQL 的用例可 SKIP,其余全绿;`test_exp_runtime_v2` registry rebuild 断言不依赖 DB,必须 PASS)。

- [ ] **Step 2: 精确扫描,确认无遗留 Bohrium poll 残留**

Run(同 Task 1 Step 23,排除 `__pycache__`,含 JSON fixture 形态,不误报无关 `_poll`):
```bash
rg -n 'action="poll"|"action"[[:space:]]*:[[:space:]]*"poll"|Bohrium\(action="poll"|_poll_with_short_loop|_POLL_MAX_WAIT|_POLL_INTERVAL|case "poll"|def _poll\(self, args' matmaster src tests -g '!**/__pycache__/**'
```
Expected: 无输出。

- [ ] **Step 3: 净代码核对(spec §9:整体净代码持平或略降)**

Run:
```bash
git diff --stat test...HEAD -- matmaster/tools/builtin/bohrium_tool/tool.py \
  matmaster/tools/builtin/bohrium_tool/registry.py src/services/bohrium_poller.py
```
Expected: tool.py 净删(删 ~60 行短轮询 > 加的标签/prompt 改动);bohrium_poller.py 净增 ~35 行(`_env_int` + `BohriumMonitor`);整体大致持平或略降。若 tool.py 净增,回看是否漏删 `_poll_with_short_loop` 整段。

- [ ] **Step 4: 对接契约自检(交付面)**

确认满足 spec §5.2 / §10 契约,无需改码,仅核对:
- `BohriumMonitor()` 无必填参数即可构造,且**构造不触发 DB**——poller(及其 `BohriumJobsTable` 连库)在 `tick()` 内惰性构造、被同一 `try` 兜住(由 `test_monitor_default_construct_is_db_free_and_lazy` 守护)✓
- `tick() -> dict[str, int]` 不抛异常(异常路径返回 `tick_failed=1`)✓
- 不调 `trigger_run`、不调 `mark_handled`(本类只 `run_once`,不触碰 completion scheduler)✓
- 未碰 `src/monitor/`、未碰 DB schema、未碰 `next_poll_at`/`poll_count` 列名 ✓

---

## Self-Review(已对 spec 逐条核对)

**Spec 覆盖:**
- §4 tool poll→query 表格 9 行 → Task 1 Step 9-20 全覆盖(enum / description / execute_with_context 删分支 / 删短轮询+常量 / match / _poll→_query / _update_registry / 未知提示 / prompt 重写)✓
- §4 prompt query 段三要点(立即返回 / 后台自动 / 仅主动确认时调) → Task 1 Step 12 ✓
- §4 前台 query 写 ledger 语义(`record_poll` + 30s backoff 保留) → `_query` 逻辑原样保留(Step 18 明确不动 ledger 调用)✓
- §5.1 `BohriumMonitor` → Task 2 Step 4 落地(复用 poller、不持循环、`_env_int`);**对 spec §5.1 的一处修正**:spec 给的 `__init__` eager `BohriumJobPoller()` 会立即连库(`BaseTable.__init__` → `init_table()`),违反 §5.2"`BohriumMonitor()` 无参可构造 + `tick()` 不抛"。改为惰性构造(poller 延到 `tick()` 内、同 `try`),语义不变、契约真正成立 ✓
- §5.2 契约 → Task 3 Step 4 自检 + `test_monitor_default_construct_is_db_free_and_lazy` 守护 ✓
- §6 两个环境变量(LIMIT=50 / CLAIM_TIMEOUT=120) → Task 2 Step 4 + 测试 Step 1 ✓
- §7 错误语义(批级 tick 兜底 → tick_failed=1;前台异常返回可见 ToolResult) → Task 2 Step 4 + Task 1 `_query` 保留 ✓
- §8 测试(query 语义、确认短轮询已删、ledger action 同步、其余文件迁移、poller 保留、BohriumMonitor 轻量测试) → Task 1 Step 2-7c + Task 2 Step 1(5 个新测试:passthrough / 注入异常吞掉 / 默认构造无库且惰性 / 默认构造读 env / `_env_int` 回退)✓
- §9 净代码 → Task 3 Step 3 ✓
- §10 协调(独立可推进、唯一对接面 tick) → Task 2 独立、Task 3 Step 4 ✓
- Non-Goals(不写进程外壳 / 不碰 completion scheduler / 不改 schema / 不抽共享层 / 不留 alias) → 全程未触及 `src/monitor/`、`trigger_run`、DB、未建共享 service、Step 23 验证无 alias ✓
- **超出 spec 但必要(gap 1/2)** → Task 1 Step 7/14/21 + 背景章节已显式标注,理由:不改则静默破坏注册表回放与更新 ✓
- **gap 1 的连带 fixture 迁移** → Task 1 Step 7a/7b/7c:`test_exp_runtime_v2.py` 是真断点(回放断言 `poll_count==1`,不迁必爆);`test_chat_events_table_spawn_id.py`(过时透传 fixture)与 `test_runtime_credential_bridge_e2e.py`(迁移后会静默假通过)一并迁移;验证集已在 Step 8/22 与 Task 3 Step 1 扩到这三条路径 ✓
- **残留扫描精度** → Step 23 / Task 3 Step 2 改用 `rg`:排 `__pycache__`、含 `"action": "poll"` JSON 形态、不误报 `agent_worker._poll`(用 `def _poll\(self, args` 锁定 Bohrium 签名)✓

**Placeholder 扫描:** 无 TBD / "适当处理" / "类似 Task N" / 无代码的步骤——每个改动步骤都给了完整前/后代码块。

**类型/命名一致性:** `BohriumMonitor.tick() -> dict[str, int]`、`_env_int(name, default)`、`run_once(limit, claim_timeout_seconds)` 全程一致;工具侧 `_query` / action `"query"` / registry 分发 `"query"` 三处对齐;ledger 动作词 `record_poll` 与 registry 方法 `update_poll`/`classify_poll_status` 一致保留不改。
