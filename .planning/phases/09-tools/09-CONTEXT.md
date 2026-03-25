# Phase 9: 文件操作 Tools - Context

**Gathered:** 2026-03-25
**Status:** Ready for planning

<domain>
## Phase Boundary

交付 Read/Write/Edit/Glob/Grep 五个独立 BuiltinTool + Read-Before-Modify 协议 + EditorTool 移除与 ExpConfig 显式列举切换。Agent 通过这些工具可在远程环境读取文件、创建/覆写文件、精确替换文件内容、按模式搜索文件路径、按正则搜索文件内容。

</domain>

<decisions>
## Implementation Decisions

### Edit 能力范围
- **D-01:** Edit tool 仅保留 str_replace 一个能力，对齐 Claude Code 设计。Write tool 覆盖全文覆写场景，str_replace 覆盖精确编辑场景。不保留 insert（行号漂移风险）和 undo_edit（BuiltinTool 构造注入模型不保证 _file_history 跨 assemble 存活）。

### Read-Before-Modify 协议
- **D-02:** 采用共享 ReadTracker 注入方案。Exp.assemble() 创建单一 ReadTracker 实例（内部维护 `_read_files: set[str]`），通过构造注入传给 Read/Write/Edit 三个 tool。ReadTool 执行时向 tracker 注册文件路径，WriteTool/EditTool 执行前检查 tracker。
- **D-03:** 违反协议时 WriteTool/EditTool 的 _execute 返回 `"Error: file '{path}' must be read before modify"` 字符串，符合 base.py 现有错误返回约定。
- **D-04:** ReadTracker 生命周期跟随 Exp run（assemble 时创建，cleanup 时销毁），per-run 状态不跨 run 保留。

### Glob/Grep 能力设计
- **D-05:** Glob/Grep 通过 session.exec_bash() 包装 find/grep 命令实现，复用已验证的远程执行路径，与 ListDirTool/BashTool 模式一致。远程环境（Bohrium 节点）文件系统只能通过 exec_bash 触达。
- **D-06:** 搜索范围强制限制在 workdir 内。所有搜索路径强制拼接 workdir 前缀，防止 path traversal。与 BashTool（无限制）不同，文件操作 tool 有明确边界。
- **D-07:** 输出通过 `| head -N` 截断防止 token 爆炸。GlobTool 封装 find，GrepTool 封装 grep -rn。

### EditorTool 切换策略
- **D-08:** Phase 9 内原子化完成 EditorTool 移除。新 native tools 交付后，同步从 _init_builtin_tools() 移除 EditorTool 的 EvoToolAdapter 注册路径。MonitorJobTool 保留 evo adapter 路径不变。
- **D-09:** ExpConfig.tools.builtin 从 wildcard `"*"` 切换到显式列举（列出所有 native tool 名称）。在 Phase 9 内完成，不留 Phase 10。

### Claude's Discretion
- GlobTool/GrepTool 的具体 find/grep 命令参数设计（maxdepth、include 模式等）
- ReadTracker 的具体实现（独立类 vs 简单 set wrapper）
- Read/Write/Edit 各 tool 的 json_schema 参数细节
- 输出截断的具体行数阈值（head -N 的 N 值）
- _init_builtin_tools 拆分后 MonitorJobTool 的注册代码组织方式

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### 项目定义
- `.planning/PROJECT.md` -- 项目愿景、核心价值、三层架构、post-v1 变更
- `.planning/REQUIREMENTS.md` -- Phase 9 需求：TOOL-01, TOOL-02, TOOL-03, TOOL-05, TOOL-06, TOOL-08
- `.planning/ROADMAP.md` -- Phase 9 目标、成功标准、依赖关系

### Phase 8 上下文（直接前驱）
- `.planning/phases/08-builtintool-tools/08-CONTEXT.md` -- BuiltinTool 基类设计、构造注入决策、source 标签、Exp 注册切换策略

### Phase 9 直接依赖的代码
- `matmaster/tools/builtin/base.py` -- BuiltinTool ABC（name/description/json_schema ClassVar + _execute + _require_session）
- `matmaster/tools/builtin/bash_tool.py` -- BashTool 实现参考（session.exec_bash 用法、proxy clear、输出格式）
- `matmaster/tools/builtin/listdir_tool.py` -- ListDirTool 实现参考（exec_bash 包装 find 命令的模式）
- `matmaster/core/exp.py` -- Exp._init_builtin_tools()（Phase 9 改造目标：新增 native tools + 移除 EditorTool + 拆分注册循环）
- `matmaster/config/exp.py` -- ExpConfig.tools.builtin（从 wildcard 切换到显式列举）
- `matmaster/tools/tool_registry.py` -- Tool Protocol + ToolRegistry.register(tool, source)

### evomaster 参考（迁移对象）
- `evomaster/agent/tools/builtin/editor.py` -- EditorTool（view/create/str_replace/insert/undo_edit 完整实现，Read/Write/Edit 的迁移参考）

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `matmaster/tools/builtin/base.py` BuiltinTool ABC -- 所有新 tool 继承此基类，session/workdir 构造注入
- `matmaster/tools/builtin/bash_tool.py` BashTool -- session.exec_bash() 用法参考，输出格式化参考
- `matmaster/tools/builtin/listdir_tool.py` ListDirTool -- exec_bash 包装 find 命令的模式，workdir 路径处理参考
- `evomaster/agent/tools/builtin/editor.py` EditorTool -- _view/_create/_str_replace 逻辑迁移，maybe_truncate 截断工具可复用

### Established Patterns
- BuiltinTool 子类: ClassVar 定义 name/description/json_schema，_execute 实现业务逻辑
- session.exec_bash(command) 远程执行，返回 dict 含 output/stdout/exit_code
- session.read_file(path) / session.write_file(path, content) 远程文件读写
- session.is_file(path) / session.is_directory(path) / session.path_exists(path) 远程路径检查
- ToolRegistry.register(tool, source="builtin") native tool 注册

### Integration Points
- `matmaster/tools/builtin/` -- 新增 read_tool.py, write_tool.py, edit_tool.py, glob_tool.py, grep_tool.py + read_tracker.py
- `matmaster/tools/builtin/__init__.py` -- 导出新 tool 类 + ReadTracker
- `matmaster/core/exp.py:_init_builtin_tools()` -- 改造：新增 5 个 native tool 注册 + 创建 ReadTracker 注入 + 移除 EditorTool + 拆分 MonitorJobTool 为独立注册行
- `matmaster/config/exp.py` -- ExpToolsConfig.builtin 字段语义更新

</code_context>

<specifics>
## Specific Ideas

- Edit tool 对齐 Claude Code 的 str_replace 设计，不保留 evomaster 的 insert/undo_edit
- ReadTracker 作为轻量共享状态，通过 Exp.assemble() 构造注入，不修改 BuiltinTool 基类签名（tracker 作为文件类 tool 子类的额外构造参数）
- Glob/Grep 的 workdir 限制与 BashTool 的无限制形成差异化：文件操作有安全边界，shell 执行保持灵活
- Phase 9 原子化完成 EditorTool → native tools 切换，避免双重路径共存

</specifics>

<deferred>
## Deferred Ideas

- MonitorJobTool 原生化 -- 当前保留 evo adapter 路径，评估是否需要迁移
- Read tool 支持 PDF/图片等非文本文件 -- 当前场景非核心
- Edit tool 的 replace_all 模式 -- 当前只支持唯一匹配替换，批量替换后续评估

None — discussion stayed within phase scope

</deferred>

---

*Phase: 09-tools*
*Context gathered: 2026-03-25*
