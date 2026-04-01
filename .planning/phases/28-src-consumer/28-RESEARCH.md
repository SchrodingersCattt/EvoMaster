# Phase 28: src 反向依赖反转与 Consumer 迁移 - Research

**Researched:** 2026-04-01
**Domain:** Python 模块依赖反转、Pydantic v2 类型迁移、回调注入模式
**Confidence:** HIGH

## Summary

Phase 28 涉及四个独立但相互关联的解耦任务：(1) bohrium_setup.py 消除对 src.services.agent_run_bohrium 的 5 处 lazy import，改为回调注入；(2) script_env.py 消除对 src.utils.constant.BOHRIUM_OPENAPI_HOST 的 lazy import，改为 matmaster 侧环境变量读取；(3) chat_history.py 从 evomaster.utils.types 消息类型切换到 matmaster.types.messages；(4) agent_run_bohrium.py 从 evomaster.agent.session.ssh 切换到 matmaster.sessions.ssh。附带将 evomaster/env/bohrium.py 的 3 个纯函数搬入 matmaster 侧。

所有变更都有明确的代码路径和已验证的字段映射。最高风险点在 chat_history.py 的消息类型切换：evomaster 和 matmaster 的 AssistantMessage 在 tool_calls 字段结构上有本质差异（嵌套 function dict vs 扁平 ToolCallData），且 ToolMessage 的字段名不同（name vs tool_name）。已通过运行时验证确认了完整的字段映射关系。

**Primary recommendation:** 按依赖深度从浅到深执行：先搬入 bohrium 纯函数 + 常量定义（无风险），再做 bohrium_setup 回调注入 + script_env 常量替换（结构改造），最后做 chat_history 类型切换 + agent_run_bohrium session 切换（需要最多验证）。

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** BohriumSetupService 改为回调注入模式。构造函数接受 `load_credentials_fn`、`apply_credentials_fn`、`setup_fn`、`cleanup_fn` 四个 callable 参数，由 src/services/agent_run_service.py 在构造时注入实际函数。逻辑不搬迁，只反转依赖方向
- **D-02:** BohriumSetupResult 类型（NamedTuple）在 matmaster 侧定义副本，消除 TYPE_CHECKING 下对 src 的导入。src 侧保留原版本或改为从 matmaster 重新导入
- **D-03:** matmaster 侧定义 `BOHRIUM_OPENAPI_HOST = os.getenv('BOHRIUM_BASE_URL', 'https://open.bohrium.com').rstrip('/')`，不依赖 src 的 URL_PART 环境感知逻辑。运维通过环境变量控制
- **D-04:** `get_bohrium_credentials`、`get_bohrium_storage_config`、`inject_bohrium_executor` 三个纯函数直接搬入 matmaster 侧（与 BOHRIUM_OPENAPI_HOST 常量放在同一模块）。它们只读环境变量和构造 dict，无外部依赖
- **D-05:** path_adaptor.py 和 job_service.py 中的 3 个 lazy import 改为从 matmaster 侧模块导入
- **D-06:** chat_history.py 顶部的 `from evomaster.utils.types import AssistantMessage, ToolCall, ToolMessage, UserMessage` 全量切换到 matmaster.types.messages 中的对应类型
- **D-07:** events_to_dialog_messages 内部的 model_validate/model_dump 调用适配 matmaster 消息类型的字段和方法签名（Pydantic v2 兼容，需研究阶段确认字段映射）
- **D-08:** `from evomaster.agent.session.ssh import SSHSession, SSHSessionConfig` 切换到 `from matmaster.sessions.ssh import SSHSession, SSHSessionConfig`。Phase 25 已建立 matmaster 原生 SSHSession
- **D-09:** L155 的 `isinstance(ssh_session, SSHSession)` 改用 matmaster SSHSession
- **D-10:** agent_run_bohrium.py 对 playground.mat_master.core.workspace_resolver 的依赖不在本 phase 处理

