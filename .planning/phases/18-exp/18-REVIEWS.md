---
phase: 18
reviewers: [codex-gpt54]
reviewed_at: 2026-03-29T00:00:00Z
plans_reviewed: [18-01-PLAN.md, 18-02-PLAN.md]
note: Phase 18 已完成实现，此为事后审查
---

# Cross-AI Plan Review -- Phase 18

## Codex Review (GPT-5.4)

**总体判断**

两份计划整体质量较高，拆分方式也基本正确：`Plan 01` 先完成 `Exp` 生命周期主链路与同步桥接层，`Plan 02` 再把 `SubAgent spawn` 接到完整异步链上，依赖关系清晰，没有明显的顺序错误。它们确实覆盖了 `EXPL-01` 到 `EXPL-04` 的核心目标。真正的风险不在主思路，而在几个容易被低估的边界：cleanup 的类型契约、build 失败时的 cleanup 保证、同步桥接层的回归测试，以及 `spawn_fn` 改成异步后类型与测试是否同步收口。

### Plan 01: Exp Core 4 Methods Async + AgentRuntime Type Update + Service/DevShell Bridge

**Summary**

这份计划很扎实，基本抓住了 Phase 18 的主干。特别是把 `run()` 的 `try/finally` 提前到 `build_runtime()` 之前，这不是小修饰，而是这次迁移里最关键的正确性点之一，能覆盖部分构建成功但尚未返回 runtime 的清理场景。范围控制也比较好，没有顺手把 service 和 devshell 大改成纯异步接口，避免了 scope creep。

**Strengths**

- 迁移模式与前面 Phase 12 到 17 一致，认知成本低，回归面也更可控。
- 明确覆盖了真正的同步调用入口：`agent_run_service`、`devshell.runner`、`devshell.repl`，不是只改 `Exp` 自身。
- `run()` 的 `finally` 提前到 `build_runtime()` 前面，能处理部分初始化后失败的 cleanup，这个设计非常对。
- `_run_cleanup_callbacks()` 同时支持 sync callback 和 async callback，方向正确，也符合未来 MCP/connector 资源释放的需要。
- 测试迁移范围看起来足够大，`test_exp.py` 加多份 integration regression，这比只改单测稳很多。

**Concerns**

- [MEDIUM] `cleanup` 类型如果只从 `Callable[[], None]` 放宽到 `Callable[[], Any]`，类型信息会退化得比较厉害。它能工作，但对 `mypy` 和后续维护不够友好，容易把真正的错误也吞成 `Any`。
- [MEDIUM] 计划里没有明确要求补 async cleanup 的定向测试。`EXPL-03` 已经把 async cleanup 作为显式目标，如果没有测试 `async def cleanup()` 和 返回 awaitable 的 sync callback，这类 bug 很容易变成 coroutine never awaited。
- [MEDIUM] 计划虽然强调了 build 失败时也要 cleanup，但没有把这个场景写成明确测试项。恰恰这个场景最值得钉死，否则 `try/finally` 提前的价值没有被验证。
- [LOW] `AgentRuntime.cleanup` 更新了类型，但桥接层仍然直接调用 `exp._run_cleanup_callbacks()`。这不是错，但说明 cleanup 的公开契约和真实使用方式还有一点割裂。
- [LOW] `agent_run_service` 的临时桥接扩展是合理的，但如果不在计划里写明为什么不能直接改成调用 `Exp.run()`，后续审查者可能会误以为这里还有重复代码没收敛。

**Suggestions**

- 增加一个更精确的类型别名，例如 `CleanupCallback = Callable[[], None | Awaitable[None]]`，然后在 `Exp` 和 `AgentRuntime` 里统一使用，避免 `Any` 过宽。
- 补 4 个定向测试：
  1. `async def cleanup()` 会被正确 `await`
  2. sync callback 返回 awaitable 时也会被 `await`
  3. `build_runtime()` 中途失败但已注册 cleanup 时，cleanup 仍会执行
  4. `agent_run_service` 与 `devshell` 桥接路径在 build 失败时也会执行 cleanup 并关闭 loop
- 在计划说明里明确：service/devshell 暂不复用 `Exp.run()`，是因为它们还需要拿到 `KernelRunResult`、注入额外 hooks、维护 history，这属于刻意保留的桥接，不是遗漏。
- 顺手把 `tests/matmaster/types/test_runtime.py` 纳入变更清单，否则 `runtime.cleanup` 的测试语义会落后于实现。

**Risk Assessment**: MEDIUM

---

### Plan 02: SubAgent Spawn Async Chain + SpawnTool Execute Override

**Summary**

