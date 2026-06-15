# Trigger 继承实际模型 Profile 设计

## 背景

当前 `trigger_run` 在调用方未显式传入 `model` 时，会把队列 job 的 `model`
保留为 `None`。设计意图是 Worker 随后走服务端默认模型链路解析，但当前
`config/config.yaml` 的 `agents.general.llm` 值为 `qwen_3_7_max`，它并不是
`llm_config.yaml` 里任何一个 profile key（现有 key 形如 `matmaster/qwen3.7-max`）。
`LLMConfig.resolve` 不做别名归一、`build_provider_bundle` 也不捕获 miss，因此
`model=None` 真正走到默认链路时会直接 `KeyError`。这条路径目前没暴露，仅因为正常
API 请求总会带显式 `requested_model`，从未真正落到 `default_key` 兜底。

本设计调整两件事：

- 程序化 trigger 默认继承同一 session 最近一次实际运行解析出的 `model_profile`。
- 把服务端默认 profile 从无效的 `qwen_3_7_max` 改为真实存在的
  `matmaster/DeepSeek-v4-Pro`，一并修复上述默认链路 `KeyError` 潜伏 bug。

config 改动是「无可继承 profile 时落回默认链路」（见模型选择规则第 3 条）能成立的
前置条件，而非可选迁移。

## 目标

- `trigger_run` 显式传入非空 `model` 时，继续使用显式模型，不被继承逻辑覆盖。
- `trigger_run` 未传 `model`（含传入 `None` 或空串）时，继承该 session 最近一次
  父级实际 LLM 输出事件中的 `model_profile`。
- 未找到可继承 profile 时，保持现有默认模型链路。
- 默认 profile 从无效的 `qwen_3_7_max` 改为真实存在的 `matmaster/DeepSeek-v4-Pro`，
  一并修复默认链路 `KeyError`。
- 不在主代码中添加旧 profile alias、兼容兜底或自动迁移逻辑。

## 非目标

- 不实现 BYOK trigger 继承。
- 不根据 trigger prompt 内容自动判断复杂度或切换便宜模型。
- 不添加模型 profile 兼容映射。
- 不增加外部迁移逻辑；如需迁移历史数据，另行用脚本处理。

## 模型选择规则

`trigger_run` 的模型选择优先级为：

1. 调用方显式传入非空 `model`，直接使用该值。
2. 调用方未传 `model`（或传入 `None`/空串），从 session 历史里读取最近一条父级
   实际运行的 `model_profile`。
3. 未读到可继承 profile，保持 `model=None`，由现有 `AgentRunService` 默认链路解析。

可继承 profile 只接受普通 profile key。判别只看最近一条 qualifying 父级事件：
若它的 `model_profile == "byok"` 或 `model_route` 以 `byok:` 开头，直接返回 `None`，
不再往更早的历史事件回溯。

继承意味着上一轮用户手动选定的模型会被后续程序化 trigger 沿用，包括较贵的模型
（如 `global.anthropic.claude-opus-4-6-v1`）。这是刻意行为，不在此自动降级。

## 数据来源

新增窄查询方法读取 `evo_chat_events` 中最近一条父级模型身份：

- `session_id` 匹配当前 session。
- `spawn_id IS NULL`，避免继承子 agent 模型。
- `type IN ('response', 'assistant_state')`。
- 按 `created_at DESC, id DESC` 取最近一条。
- 从该行 JSON content 中取 `model_profile`；若字段缺失或为空，返回 `None`
  （落回默认链路），不做进一步回溯。

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
  - 将 `agents.general.llm` 由 `qwen_3_7_max` 改为 `matmaster/DeepSeek-v4-Pro`。

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
- 显式传入空串 `model=""` 时，按未传处理，走继承链路。
- 最近模型身份为 BYOK 时跳过继承，队列 job 的 `model` 保持 `None`。
- 最近 qualifying 事件 `model_profile` 缺失或为空时，队列 job 的 `model` 保持 `None`。
- 无可继承历史时，队列 job 的 `model` 保持 `None`。
- 默认模型配置为 `matmaster/DeepSeek-v4-Pro`。