### Claude's Discretion
- 搬入的 bohrium 函数在 matmaster/ 内的具体模块位置（`matmaster/integration/` 或 `matmaster/adaptors/calculation/` 均可）
- matmaster 消息类型与 evomaster 类型的具体字段映射细节（ToolCall vs ToolCallData 等）
- BohriumSetupResult 副本的具体字段定义（需从 src 侧读取）

### Deferred Ideas (OUT OF SCOPE)
- agent_run_bohrium.py 对 `playground.mat_master.core.workspace_resolver` 的依赖（get_remote_session_workspace_root, load_workspace_config_dict）-- 留后续 phase 处理
- matmaster 内其他 evomaster 残留（bash_tool.py:135 的 evomaster LocalSession、monitor_job/_llm.py 的 ConfigManager/create_llm）-- 不在 Phase 28 范围
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| INVR-01 | `matmaster/integration/bohrium_setup.py` 不再 lazy import `src.services.agent_run_bohrium` 的 5 个函数，改为回调注入 | 回调注入模式分析、BohriumSetupResult 字段确认、agent_run_service 构造点定位 |
| INVR-02 | `matmaster/tools/script_env.py` 不再 lazy import `src.utils.constant.BOHRIUM_OPENAPI_HOST` | src.utils.constant.py 的 BOHRIUM_OPENAPI_HOST 定义分析、matmaster 侧等效定义方案 |
| CONS-03 | `src/services/chat_history.py` 消费 matmaster 原生 message / tool_call 数据结构 | evomaster vs matmaster 消息类型完整字段映射验证（model_dump、model_validate、model_copy） |
| CONS-04 | `src/services/agent_run_bohrium.py` 切换到 matmaster session abstraction | evomaster SSHSession._env 与 matmaster SSHSession 直接方法的对应关系 |
</phase_requirements>

## Architecture Patterns

### 变更文件全景

```
matmaster/                       (修改 -- 消除反向依赖)
├── integration/
│   ├── bohrium_setup.py         # D-01/D-02: 回调注入替代 5 处 src lazy import
│   └── bohrium_env.py           # D-04: 新文件，搬入 3 个 bohrium 纯函数 + BOHRIUM_OPENAPI_HOST
├── tools/
│   └── script_env.py            # D-03: BOHRIUM_OPENAPI_HOST 改为 matmaster 侧常量
└── adaptors/calculation/
    ├── path_adaptor.py          # D-05: 2 处 lazy import 改为 matmaster 侧模块
    └── job_service.py           # D-05: 1 处 lazy import 改为 matmaster 侧模块

src/                             (修改 -- consumer 迁移)
├── services/
│   ├── chat_history.py          # D-06/D-07: 消息类型从 evomaster -> matmaster
│   ├── agent_run_bohrium.py     # D-08/D-09: SSHSession 从 evomaster -> matmaster
│   └── agent_run_service.py     # D-01: 构造 BohriumSetupService 时注入回调

tests/matmaster/
├── test_import_audit.py         # 更新：bohrium lazy import 检测规则（删除或改为 matmaster 路径）
└── integration/
    └── test_events_to_messages.py  # 不需要改（已通过 matmaster types 测试）
```

### Pattern 1: 回调注入（Dependency Inversion via Callable）

**What:** BohriumSetupService 的 4 个方法当前直接 lazy import src 模块的函数。改为在构造时接受 4 个 callable 参数，调用方（agent_run_service.py）负责传入实际实现。

**When to use:** 核心包（matmaster）不应依赖应用层（src），但运行时需要应用层提供的具体逻辑。

**当前结构（反向依赖）：**
```python
# matmaster/integration/bohrium_setup.py (当前)
class BohriumSetupService:
    def load_credentials(self, session_id):
        from src.services.agent_run_bohrium import load_run_credentials  # 反向!
        return load_run_credentials(self._sessions_service, session_id)
```

