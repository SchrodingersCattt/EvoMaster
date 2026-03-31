# Phase 20: Confirmation Flow Recovery - Context

**Gathered:** 2026-03-30
**Status:** Ready for planning
**Source:** Gap-closure synthesis from milestone audit + Phase 15 verification artifacts

<domain>
## Phase Boundary

本 phase 只处理 confirmation flow 的回归修复，不重做新的确认产品形态，也不顺带扩展其他 hook 功能。

目标是把已在 Phase 15 设计并验证过的 async confirmation 模型恢复到当前代码线上：

- `ConfirmationHook` 恢复为 asyncio 兼容等待模型
- `stream_service` 与 `agent_run_service` 的 confirmation 桥接重新一致
- `POST /confirmation_reply` 到 tool approval/skip 的链路重新可用
- 回归测试覆盖当前 regression 与 re-enable 路径

明确不在本 phase 处理：

- `TOOL-02` / `OpenAIProvider.chat_with_retry` / 其他 audit tech debt
- 多 agent 编排
- 前端 UI 形态修改
- 与 confirmation 无关的 bus/router 设计清理

</domain>

<decisions>
## Implementation Decisions

### Locked Decisions

- 这是 regression recovery，不是重新设计。实现目标以 `.planning/phases/15-hook/15-VERIFICATION.md` 和 `.planning/phases/15-hook/15-02-SUMMARY.md` 中已经通过验证的模型为基线。
- `HOOK-02` 当前必须保持 `Pending`，直到本 phase 修复完成并重新验证；计划必须显式关闭该 requirement gap。
- `ConfirmationHook` 应恢复到 asyncio 方案，而不是继续沿用 `queue.Queue.get()` 阻塞模型。
- `ConfirmationHook` 对外必须重新提供 `resolve()` / `cancel()`，因为 `src/services/stream_service.py` 的 `ConfirmationHookAdapter` 和 `/confirmation_reply` 写入路径都依赖这两个接口。
- 修复必须覆盖代码恢复与运行时接线两层：仅恢复 hook 文件本身不够，还要处理 `src/services/agent_run_service.py` 中当前被注释掉的启用路径。
- 重新启用 confirmation 时要保持受控范围，优先沿用 roadmap 已写明的 `confirm_tools` gating 思路，避免把所有 tool 默认变成必须确认。
- 计划必须包含测试回归任务，至少覆盖：
  - hook 级 async 等待与 timeout/cancel 行为
  - adapter 到 hook 的接口契约
  - service 层启用后不会再次因为接口错配触发 `AttributeError`

### the agent's Discretion

- `confirm_tools` 的默认来源和装配细节可在执行时决定，但计划里必须给出具体落点文件与可验证条件。
- 是否保留 `ReplyQueueLike` 作为过渡接口可以自行决定，但不能破坏当前 `/confirmation_reply` 写入路径。
- 可以参考 `70521e2` / `e74addb` 中的实现细节，但若与当前 async kernel / service 现状冲突，应以当前主干兼容性为准做最小偏差。

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Audit and Phase History

- `.planning/v2.0-MILESTONE-AUDIT.md` — 当前 gap 的直接来源，包含 regression 根因、受影响 flow、mitigation、以及 requirement/integration gap 描述
- `.planning/ROADMAP.md` — Phase 20 的目标、依赖、success criteria
- `.planning/REQUIREMENTS.md` — `HOOK-02` 当前状态与 traceability
- `.planning/phases/15-hook/15-VERIFICATION.md` — 已验证通过的目标状态，明确了 `async def pre_tool_call`、`resolve/cancel`、`ConfirmationHookAdapter` 的期望接口
- `.planning/phases/15-hook/15-02-SUMMARY.md` — 原实现意图、关键文件、commit hash、设计决策

### Current Runtime Files

- `matmaster/hooks/confirmation.py` — 当前已回退到 `queue.Queue.get()` 的实现，phase 直接修复对象
- `src/services/stream_service.py` — `ConfirmationHookAdapter` 当前仍调用 `hook.resolve()` / `hook.cancel()`
- `src/services/agent_run_service.py` — confirmation hook 当前被注释禁用，phase 需要决定如何安全重新接线
- `matmaster/core/agent.py` — Phase 15 里 loop 注入与 hook 调用路径的既有机制，需要确认仍可复用

### Tests

- `tests/matmaster/hooks/test_confirmation.py` — confirmation 相关回归测试主入口
- `tests/matmaster/integration/test_upstream_scenarios.py` — Phase 15 曾改过的上游/跨层测试，可能需要一起检查
- `tests/matmaster/core/test_agent.py` — 若 hook 调用契约变化，需要核对 kernel 对 hook 的 async 调用方式

### Historical Reference Implementation

- `git show 70521e2:matmaster/hooks/confirmation.py` — 回退前的 asyncio.Future 版 `ConfirmationHook`
- `git show e74addb:src/services/stream_service.py` — 与 Future 版 hook 对齐的 adapter 参考

</canonical_refs>

<specifics>
## Specific Ideas

- audit 明确指出当前 broken flow 为：
  `POST /confirmation_reply` -> `stream_service` -> `ConfirmationHookAdapter.put_content()` -> `hook.resolve()` -> `AttributeError`
- audit 明确指出当前 mitigation 是：
  `src/services/agent_run_service.py` 暂时注释掉 confirmation hook，生产流量未命中该路径
- Phase 15 verification 里已经给出通过态特征：
  - `confirmation.py` 不应再有 `import queue`
  - `pre_tool_call` 应为 async，并通过 `await asyncio.wait_for(...)` 等待
  - `resolve()` / `cancel()` 采用 atomic swap + `loop.call_soon_threadsafe(...)`
  - `stream_service.py` 的 adapter 应桥接 `put_content -> resolve`, `put_cancel -> cancel`

</specifics>

<deferred>
## Deferred Ideas

- confirmation 交互体验优化
- 更完整的 MCP/tool registration 驱动 `confirm_tools` 配置体系
- 去掉过渡层 `ReplyQueueLike` 的彻底清理

</deferred>

---

*Phase: 20-confirmation-flow-recovery*
*Context gathered: 2026-03-30 via milestone audit synthesis*
