# Tool Runtime v2 并行功能补齐

**日期:** 2026-04-02
**状态:** 已确认
**范围:** tool-runtime-v2 spec 中未被 Phase 34-36 覆盖的 3 个独立功能

---

## 背景

Phase 32-33 建立了 Tool Runtime v2 核心骨架（ToolSpec/ToolBinding/ToolInstance/ToolCatalog/FullToolRunner/ToolScheduler）。Phase 34-36 聚焦于 generator 事件链贯通、Hook 退役和去总线化。

但 spec（`docs/specs/2026-04-02-tool-runtime-v2.md`）中有多个 tool 层功能在 Phase 32-36 中均未覆盖。这些功能与 generator 链路无关，可以在独立分支上并行实现。

### 实施范围

本设计覆盖 3 个 plan：

| Plan | 功能 | 改动文件 |
|------|------|---------|
| 1 | ToolSpec 补齐 + 结果裁剪 | tool_spec.py, tool_runner.py, tool_compiler.py |
| 2 | ToolCompiler 拓扑依赖绑定 | tool_compiler.py |
| 3 | input_validator 体系 | tool_spec.py, base.py, write_tool.py, edit_tool.py, tool_compiler.py, tool_runner.py |

### 命名约定

代码中 effect_level 使用 `"pure_read" / "local_mutation" / "external_write"`，与 spec 的 `"none" / "local_mutation" / "external_effect"` 不同。**本设计三个 plan 统一沿用代码现状命名**，effect_level 对齐作为独立 task defer。

### 显式排除

以下功能 defer 到 Phase 34-36 合并后：

- **ToolExecutionContext 重构 + on_progress + ToolProgressEvent** — 需要改 executor 签名，波及所有工具
- **execute_batch asyncio.gather 并发化** — 需要与 Phase 34 generator 事件流对齐
- **Post hook ToolResult|None 返回** — FullToolRunner 不调 hook（D-01），低优先级
- **overlay_factory.py** — 等 Phase 34 skill overlay 路径稳定
- **effect_level 命名对齐** — 代码用 pure_read/external_write，spec 用 none/external_effect，待统一确认

### 与 Phase 34-36 的隔离

Phase 34-36 主要改动：agent.py、exp.py、hooks.py、agent_run_service.py、event_payloads.py