**目标结构（回调注入）：**
```python
# matmaster/integration/bohrium_setup.py (目标)
class BohriumSetupService:
    def __init__(
        self,
        *,
        load_credentials_fn: Callable[[str], tuple[dict[str, Any], str | None, str]],
        apply_credentials_fn: Callable[[Any, dict[str, Any]], None],
        setup_fn: Callable[..., BohriumSetupResult],
        cleanup_fn: Callable[..., None],
        bus: MessageBus | None = None,
    ) -> None:
        self._load_credentials = load_credentials_fn
        self._apply_credentials = apply_credentials_fn
        self._setup = setup_fn
        self._cleanup = cleanup_fn
        self._bus = bus

    def load_credentials(self, session_id: str):
        return self._load_credentials(session_id)
```

```python
# src/services/agent_run_service.py (调用方)
from src.services.agent_run_bohrium import (
    load_run_credentials,
    apply_run_credentials_to_session,
    setup_bohrium_for_run,
    cleanup_bohrium_after_run,
)

bohrium_svc = BohriumSetupService(
    load_credentials_fn=lambda sid: load_run_credentials(sessions_service, sid),
    apply_credentials_fn=apply_run_credentials_to_session,
    setup_fn=setup_bohrium_for_run,
    cleanup_fn=lambda **kw: cleanup_bohrium_after_run(
        sessions_service=sessions_service, **kw
    ),
    bus=bus,
)
```

### Pattern 2: 常量本地化 + 纯函数搬迁

**What:** BOHRIUM_OPENAPI_HOST 和 3 个 bohrium 环境函数从 evomaster/src 搬入 matmaster 侧。

**当前 src 侧定义（带 URL_PART 环境感知）：**
```python
# src/utils/constant.py
from utils.env import SERVICE_ENV, URL_PART
BOHRIUM_OPENAPI_HOST = os.getenv(
    'BOHRIUM_BASE_URL',
    (f'https://openapi{URL_PART}.dp.tech' if URL_PART else 'https://open.bohrium.com'),
).rstrip('/')
```

**matmaster 侧简化定义（D-03 决定）：**
```python
# matmaster/integration/bohrium_env.py (新文件)
BOHRIUM_OPENAPI_HOST = os.getenv(
    'BOHRIUM_BASE_URL', 'https://open.bohrium.com'
).rstrip('/')
```

差异说明：matmaster 侧不使用 URL_PART 环境感知，统一由 `BOHRIUM_BASE_URL` 环境变量控制。线上部署通过环境变量注入实际 host（test/uat/prod 环境各自设置），所以功能等效。

### Anti-Patterns to Avoid
- **TYPE_CHECKING import 残留:** BohriumSetupResult 当前在 TYPE_CHECKING 下 import from src。改用 matmaster 侧副本后，确保删除此 import 路径
- **`_env` 属性穿透:** agent_run_bohrium.py 中 `ssh_session._env` 访问的是 evomaster SSHSession 内部的 SSHEnv 对象。matmaster SSHSession 没有 `_env` -- 它把 SSHEnv 功能直接合并到了 SSHSession 本身。`_sync_skills_to_ssh_session` 中的 `_env.upload_directory_tarball` 需改为直接调用 `ssh_session.upload_directory_tarball`
- **model_dump 字段不匹配:** evomaster model_dump 输出包含 `meta` 字段，matmaster 不包含。chat_history 中所有依赖 `meta` 字段的逻辑需要审查（但实测 chat_history 的 `_assistant_reasoning_content` 读取 raw dict 的 `meta` 字段，这是从 DB event content 来的，不受消息类型影响）

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| SSHSession._env 方法调用 | 不要在 matmaster SSHSession 上加 _env 兼容属性 | 直接调用 ssh_session.upload_directory_tarball / ssh_bash_noninteractive | matmaster SSHSession 已经直接暴露这些方法（Phase 25 设计决策） |
| 消息类型适配层 | 不要在 matmaster 和 evomaster 之间加 adapter 类 | 直接切换 import 路径，修改字段引用 | 只有一个消费方（chat_history），adapter 增加复杂性 |
| BOHRIUM_OPENAPI_HOST 环境感知 | 不要复制 URL_PART 逻辑到 matmaster | 简单 os.getenv 加默认值 | 线上部署已通过环境变量控制 |

