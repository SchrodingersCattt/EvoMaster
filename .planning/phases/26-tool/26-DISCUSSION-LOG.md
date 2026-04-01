# Phase 26: Tool 内化与遗留工具收归 - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-04-01
**Phase:** 26-tool
**Areas discussed:** MonitorJobTool 收归方式, web_search 统一策略, Helper 内化粒度, EvoToolAdapter 清理边界

---

## MonitorJobTool 收归方式

| Option | Description | Selected |
|--------|-------------|----------|
| 移植为 BuiltinTool 子类 | 将 _tool.py/_constants.py/_lifecycle.py 搬入 matmaster/tools/builtin/monitor_job/，改继承 BuiltinTool ABC，session 依赖通过 self.session 注入 | ✓ |
| 转为 Skill 机制 | 将 MonitorJobTool 包装为 MCP Skill，LLM 通过 use_skill 触发。无需搬运 session 依赖，但调用路径更长 | |
| Protocol 抽象层 | 定义 MonitorJobProtocol，由外部注入实现。保留灵活性但增加复杂度 | |

**User's choice:** 移植为 BuiltinTool 子类
**Notes:** 无额外说明

### Follow-up: Session 适配方式

| Option | Description | Selected |
|--------|-------------|----------|
| 用 self.session 接口 | BuiltinTool 已有 self.session 注入，通过 getattr 取 workspace/credentials/stop_event，类似 bash_tool 双路径模式 | ✓ |
| 参数显式传入 | 将 workspace/credentials/stop_event 作为 tool 参数显式传入，不依赖 session 对象属性 | |

**User's choice:** 用 self.session 接口
**Notes:** 无额外说明

---

## web_search 统一策略

| Option | Description | Selected |
|--------|-------------|----------|
| 直接切换原生版 | exp.py 改用 matmaster 原生 WebSearchTool。page/location 参数不补。名称统一为 web_search | ✓ |
| 切换并补齐参数 | 切换到原生版同时补上 page/location 参数，保持功能完全对等 | |
| 用 Skill 替代 | 删除 builtin web_search，完全通过 MCP Skill (mat_sn_web-search) 提供 | |

**User's choice:** 直接切换原生版
**Notes:** 无额外说明

---

## Helper 内化粒度

| Option | Description | Selected |
|--------|-------------|----------|
| 独立文件复制 | bash_safety.py 复制到 matmaster/tools/builtin/bash_safety.py，editor helper 复制到 editor_helpers.py | |
| 内联到使用处 | bash_safety 逻辑内联到 bash_tool.py，editor 常量/函数内联到 edit_tool.py | ✓ |

**User's choice:** 内联到使用处（最小成本断依赖）
**Notes:** 用户表示解耦后会重新更新 tool，因此暂时不需要在这部分内容上下功夫。采用最小成本方案。

---

## EvoToolAdapter 清理边界

| Option | Description | Selected |
|--------|-------------|----------|
| 完全删除 | 删除 evomaster_tool_adapter.py 文件和 __init__.py 中的导出。exp.py 中 evo adapter 段替换为原生注册 | ✓ |
| 保留空壳 | 清空内容但保留文件，加 deprecation warning | |

**User's choice:** 完全删除
**Notes:** 无额外说明

---

## Claude's Discretion

- MonitorJobTool `_lifecycle.py` 中 isinstance SSHSession 判断的具体替代方式
- 内联 helper 时的代码组织位置

## Deferred Ideas

None
