# Phase 10: Tool Description 与 System Prompt 设计 - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-03-25
**Phase:** 10-tool-description-system-prompt
**Areas discussed:** Description 内容策略, System Prompt 行为规范, 工具路由指导位置, Schema 精细化深度

---

## Description 内容策略

| Option | Description | Selected |
|--------|-------------|----------|
| 功能 + when-to-use + gotcha | 3-5 句，涵盖功能描述 + 使用场景 + 关键注意事项，消除 bash 替代专用工具的歧义 | |
| 纯功能描述（现状） | 1-2 句纯功能说明，token 开销最低，依赖 system prompt 解决工具选择问题 | |
| 严格参考 Claude Code | 功能描述 + Usage 段落 + 多条 bullet point，对标 Claude Code 的实际 tool description 模式 | ✓ |

**User's choice:** Other — 严格参考 Claude Code 的实现
**Notes:** 用户提供了两个 Claude Code 参考资源作为 canonical reference：
- https://gist.github.com/wong2/e0f34aac66caf890a332f7b6f9e2ba8f
- https://github.com/Piebald-AI/claude-code-system-prompts

Advisor research 建议的「功能 + when-to-use + gotcha（3-5 句）」被更明确的「对标 Claude Code」方向取代。

---

## System Prompt 行为规范

| Option | Description | Selected |
|--------|-------------|----------|
| 全面对标 Claude Code | 身份定义 + 工具使用规范 + 行为约束 + 输出风格 + 科研场景规则，对标 Claude Code 的模块化片段设计 | ✓ |
| 最小可用集 | 只写身份 + 工具路由规则 + 科研场景核心规范，其他维度后续迭代补充 | |

**User's choice:** 全面对标 Claude Code
**Notes:** Advisor research 建议 developer_instructions 扩展为完整行为规范文档（非拆分到 mode_contract），涵盖 HPC 节点操作原则、工具使用偏好、科研输出规范、错误处理策略。用户选择全面方案。

---

## 工具路由指导位置

| Option | Description | Selected |
|--------|-------------|----------|
| 三层写法 | Bash description + system prompt tool-usage 段 + 各专用工具 description，对标 Claude Code | ✓ |
| 双层写法 | 只在 Bash description 和各专用工具 description 里写，不在 system prompt 里重复 | |

**User's choice:** 三层写法（推荐）
**Notes:** Claude Code 实际实现验证了三层冗余的有效性。确保 LLM 在任何注意力分布下都能收到路由信号。

---

## Schema 精细化深度

| Option | Description | Selected |
|--------|-------------|----------|
| 按需约束 + description 精化 | 沿用 type/description/enum/minItems 按需添加，重点精化参数 description 文本，对标 Claude Code | ✓ |
| 扩展约束 | 额外加 examples、pattern 等字段，尽管 OpenAI API 可能不处理这些字段 | |

**User's choice:** 按需约束 + description 精化
**Notes:** Advisor research 确认 OpenAI function calling 不支持 examples 字段（会被静默忽略），pattern 对 LLM 生成无执行强制力。当前 schema 已有按需精细化的正确模式（bash_tool enum、read_tool minItems/maxItems）。

---

## Claude's Discretion

- 各 tool description 的具体措辞和 Usage bullet 内容
- developer_instructions 各维度的具体文本
- mode_contract 是否需要扩展
- ContextBuilder._build_tools() 是否需要增强展示格式

## Deferred Ideas

- Prompt 模板加载器基础设施（INFR-D01）
- MonitorJobTool description 精细化
- ContextBuilder tools section 增强格式