## Common Pitfalls

### Pitfall 1: _adapt_tool_calls_format 方向反转

**What goes wrong:** 当前 `_adapt_tool_calls_format` 将 matmaster 扁平格式 `{"id", "name", "arguments": dict}` 转换为 evomaster 嵌套格式 `{"id", "type", "function": {"name", "arguments": str}}`。切换到 matmaster 类型后，`model_validate` 需要的恰恰是扁平格式。如果不更新 `_adapt_tool_calls_format`，assistant_state 事件的 tool_calls 会继续被转成嵌套格式，导致 matmaster `AssistantMessage.model_validate` 失败。

**Why it happens:** `_adapt_tool_calls_format` 的存在是因为 DB 中存的是 matmaster 格式，但之前需要转成 evomaster 格式给 evomaster 类型消费。迁移后方向反了。

**How to avoid:** 迁移后，`_adapt_tool_calls_format` 应该反转逻辑：保留 matmaster 扁平格式原样，把遗留 evomaster 嵌套格式（如果 DB 中有历史数据）转成扁平格式。或者更简单：先尝试 matmaster 扁平格式 model_validate，失败则尝试转换。

**Warning signs:** `model_validate` 抛出 `ValidationError: tool_calls.0.name Field required`

**已验证证据（运行时测试）：**
```
matmaster AssistantMessage.model_validate(evomaster nested format) -> FAILED
  ValidationError: tool_calls.0.name Field required, tool_calls.0.arguments Field required

matmaster AssistantMessage.model_validate(matmaster flat format) -> OK
  tool_calls=[ToolCallData(id='tc1', name='test', arguments={})]
```

### Pitfall 2: ToolMessage 字段名差异

**What goes wrong:** evomaster ToolMessage 使用 `name` 字段，matmaster ToolMessage 使用 `tool_name` 字段。chat_history.py 中 `_repair_incomplete_tool_turns` 和 `events_to_dialog_messages` 构造 ToolMessage 时传入 `name=` 参数，切换后需改为 `tool_name=`。

**Why it happens:** matmaster 类型重新设计时将 `name` 改为更明确的 `tool_name`。

**How to avoid:** 全局搜索 `ToolMessage(` 构造，确保所有 `name=` 参数改为 `tool_name=`。

**Warning signs:** `TypeError: unexpected keyword argument 'name'` 或字段被忽略（Pydantic v2 的 extra='ignore' 配置下会静默丢失）

### Pitfall 3: ToolCall vs ToolCallData 类型名 + 结构差异

**What goes wrong:** evomaster `ToolCall` 是嵌套结构 `{id, type, function: {name, arguments: str}}`。matmaster `ToolCallData` 是扁平结构 `{id, name, arguments: dict}`。chat_history 中 `flush_tool_calls` 使用 `ToolCall.model_validate(tc)` -- tc 是 evomaster 嵌套 dict 格式。切换后需改用 `ToolCallData.model_validate`，且输入 dict 必须是扁平格式。

**Why it happens:** 两个类型体系的设计不同。

**How to avoid:** `_tool_call_from_event` 当前返回 evomaster 嵌套格式 dict。切换后应改为返回 matmaster 扁平格式 dict `{"id": call_id, "name": name, "arguments": args_dict}`。

### Pitfall 4: model_dump 输出差异影响下游消费

**What goes wrong:** `events_to_dialog_messages` 最终 return 的是 `list[dict]`，通过 `msg.model_dump()` 产生。evomaster 和 matmaster 的 model_dump 输出有差异：

| 字段 | evomaster | matmaster |
|------|-----------|-----------|
| role | `<MessageRole.USER: 'user'>` (enum) | `<Role.USER: 'user'>` (enum) |
| tool_calls 结构 | `[{id, type, function: {name, arguments: str}}]` | `[{id, name, arguments: dict}]` |
| ToolMessage tool name | `name` | `tool_name` |
| meta 字段 | 存在（default `{}`） | 不存在 |

