# E3 Incremental Message Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `AgentKernel` 主循环里每轮全量 `canonicalize + normalize + validate` 改成增量复用 prefix 缓存的 `IncrementalMessagePipeline`，把 message normalization 从 O(turns² × avg_msg_size) 降到 O(turns × k × avg_msg_size)。

**Architecture:** 新建 `matmaster/core/message_pipeline.py`，里面是 `IncrementalMessagePipeline` class（持久 prefix 缓存）+ module-private `_ToolTurnValidator`（增量 tool-turn 状态机）。`_KernelState` 持有一个 pipeline 实例；`agent.py:287-289` 用 `pipeline.feed_tail()` 替换原来的纯函数链；`agent_compaction.py` 在 compactor 路径后显式 `pipeline.reset()`。同时给 `ToolCallData.arguments_json` 加 `cached_property` 消除 `json.dumps` 的乘性因子。`message_normalization.py` 的 4 个纯函数**保持不变**，作为 checkpoint codec 路径和 `revalidate_full` 的参考实现。

**Tech Stack:** Python 3.10+, pydantic v2, pytest, stdlib `time.perf_counter`（避免引入 pytest-benchmark 新依赖）, `functools.cached_property`。

**Spec:** [docs/superpowers/plans/../specs/2026-05-17-e3-incremental-message-pipeline-design.md](../specs/2026-05-17-e3-incremental-message-pipeline-design.md)

**特别约束（来自 user CLAUDE.md）：** 绝对不向 `docs/` 提交 git 改动。Task 5 的 docs 改动**不**走 commit step，由用户自行处理。Source code 改动（`matmaster/`, `tests/`, `benchmarks/`, `scripts/`）正常 commit。

---

## File Structure

| 文件 | 操作 | 责任 |
|------|------|------|
| `benchmarks/test_message_pipeline_perf.py` | 创建 | 性能 baseline + 改善实测，使用 stdlib timing |
| `matmaster/core/message_pipeline.py` | 创建 | `IncrementalMessagePipeline` + `_ToolTurnValidator` + `_to_normalized_api_dict` helper |
| `tests/matmaster/core/test_message_pipeline.py` | 创建 | 14+ 个单元测试 + invariant 双约束测试 + R2 守护测试组 |
| `scripts/lint_no_arguments_mutation.py` | 创建 | CI 静态 grep，检测 `ToolCallData.arguments` 深层 mutation |
| `matmaster/types/messages.py` | 修改（行 32-41, 223-243） | `ToolCallData` 加 `frozen=True` + `arguments_json` cached_property；`AssistantMessage.to_api_dict` 用 cache |
| `matmaster/core/kernel_items.py` | 修改（行 31-38） | `_KernelState` 新增 `pipeline` 字段 |
| `matmaster/core/agent.py` | 修改（行 287-289） | 用 `state.pipeline.feed_tail(state.messages)` 替换纯函数链 |
| `matmaster/core/agent_compaction.py` | 修改（行 88-89 之间） | 在 try/except 之后、`messages_after = ...` 之前加 `state.pipeline.reset()` |
| `matmaster/core/tool_runner.py` | 修改（行 239 附近） | 在 module docstring 或 `FullToolRunner.execute_batch` docstring 加 contract："tool 实现不得 mutate `arguments`" |
| `docs/superpowers/plans/2026-05-17-core-refactor-deferred-simplifications.md` | 修改 | **不 commit**——删除整段 E3 section（line 432-501 左右） |

---

## Task 1: Build Benchmark Baseline

**目的：** 在改任何代码前先建立性能基线，后续每个 task 完成后可以对比改善幅度。使用 stdlib `time.perf_counter` 而非 pytest-benchmark（仓库未装）。

**Files:**
- Create: `benchmarks/test_message_pipeline_perf.py`
- Create: `benchmarks/__init__.py`（如果不存在）

### Step 1.1: 创建 benchmarks 目录与空 `__init__.py`

- [ ] **创建 benchmarks/__init__.py**

Run:
```bash
mkdir -p /Users/kealdoom/Developer/dp/matmaster/matmaster-evo/benchmarks
touch /Users/kealdoom/Developer/dp/matmaster/matmaster-evo/benchmarks/__init__.py
```

Expected: 目录和空文件创建成功。

### Step 1.2: 写 benchmark fixture builder

- [ ] **创建 `benchmarks/test_message_pipeline_perf.py`，先写 fixture builder + 纯函数 baseline**

```python
"""Performance baseline for E3 message pipeline optimization.

Uses stdlib time.perf_counter (no pytest-benchmark dependency).
This file lives under benchmarks/ which is NOT collected by the default
test path (pytest.ini sets testpaths = tests). Run explicitly:
    uv run pytest benchmarks/test_message_pipeline_perf.py -v -s
"""

from __future__ import annotations

import json
import os
import time
from unittest.mock import patch

import pytest

from matmaster.types.messages import (
    AssistantMessage,
    SystemMessage,
    ToolCallData,
    ToolMessage,
    UserMessage,
)
from matmaster.types.message_normalization import (
    canonicalize_messages_for_provider,
    normalize_and_validate_openai_messages,
)


def _build_fixture(num_turns: int, calls_per_turn: int, arg_size_bytes: int):
    """Build a legal message sequence of num_turns × calls_per_turn tool calls.

    Each tool_call's arguments is a dict containing one string of approximately
    arg_size_bytes. ToolCallData instances are fresh; callers should rebuild
    the fixture between pure-path and pipeline-path runs to avoid cross-run
    arguments_json cache pollution.
    """
    big_payload = "x" * arg_size_bytes
    messages: list = [
        SystemMessage(content="You are a helpful assistant."),
        UserMessage(content="Help me with this task."),
    ]
    for turn in range(num_turns):
        tool_calls = [
            ToolCallData(
                id=f"call_{turn}_{i}",
                name="search",
                arguments={"query": f"q{turn}_{i}", "payload": big_payload},
            )
            for i in range(calls_per_turn)
        ]
        messages.append(
            AssistantMessage(
                content=None,
                tool_calls=tool_calls,
                reasoning_content=None,
            )
        )
        for tc in tool_calls:
            messages.append(
                ToolMessage(
                    tool_call_id=tc.id,
                    tool_name=tc.name,
                    content=f"result for {tc.id}",
                )
            )
    return messages


FIXTURE_CONFIGS = [
    ("small", 10, 3, 500),
    ("medium", 30, 3, 2048),
    ("large", 50, 5, 5120),
]


def _run_pure_path_per_turn(messages: list) -> tuple[float, int]:
    """Simulate the main loop: walk prefix-by-prefix, one turn at a time.

    Each iteration runs canonicalize + normalize + validate on the prefix.
    Returns (wall_time_seconds, json_dumps_call_count).
    """
    dumps_count = 0
    orig_dumps = json.dumps

    def counting_dumps(*args, **kwargs):
        nonlocal dumps_count
        dumps_count += 1
        return orig_dumps(*args, **kwargs)

    # 模拟主循环 prefix 增长：找出每个 turn 结束点（一个 assistant + 它的 tool messages 都到位）
    boundaries = [2]  # system + first user
    i = 2
    while i < len(messages):
        if isinstance(messages[i], AssistantMessage) and messages[i].tool_calls:
            # 跳过一个 turn：1 个 assistant + N 个 tool messages
            i += 1 + len(messages[i].tool_calls)
            boundaries.append(i)
        else:
            i += 1

    start = time.perf_counter()
    with patch("json.dumps", side_effect=counting_dumps):
        for end in boundaries:
            prefix = messages[:end]
            normalize_and_validate_openai_messages(
                canonicalize_messages_for_provider(prefix)
            )
    elapsed = time.perf_counter() - start
    return elapsed, dumps_count


@pytest.mark.parametrize("label,num_turns,calls,arg_size", FIXTURE_CONFIGS)
def test_pure_path_baseline(label: str, num_turns: int, calls: int, arg_size: int):
    messages = _build_fixture(num_turns, calls, arg_size)
    elapsed, dumps_count = _run_pure_path_per_turn(messages)
    print(
        f"\n[BASELINE pure] fixture={label} "
        f"turns={num_turns} calls/turn={calls} arg_size={arg_size}B "
        f"wall={elapsed * 1000:.2f}ms json.dumps_calls={dumps_count}"
    )
```

### Step 1.3: 跑 baseline 并记录数字

- [ ] **运行 baseline benchmark，把输出粘进 commit message**

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo
uv run pytest benchmarks/test_message_pipeline_perf.py -v -s 2>&1 | tee /tmp/e3_baseline.txt
```

Expected: 3 个测试全 PASS，输出包含 3 条 `[BASELINE pure]` 行，每行有 `wall=...ms` 和 `json.dumps_calls=...`。记下这 3 行作为 Task 5 的对比基准。

### Step 1.4: Commit baseline

- [ ] **提交 baseline benchmark**

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo
git add benchmarks/__init__.py benchmarks/test_message_pipeline_perf.py
git status  # 确认只 stage 这两个新文件
git commit -m "$(cat <<'EOF'
test(perf): add message pipeline baseline benchmark

Establish O(turns² × avg_msg_size) baseline for E3 fix. Uses stdlib
time.perf_counter (no pytest-benchmark dep). Three fixtures: small/medium/large
to capture both constant-factor and scaling improvements.
EOF
)"
```

Expected: commit 成功。

---

## Task 2: Cache `ToolCallData.arguments_json` + R2 Guards

**目的：** 在循环结构不变前先消除 `json.dumps(tc.arguments)` 的乘性因子——这是 E3 性能问题的第二层（参见 spec §1.2）。同时建立 R2 三层守护：CI 静态 grep + 纯本地 sample 测试 + synthetic tool contract 测试。

