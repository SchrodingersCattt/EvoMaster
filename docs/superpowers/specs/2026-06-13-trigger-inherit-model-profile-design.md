# Trigger 继承实际模型 Profile 设计

## 背景

当前 `trigger_run` 在调用方未显式传入 `model` 时，会把队列 job 的 `model`
保留为 `None`，Worker 随后通过服务端默认模型链路解析。本设计调整为：
程序化 trigger 默认继承同一 session 最近一次实际运行解析出的 `model_profile`。

同时，服务端默认模型 profile 调整为 `matmaster/DeepSeek-v4-Pro`。

## 目标

- `trigger_run(model=...)` 显式传入模型时，继续使用显式模型，不被继承逻辑覆盖。
- `trigger_run(model=None)` 时，继承该 session 最近一次父级实际 LLM 输出事件中的 `model_profile`。
- 未找到可继承 profile 时，保持现有默认模型链路。
- 默认模型 profile 从当前值迁移为 `matmaster/DeepSeek-v4-Pro`。
- 不在主代码中添加旧 profile alias、兼容兜底或自动迁移逻辑。

## 非目标

- 不实现 BYOK trigger 继承。
- 不根据 trigger prompt 内容自动判断复杂度或切换便宜模型。
- 不添加模型 profile 兼容映射。
- 不增加外部迁移逻辑；如需迁移历史数据，另行用脚本处理。

## 模型选择规则

`trigger_run` 的模型选择优先级为：

1. 调用方显式传入 `model`，直接使用该值。
2. 调用方未传 `model`，从 session 历史里读取最近一次实际运行的 `model_profile`。
3. 未读到可继承 profile，保持 `model=None`，由现有 `AgentRunService` 默认链路解析。

可继承 profile 只接受普通 profile key。若历史事件显示 `model_profile == "byok"`，
或 `model_route` 以 `byok:` 开头，则跳过继承，保持 `None`。

## 数据来源

新增窄查询方法读取 `evo_chat_events` 中最近的父级模型身份：

- `session_id` 匹配当前 session。
- `spawn_id IS NULL`，避免继承子 agent 模型。
- `type IN ('response', 'assistant_state')`。
- 倒序按 `created_at DESC, id DESC` 扫描。
- 从 JSON content 中取 `model_profile`。

优先使用实际 LLM 输出事件，而不是 User/query 的 `requested_model`，因为
`requested_model` 是请求值，不能保证等于最终解析出的 profile。

## 接入点

- `src/dao/chat_events_table.py`
  - 新增 `get_last_resolved_model_profile(session_id) -> str | None`。
- `src/services/events_service.py`
  - 新增同名服务方法，封装 DAO 查询。
- `src/services/stream_service.py`
  - 在 `trigger_run` 中，当显式 `model` 为空时调用服务方法填充 `model_val`。
- `config/config.yaml`
  - 将 `agents.general.llm` 改为 `matmaster/DeepSeek-v4-Pro`。

Worker、`AgentRunService` 和 provider factory 不需要修改。

## 失败语义

- 历史查询失败时，记录 warning 并返回 `None`，不阻断 trigger 入队。
- 继承到的普通 profile 若已不在当前 `llm_config.yaml` 中，不在查询层做兜底；
  后续仍由现有模型装配逻辑 fail-fast。
- BYOK 历史不继承，避免没有凭证 ID 时构造出不可用模型。

## 测试

覆盖以下行为：

- `trigger_run` 未显式传 model 时，继承最近 `response` 或 `assistant_state`
  事件中的 `model_profile`。
- 显式传 model 时，不调用继承查询或不覆盖显式值。
- 最近模型身份为 BYOK 时跳过继承，队列 job 的 `model` 保持 `None`。
- 无可继承历史时，队列 job 的 `model` 保持 `None`。
- 默认模型配置为 `matmaster/DeepSeek-v4-Pro`。
