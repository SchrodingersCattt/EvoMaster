# Phase 26: Tool 内化与遗留工具收归 - Context

**Gathered:** 2026-04-01
**Status:** Ready for planning

<domain>
## Phase Boundary

全部 tool 能力在 matmaster.tools 原生运行，消除 EvoToolAdapter、evomaster builtin helper 依赖、MonitorJobTool 和 web_search_tool 的外部导入。本 phase 不涉及 lazy_mcp.py 和 cache_mcp_schemas.py 中的 evomaster MCP 依赖（属 Phase 27）。

</domain>

<decisions>
## Implementation Decisions

### MonitorJobTool 收归 (TOOL-09)
- **D-01:** MonitorJobTool 移植为 BuiltinTool 子类，放入 `matmaster/tools/builtin/monitor_job/` 目录
- **D-02:** session 依赖通过 self.session 接口获取（workspace、credentials、stop_event），使用 getattr 取属性，与 bash_tool 双路径模式一致
- **D-03:** `_tool.py`、`_constants.py`、`_lifecycle.py` 三文件整体搬入，改继承 BuiltinTool ABC，参数从 BaseToolParams 改为 json_schema dict + arguments dict 模式

### web_search 统一 (TOOL-10)
- **D-04:** exp.py 直接切换到 matmaster 原生 WebSearchTool（`matmaster/tools/builtin/web_search_tool.py`），不再 import `playground.mat_master.tools.web_search`
- **D-05:** 不补齐 playground 旧版的 page/location 参数，名称统一为 `web_search`（与 TOML 配置一致）

### Helper 内化 (TOOL-08)
- **D-06:** 最小成本断依赖。`is_dangerous_bash_command` 及相关常量/正则直接内联到 `bash_tool.py` 中；`MAX_OUTPUT_SIZE`、`SNIPPET_LINES`、`maybe_truncate` 直接内联到 `edit_tool.py` 中
- **D-07:** 不创建独立 helper 模块，解耦后会重新更新 tool，届时一并重构

### EvoToolAdapter 清理 (TOOL-07)
- **D-08:** 完全删除 `matmaster/tools/evomaster_tool_adapter.py` 文件
- **D-09:** 删除 `matmaster/tools/__init__.py` 中 EvoToolAdapter 的导出
- **D-10:** exp.py 中 `# 2. Evo adapter tools` 整段替换为原生注册（MonitorJobTool + WebSearchTool 作为 builtin 直接注册，source='builtin'）

### Claude's Discretion
- MonitorJobTool 内部 `_lifecycle.py` 中对 evomaster session 类型判断（isinstance SSHSession）的具体替代方式
- 内联 helper 时的代码组织细节（放文件顶部 vs 使用处附近）

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Tool 系统架构
- `matmaster/tools/builtin/base.py` — BuiltinTool ABC 定义，所有内置工具的基类
- `matmaster/tools/tool_result.py` — ToolResult 统一结果模型
- `matmaster/tools/tool_registry.py` — Tool Protocol 定义和注册机制

### 当前依赖点（需要修改的文件）
- `matmaster/core/exp.py` §380-414 — builtin tool 注册逻辑（含 evo adapter 段）
- `matmaster/tools/builtin/bash_tool.py` §18 — bash_safety import
- `matmaster/tools/builtin/edit_tool.py` §17-21 — editor helper imports
- `matmaster/tools/evomaster_tool_adapter.py` — 待删除
- `matmaster/tools/__init__.py` — EvoToolAdapter 导出待删除

### 移植源文件
- `evomaster/agent/tools/builtin/bash_safety.py` — is_dangerous_bash_command 源码
- `evomaster/agent/tools/builtin/editor.py` §32-49 — MAX_OUTPUT_SIZE/SNIPPET_LINES/maybe_truncate 源码
- `evomaster/agent/tools/builtin/monitor_job/_tool.py` — MonitorJobTool 主文件
- `evomaster/agent/tools/builtin/monitor_job/_constants.py` — 常量定义
- `evomaster/agent/tools/builtin/monitor_job/_lifecycle.py` — 作业轮询生命周期

### 已有原生实现（可直接使用）
- `matmaster/tools/builtin/web_search_tool.py` — matmaster 原生 WebSearchTool

### Exp 配置
- `matmaster/exps/direct.toml` — 主模式 builtin 工具列表
- `matmaster/exps/explore.toml` — 探索模式 builtin 工具列表

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `matmaster/tools/builtin/web_search_tool.py`: matmaster 原生 WebSearchTool 已完整实现，httpx + ToolResult，可直接替换 playground 旧版
- `matmaster/tools/builtin/base.py`: BuiltinTool ABC 提供 self.session 注入和 _execute → execute 异步包装
- `matmaster/tools/builtin/bash_tool.py`: 双路径模式（matmaster LocalSession vs 其他 session）是 MonitorJobTool session 适配的参考

### Established Patterns
- builtin tool 注册: `registry.register(tool, source='builtin')`
- 参数定义: ClassVar json_schema dict，execute 接收 arguments dict，返回 ToolResult
- session 注入: base class 的 `self.session` 在 Exp 注册时绑定

### Integration Points
- `matmaster/core/exp.py:_init_builtin_tools()` — tool 注册入口，需要把 evo adapter 段改为原生注册
- `matmaster/exps/*.toml` — builtin 工具白名单，`monitor_job` 和 `web_search` 已在列表中
- `matmaster/eval_tooling_snapshot.py` — 工具快照评估，引用了 `monitor_job`

</code_context>

<specifics>
## Specific Ideas

- 用户明确表示解耦后会重新更新 tool，因此 helper 内化采用最小成本方案（内联），不做独立模块
- MonitorJobTool 的 session 适配参考 bash_tool 的双路径模式

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 26-tool*
*Context gathered: 2026-04-01*