**Files:**
- Modify: `matmaster/types/messages.py:32-41`（ToolCallData）
- Modify: `matmaster/types/messages.py:223-243`（AssistantMessage.to_api_dict）
- Modify: `matmaster/core/tool_runner.py:239` 附近（contract docstring）
- Create: `tests/matmaster/types/test_tool_call_data_caching.py`
- Create: `scripts/lint_no_arguments_mutation.py`
- Create/extend: `tests/matmaster/core/test_tool_runner_arguments_contract.py`

### Step 2.1: 写第一个 failing test：`arguments_json` 命中缓存

- [ ] **创建 `tests/matmaster/types/test_tool_call_data_caching.py`**

```python
"""Tests for ToolCallData.arguments_json caching (E3 fix layer 1)."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from matmaster.types.messages import ToolCallData


def test_arguments_json_cached_once():
    """第二次访问 arguments_json 不应再调 json.dumps。"""
    tc = ToolCallData(id="c1", name="search", arguments={"q": "hello", "n": 5})
    orig_dumps = json.dumps
    call_count = 0

    def counting(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return orig_dumps(*args, **kwargs)

    with patch("matmaster.types.messages.json.dumps", side_effect=counting):
        first = tc.arguments_json
        second = tc.arguments_json
        third = tc.arguments_json

    assert first == second == third
    assert call_count == 1, f"expected 1 json.dumps call, got {call_count}"
    assert json.loads(first) == {"q": "hello", "n": 5}


def test_arguments_json_equivalent_to_direct_dumps():
    """arguments_json 与 json.dumps(tc.arguments) 字符串等价。"""
    args = {"complex": {"nested": [1, 2, 3]}, "str": "value"}
    tc = ToolCallData(id="c1", name="search", arguments=args)
    assert tc.arguments_json == json.dumps(args)


def test_tool_call_data_frozen_blocks_field_rebind():
    """frozen=True 阻止字段重绑定（第一层防御）。"""
    tc = ToolCallData(id="c1", name="search", arguments={"q": "x"})
    with pytest.raises((TypeError, ValueError, Exception)):
        # pydantic v2 frozen 抛 ValidationError 或类似
        tc.arguments = {"q": "y"}  # type: ignore[misc]
```

### Step 2.2: 运行测试，确认 FAIL（`arguments_json` 不存在）

- [ ] **跑测试看到失败信号**

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo
uv run pytest tests/matmaster/types/test_tool_call_data_caching.py -v
```

Expected: 3 个测试全 FAIL，原因是 `AttributeError: 'ToolCallData' object has no attribute 'arguments_json'`，以及 frozen 测试期望抛错但不抛（因为 frozen 还没启用）。

### Step 2.3: 修改 ToolCallData——加 frozen + cached_property

- [ ] **打开 `matmaster/types/messages.py`，先在 import 段加 `cached_property` 和 `ConfigDict`**

修改 `matmaster/types/messages.py:11-16`：

```python
from __future__ import annotations

import json
import logging
from enum import Enum
from functools import cached_property
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
```

### Step 2.4: 给 ToolCallData 加 model_config 与 arguments_json

修改 `matmaster/types/messages.py:32-41`，替换整个 ToolCallData 定义：

```python
class ToolCallData(BaseModel):
    """A single tool call requested by the LLM.

    arguments is dict[str, Any], not raw JSON string -- parsing is done
    at the provider boundary.

    **Immutability contract (E3 fix layer 1):**
    - frozen=True blocks field rebinding (`tc.arguments = ...`).
    - Nested mutation of `arguments` (e.g. `tc.arguments["k"] = v`) is
      NOT blocked by frozen and is forbidden by convention -- it would
      stale `arguments_json` cache. Tool executors must not mutate input
      arguments; construct a new ToolCallData if a change is needed.
    - Do NOT use `model_copy(update={"arguments": ...})`: it would carry
      the stale cached `arguments_json`. Construct a fresh instance.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    arguments: dict[str, Any]

    @cached_property
    def arguments_json(self) -> str:
        """JSON-serialized arguments, cached once per instance."""
        return json.dumps(self.arguments)
```

### Step 2.5: 改 AssistantMessage.to_api_dict 用缓存

修改 `matmaster/types/messages.py:223-243`，把 `json.dumps(tc.arguments)` 改成 `tc.arguments_json`：

```python
class AssistantMessage(Message):
    """Assistant (LLM) response message.

    May include tool_calls when the LLM requests tool invocations.
    to_api_dict() includes tool_calls only when present (not None).
    """

    role: Role = Role.ASSISTANT
    tool_calls: list[ToolCallData] | None = None
    reasoning_content: str | None = None

    def to_api_dict(self) -> dict[str, Any]:
        """Convert to OpenAI API-compatible dict.

        Includes tool_calls only when self.tool_calls is not None.
        Each tool call formatted as:
        {"id": ..., "type": "function", "function": {"name": ..., "arguments": json_str}}
        Uses ToolCallData.arguments_json cached property to avoid repeated
        json.dumps work in the main loop hot path.
        """
        d: dict[str, Any] = {"role": self.role.value, "content": self.content}
        if self.tool_calls is not None:
            d["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": tc.arguments_json,
                    },
                }
                for tc in self.tool_calls
            ]
        return d
```

### Step 2.6: 运行缓存测试，确认 PASS

- [ ] **跑 Task 2.1 的测试**

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo
uv run pytest tests/matmaster/types/test_tool_call_data_caching.py -v
```

Expected: 3 个测试全 PASS。

### Step 2.7: 跑全仓测试确认无回归

- [ ] **确认 ToolCallData / AssistantMessage 改动不影响其他测试**

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo
uv run pytest tests/ -x -q 2>&1 | tail -30
```

Expected: 全 PASS（关注 `tests/matmaster/types/` 与 `tests/matmaster/core/test_agent_kernel_*` 全绿）。如果某个测试因 frozen 报错（例如直接构造后改字段的旧测试），就近修——构造新 ToolCallData 实例代替 mutate。

### Step 2.8: 写 CI 静态 grep 脚本

- [ ] **创建 `scripts/lint_no_arguments_mutation.py`**

```python
#!/usr/bin/env python3
"""Heuristic lint: detect mutation of ToolCallData.arguments (E3 R2 layer 1).

**Heuristic, not authoritative.** The script grep-matches on identifiers that
typically refer to a ToolCallData instance (tc, tool_call, declared
ToolCallData variables) AND on subscript / method-call mutation patterns on
.arguments. It deliberately does NOT match every `.arguments` attribute in
the codebase, because other classes legitimately own a field named
`arguments` (e.g. matmaster/providers/openai_provider.py
`_StreamToolCallState.arguments: str`). Those modules are allowlisted.

The lint is NOT an AST analyzer; if a future codebase adds a non-tool-call
class with `arguments` AND uses an identifier matching the heuristic, the
script may either miss real bugs or false-positive. Treat the synthetic test
in tests/matmaster/core/test_tool_runner_arguments_contract.py and the
docstring contract in matmaster/types/messages.py as the canonical sources;
this script is a fast PR-time tripwire.

Run: uv run python scripts/lint_no_arguments_mutation.py
Exit 0 if clean, exit 1 if any violation found.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SEARCH_DIRS = [REPO_ROOT / "matmaster", REPO_ROOT / "src"]

# Allowlist: modules that own non-ToolCallData `arguments` fields.
# Paths are relative to REPO_ROOT and matched as path prefixes.
ALLOWLIST_PREFIXES = [
    "matmaster/providers/openai_provider.py",  # _StreamToolCallState.arguments: str
]

# Identifier prefix groups: only flag mutations whose left-hand side reads as
# a ToolCallData instance attribute access. This keeps the grep narrow.
# `tool_calls\[\d+\]` covers patterns like `response.tool_calls[0].arguments[..]`.
_TC_IDENTIFIERS = r"(?:tc|tool_call|tool_calls\[\s*\d+\s*\])"

# Mutation pattern groups (each matched after the identifier prefix).
PATTERNS = [
    (
        rf"\b{_TC_IDENTIFIERS}\.arguments\s*=\s*[^=]",
        "rebind <tc>.arguments = ...",
    ),
    (
        rf"\b{_TC_IDENTIFIERS}\.arguments\[[^\]]+\]\s*=\s*",
        "subscript assign <tc>.arguments[k] = ...",
    ),
    (
        rf"\b{_TC_IDENTIFIERS}\.arguments\.(?:update|pop|clear|setdefault|popitem)\b",
        "mutate via <tc>.arguments.<method>(...)",
    ),
    (
        rf"\b{_TC_IDENTIFIERS}\.model_copy\([^)]*update\s*=\s*\{{[^}}]*['\"]arguments['\"]",
        "<tc>.model_copy(update={'arguments': ...}) carries stale cache",
    ),
]


def _is_allowlisted(rel_path: str) -> bool:
    return any(rel_path.startswith(prefix) for prefix in ALLOWLIST_PREFIXES)


