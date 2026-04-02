# Phase 28: src 反向依赖反转与 Consumer 迁移 - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-04-01
**Phase:** 28-src-consumer
**Areas discussed:** bohrium_setup 解耦模式, evomaster.env.bohrium 收归, chat_history 消息类型过渡, agent_run_bohrium session 迁移

---

## bohrium_setup 解耦模式

### Q1: bohrium_setup.py 对 src.services.agent_run_bohrium 5 个函数的依赖如何处理？

| Option | Description | Selected |
|--------|-------------|----------|
| 回调注入 | BohriumSetupService 改为接受 4 个 callable 参数，由 src/services/agent_run_service.py 在构造时注入实际函数。逻辑不动，只反转依赖方向 | ✓ |
| 逻辑搬入 matmaster | agent_run_bohrium.py 中的 4 个函数逻辑直接搬入 matmaster/integration/bohrium_setup.py，src 侧改为调用 matmaster | |
| Protocol 抽象 | 定义 BohriumSetupBackend Protocol（在 matmaster/types/），声明 4 个方法签名。src 侧提供实现，注入时符合 Protocol | |

**User's choice:** 回调注入
**Notes:** 无

### Q2: BohriumSetupResult 类型如何处理？

| Option | Description | Selected |
|--------|-------------|----------|
| 复制到 matmaster | BohriumSetupResult 是简单 NamedTuple，直接在 matmaster 侧定义副本 | ✓ |
| 改为 Any | run_setup 返回值标注改为 Any，避免引入类型依赖 | |
| 移入 matmaster 并反向 | 定义放 matmaster/types/，src 侧从 matmaster 导入 | |

**User's choice:** 复制到 matmaster
**Notes:** 无

---

## evomaster.env.bohrium 收归

### Q1: evomaster.env.bohrium 的 3 个函数是否纳入 Phase 28？

| Option | Description | Selected |
|--------|-------------|----------|
| 纳入 Phase 28 | 和 bohrium_setup 解耦同主题，一并处理避免多一个 phase | ✓ |
| 留给后续 phase | 这 3 个是 matmaster → evomaster 方向而非 → src，不属于 INVR 范围 | |

**User's choice:** 纳入 Phase 28
**Notes:** 无

### Q2: evomaster.env.bohrium 中的 3 个函数怎么处理？

| Option | Description | Selected |
|--------|-------------|----------|
| 搬入 matmaster | 直接复制到 matmaster 侧，都是纯函数无外部依赖 | ✓ |
| 配置注入替代 | 不搬函数，让 path_adaptor/job_service 通过构造参数接受 callable | |

**User's choice:** 搬入 matmaster
**Notes:** 无

### Q3: BOHRIUM_OPENAPI_HOST 常量在 matmaster 侧怎么提供？

| Option | Description | Selected |
|--------|-------------|----------|
| 环境变量 + 默认值 | matmaster 侧直接读 BOHRIUM_BASE_URL 环境变量，默认值 'https://open.bohrium.com' | ✓ |
| config.yaml 注入 | 放入 matmaster_config/config.yaml 的 bohrium 段 | |
| 复制 URL_PART 逻辑 | 将 URL_PART 环境感知逻辑也复制到 matmaster | |

**User's choice:** 环境变量 + 默认值
**Notes:** 无

---

## chat_history 消息类型过渡

### Q1: chat_history.py 的 evomaster 消息类型依赖如何处理？

| Option | Description | Selected |
|--------|-------------|----------|
| 全量切换到 matmaster 类型 | events_to_dialog_messages 内部的 AssistantMessage/ToolCall/ToolMessage/UserMessage 全部换成 matmaster 版本 | ✓ |
| 只迁移入口 import | 保持逻辑不动，只把 import 源从 evomaster 改为 matmaster（前提：类型兼容） | |
| 双路径共存 | 维持现状，events_to_dialog_messages 继续用 evomaster 类型 | |

**User's choice:** 全量切换到 matmaster 类型
**Notes:** 需确认 matmaster 消息类型与 evomaster 类型在字段和方法上的兼容性

---

## agent_run_bohrium session 迁移

### Q1: agent_run_bohrium.py 中的 evomaster SSHSession 依赖怎么处理？

| Option | Description | Selected |
|--------|-------------|----------|
| 切换到 matmaster SSHSession | import 改为 matmaster.sessions.ssh，isinstance 检查和构造都用 matmaster 版本 | ✓ |
| duck-typing 替代 | 不导入具体类，用 hasattr 检查能力 | |
| matmaster Session Protocol | 用 Protocol 做类型标注，构造时仍 import 具体类 | |

**User's choice:** 切换到 matmaster SSHSession
**Notes:** 无

### Q2: agent_run_bohrium.py 的 playground workspace_resolver 依赖是否一并处理？

| Option | Description | Selected |
|--------|-------------|----------|
| 留给后续 phase | Phase 28 范围是 INVR + CONS，playground 依赖不在此范围 | ✓ |
| 顺便一并处理 | 既然已在改这个文件，顺便迁移 workspace_resolver | |

**User's choice:** 留给后续 phase
**Notes:** 无

---

## Claude's Discretion

- 搬入 bohrium 函数在 matmaster 内的具体模块位置
- matmaster 消息类型与 evomaster 类型的具体字段映射
- BohriumSetupResult 副本的具体字段

## Deferred Ideas

- agent_run_bohrium.py 对 playground.mat_master.core.workspace_resolver 的依赖
- matmaster 内其他 evomaster 残留（bash_tool LocalSession、monitor_job/_llm.py）