本设计改动：tool_spec.py、tool_runner.py、tool_compiler.py、builtin/*.py

**零文件交叉**，合并时无冲突风险。

---

## Plan 1: ToolSpec 补齐 + 结果裁剪

### 目标

ToolSpec 补齐 max_result_chars 和 usage_hint 字段。FullToolRunner 在 executor 返回后执行结果裁剪，防止长输出膨胀上下文。

### 改动细节

#### 1.1 ToolSpec 新增字段

文件：`matmaster/types/tool_spec.py`

ToolSpec 是 Pydantic BaseModel（frozen=True ConfigDict），新增两个字段：

```python
class ToolSpec(BaseModel):
    # ... existing fields ...
    max_result_chars: int = 0       # 0 = 不限；>0 时 ToolRunner 裁剪 content
    usage_hint: str = ""            # 模型可见的使用提示
```

#### 1.2 BUILTIN_META 扩展

文件：`matmaster/tools/tool_compiler.py`

BUILTIN_META 从 `(ToolPlane, effect_level, fast_path_eligible)` 扩展为 `(ToolPlane, effect_level, fast_path_eligible, max_result_chars)`。`compile()` 中的位置解包（当前 `plane, effect_level, fast_path = BUILTIN_META.get(...)`）需同步更新为 4 元素解包。

按 spec 第 10 章总表：

| 工具 | max_result_chars |
|------|-----------------|
| execute_bash | 12000 |
| read_file | 12000 |
| list_dir | 8000 |
| glob | 8000 |
| grep | 8000 |
| web_fetch | 16000 |
| 其他 | 0 |

#### 1.3 FullToolRunner 裁剪逻辑

文件：`matmaster/core/tool_runner.py`

在 Step 8（executor 执行）返回后、append result 前插入归一化步骤（行号基于未修改的代码，Plan 3 插入后会偏移）：

```python
# 结果裁剪
if instance.tool_spec.max_result_chars > 0 and len(tr.content) > instance.tool_spec.max_result_chars:
    tr = self._truncate_result(tr, instance.tool_spec.max_result_chars, tc.tool_call_id)
```

`_truncate_result` 是 FullToolRunner 的实例方法，通过 `self._topology.workspace_root` 获取输出目录：

1. 确保 `{workspace_root}/.tool_results/` 目录存在（`Path.mkdir(parents=True, exist_ok=True)`）
2. 完整 content 写入 `{workspace_root}/.tool_results/{tool_call_id}.txt`
3. 计算 tail 长度：`tail_len = min(2000, max_result_chars // 4)`，使裁剪后内容接近上限
4. 裁剪策略：`head[:max//2] + "\n\n... [{n} chars truncated, full result at {path}] ...\n\n" + tail[-tail_len:]`
5. 返回新 ToolResult，`meta` 中新增 `full_result_path` 指向完整结果文件
6. 裁剪只作用于 content，不影响 payload 和 meta（除了新增 full_result_path）

### 测试策略

- 单测：ToolSpec 构造时 max_result_chars 字段存在且默认 0
- 单测：_truncate_result 对超限 content 正确裁剪，保留头尾
- 单测：_truncate_result 写入磁盘文件且 meta["full_result_path"] 正确
- 单测：content 未超限时不裁剪
- 集成测：BUILTIN_META 中 read_file 的 max_result_chars=12000 被 ToolCompiler 传入 ToolSpec

---

## Plan 2: ToolCompiler 拓扑依赖绑定

### 目标

让 ToolCompiler.compile() 真正消费 RuntimeTopology，在 local session 下将 glob/grep/list_dir 的 resource_claims 从 exclusive 放宽为 shared_read。

### 改动细节

文件：`matmaster/tools/tool_compiler.py`

在 compile() 中，查完 BUILTIN_CLAIMS 静态表后，根据 topology 条件放宽：

```python
# 拓扑依赖绑定放宽（spec 8.2）
if (
    topology.session_kind == "local"
    and topology.session_capabilities is not None
    and topology.session_capabilities.shell_persistence == "stateless"
    and tool.name in ("list_dir", "glob", "grep")
):
    claims = (ResourceClaim(resource_id="session", mode="shared_read"),)
```

放宽条件：
- `session_kind == "local"`：本地子进程天然隔离
- `shell_persistence == "stateless"`：无共享 shell 状态
- 仅限 list_dir/glob/grep：这三个工具底层用 subprocess.run，进程间无状态共享

SSH session 即使 stateless 也保持 exclusive，因为共享 SSH 连接的 channel 复用。

### 运行时影响

放宽后，这三个工具在 local session 下满足 fast path 条件（effect_level=pure_read + shared_read claims + fast_path_eligible=True），跳过 Scheduler 直接并发执行。搜索密集型任务的吞吐量显著提升。

### 测试策略

- 单测：local + stateless topology 下 glob 编译出 shared_read claim
- 单测：ssh + stateless topology 下 glob 仍为 exclusive claim
- 单测：local + session_capabilities=None 时不放宽（保持 exclusive）
- 单测：local topology 下 execute_bash 仍为 exclusive claim（不受放宽影响）
- 单测：非 builtin 工具（无 BUILTIN_CLAIMS 条目）不受拓扑放宽影响

---

## Plan 3: input_validator 体系

### 目标

在 ToolInstance 上新增 input_validator 字段，BuiltinTool 基类提供 validate_input() 钩子，WriteTool 和 EditTool 实现语义校验，FullToolRunner 在执行链中调用。

### 改动细节

#### 3.1 ToolInstance 新增字段

文件：`matmaster/types/tool_spec.py`

```python
@dataclass(frozen=True)
class ToolInstance:
    tool_spec: ToolSpec
    tool_binding: ToolBinding
    tool_executor: Callable[[dict[str, Any]], Awaitable[ToolResult]]
    input_validator: Callable[[dict[str, Any]], Awaitable[ToolDecision | None]] | None = None
```

#### 3.2 BuiltinTool 基类钩子

文件：`matmaster/tools/builtin/base.py`

```python
async def validate_input(self, arguments: dict[str, Any]) -> ToolDecision | None:
    """Override to add tool-specific semantic validation.
    Return None to pass, ToolDecision(decision='deny', ...) to reject."""
    return None
```

#### 3.3 WriteTool.validate_input

文件：`matmaster/tools/builtin/write_tool.py`

提取现有 L61-67 的 read-before-modify 检查逻辑。注意 `session.path_exists()` 在 LocalSession 下是 `os.path.exists`（微秒级），在 SSHSession 下是 SFTP 调用（需要网络 I/O）。对于 SSHSession 场景，path_exists 调用应通过 `asyncio.to_thread()` 包装以避免阻塞事件循环：

```python
async def validate_input(self, arguments: dict[str, Any]) -> ToolDecision | None:
    file_path = arguments.get("file_path", "")
    if self._tracker is None:
        return None
    exists = await asyncio.to_thread(self._session.path_exists, file_path)
    if exists and not self._tracker.has_been_read(posixpath.normpath(file_path)):
        return ToolDecision(
            decision="deny",
            reason=f"file '{file_path}' must be read before modify",
        )
    return None
```

原位置的检查代码保留（双保险）。Phase 35 CMIG-01 统一迁移后再删除原位置代码。

#### 3.4 EditTool.validate_input

文件：`matmaster/tools/builtin/edit_tool.py`

提取 old_str == new_str 的 no-op 检查：

```python
async def validate_input(self, arguments: dict[str, Any]) -> ToolDecision | None:
    old_str = arguments.get("old_str", "")
    new_str = arguments.get("new_str", "")
    if old_str == new_str:
        return ToolDecision(
            decision="deny",
            reason="old_str and new_str are identical, no edit needed",
        )
    return None
```

原位置保留。

#### 3.5 ToolCompiler 绑定

文件：`matmaster/tools/tool_compiler.py`

compile() 中检测工具是否实现 validate_input：

```python
validator = None
if hasattr(tool, 'validate_input') and callable(tool.validate_input):
    validator = tool.validate_input

return ToolInstance(
    tool_spec=spec,
    tool_binding=binding,
    tool_executor=executor,
    input_validator=validator,
)
```

#### 3.6 FullToolRunner 执行链 Step 3

文件：`matmaster/core/tool_runner.py`

在 Step 2（Layer A StructuralValidation）之后、Step 4（Layer B RunStateGuard）之前插入（对应 spec 9.1 的 Step 3 工具级语义校验）：

```python
# Step 3: input_validator (tool-specific semantic check)
if instance.input_validator is not None:
    try:
        decision = await instance.input_validator(tc.arguments)
    except Exception as exc:
        tr = ToolResult(
            status="error",
            content=str(exc),
            meta={"layer": "input_validation"},
        )
        if on_result:
            await on_result(tc, tr)
        results.append((tc, tr))
        continue
    if decision is not None and decision.decision == "deny":
        tr = ToolResult(
            status="error",
            content=decision.reason,
            meta={"layer": "input_validation"},
        )
        if on_result:
            await on_result(tc, tr)
        results.append((tc, tr))
        continue
```

### 约束

- WriteTool/EditTool 原有检查不删除，Phase 35 CMIG-01 负责统一迁移
- BashTool 的 _is_dangerous_command 不在此 plan 迁移（Phase 35 CMIG-02，属 CapabilityPolicy）
- input_validator 是纯语义校验，不依赖运行态（区别于 RunStateGuard）
- 当前 tool_executor 签名尚未包含 ToolExecutionContext（defer 到 Plan 4），input_validator 的 Callable 签名基于当前代码的单参数形式
- input_validator 抛出异常时，FullToolRunner 应 catch 并返回 `ToolResult(status="error", content=str(exc), meta={"layer": "input_validation"})`，不让异常传播

### 测试策略

- 单测：ToolInstance 构造时 input_validator=None 默认正常工作
- 单测：WriteTool.validate_input 未读文件时返回 deny
- 单测：WriteTool.validate_input 新文件（path_exists=False）返回 None
- 单测：EditTool.validate_input old==new 时返回 deny
- 单测：ToolCompiler 对有 validate_input 的工具绑定到 ToolInstance.input_validator
- 单测：ToolCompiler 对无 validate_input 的工具 input_validator=None
- 集成测：FullToolRunner 对 deny 的 input_validator 返回 error ToolResult 且不执行 executor
- 集成测：input_validator 抛异常时 FullToolRunner 返回 error ToolResult 而非传播异常

---

## 执行顺序

```
Plan 1 (ToolSpec + 裁剪)  ─┐
                            ├─→ 合并到主分支
Plan 2 (拓扑依赖绑定)      ─┤
                            │
Plan 3 (input_validator)   ─┘
```

三个 plan 互相独立，无依赖关系，可以任意顺序或并行执行。

Plan 3 改动文件与 Plan 1 有交叉（tool_spec.py、tool_runner.py、tool_compiler.py），如果并行执行需要注意合并顺序。建议串行：Plan 1 → Plan 2 → Plan 3，或 Plan 2 先行（最小改动量）→ Plan 1 → Plan 3。
