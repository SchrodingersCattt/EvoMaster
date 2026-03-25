# Phase 9: 文件操作 Tools - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-03-25
**Phase:** 09-tools
**Areas discussed:** Edit 能力范围, Read-Before-Modify 协议, Glob/Grep 能力设计, EditorTool 切换策略
**Mode:** Advisor (minimal_decisive calibration)

---

## Edit 能力范围

| Option | Description | Selected |
|--------|-------------|----------|
| 仅 str_replace | 对齐 Claude Code，字符串锚定替换。Write 覆盖全文覆写，str_replace 覆盖精确编辑。 | ✓ |
| str_replace + insert | 保留行号插入能力，放弃 undo_edit（架构不支持状态持久化）。 | |

**User's choice:** 仅 str_replace
**Notes:** BuiltinTool 构造注入模型下 undo_edit 的 _file_history 无法跨 assemble 存活；insert 行号漂移风险；Read-Before-Modify 协议消除了 undo 的主要动机。

---

## Read-Before-Modify 协议

| Option | Description | Selected |
|--------|-------------|----------|
| 共享 ReadTracker 注入 | Exp.assemble() 创建单一 ReadTracker，构造注入给 Read/Write/Edit。跨 tool 判断正确。 | ✓ |
| 各 tool 独立追踪 | Write/Edit 各自维护 _read_files set。简单但跨 tool 漏检。 | |

**User's choice:** 共享 ReadTracker 注入
**Notes:** Read→Write 是跨 tool 实例调用，独立追踪导致 WriteTool 永远无法感知 ReadTool 已读过该文件，协议形同虚设。

---

## Glob/Grep 能力设计

| Option | Description | Selected |
|--------|-------------|----------|
| exec_bash 包装 find/grep + workdir 限制 | 复用已验证的 exec_bash 路径，搜索强制限制在 workdir 内。 | ✓ |
| Python pathlib/re 本地执行 | 结构化输出但远程环境不可行。 | |

**User's choice:** exec_bash 包装 + 强制 workdir 内
**Notes:** 远程环境（Bohrium 节点）文件系统只能通过 session.exec_bash() 触达，排除所有本地 Python 方案。workdir 限制与 BashTool 无限制形成差异化。

---

## EditorTool 切换策略

| Option | Description | Selected |
|--------|-------------|----------|
| Phase 9 内完成切换 | 原子化：新 native tools + 移除 EditorTool + 切换显式列举。 | ✓ |
| 留给 Phase 10 | Phase 9 只交付 native tools，EditorTool 并行存在。 | |

**User's choice:** Phase 9 内完成切换
**Notes:** 避免双重文件操作路径共存。_init_builtin_tools 中 EditorTool 和 MonitorJobTool 共用循环需拆分，MonitorJobTool 保留。

---

## Claude's Discretion

- GlobTool/GrepTool 的具体 find/grep 命令参数
- ReadTracker 的具体实现形式
- Read/Write/Edit 的 json_schema 参数细节
- 输出截断阈值
- MonitorJobTool 注册代码组织

## Deferred Ideas

- MonitorJobTool 原生化
- Read tool 非文本文件支持
- Edit tool replace_all 模式