**How to avoid:** 检查所有读取 `events_to_dialog_messages` 返回值的下游代码。核心消费者包括：
1. `task.meta['dialog_history']` -- 传给 LLM 前会再处理
2. `validate_dialog_messages_for_llm` -- 内部读取 `role`、`tool_calls`、`tool_call_id`
3. `summarize_dialog_messages_for_log` -- 读取 `role`、`tool_calls`、`function.name`

其中 (2) 和 (3) 都在 chat_history.py 内部，可一并修改。(1) 的消费者在 `exp.py` 的 `_build_dialog_history`，需确认兼容性。

**Warning signs:** LLM 调用失败，tool_calls 格式不符合 OpenAI API 规范

### Pitfall 5: _sync_skills_to_ssh_session 的 _env 属性

**What goes wrong:** `_sync_skills_to_ssh_session` (agent_run_bohrium.py:159) 访问 `ssh_session._env` 获取 SSHEnv 对象，然后调用 `env.upload_directory_tarball` 和 `env.ssh_bash_noninteractive`。matmaster SSHSession 没有 `_env` 属性 -- 它把这些方法直接放在 SSHSession 上。

**How to avoid:** 将 `env = ssh_session._env` 改为直接使用 `ssh_session`，调用 `ssh_session.upload_directory_tarball` 和 `ssh_session.ssh_bash_noninteractive`。

## Code Examples

### 字段映射对照表（已验证）

```python
# evomaster.utils.types -> matmaster.types.messages 完整映射
#
# 1. Import 映射
#    evomaster: from evomaster.utils.types import AssistantMessage, ToolCall, ToolMessage, UserMessage
#    matmaster: from matmaster.types.messages import AssistantMessage, ToolCallData, ToolMessage, UserMessage
#
# 2. ToolCall 构造 (chat_history 的 flush_tool_calls)
#    evomaster: ToolCall.model_validate({"id": "tc1", "type": "function", "function": {"name": "bash", "arguments": "{\"cmd\":\"ls\"}"}})
#    matmaster: ToolCallData.model_validate({"id": "tc1", "name": "bash", "arguments": {"cmd": "ls"}})
#    注意: arguments 从 str -> dict
#
# 3. ToolMessage 构造
#    evomaster: ToolMessage(tool_call_id="tc1", name="bash", content="result")
#    matmaster: ToolMessage(tool_call_id="tc1", tool_name="bash", content="result")
#    注意: name -> tool_name
#
# 4. AssistantMessage.tool_calls 访问
#    evomaster: tc.id, tc.function.name, tc.function.arguments
#    matmaster: tc.id, tc.name, tc.arguments
#
# 5. model_dump() 输出差异
#    evomaster: {role: <MessageRole.ASSISTANT>, content: ..., meta: {}, tool_calls: [{id, type, function: {name, arguments: str}}], reasoning_content: ...}
#    matmaster: {role: <Role.ASSISTANT>, content: ..., tool_calls: [{id, name, arguments: dict}], reasoning_content: ...}
#    注意: 无 meta 字段; tool_calls 扁平; arguments 是 dict 不是 str
```

### _adapt_tool_calls_format 反转逻辑