这份计划在架构上是对的，而且比手工拼 `build_runtime()` + `kernel.run()` + cleanup 更干净。让 `spawn_fn` 直接 `await child_exp.run(...)`，等于把子 agent 生命周期重新收拢到 `Exp` 的单一入口里，减少重复和分叉逻辑。`SpawnTool.execute()` 单独改成原生异步也很合理，因为它确实是 builtin tool 里那个真正需要逃离 `to_thread(_execute)` 模式的特例。

**Strengths**

- 复用 `Exp.run()` 的完整生命周期，避免子 agent 路径和主路径各维护一套 cleanup/bridge 逻辑。
- 去掉 child bridge loop 后，spawn 链路更简单，也减少了 event loop 管理负担。
- `Plan 02` 依赖 `Plan 01` 的顺序完全正确，先把 `Exp.run()` 变成稳定 async API，再让 spawn 复用它。
- `SpawnTool.execute()` 直接异步执行，比在 `_execute()` 里硬塞异步逻辑更干净，也更符合当前 `BuiltinTool` 基类的分层。
- 全量回归跑完整套测试是必要的，因为 spawn 涉及 tool 调用、event bus、stop_event 传播和 child exp config，多处联动。

**Concerns**

- [MEDIUM] 计划里提到了 `_make_spawn_fn` 返回类型是个坑，但没有把它写进明确修改项。`SpawnTool.__init__` 的 `spawn_fn` 类型也需要一起改成 `Callable[..., Awaitable[str]] | None`，否则类型系统和实现会脱节。
- [MEDIUM] 给 `Exp.run()` 增加 `source_override` 和 `spawn_id` 是可行的，但这会把 spawn/观测性语义带进通用生命周期 API。必须保持 keyword-only，并在文档里说明这是内部参数，不然接口会逐渐变脏。
- [MEDIUM] 计划没有明确覆盖 child 非成功结束时的返回文本分支，也就是 `SubAgent finished with status=..., reason=...`。这个分支很容易被忽略，但它直接影响父 agent 看到的观测结果。
- [LOW] 没有明确要求测试 child 不应继承 parent `history`。这其实是 `SpawnTool` 语义的一部分，不只是实现细节。
- [LOW] `_execute()` 改成 `NotImplementedError` stub 虽然能满足 ABC，但如果还有测试或外部代码私下直接调用 `_execute()`，会出现兼容性波动。

**Suggestions**

- 把类型修改写进任务正文，而不是只留在 pitfall 里：
  `SpawnTool.__init__(..., spawn_fn: Callable[..., Awaitable[str]] | None = None)`
  与 `_make_spawn_fn(...) -> Callable[..., Awaitable[str]]`
- 增加 3 个定向测试：
  1. child 返回 `cancelled` 或 `failed` 时，`spawn_fn` 文本格式符合预期
  2. child `Exp.run()` 调用中 `history` 没有被传入 parent history
  3. `source_override` 与 `spawn_id` 只通过 keyword 传递，避免位置参数误用
- 明确说明 `Exp.run()` 新增参数只服务于 event source 和 spawn tracing，不是给普通调用方自由使用的业务参数。
- 不建议在这份计划里继续推进 service/devshell 复用 `Exp.run()`。那会把一个边界清晰的异步迁移，扩成一次更大的生命周期接口重构。

**Risk Assessment**: MEDIUM-LOW

---

## Consensus Summary

> Single reviewer (Codex/GPT-5.4). Phase 18 implementation already completed.

### Agreed Strengths
- 迁移模式与 Phase 12-17 一致，认知成本低
- `run()` try/finally 从 build_runtime 开始覆盖，正确性设计到位
- spawn_fn 复用 Exp.run() 避免逻辑重复，架构改进明显
- 范围控制得当，未引入 scope creep

### Key Concerns (Priority Order)

1. **Cleanup 类型契约** [MEDIUM] -- `Callable[[], Any]` 过宽，建议引入精确类型别名 `CleanupCallback`
2. **Cleanup 测试覆盖不足** [MEDIUM] -- async cleanup dispatch、build 失败时的 cleanup 保证缺少定向测试
3. **spawn_fn 类型注解未同步收口** [MEDIUM] -- `_make_spawn_fn` 返回类型和 `SpawnTool.__init__` 参数类型未在任务正文中明确要求更新
4. **child 非成功结束路径未测试** [MEDIUM] -- `SubAgent finished with status=...` 分支缺少覆盖

### Divergent Views
N/A (single reviewer)

### Action Items for Future Phases
- 考虑在 Phase 19 或后续引入 `CleanupCallback` 类型别名替代 `Callable[[], Any]`
- 补充 async cleanup 定向测试（如尚未覆盖）
- 补充 spawn child 非成功路径的测试覆盖