def check_file(path: Path) -> list[tuple[int, str, str]]:
    """Return list of (line_no, pattern_label, line) violations."""
    violations: list[tuple[int, str, str]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return violations
    for line_no, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        for regex, label in PATTERNS:
            if re.search(regex, line):
                violations.append((line_no, label, line.rstrip()))
    return violations


def main() -> int:
    all_violations: list[tuple[Path, int, str, str]] = []
    for base in SEARCH_DIRS:
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            rel = str(path.relative_to(REPO_ROOT))
            if _is_allowlisted(rel):
                continue
            for v in check_file(path):
                all_violations.append((path, *v))

    if all_violations:
        print("ToolCallData.arguments mutation detected (E3 R2 violation):")
        for path, line_no, label, line in all_violations:
            rel = path.relative_to(REPO_ROOT)
            print(f"  {rel}:{line_no}: {label}")
            print(f"    {line}")
        print(
            "\nFix: construct a new ToolCallData / new dict instead of mutating "
            "in place. See matmaster/types/messages.py ToolCallData docstring."
        )
        return 1
    print("OK: no ToolCallData.arguments mutation found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

### Step 2.9: 跑 lint 脚本，确认现有代码 clean

- [ ] **运行 CI lint，确认 baseline clean**

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo
chmod +x scripts/lint_no_arguments_mutation.py
uv run python scripts/lint_no_arguments_mutation.py
echo "exit code: $?"
```

Expected: 输出 `OK: no .arguments mutation found.`，exit code 0。
如果有命中：要么是真 bug（修代码），要么是脚本误判（调 pattern）。

### Step 2.10: 写 R2 第二/三层守护测试

- [ ] **创建 `tests/matmaster/core/test_tool_runner_arguments_contract.py`**

```python
"""E3 R2 layer 2+3 guards: ToolCallData.arguments nested mutation contract.

Layer 1 lives in scripts/lint_no_arguments_mutation.py (heuristic static grep
gated by pre-commit -- see plan step 2.13).

Layer 2 (this file, test_full_tool_runner_chain_does_not_mutate_arguments):
        a regression test exercising the runner chain. tc.arguments is
        shared with PreToolCallContext, StructuralValidation,
        input_validator, CapabilityPolicy, and finally tool.execute
        (see matmaster/core/tool_runner.py:186-280). Any one of those
        consumers mutating .arguments would poison arguments_json cache.

Layer 3 (test_nested_mutation_stales_arguments_json_cache):
        demonstrates the actual failure mode -- pydantic v2 frozen blocks
        rebinding (`tc.arguments = ...`) but NOT nested mutation
        (`tc.arguments[k] = v`). The cached_property arguments_json holds
        its first value and goes stale relative to the mutated dict.
"""

from __future__ import annotations

import json

import pytest

from matmaster.types.messages import ToolCallData


def test_nested_mutation_stales_arguments_json_cache():
    """Layer 3: directly mutate tc.arguments[k] = v; cached JSON goes stale.

    Note: pydantic v2 copies the input dict on construction, so mutating
    the *original* dict (`args["q"] = ...` after `ToolCallData(arguments=args)`)
    does NOT affect tc.arguments. The real risk is in-place mutation of
    tc.arguments itself, which IS not blocked by frozen.
    """
    tc = ToolCallData(id="c1", name="synthetic_mut", arguments={"q": "hello", "n": 5})

    # First access populates the cached_property.
    cached_before = tc.arguments_json
    assert json.loads(cached_before) == {"q": "hello", "n": 5}

    # frozen blocks the FIRST layer (field rebinding).
    with pytest.raises(Exception):  # pydantic ValidationError or similar
        tc.arguments = {"q": "rebind"}  # type: ignore[misc]

    # frozen does NOT block the SECOND layer (nested mutation of the dict).
    tc.arguments["q"] = "MUTATED"
    assert tc.arguments["q"] == "MUTATED", (
        "frozen does not deep-freeze; nested mutation is allowed. "
        "Contract relies on consumers not mutating."
    )

    # cached_property holds first value -- this is the failure mode that
    # justifies the R2 contract.
    cached_after = tc.arguments_json
    assert cached_before == cached_after, "cached_property holds first value"
    assert "MUTATED" not in cached_after, (
        "arguments_json stale relative to mutated arguments dict"
    )


def test_full_tool_runner_chain_does_not_mutate_arguments():
    """Layer 2: regression that the runner chain leaves tc.arguments untouched.

    Builds a minimal FullToolRunner scenario through public test fixtures
    (see tests/matmaster/core/agent_kernel_test_helpers.py for available
    helpers). Asserts that after execute_batch returns, tc.arguments equals
    a snapshot taken before the call.

    The exact fixture construction depends on what helpers exist; the
    implementation step inspects agent_kernel_test_helpers.py and reuses
    the smallest available setup. Acceptable forms:
    (a) reuse an existing fixture and add a snapshot+assert wrapper
    (b) construct a no-op ToolInstance directly and run execute_batch

    The assertion is independent of which consumer in the chain ran:
    if PreToolCallContext, StructuralValidation, input_validator,
    CapabilityPolicy, or executor mutate .arguments, the snapshot
    comparison fails. This is the project-wide runtime guard for R2.
    """
    pytest.importorskip("matmaster.core.tool_runner")
    pytest.skip(
        "Implement using agent_kernel_test_helpers fixtures. See "
        "tests/matmaster/core/conftest.py + agent_kernel_test_helpers.py "
        "for available no-op tool builders. The test must: "
        "(1) snapshot tc.arguments via copy.deepcopy before execute_batch, "
        "(2) run a single tool_call through the public runner API, "
        "(3) assert tc.arguments == snapshot AND tc.arguments_json was "
        "not invalidated. If no helper exists, build a minimal in-test "
        "ToolInstance with a no-op execute callable."
    )
```

**Note about the skipped Layer 2 test:** It is intentionally a skip with
a precise implementation prompt, not a placeholder. The reason for not
inlining a concrete implementation here is that
`agent_kernel_test_helpers.py` is the right place to look for an
appropriate fixture, and the implementation step (2.11) is where that
inspection happens. If during execution the helper inventory turns out
insufficient, the executing agent should build a tiny in-test
ToolInstance rather than skip silently; the docstring above is the
implementation contract.

### Step 2.11: Run R2 guard tests

- [ ] **Run R2 contract tests; confirm Layer 3 PASS**

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo
uv run pytest tests/matmaster/core/test_tool_runner_arguments_contract.py -v
```

Expected: `test_nested_mutation_stales_arguments_json_cache` PASS (Layer 3).
`test_full_tool_runner_chain_does_not_mutate_arguments` SKIPPED with the
implementation-prompt reason (Layer 2 — to be implemented inline using
existing `agent_kernel_test_helpers.py` fixtures if available, else
build minimal in-test ToolInstance).

### Step 2.12: Extend tool runner contract docstring to all chain consumers

- [ ] **Open `matmaster/core/tool_runner.py`, locate `FullToolRunner` class docstring or `execute_batch` method docstring**

Append the following to the chosen docstring. The contract scope is
**every consumer along the runner chain**, not only the executor: see
[tool_runner.py:186-280](../../../matmaster/core/tool_runner.py) where
`tc.arguments` is shared with `PreToolCallContext` (hook), the
`StructuralValidation.validate` call, the optional `input_validator`,
the `CapabilityPolicy.evaluate` call, and finally `tool.execute` (via
`effective_args`, which is `decision.modified_args or tc.arguments`).

```python
# Append to FullToolRunner / execute_batch / module docstring:
"""
...existing docstring...

**Argument immutability contract (E3 R2):**

Every consumer along the runner chain MUST NOT mutate the `arguments`
dict they receive (whether via `tool_call.arguments[k] = v`,
`.update(...)`, `.pop(...)`, `.setdefault(...)`, or any other in-place
modification). This applies to ALL of:

- Pre/post tool-call hooks (PreToolCallContext.arguments)
- StructuralValidation.validate (tc.arguments)
- input_validator callables (effective_args)
- CapabilityPolicy.evaluate (effective_args)
- tool.execute (effective_args)

Mutation is forbidden because:

1. ToolCallData caches `arguments_json` via @cached_property; mutating
   `arguments` after the cache is populated produces stale JSON when
   the same ToolCallData is reused (e.g. across retries or replay).
2. The runner does NOT defensively deep-copy `arguments` before passing
   it through the chain; the dict identity is shared end-to-end.
3. `decision.modified_args` is the only sanctioned way to derive a new
   argument set (it builds a fresh dict; the original `tc.arguments`
   stays untouched). Hooks and validators that need to "change"
   arguments must produce a fresh dict via `modified_args` rather than
   in-place edit.

If a tool implementation needs different parameters, construct a fresh
dict. Lint enforced by `scripts/lint_no_arguments_mutation.py`
(heuristic, gated by pre-commit -- see plan step 2.13).
"""
```

(Edit the actual docstring at the discovered location; do not duplicate
existing content.)

### Step 2.12b: Wire lint script into pre-commit

- [ ] **Add a local pre-commit hook running the lint script**

Open `.pre-commit-config.yaml`. The first `repos:` entry is a `local`
repo group containing `check-file-line-count`. Append a sibling local
hook in the same group:

```yaml
  - repo: local
    hooks:
      - id: check-file-line-count
        name: check single file does not exceed 1000 lines
        entry: python3 .pre-commit/check_file_lines.py
        language: system
        files: \.(py|ts|tsx|js|jsx)$
      - id: lint-no-arguments-mutation
        name: forbid mutation of ToolCallData.arguments (E3 R2)
        entry: python3 scripts/lint_no_arguments_mutation.py
        language: system
        pass_filenames: false
        files: \.py$
```

`pass_filenames: false` because the script does its own filesystem walk
over `matmaster/` and `src/`, not per-file. `files: \.py$` ensures the
hook is triggered on Python changes; the body still scans the full
configured directories.

Run a one-shot verification:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo
pre-commit run lint-no-arguments-mutation --all-files 2>&1 | tail -10
```

Expected: hook reports `OK: no ToolCallData.arguments mutation found.`
and exits 0. If pre-commit is not installed, fall back to:
```bash
uv run python scripts/lint_no_arguments_mutation.py
```

### Step 2.13: 跑全仓测试一遍，确认无回归

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo
uv run pytest tests/ -x -q 2>&1 | tail -20
```

Expected: 全 PASS。

### Step 2.14: 跑 benchmark，看 baseline 第一次跳

- [ ] **跑 Task 1 的 benchmark，对比记下的 baseline 数字**

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo
uv run pytest benchmarks/test_message_pipeline_perf.py -v -s 2>&1 | tee /tmp/e3_after_layer1.txt
```

Expected: 3 个测试都 PASS。`json.dumps_calls` 数字应该比 baseline 显著降低（每个 ToolCallData 只 dumps 一次），wall time 应该有 10–30% 改善（取决于 arg_size）。
在 commit message 里写明实测数字。

### Step 2.15: Commit

- [ ] **提交 Layer 1 修复**

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo
git add matmaster/types/messages.py
git add matmaster/core/tool_runner.py
git add scripts/lint_no_arguments_mutation.py
git add tests/matmaster/types/test_tool_call_data_caching.py
git add tests/matmaster/core/test_tool_runner_arguments_contract.py
git add .pre-commit-config.yaml
git status
git commit -m "$(cat <<'EOF'
refactor(types): cache ToolCallData.arguments_json

Add functools.cached_property for arguments_json and frozen=True on
ToolCallData. Switch AssistantMessage.to_api_dict to use the cache,
eliminating the repeated json.dumps work that turned the agent main
loop hot path into O(turns² × avg_arg_size).

Also add R2 immutability guards across the full runner chain (hooks,
validators, policy, executor):
- Heuristic lint script scripts/lint_no_arguments_mutation.py,
  gated by .pre-commit-config.yaml local hook
- Layer 3 test: nested mutation stales arguments_json cache
  (test_tool_runner_arguments_contract.py)
- Layer 2 placeholder: full-runner-chain regression (currently SKIPPED
  with a precise implementation prompt; executing agent should fill in
  using agent_kernel_test_helpers fixtures or a minimal in-test
  ToolInstance)
- Tool runner contract docstring extends the immutability rule to ALL
  consumers (pre-hook, StructuralValidation, input_validator, policy,
  executor) -- decision.modified_args is the only sanctioned way to
  derive new arguments

Benchmark vs baseline (replace placeholder numbers with actual):
- small/medium/large fixtures: json.dumps_calls drop ~Nx
- wall time: -X%

Part of E3 fix; spec at
docs/superpowers/specs/2026-05-17-e3-incremental-message-pipeline-design.md
EOF
)"
```

Expected: commit succeeds. If lint hook fails on the staged files
themselves, check that no fresh `.arguments[k] = v` snuck into the new
helper code. If `name-tests-test` complains about the new test files
not starting with `test_`, ensure both files do.

---

## Task 3: `IncrementalMessagePipeline` Skeleton + Tests

**目的：** 在不接入主循环的前提下，独立实现 `IncrementalMessagePipeline` 并跑通全部单元测试 + invariant 双约束测试 + R2 守护。**这个 task 不动 agent.py / kernel_items.py**——方便单独 review。

**Files:**
- Create: `matmaster/core/message_pipeline.py`
- Create: `tests/matmaster/core/test_message_pipeline.py`

### Step 3.1: 写第一个测试：empty messages → empty output

- [ ] **创建 `tests/matmaster/core/test_message_pipeline.py`，写最简测试**

```python
"""Tests for IncrementalMessagePipeline (E3 fix layer 2)."""

from __future__ import annotations

import pytest

from matmaster.core.message_pipeline import IncrementalMessagePipeline
from matmaster.types.errors import LLMError
from matmaster.types.message_normalization import (
    canonicalize_messages_for_provider,
    normalize_and_validate_openai_messages,
    validate_openai_messages,
    validate_openai_tool_turn_sequence,
)
from matmaster.types.messages import (
    AssistantMessage,
    SystemMessage,
    ToolCallData,
    ToolMessage,
    UserMessage,
)


def _pure_pipeline(messages):
    """Reference impl: nucleus of the pre-E3 main-loop call."""
    return normalize_and_validate_openai_messages(
        canonicalize_messages_for_provider(messages)
    )


def test_empty_then_first_feed():
    """空 list 输入返回空 list；首次非空 feed 等价于纯函数。"""
    p = IncrementalMessagePipeline()
    assert p.feed_tail([]) == []

    msgs = [SystemMessage(content="hi"), UserMessage(content="hello")]
    out = p.feed_tail(msgs)
    assert out == _pure_pipeline(msgs)
```

### Step 3.2: 跑测试，确认 FAIL（模块不存在）

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo
uv run pytest tests/matmaster/core/test_message_pipeline.py::test_empty_then_first_feed -v
```

Expected: `ModuleNotFoundError: No module named 'matmaster.core.message_pipeline'`。

### Step 3.3: 创建 message_pipeline.py 骨架

- [ ] **创建 `matmaster/core/message_pipeline.py`**

```python
"""Incremental message pipeline for the agent main loop (E3 fix).

Replaces the per-turn full re-run of
canonicalize_messages_for_provider + normalize_and_validate_openai_messages
with a stateful pipeline that caches the processed prefix and only
re-processes the tail of state.messages between turns.

See docs/superpowers/specs/2026-05-17-e3-incremental-message-pipeline-design.md
for the design rationale and detailed algorithm.
"""

from __future__ import annotations

import logging
from typing import Any

from matmaster.types.errors import LLMError
from matmaster.types.message_normalization import (
    _merge_user_messages,
    validate_openai_messages,
    validate_openai_tool_turn_sequence,
)
from matmaster.types.messages import Message, UserMessage

logger = logging.getLogger(__name__)


def _to_normalized_api_dict(msg: Message) -> dict[str, Any]:
    """Convert a Message to API-ready dict, normalizing content=None → "".

    Mirrors the normalize step of normalize_messages_for_openai for a
    single message. Critical for AssistantMessage(content=None, tool_calls=[...])
    which would otherwise fail validate_openai_messages's str-content check.
    """
    payload = msg.to_api_dict()
    if "content" not in payload or payload.get("content") is None:
        payload["content"] = ""
    return payload


class _ToolTurnValidator:
    """Stateful tool-turn validator (E3 fix internal).

    Mirrors validate_openai_tool_turn_sequence's state machine but keeps
    pending/seen as instance fields across calls. Mid-tail pending IS
    legal (one tool-turn straddles within a single feed); tail-end
    pending is enforced by IncrementalMessagePipeline.feed_tail step 7.
    """

    def __init__(self) -> None:
        self._pending_tool_ids: set[str] = set()
        self._seen_tool_ids: set[str] = set()

    @property
    def pending_tool_ids(self) -> set[str]:
        return self._pending_tool_ids

    def reset(self) -> None:
        self._pending_tool_ids.clear()
        self._seen_tool_ids.clear()

    def feed_tail(self, new_msgs: list[dict[str, Any]]) -> None:
        """Incrementally validate new_msgs against persistent state.

        Mirrors validate_openai_tool_turn_sequence body but does NOT
        run the function-end pending-empty assertion -- that is the
        caller's (pipeline.feed_tail step 7) responsibility.
        """
        for message in new_msgs:
            role = message.get("role")

            if role == "tool":
                tool_id = str(message.get("tool_call_id") or "")
                if tool_id in self._seen_tool_ids:
                    raise LLMError(
                        f"duplicate tool_result ids for assistant turn: {tool_id}",
                        retryable=False,
                        error_category="bad_request",
                    )
                if not self._pending_tool_ids and not self._seen_tool_ids:
                    raise LLMError(
                        "orphan tool message after assistant without tool_calls",
                        retryable=False,
                        error_category="bad_request",
                    )
                if not tool_id or tool_id not in self._pending_tool_ids:
                    raise LLMError(
                        f"tool_result without matching previous assistant tool_call: {tool_id}",
                        retryable=False,
                        error_category="bad_request",
                    )
                self._seen_tool_ids.add(tool_id)
                self._pending_tool_ids.remove(tool_id)
                continue

            if self._pending_tool_ids:
                raise LLMError(
                    f"missing tool_result ids for assistant turn: "
                    f"{sorted(self._pending_tool_ids)}",
                    retryable=False,
                    error_category="bad_request",
                )

            self._seen_tool_ids.clear()

            if role != "assistant":
                continue

            raw_tool_calls = message.get("tool_calls") or []
            declared_ids: list[str] = []
            for tool_call in raw_tool_calls:
                if not isinstance(tool_call, dict):
                    raise LLMError(
                        "assistant tool_call payload must be a dict",
                        retryable=False,
                        error_category="bad_request",
                    )
                tool_id = str(tool_call.get("id") or "")
                if not tool_id:
                    raise LLMError(
                        "assistant tool_call missing id",
                        retryable=False,
                        error_category="bad_request",
                    )
                declared_ids.append(tool_id)

            if len(declared_ids) != len(set(declared_ids)):
                duplicates = sorted(
                    {tid for tid in declared_ids if declared_ids.count(tid) > 1}
                )
                raise LLMError(
                    f"duplicate tool_call ids in outbound assistant turn: {duplicates}",
                    retryable=False,
                    error_category="bad_request",
                )

            self._seen_tool_ids = set()
            self._pending_tool_ids = set(declared_ids)


class IncrementalMessagePipeline:
    """Stateful provider-payload builder for the agent main loop.

    See feed_tail docstring for full usage contract. Summary:
    - feed_tail(messages) -> API-ready dicts (only tail re-processed)
    - reset() -> drop caches; required after compactor mutates prefix
    - revalidate_full(api_messages) -> paranoia check on normalized payload
    """

    def __init__(self) -> None:
        self._canonical_cache: list[Message] = []
        self._api_cache: list[dict[str, Any]] = []
        self._source_len: int = 0
        self._prefix_fingerprint: tuple[int, int, int] | None = None
        self._validator = _ToolTurnValidator()

    def reset(self) -> None:
        """Drop all caches. Next feed_tail rebuilds from scratch.

        Must be called after any code path that mutates the prefix of
        the messages list passed to feed_tail (e.g. compactor in-place
        rewrite). fingerprint auto-detect is best-effort and CANNOT
        catch middle-position replacement or in-place mutation; explicit
        reset is the only correctness guarantee.
        """
        self._canonical_cache = []
        self._api_cache = []
        self._source_len = 0
        self._prefix_fingerprint = None
        self._validator.reset()

    def feed_tail(self, messages: list[Message]) -> list[dict[str, Any]]:
        """Process tail of messages and return API-ready dicts.

        Reuses prefix cache; only re-processes messages[self._source_len:].

        **Fingerprint detection is best-effort, NOT a correctness guarantee.**
        It only catches:
        - prefix shrinkage (len decreased)
        - first-message replacement (id(messages[0]) changed)
        - last-processed-message replacement (id(messages[source_len-1]) changed)

        It CANNOT catch:
        - middle-position replacement (e.g. messages[5] swapped, ends unchanged)
        - in-place mutation (same object, content modified)

        All prefix-rewriting paths MUST call reset() explicitly.

        **Usage constraints:**
        - Provider-payload entry only (synchronous call point right before LLM).
          Returns guaranteed to satisfy validate_openai_tool_turn_sequence's
          end invariant (no pending tool ids); raises LLMError otherwise.
        - Returned list is two-layer shallow copy (outer list + each dict).
          Caller MUST NOT mutate any nested structure (tool_calls list,
          function dict, list-form content). Deep mutation poisons pipeline
          cache; behavior undefined.
        - On failure (normalize/validate raises), pipeline auto-resets;
          caller's next feed_tail rebuilds from scratch.
        """
        # Step 1: shrinkage detect
        if len(messages) < self._source_len:
            logger.warning(
                "pipeline prefix shrunk; auto-reset",
                extra={
                    "observed_len": len(messages),
                    "expected_source_len": self._source_len,
                },
            )
            self.reset()

        # Step 2: fingerprint comparison (only when prior state exists)
        if self._source_len > 0 and self._prefix_fingerprint is not None:
            current = (
                self._source_len,
                id(messages[0]),
                id(messages[self._source_len - 1]),
            )
            if current != self._prefix_fingerprint:
                logger.warning(
                    "pipeline prefix mutation detected; auto-reset",
                    extra={
                        "observed": current,
                        "expected": self._prefix_fingerprint,
                    },
                )
                self.reset()

        # Step 3: tail slice
        tail = messages[self._source_len:]
        if not tail:
            return [dict(m) for m in self._api_cache]

        # Step 4-7: transaction
        orig_api_len = len(self._api_cache)
        was_merged = False
        try:
            # Step 5: canonicalize + normalize per message
            for i, msg in enumerate(tail):
                if (
                    self._canonical_cache
                    and isinstance(self._canonical_cache[-1], UserMessage)
                    and isinstance(msg, UserMessage)
                ):
                    merged = _merge_user_messages(self._canonical_cache[-1], msg)
                    self._canonical_cache[-1] = merged
                    self._api_cache[-1] = _to_normalized_api_dict(merged)
                    if i == 0:
                        was_merged = True
                else:
                    self._canonical_cache.append(msg)
                    self._api_cache.append(_to_normalized_api_dict(msg))

            # Step 6: incremental validator feed
            start = orig_api_len - 1 if was_merged else orig_api_len
            new_api_segment = self._api_cache[start:]
            # Per-message non-stateful checks first (mirrors validate_openai_messages)
            validate_openai_messages(new_api_segment)
            # Then tool-turn state machine
            self._validator.feed_tail(new_api_segment)

            # Step 7: end-invariant
            if self._validator.pending_tool_ids:
                raise LLMError(
                    f"missing tool_result ids for assistant turn: "
                    f"{sorted(self._validator.pending_tool_ids)}",
                    retryable=False,
                    error_category="bad_request",
                )

        except Exception:
            self.reset()
            raise

        # Step 8: commit fingerprint
        self._source_len = len(messages)
        self._prefix_fingerprint = (
            self._source_len,
            id(messages[0]),
            id(messages[self._source_len - 1]),
        )

        # Step 9: return two-layer shallow copy
        return [dict(m) for m in self._api_cache]

    def revalidate_full(self, api_messages: list[dict[str, Any]]) -> None:
        """Run full validators on already-normalized api_messages.

        **Input contract:** api_messages MUST already be normalized
        (content=None replaced by ""). This method does NOT normalize;
        it only runs validate_openai_messages + validate_openai_tool_turn_sequence.
        Intended for paranoia checks on pipeline-internal _api_cache.
        Does not read or write pipeline state.
        """
        validate_openai_messages(api_messages)
        validate_openai_tool_turn_sequence(api_messages)
```

Note: this file uses `from matmaster.types.message_normalization import _merge_user_messages`. `_merge_user_messages` is a module-private helper in that module; if Python import linting flags the underscore-prefixed import, either promote it to public or move it to a shared `_internal` module. For the scope of this task, the underscore import is acceptable and explicit about reuse intent.

### Step 3.4: 跑 step 3.1 测试，确认 PASS

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo
uv run pytest tests/matmaster/core/test_message_pipeline.py::test_empty_then_first_feed -v
```

Expected: PASS。

### Step 3.5: 写 append-only growth 测试

- [ ] **追加到 `tests/matmaster/core/test_message_pipeline.py`**

```python
def test_append_only_growth():
    """多次 append 后 cache 增量正确，等价于纯函数一次性输出。"""
    p = IncrementalMessagePipeline()
    msgs = [
        SystemMessage(content="sys"),
        UserMessage(content="u1"),
    ]
    out1 = p.feed_tail(msgs)
    assert out1 == _pure_pipeline(msgs)

    # Append one more user message
    msgs.append(UserMessage(content="u2"))
    out2 = p.feed_tail(msgs)
    # u1 + u2 merged because both User
    assert out2 == _pure_pipeline(msgs)

    # Append a non-user message: appended as new entry
    msgs.append(SystemMessage(content="more system"))
    out3 = p.feed_tail(msgs)
    assert out3 == _pure_pipeline(msgs)
```

### Step 3.6: 跑测试，验证 PASS

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo
uv run pytest tests/matmaster/core/test_message_pipeline.py::test_append_only_growth -v
```

Expected: PASS。

### Step 3.7: 写 user-merge 边界测试

```python
def test_user_merge_at_cache_boundary():
    """Cache 末尾 user + tail 头 user → 合并改写 _canonical_cache[-1] + _api_cache[-1]."""
    p = IncrementalMessagePipeline()
    msgs = [SystemMessage(content="sys"), UserMessage(content="u1")]
    p.feed_tail(msgs)

    # Now append another user; should merge into u1
    msgs.append(UserMessage(content="u2"))
    out = p.feed_tail(msgs)

    # Verify the cache structure
    assert len(p._canonical_cache) == 2  # system + merged user
    assert isinstance(p._canonical_cache[-1], UserMessage)
    assert p._canonical_cache[-1].content == "u1\n\nu2"
    # And the api cache stays in sync
    assert p._api_cache[-1]["content"] == "u1\n\nu2"
    # Output matches pure
    assert out == _pure_pipeline(msgs)


def test_user_merge_within_tail():
    """Tail 内部连续 user 合并。"""
    p = IncrementalMessagePipeline()
    msgs = [
        SystemMessage(content="sys"),
        UserMessage(content="a"),
        UserMessage(content="b"),
        UserMessage(content="c"),
    ]
    out = p.feed_tail(msgs)
    assert out == _pure_pipeline(msgs)
    # 3 user messages should collapse to 1
    assert sum(1 for m in p._canonical_cache if isinstance(m, UserMessage)) == 1
```

Run after writing:
```bash
uv run pytest tests/matmaster/core/test_message_pipeline.py::test_user_merge_at_cache_boundary tests/matmaster/core/test_message_pipeline.py::test_user_merge_within_tail -v
```

Expected: PASS。

### Step 3.8: 写 tool-call sequence 测试

```python
def test_tool_call_assistant_then_tool_messages_across_feeds():
    """跨多次 feed_tail 处理一个 tool turn。"""
    p = IncrementalMessagePipeline()
    msgs = [
        SystemMessage(content="sys"),
        UserMessage(content="u"),
        AssistantMessage(
            content=None,
            tool_calls=[
                ToolCallData(id="c1", name="search", arguments={"q": "x"}),
            ],
        ),
        ToolMessage(tool_call_id="c1", tool_name="search", content="result"),
    ]

    # Feed in two halves to verify state machine survives the split:
    # First feed has the unclosed tool turn -> should raise.
    with pytest.raises(LLMError, match="missing tool_result ids"):
        p.feed_tail(msgs[:3])

    # After raise, pipeline auto-resets. Now feed the complete sequence.
    out = p.feed_tail(msgs)
    assert out == _pure_pipeline(msgs)
```

Run:
```bash
uv run pytest tests/matmaster/core/test_message_pipeline.py::test_tool_call_assistant_then_tool_messages_across_feeds -v
```

Expected: PASS。

### Step 3.9: 写 reset + truncation/replacement auto-reset 测试

```python
def test_explicit_reset_drops_cache():
    p = IncrementalMessagePipeline()
    msgs = [SystemMessage(content="s"), UserMessage(content="u")]
    p.feed_tail(msgs)
    assert p._source_len == 2

    p.reset()
    assert p._source_len == 0
    assert p._canonical_cache == []
    assert p._api_cache == []
    assert p._prefix_fingerprint is None

    # Next feed rebuilds
    out = p.feed_tail(msgs)
    assert out == _pure_pipeline(msgs)


def test_prefix_truncation_auto_reset(caplog):
    p = IncrementalMessagePipeline()
    msgs = [SystemMessage(content="s"), UserMessage(content="u1"), UserMessage(content="u2")]
    p.feed_tail(msgs)

    # Simulate compactor cutting prefix
    shorter = msgs[:1]
    with caplog.at_level("WARNING"):
        out = p.feed_tail(shorter)

    assert any("pipeline prefix shrunk" in rec.message for rec in caplog.records)
    assert out == _pure_pipeline(shorter)


def test_prefix_replacement_auto_reset(caplog):
    p = IncrementalMessagePipeline()
    msgs = [SystemMessage(content="s"), UserMessage(content="u1")]
    p.feed_tail(msgs)

    # Replace messages[0] with a different object (same content)
    replaced = [SystemMessage(content="s"), UserMessage(content="u1"), UserMessage(content="u2")]
    with caplog.at_level("WARNING"):
        out = p.feed_tail(replaced)

    assert any("pipeline prefix mutation detected" in rec.message for rec in caplog.records)
    assert out == _pure_pipeline(replaced)
```

Run:
```bash
uv run pytest tests/matmaster/core/test_message_pipeline.py -k "reset_drops_cache or truncation_auto or replacement_auto" -v
```

Expected: PASS。

### Step 3.10: 写 revalidate_full + pending-tool-call-raises 测试

```python
def test_revalidate_full_matches_pure_validators_on_normalized_payloads():
    """revalidate_full 与两个纯函数 validator 行为等价（已 normalize 的输入）。"""
    p = IncrementalMessagePipeline()
    msgs = [
        SystemMessage(content="s"),
        UserMessage(content="u"),
        AssistantMessage(
            content=None,
            tool_calls=[ToolCallData(id="c1", name="t", arguments={})],
        ),
        ToolMessage(tool_call_id="c1", tool_name="t", content="r"),
    ]
    api = p.feed_tail(msgs)  # api is already normalized
    p.revalidate_full(api)   # should not raise

    # Compare against pure validators directly
    validate_openai_messages(api)
    validate_openai_tool_turn_sequence(api)


def test_pending_tool_call_raises_on_feed_tail_exit():
    """Messages 末尾 assistant(tool_calls) 但缺 tool messages → 抛 LLMError + reset."""
    p = IncrementalMessagePipeline()
    msgs = [
        SystemMessage(content="s"),
        UserMessage(content="u"),
        AssistantMessage(
            content=None,
            tool_calls=[ToolCallData(id="c1", name="t", arguments={})],
        ),
    ]
    with pytest.raises(LLMError, match="missing tool_result ids"):
        p.feed_tail(msgs)
    # After raise, cache must be reset
    assert p._source_len == 0
    assert p._canonical_cache == []


def test_invalid_tool_id_raises_lazily_with_cache_reset():
    """tool_call_id 不匹配 → LLMError + cache reset；再次 feed 合法序列可恢复."""
    p = IncrementalMessagePipeline()
    bad = [
        SystemMessage(content="s"),
        UserMessage(content="u"),
        AssistantMessage(
            content=None,
            tool_calls=[ToolCallData(id="c1", name="t", arguments={})],
        ),
        ToolMessage(tool_call_id="WRONG", tool_name="t", content="r"),
    ]
    with pytest.raises(LLMError):
        p.feed_tail(bad)
    assert p._source_len == 0

    good = [
        SystemMessage(content="s"),
        UserMessage(content="u"),
        AssistantMessage(
            content=None,
            tool_calls=[ToolCallData(id="c1", name="t", arguments={})],
        ),
        ToolMessage(tool_call_id="c1", tool_name="t", content="r"),
    ]
    out = p.feed_tail(good)
    assert out == _pure_pipeline(good)
```

Run:
```bash
uv run pytest tests/matmaster/core/test_message_pipeline.py -k "revalidate_full or pending_tool or invalid_tool_id" -v
```

Expected: PASS。

### Step 3.11: 写 top-level caller mutation 防御测试

```python
def test_top_level_caller_mutation_does_not_pollute_cache():
    """调用者改 result[i]['content'] 不应污染 pipeline cache。

    Note: nested mutation (e.g. result[i]['tool_calls'][0]['id'] = ...)
    is unsupported per §12 of design. This test only covers the top-level
    shallow-copy defense (step 9).
    """
    p = IncrementalMessagePipeline()
    msgs = [SystemMessage(content="sys"), UserMessage(content="hello")]
    out1 = p.feed_tail(msgs)
    assert out1[0]["content"] == "sys"

    # Caller mutates top-level
    out1[0]["content"] = "POLLUTED"

    # Next feed should still see original content
    out2 = p.feed_tail(msgs)
    assert out2[0]["content"] == "sys"
```

Run:
```bash
uv run pytest tests/matmaster/core/test_message_pipeline.py::test_top_level_caller_mutation_does_not_pollute_cache -v
```

Expected: PASS。

### Step 3.12: 写 invariant 双约束测试

```python
def _build_complex_fixture() -> list:
    """Multi-turn fixture: system + user + (assistant_with_tools + tool_msgs) × 3 + final assistant."""
    fixture: list = [
        SystemMessage(content="You are a helpful assistant."),
        UserMessage(content="user request"),
    ]
    for turn in range(3):
        fixture.append(
            AssistantMessage(
                content="thinking..." if turn == 0 else None,
                tool_calls=[
                    ToolCallData(id=f"c{turn}_0", name="tool_a", arguments={"q": turn}),
                    ToolCallData(id=f"c{turn}_1", name="tool_b", arguments={"q": -turn}),
                ],
            )
        )
        fixture.append(
            ToolMessage(tool_call_id=f"c{turn}_0", tool_name="tool_a", content=f"a{turn}")
        )
        fixture.append(
            ToolMessage(tool_call_id=f"c{turn}_1", tool_name="tool_b", content=f"b{turn}")
        )
    # Natural finish
    fixture.append(AssistantMessage(content="done", tool_calls=None))
    return fixture


def _clean_boundary_indices(messages: list) -> list[int]:
    """End indices where messages[:idx] is a clean tool-turn boundary."""
    boundaries: list[int] = []
    pending = 0
    for i, m in enumerate(messages, start=1):
        if isinstance(m, AssistantMessage) and m.tool_calls:
            pending = len(m.tool_calls)
        elif isinstance(m, ToolMessage):
            pending = max(0, pending - 1)
        if pending == 0:
            boundaries.append(i)
    return boundaries


def _pending_boundary_indices(messages: list) -> list[int]:
    """End indices where messages[:idx] sits mid-tool-turn."""
    pending_lens: list[int] = []
    pending = 0
    for i, m in enumerate(messages, start=1):
        if isinstance(m, AssistantMessage) and m.tool_calls:
            pending = len(m.tool_calls)
        elif isinstance(m, ToolMessage):
            pending = max(0, pending - 1)
        if pending > 0:
            pending_lens.append(i)
    return pending_lens


def test_pipeline_output_equals_pure_pipeline_for_clean_prefixes():
    """合法 prefix 上 pipeline 输出与纯函数位级等价。"""
    msgs = _build_complex_fixture()
    p = IncrementalMessagePipeline()
    for prefix_len in _clean_boundary_indices(msgs):
        out = p.feed_tail(msgs[:prefix_len])
        assert out == _pure_pipeline(msgs[:prefix_len]), (
            f"divergence at clean prefix_len={prefix_len}"
        )


def test_pipeline_and_pure_both_raise_on_pending_tool_boundary():
    """非法 prefix 上两条路径同样抛 LLMError。"""
    msgs = _build_complex_fixture()
    for prefix_len in _pending_boundary_indices(msgs):
        p = IncrementalMessagePipeline()
        with pytest.raises(LLMError, match="missing tool_result ids"):
            p.feed_tail(msgs[:prefix_len])
        with pytest.raises(LLMError, match="missing tool_result ids"):
            _pure_pipeline(msgs[:prefix_len])
```

Run:
```bash
uv run pytest tests/matmaster/core/test_message_pipeline.py -k "pipeline_output_equals or both_raise_on_pending" -v
```

Expected: PASS。如果有 divergence，print 出来的 prefix_len 直接定位 bug。

### Step 3.13: 跑整个 pipeline 测试文件

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo
uv run pytest tests/matmaster/core/test_message_pipeline.py -v 2>&1 | tail -40
```

Expected: 全 PASS（14+ 测试）。

### Step 3.14: 跑全仓测试一遍

- [ ] **确认新模块不影响其他测试**

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo
uv run pytest tests/ -x -q 2>&1 | tail -20
```

Expected: 全 PASS。

### Step 3.15: Commit pipeline skeleton

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo
git add matmaster/core/message_pipeline.py
git add tests/matmaster/core/test_message_pipeline.py
git status
git commit -m "$(cat <<'EOF'
feat(core): add IncrementalMessagePipeline skeleton

New module matmaster/core/message_pipeline.py implements the stateful
provider-payload builder for E3 fix. Not yet wired into the main loop;
agent.py keeps using the pure-function path until Task 4 lands.

Components:
- IncrementalMessagePipeline: feed_tail / reset / revalidate_full
- _ToolTurnValidator: stateful tool-turn state machine
- _to_normalized_api_dict: single-message normalize helper

Cache invalidation strategy:
- Explicit pipeline.reset() is the only correctness guarantee
- Fingerprint auto-detect (best-effort) catches shrink/head/tail
  identity change; middle replacement and in-place mutation are blind
  spots (see §6.2 of design spec for the full list)

Test coverage in tests/matmaster/core/test_message_pipeline.py:
- 11 unit tests for individual behaviors
- 2 invariant tests (clean prefixes equivalent / pending boundaries both raise)
- Top-level caller-mutation defense test

Spec: docs/superpowers/specs/2026-05-17-e3-incremental-message-pipeline-design.md
EOF
)"
```

Expected: commit 成功。

---

## Task 4: Wire `IncrementalMessagePipeline` Into Kernel

**目的：** 把 pipeline 实际接到 `_KernelState`，替换 `agent.py:287-289` 的纯函数链，在 `agent_compaction.py` 加 `pipeline.reset()` 调用。这是真正切换 hot path 的一步。

**Files:**
- Modify: `matmaster/core/kernel_items.py:31-38`
- Modify: `matmaster/core/agent.py:287-289`
- Modify: `matmaster/core/agent_compaction.py:88-89`

### Step 4.1: 给 `_KernelState` 加 pipeline 字段

- [ ] **编辑 `matmaster/core/kernel_items.py`**

修改 import + class：

```python
"""Internal kernel item dataclasses used by AgentKernel."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any

from matmaster.core.message_pipeline import IncrementalMessagePipeline
from matmaster.types.events import FinishDetail
from matmaster.types.messages import LLMResponse


@dataclass
class _TerminalItem:
    reason: str
    final_content: str | None = None
    num_turns: int = 0
    usage: dict[str, int] = dc_field(default_factory=dict)
    usage_vendor_by_turn: list[dict[str, Any]] = dc_field(default_factory=list)
    messages: list[Any] = dc_field(default_factory=list)
    finish_detail: FinishDetail | None = None


@dataclass
class _KernelItem:
    event: Any = None
    llm_response: LLMResponse | None = None
    terminal: _TerminalItem | None = None


@dataclass
class _KernelState:
    messages: list[Any]
    turn: int = 0
    total_usage: dict[str, int] = dc_field(default_factory=dict)
    usage_vendor_by_turn: list[dict[str, Any]] = dc_field(default_factory=list)
    cached_tool_definitions: list[dict[str, Any]] | None = None
    last_catalog_version: int = -1
    pipeline: IncrementalMessagePipeline = dc_field(
        default_factory=IncrementalMessagePipeline
    )


class _KernelStopRequested(Exception):
    pass
```

### Step 4.2: 切换 agent.py 主循环到 feed_tail

- [ ] **编辑 `matmaster/core/agent.py:287-289`**

定位：

```python
api_messages = normalize_and_validate_openai_messages(
    canonicalize_messages_for_provider(state.messages)
)
```

替换为：

```python
api_messages = state.pipeline.feed_tail(state.messages)
```

### Step 4.3: 清理 agent.py 顶部的死 import（如有）

- [ ] **检查 `matmaster/core/agent.py:60-66` 区域**

如果 `canonicalize_messages_for_provider` 和 `normalize_and_validate_openai_messages` 不再有其他调用点，删除对应 import 行。先 grep 确认：

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo
grep -n "canonicalize_messages_for_provider\|normalize_and_validate_openai_messages" matmaster/core/agent.py
```

Expected: 应该只剩 import 行命中（说明 step 4.2 已经移除唯一调用点）。
如果 grep 显示仅 import 命中，删除 import；如果还有其他调用，保留。

### Step 4.4: 在 agent_compaction.py 加 pipeline.reset() 调用

- [ ] **编辑 `matmaster/core/agent_compaction.py`**

定位 `run_compaction_plan` 函数里 try/except block 结束、`messages_after = len(state.messages)` 之前的位置（当前文件行 88-89）。

修改前结构（line 54-89）：
```python
    try:
        ...
        result = await spec.compactor.apply_summary(plan, state.messages, summary, turn_input=turn_input)
    except Exception as exc:
        ...
        result = await spec.compactor.apply_fallback(plan, state.messages, failure_reason=str(exc))
    messages_after = len(state.messages)
```

修改后：
```python
    try:
        ...
        result = await spec.compactor.apply_summary(plan, state.messages, summary, turn_input=turn_input)
    except Exception as exc:
        ...
        result = await spec.compactor.apply_fallback(plan, state.messages, failure_reason=str(exc))

    # Compactor mutated state.messages in-place (truncate/replace prefix).
    # Reset the incremental pipeline so the next feed_tail rebuilds from
    # scratch. Fingerprint auto-detect cannot reliably catch all forms of
    # in-place mutation; explicit reset is the only correctness guarantee
    # (see message_pipeline.IncrementalMessagePipeline.feed_tail docstring).
    state.pipeline.reset()

    messages_after = len(state.messages)
```

### Step 4.5: 跑全仓测试

- [ ] **确认接入后所有现有测试仍通过**

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo
uv run pytest tests/ -x -q 2>&1 | tail -30
```

Expected: 全 PASS。重点关注：
- `tests/matmaster/core/test_agent_kernel_*` 全绿
- `tests/matmaster/core/test_agent_compaction.py` 全绿
- `tests/matmaster/core/test_agent_kernel_compaction.py` 全绿
- 其他 services 层测试不受影响

如有失败：失败通常来自：
- 旧测试模拟 `state.messages` 但没初始化 `state.pipeline`（dataclass default_factory 会自动给，正常应该不出问题）
- 某个测试在 compaction 后直接断言 messages 状态——确认是否需要在断言前调 pipeline.reset

### Step 4.6: 跑 benchmark，看 wall time 跳到 Task 5 的对比期望

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo
uv run pytest benchmarks/test_message_pipeline_perf.py -v -s 2>&1 | tee /tmp/e3_after_wire.txt
```

Expected: baseline 还在跑（只是参考），但**注意此时 benchmark 没接 pipeline**——它跑的是纯函数路径，所以数字应该跟 Task 2 之后差不多（仅 json.dumps 缓存的常数因子改善）。pipeline 自身的对比 benchmark 在 Task 5 加。

### Step 4.7: Commit kernel wire-in

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo
git add matmaster/core/kernel_items.py
git add matmaster/core/agent.py
git add matmaster/core/agent_compaction.py
git status
git commit -m "$(cat <<'EOF'
feat(core): wire IncrementalMessagePipeline into agent kernel

Replace the per-turn pure-function chain
  normalize_and_validate_openai_messages(canonicalize_messages_for_provider(...))
in agent._run_items with state.pipeline.feed_tail(state.messages), which
reuses cached prefix processing across turns. This is the actual hot-path
switch that turns E3 from O(turns² × avg_msg_size) into roughly
O(turns × k × avg_msg_size).

Changes:
- _KernelState gains a pipeline: IncrementalMessagePipeline field
  (default_factory; one instance per kernel run)
- agent.py:287-289 swapped to pipeline.feed_tail
- agent_compaction.run_compaction_plan calls state.pipeline.reset()
  after the try/except so both apply_summary and apply_fallback paths
  invalidate the cache before the next LLM turn

Fingerprint auto-detect is not relied on for correctness here -- the
explicit reset is the contract. See spec §6 for blind-spot analysis.
EOF
)"
```

Expected: commit 成功。

---

## Task 5: Measure Improvement + Retire E3 Deferred Entry

**目的：** 加 pipeline 对比 benchmark，量化实际改善，更新 deferred-simplifications 文档删除 E3 section。

**特别约束：** Task 5 修改的 deferred-simplifications.md 在 `docs/` 下，**不 commit**（按 user CLAUDE.md）。修改后由用户自行处理。Benchmark 改动（source code）正常 commit。

**Files:**
- Modify: `benchmarks/test_message_pipeline_perf.py`（加 pipeline 对比 fixture）
- Modify (no commit): `docs/superpowers/plans/2026-05-17-core-refactor-deferred-simplifications.md`

### Step 5.1: Add pipeline-vs-pure comparison benchmark with fresh fixtures

- [ ] **Append to `benchmarks/test_message_pipeline_perf.py`**

**Key fairness requirement:** pure and pipeline runs MUST use freshly-built
`ToolCallData` instances. Sharing instances across runs lets the first run
warm the `arguments_json` cached_property, making the second run's
`json.dumps` count read as 0 (or near-0) and inflating `dumps_ratio` to
infinity. Each `_run_*` call builds its own fixture.

The wall-time threshold is **soft** by default (prints a warning instead of
failing the test) because benchmarks under variable CPU load are flaky.
Set `RUN_PERF_GATE=1` to enforce the hard threshold locally before
recording acceptance numbers in the commit body. CI will not enforce
(benchmarks/ is not in `testpaths`).

```python
def _run_pipeline_per_turn(messages: list) -> tuple[float, int]:
    """Same simulation as _run_pure_path_per_turn but through pipeline.feed_tail.

    Caller must pass a fresh fixture (not one already touched by a pure
    run) to avoid arguments_json cache cross-contamination.
    """
    from matmaster.core.message_pipeline import IncrementalMessagePipeline

    dumps_count = 0
    orig_dumps = json.dumps

    def counting_dumps(*args, **kwargs):
        nonlocal dumps_count
        dumps_count += 1
        return orig_dumps(*args, **kwargs)

    boundaries = [2]
    i = 2
    while i < len(messages):
        if isinstance(messages[i], AssistantMessage) and messages[i].tool_calls:
            i += 1 + len(messages[i].tool_calls)
            boundaries.append(i)
        else:
            i += 1

    pipeline = IncrementalMessagePipeline()
    start = time.perf_counter()
    with patch("json.dumps", side_effect=counting_dumps):
        for end in boundaries:
            prefix = messages[:end]
            pipeline.feed_tail(prefix)
    elapsed = time.perf_counter() - start
    return elapsed, dumps_count


@pytest.mark.parametrize("label,num_turns,calls,arg_size", FIXTURE_CONFIGS)
def test_pipeline_path_improvement(label: str, num_turns: int, calls: int, arg_size: int):
    """Compare incremental pipeline against pure path on wall time + dumps count.

    Builds two independent fixtures so neither run pollutes the other's
    arguments_json cache. Hard wall-time gate only enforced when
    RUN_PERF_GATE=1 in the environment.
    """
    pure_messages = _build_fixture(num_turns, calls, arg_size)
    pure_elapsed, pure_dumps = _run_pure_path_per_turn(pure_messages)

    # Fresh fixture for pipeline run: no shared ToolCallData instances,
    # no warmed arguments_json cache.
    pipe_messages = _build_fixture(num_turns, calls, arg_size)
    pipe_elapsed, pipe_dumps = _run_pipeline_per_turn(pipe_messages)

    speedup = pure_elapsed / pipe_elapsed if pipe_elapsed > 0 else float("inf")
    dumps_ratio = pure_dumps / pipe_dumps if pipe_dumps > 0 else float("inf")

    print(
        f"\n[E3 IMPROVEMENT] fixture={label} "
        f"turns={num_turns} calls/turn={calls} arg_size={arg_size}B"
    )
    print(f"  pure:     wall={pure_elapsed * 1000:.2f}ms dumps={pure_dumps}")
    print(f"  pipeline: wall={pipe_elapsed * 1000:.2f}ms dumps={pipe_dumps}")
    print(f"  speedup={speedup:.2f}x  dumps_reduction={dumps_ratio:.2f}x")

    # dumps_ratio is a deterministic correctness check (not timing-flaky):
    # if pipeline does not reduce json.dumps by at least 1.5x, the cache
    # is not working as designed.
    assert dumps_ratio >= 1.5, (
        f"{label}: json.dumps reduction {dumps_ratio:.2f}x below 1.5x "
        f"-- arguments_json cache is not engaged or fixture is mis-shared"
    )

    # Wall-time gate is environmental; only enforced when explicitly requested.
    if os.environ.get("RUN_PERF_GATE") == "1" and label == "large":
        assert speedup >= 2.0, (
            f"large fixture speedup {speedup:.2f}x below 2x acceptance "
            f"threshold (RUN_PERF_GATE=1 enforced). On a quiet machine, "
            f"this should comfortably exceed 2x; flaky failures may "
            f"indicate background load."
        )
```

### Step 5.2: Run improvement benchmark

Run (soft mode — captures numbers without failing on wall-time variance):
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo
uv run pytest benchmarks/test_message_pipeline_perf.py::test_pipeline_path_improvement -v -s 2>&1 | tee /tmp/e3_improvement.txt
```

Expected: 3 parametrize cases all PASS. Output includes `[E3 IMPROVEMENT]`
lines with `pure wall`, `pipeline wall`, `speedup`, `dumps_reduction`.
`dumps_ratio` should be substantially > 1.5 for all sizes (this is the
deterministic correctness signal: confirms `arguments_json` cache is
engaged on fresh fixtures).

Then run with the hard wall-time gate on a quiet machine, recording the
numbers that go into the commit message:
```bash
RUN_PERF_GATE=1 uv run pytest benchmarks/test_message_pipeline_perf.py::test_pipeline_path_improvement -v -s 2>&1 | tee /tmp/e3_improvement_gated.txt
```

Expected: large fixture speedup ≥ 2x; if not, do NOT proceed to commit
until investigating. Likely root causes:
- pipeline.feed_tail not actually reusing the cache (inspect `_source_len`)
- shared fixture mistake (verify each `_run_*` is called with a fresh
  `_build_fixture(...)` result)
- background CPU load; re-run when the machine is idle

### Step 5.3: Commit improvement benchmark

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo
git add benchmarks/test_message_pipeline_perf.py
git status
git commit -m "$(cat <<'EOF'
chore(perf): record E3 fix benchmark improvement

Add test_pipeline_path_improvement to compare incremental pipeline
against pure-function path. Acceptance gate: large fixture (50 turns,
5 calls/turn, 5KB args) must achieve ≥ 2x wall-time speedup, and
json.dumps reduction must be ≥ 1.5x across all fixture sizes.

Measured numbers (see /tmp/e3_improvement.txt or commit body):
  small  fixture: speedup=Xx  dumps=Yx
  medium fixture: speedup=Xx  dumps=Yx
  large  fixture: speedup=Xx  dumps=Yx
EOF
)"
```

Expected: commit 成功。把 X/Y 替换为实测数字。

### Step 5.4: 更新 deferred-simplifications.md（NO COMMIT）

- [ ] **打开 `docs/superpowers/plans/2026-05-17-core-refactor-deferred-simplifications.md`**

定位 E3 section（约 line 432–501）：

```markdown
## E3: `_run_items` 每轮重复 `canonicalize_messages_for_provider` + ...
```

整段删除（从 `## E3` 这行到下一个 `##` 出现之前的所有内容）。
不留删除线、不留"已修复"标记——按文档自身约定"干净就是干净"。

也检查文件开头或顶部"维护这份清单"段是否提到 E3 计数，若有相应调整。

### Step 5.5: 验证 docs 改动正确

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo
grep -n "^## E[0-9]\|^## R[0-9]" docs/superpowers/plans/2026-05-17-core-refactor-deferred-simplifications.md
```

Expected: 应该看到剩余的 deferred 条目（R1-R5、R6、R7、E6、E7、E8 等），但**不含** E3。

### Step 5.6: 不 commit docs 改动；告知用户

- [ ] **打印 git status，告知用户 docs 改动等待手工处理**

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo
git status
```

Expected: `modified: docs/superpowers/plans/2026-05-17-core-refactor-deferred-simplifications.md` 处于 unstaged 状态。

不要 `git add` 或 commit 这个文件——按 user CLAUDE.md 约定，docs 改动由用户处理。

向用户报告：
> E3 deferred entry 已从 `docs/superpowers/plans/2026-05-17-core-refactor-deferred-simplifications.md` 删除，按你的 CLAUDE.md 约定我没 git commit 这个 docs 改动，留在 unstaged 状态等你处理。

---

## Self-Review（plan 自身的）

**Spec coverage 对照：**

| Spec section | Task 覆盖 |
|--------------|----------|
| §3 模块边界 | Task 3 step 3.3 创建 message_pipeline.py；Task 4 step 4.1 加 _KernelState 字段 |
| §4 API surface | Task 3 step 3.3 三个方法全部实现并带 docstring |
| §5.1 Canonicalize 增量（9 步） | Task 3 step 3.3 完整实现 step 1-9；step 3.5–3.11 各测试覆盖 |
| §5.2 arguments_json 缓存 | Task 2 全部 |
| §5.3 Validator 增量 | Task 3 step 3.3 `_ToolTurnValidator` |
| §5.4 revalidate_full | Task 3 step 3.3 `revalidate_full`；step 3.10 测试 |
| §6.1 显式 reset() | Task 4 step 4.4 |
| §6.2 fingerprint 自动检测 + 盲区 | Task 3 step 3.3 step 1/2；step 3.9 truncation/replacement 测试 |
| §6.3 边界 case | Task 3 step 3.3 step 1/2/3 边界逻辑；test_empty_then_first_feed 覆盖 |
| §7 checkpoint codec 路径 | 不修改——已在 spec 明确，plan 无对应 task（正确） |
| §8 风险登记 R1–R9 | R1 step 3.7; R2 Task 2 全部 + step 2.10/2.12; R3 fingerprint 默认开启; R4 step 3.10/3.12; R5 Task 1 fixture 真实化注释; R6 step 2.4; R7 step 3.11; R7b §12 文档化（无测试是有意）; R8 step 3.7 + step 3.12; R9 §6.2 + spec 明确 |
| §9 测试覆盖 | Task 3 step 3.5–3.12 全部 |
| §9 R2 守护（声称三层，实际有效 2 + 0.5 层）| **Layer 1（lint + pre-commit）**: step 2.8 写脚本 + step 2.12b 接入 `.pre-commit-config.yaml`，有效；**Layer 3（nested mutation cache stale）**: step 2.10 `test_nested_mutation_stales_arguments_json_cache`，有效；**Layer 2（full runner chain regression）**: step 2.10 `test_full_tool_runner_chain_does_not_mutate_arguments` 当前 SKIPPED with implementation prompt，**执行 Task 2 时必须 inline 填掉这个 skip**（用 `agent_kernel_test_helpers` 或最小 in-test ToolInstance）。如果执行时确实无法填，R2 实际只有 1.5 层守护，必须在 commit message 里诚实标注 |
| §10 性能验证 | Task 1 baseline + Task 5 improvement |
| §11 5 个 commit | Task 1–5 一对一对应 |
| §12 非目标 | message_normalization.py / history_checkpoint_codec.py 未触；read-only contract 在 step 3.3 docstring + step 3.11 测试；feed_tail strict invariant 在 step 3.10 强制 |

**Placeholder scan：** ✓ 全部 step 含实际代码，无 TBD/TODO。

**Type consistency 检查：** 
- `IncrementalMessagePipeline.feed_tail(messages: list[Message]) -> list[dict[str, Any]]`：Task 3 step 3.3 定义，Task 4 step 4.2 调用形式一致
- `state.pipeline.reset()`：Task 3 step 3.3 + Task 4 step 4.4 一致
- `ToolCallData.arguments_json` (cached_property)：Task 2 step 2.4 定义，step 2.5 使用，benchmark 间接验证一致
- `_KernelState.pipeline`：Task 4 step 4.1 字段定义，agent.py / agent_compaction.py 访问形式 `state.pipeline.xxx` 一致

无类型不一致。

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-05-17-e3-incremental-message-pipeline.md`. Two execution options:**

**1. Subagent-Driven (recommended)** - 我每个 task 派一个 fresh subagent 执行 + 两阶段 review，迭代快、上下文不污染。

**2. Inline Execution** - 在当前 session 用 executing-plans 批量执行带 checkpoint。

**哪一种？**