```python
# 当前逻辑：matmaster flat -> evomaster nested (为 evomaster 类型消费)
# 迁移后逻辑：evomaster nested -> matmaster flat (为 matmaster 类型消费)
def _adapt_tool_calls_format(raw: dict) -> dict:
    """Adapt legacy evomaster nested ToolCall format to matmaster flat ToolCallData format.

    evomaster serializes: {"id", "type", "function": {"name", "arguments": str}}
    matmaster expects:    {"id", "name", "arguments": dict}

    If a tool_call already has 'name' at top level (matmaster format), leave as-is.
    """
    tcs = raw.get('tool_calls')
    if not tcs or not isinstance(tcs, list):
        return raw
    adapted = []
    for tc in tcs:
        if not isinstance(tc, dict):
            adapted.append(tc)
            continue
        if 'name' in tc and 'arguments' in tc and 'function' not in tc:
            # Already matmaster flat format
            args = tc['arguments']
            if isinstance(args, str):
                import json
                try:
                    args = json.loads(args)
                except (json.JSONDecodeError, ValueError):
                    args = {}
            adapted.append({**tc, 'arguments': args})
        elif 'function' in tc:
            # Legacy evomaster nested format -> convert to flat
            func = tc['function']
            args_raw = func.get('arguments', '{}')
            if isinstance(args_raw, str):
                import json
                try:
                    args = json.loads(args_raw)
                except (json.JSONDecodeError, ValueError):
                    args = {}
            elif isinstance(args_raw, dict):
                args = args_raw
            else:
                args = {}
            adapted.append({
                'id': tc.get('id', ''),
                'name': func.get('name', ''),
                'arguments': args,
            })
        else:
            adapted.append(tc)
    return {**raw, 'tool_calls': adapted}
```

### _tool_call_from_event 迁移

```python
# 当前：返回 evomaster nested 格式
# 迁移后：返回 matmaster flat 格式
@staticmethod
def _tool_call_from_event(ev: dict) -> dict | None:
    c = ev.get('content')
    if not isinstance(c, dict):
        return None
    call_id = c.get('id') or ''
    name = c.get('name') or ''
    args = c.get('args')
    if isinstance(args, str):
        import json
        try:
            args = json.loads(args)
        except (json.JSONDecodeError, ValueError):
            args = {}
    elif not isinstance(args, dict):
        args = {}
    return {
        'id': call_id,
        'name': name,
        'arguments': args,
    }
```

### BohriumSetupResult matmaster 侧副本

```python
# 从 src/services/agent_run_bohrium.py L238-245 读取的字段定义
from typing import Any, NamedTuple

class BohriumSetupResult(NamedTuple):
    """Result of Bohrium setup for a run."""
    ssh_attached: bool
    abort_result: tuple[Any, int] | None
    execution_session: Any | None
    execution_workdir: str | None
    session_type: str | None
```

### bohrium 纯函数搬迁（推荐位置）

```python
# matmaster/integration/bohrium_env.py (新文件)
# 包含:
#   BOHRIUM_OPENAPI_HOST 常量 (D-03)
#   get_bohrium_credentials() (D-04)
#   get_bohrium_storage_config() (D-04)
#   inject_bohrium_executor() (D-04)
#   BohriumSetupResult 副本 (D-02)
```

推荐放在 `matmaster/integration/` 而非 `matmaster/adaptors/calculation/`，原因：
1. `BOHRIUM_OPENAPI_HOST` 被 script_env.py（tools 层）使用，不仅限于 calculation
2. `BohriumSetupResult` 被 bohrium_setup.py（integration 层）使用
3. 三个 bohrium 函数被 path_adaptor.py 和 job_service.py（adaptors 层）使用，放在 integration 层可以被两个层级都 import

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest + pytest-asyncio (auto mode) |
| Config file | pyproject.toml `[tool.pytest.ini_options]` |
| Quick run command | `uv run pytest tests/matmaster/test_import_audit.py -x -q` |
| Full suite command | `uv run pytest tests/matmaster/ -x -q` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| INVR-01 | bohrium_setup.py 无 src lazy import | unit (AST audit) | `uv run pytest tests/matmaster/test_import_audit.py -x -q` | Update needed |
| INVR-02 | script_env.py 无 src lazy import | unit (AST audit) | `uv run pytest tests/matmaster/test_import_audit.py -x -q` | Update needed |
| CONS-03 | chat_history.py 使用 matmaster 消息类型 | unit (import audit + behavior) | `uv run pytest tests/matmaster/integration/test_events_to_messages.py -x -q` | Exists (passes baseline) |
| CONS-04 | agent_run_bohrium.py 使用 matmaster SSHSession | unit (import audit) | `uv run pytest tests/matmaster/test_import_audit.py -x -q` | Update needed |

### Sampling Rate
- **Per task commit:** `uv run pytest tests/matmaster/test_import_audit.py tests/matmaster/integration/test_events_to_messages.py -x -q`
- **Per wave merge:** `uv run pytest tests/matmaster/ -x -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/matmaster/test_import_audit.py` -- 更新规则：(1) 删除 `TestExpectedLazyBohrimImportsExist`（bohrium lazy imports 将不存在）；(2) 新增检测 matmaster/ 无 src lazy import 的规则；(3) 新增检测 matmaster/ 无 evomaster.agent.session lazy import 的规则
- [ ] `tests/matmaster/integration/test_events_to_messages.py` -- 已存在 17 个测试，baseline 全部 pass。迁移后行为应保持一致（输出 matmaster 类型而非 evomaster 类型）
- [ ] 新测试：bohrium_setup 回调注入 unit test（mock callable，验证不触发 src import）
- [ ] 新测试：model_dump 输出格式断言（确认 tool_calls 使用 matmaster 扁平格式）

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| evomaster SSHSession + SSHEnv 分层 | matmaster SSHSession 单类直接持有 paramiko | Phase 25 (2026-04) | 无 _env 属性，方法直接暴露 |
| evomaster ToolCall (nested function dict) | matmaster ToolCallData (flat name+arguments) | matmaster v2.0 | chat_history 类型切换核心差异 |
| URL_PART 环境感知 BOHRIUM_OPENAPI_HOST | os.getenv 直接读取 BOHRIUM_BASE_URL | Phase 28 (D-03) | 运维通过环境变量统一控制 |

## Open Questions

1. **events_to_dialog_messages 输出格式变化对 exp.py 的影响**
   - What we know: `events_to_dialog_messages` 返回 `list[dict]`，被 `exp.py` 的 `_build_dialog_history` 消费，最终传给 LLM。tool_calls 格式从 evomaster nested 变为 matmaster flat
   - What's unclear: exp.py 内部是否有对 tool_calls nested 格式的硬编码依赖
   - Recommendation: 实施时检查 exp.py 中 dialog_history 的消费路径，确认 to_api_dict() 会正确转换

2. **DB 中历史事件数据的 assistant_state 格式**
   - What we know: assistant_state 事件的 content 可能存储为 evomaster nested 格式（历史数据）或 matmaster flat 格式（新数据）
   - What's unclear: 历史数据量和格式分布
   - Recommendation: `_adapt_tool_calls_format` 必须处理两种输入格式，保留向后兼容

3. **events_to_messages 方法已经使用 matmaster 类型**
   - What we know: `ChatHistoryConverter.events_to_messages()` 已经 import matmaster 类型并手动转换。但 `events_to_dialog_messages()` 仍使用 evomaster 类型
   - What's unclear: 两个方法是否有独立调用方
   - Recommendation: 迁移后两个方法统一使用 matmaster 类型，`events_to_messages` 可简化（不再需要二次转换）

## Sources

### Primary (HIGH confidence)
- 运行时验证：`uv run python3 -c` 实际测试了 evomaster 和 matmaster 消息类型的 model_dump/model_validate 行为
- 源码直接阅读：bohrium_setup.py、script_env.py、chat_history.py、agent_run_bohrium.py、messages.py、evomaster/utils/types.py

### Secondary (MEDIUM confidence)
- evomaster/env/bohrium.py 的 3 个纯函数依赖分析（仅使用 os、copy、typing，无外部依赖）
- src/utils/constant.py 的 BOHRIUM_OPENAPI_HOST 定义分析

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - 无新依赖引入，纯代码重构
- Architecture: HIGH - 回调注入和类型切换都是已验证模式
- Pitfalls: HIGH - 通过运行时测试验证了字段映射差异，找到了 5 个关键 pitfall

**Research date:** 2026-04-01
**Valid until:** 2026-05-01 (纯代码重构，不受外部版本变化影响)
