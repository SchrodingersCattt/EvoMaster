# Per-subagent LLM profile 设计

## 目标

为每个 subagent（即 `matmaster/exps/*.toml` 定义的 exp）独立配置其使用的 LLM
profile，而非现状下所有 subagent 一律继承父 agent 的同一个 profile。

## 现状

- subagent = `matmaster/exps/*.toml` 定义的 exp（direct / explore / planner /
  verification）。父 agent 通过内置 `Agent` 工具 spawn 它们。
- LLM profile 在 `config/llm_config.yaml` 按 `profile_key` 定义，结构为
  `matmaster/config/llm.py::LLMProfileConfig`。
- `matmaster/core/exp.py::_make_child_run_factory()` 内的 `child_run_factory`
  **直接复用父 `AgentRunContext`**，child Exp 从 `ctx.request.llm_provider`
  （已被 `BillingLLMProvider` 包裹）取模型 → 所有 subagent 共用父 profile。
- 真正构造 provider 的工厂是
  `matmaster/providers/llm_factory.py::build_provider_bundle()`，能从任意
  profile_key 造出独立的 `LLMProviderBundle`。
- 计费包裹 `BillingLLMProvider` 仅在 service 层
  (`src/services/agent_run_service.py` Stage 4) 装配，所需上下文
  （`BillingRunContext` / `billing_service` / `billing_mode` / `budget_micro` /
  `cancel_controller`）只在该层齐备；核心 `Exp` 层无法获取。

## 设计决策

1. **绑定粒度**：按 exp 类型静态配置。每个 exp 在自己的 `.toml` 里声明用哪个
   profile，与具体调用无关。LLM 不参与模型选择。
2. **优先级与回退**：exp 配置居中。
   - subagent：exp.toml 写了 `llm` 就用它（固定，不受用户顶层模型选择影响）；
     没写则回退到继承父 agent 的 profile（保持现状）。
   - 根 agent：仍由用户顶层模型选择（`model_override`）决定，不受本特性影响。
3. **BYOK 处理**：BYOK 模式下忽略 exp 配置，所有 subagent 继承父 agent 的 BYOK
   provider。语义：BYOK = 一个凭证一个模型，不跳到没有凭证的平台 profile。
4. **非法 profile_key**：不做启动期 fail-fast，也不抛错给模型。exp.toml 写了
   无法解析的 profile_key 时，child 记一条 warning 并**回退继承父 agent 的
   profile**（与"未配置"同一回退路径）。typo 不中断 run，运营降级而非硬失败。
5. **devshell 一致性**：devshell（`DevRunner`）也接入该能力，挂一个不含计费的
   subagent provider factory，使本地能复现与线上一致的 per-subagent profile 行为。

## 架构：provider 工厂端口（方案 A）

核心约束是计费上下文只在 service 层齐备。因此把"解析 profile + 包计费"的能力
闭包进 service 层，经 `AgentRunPorts` 端口暴露给核心层调用——与现有
`checkpoint_sink_factory`、`interaction_bridge`、`interrupt_checker` 等端口同构。
核心 `Exp` 层不持有任何计费概念，只调用端口"给我一个 profile X 的（已计费）
provider"。

被否决的替代方案：
- 方案 B（Exp 自己 build + 包计费）：迫使 Exp 持有 `billing_service` /
  `billing_mode` / `budget_micro` / `cancel_controller`，污染核心层边界。
- 方案 C（service 预建所有 profile 的 provider 塞进 dict）：需预知哪些 exp 会被
  spawn，提前建一堆可能用不上的网络客户端，新增 exp 还要改装配。

## 实现单元

### 1. 配置字段 — `matmaster/config/exp.py`

`ExpConfig` 新增可选字段：

```python
class ExpConfig(BaseModel):
    ...
    llm: str | None = None   # profile_key，None = 继承父 agent
```

- **只**加在 `ExpConfig`，不加到 `ExpSubagentMeta`——profile 是运行时装配信息，
  不是 model-visible 元数据。
- 不在配置加载期校验：`load_exp_config()` 仅 `tomllib.load` + `ExpConfig`
  校验，拿不到 `llm_config`。非法 profile_key 在 spawn 时由 factory 调
  `build_provider_bundle()` → `LLMConfig.resolve()` 抛 `KeyError`，由
  `child_run_factory` 捕获并回退继承父 profile（见单元 4）。

### 2. provider 工厂端口 — `matmaster/types/runtime_ports.py`

```python
@runtime_checkable
class SubagentProviderFactory(Protocol):
    # 返回的 bundle.provider 已被 BillingLLMProvider 包裹
    def __call__(self, *, profile_key: str) -> Any: ...   # LLMProviderBundle

@dataclass(frozen=True)
class AgentRunPorts:
    ...
    subagent_provider_factory: SubagentProviderFactory | None = None
```

返回类型用 `Any`（或 `TYPE_CHECKING` 引用），避免 `types` 层反向依赖
`providers` 层——与 `AgentRunRequest.llm_provider: Any` 的处理一致。

端口契约（必须在实现中遵守）：

- **消费者**：仅 `Exp._make_child_run_factory()` 内的 `child_run_factory`。
- **调用时机**：child config 解析后、child `run_stream()` 之前。
- **返回值**：每次调用返回**全新**的 `LLMProviderBundle`，其 `.provider` 已被
  `BillingLLMProvider` 包裹。**严禁按 profile 缓存复用同一个 bundle**——并发
  spawn 两个同 profile 的 subagent 时，缓存会让它们共用一个 async context
  manager（`__aenter__/__aexit__`）与一份 `_http_session`，先退出者会关掉另一个
  仍在用的 session（`BillingLLMProvider` 不对 `_http_session` 做引用计数）。
- **生命周期**：provider 由 child kernel 的 `async with kernel_resources.llm_provider`
  （`matmaster/core/agent.py:138`）托管，进入一次、退出一次。
- **缺失语义**：端口为 `None`（BYOK / devshell 未装）表示继承父 provider。
- **异常语义**：未知 profile_key 时 `build_provider_bundle()` 抛 `KeyError`，
  由消费者捕获并回退继承父 profile（不向上冒泡中断 run，见单元 4）。

### 3. 共享计费 run 状态 — `src/services/billing_llm_provider.py`

**问题**：现状只有一个 `BillingLLMProvider` 实例，run 级计费状态
（`_call_index`、`_spent_micro`、`_guard_tripped`）是实例级的，root / subagent /
compaction 的花费都累加进同一个实例，预算熔断按全 run 触发（billing_llm_provider.py
:59,67,155）。若按"每个 child 一个 wrapper"裸实现，这套 run 级状态会被拆散：
`budget_micro=1000` 时 root 花 800、explore 花 800、verification 花 800，每个 wrapper
各自 `_spent_micro` 都没超 1000，但整 run 已花 2400 —— in-run 熔断被绕松。

**修法**：抽出共享的 `BillingRunState`，承载 run 级计数与熔断：

```python
class BillingRunState:
    def __init__(self, *, session_id, budget_micro=None, cancel_controller=None):
        self._call_index = 0
        self._spent_micro = 0
        self._guard_tripped = False
        self._budget_micro = budget_micro
        self._cancel_controller = cancel_controller
        self._session_id = session_id

    def next_call_index(self) -> int: ...        # 全 run 单调
    def accumulate(self, cost_micro: int) -> None: ...   # 累加 + 超预算触发熔断
```

`BillingLLMProvider` 改为持有 `run_state: BillingRunState`，把 `_next_call_index`
委派给 `run_state.next_call_index()`，`_accumulate_cost` / `_maybe_trip_guard`
委派给 `run_state.accumulate()`。每个 wrapper 仍各自持有 `_inner` / `_model` /
`_pending` / `_http_session`（连接池与 inner 是 per-profile 的，必须独立）。
asyncio 单线程下计数器 `+= 1` 与累加都是同步原子操作，无需锁。

root wrapper 与 subagent factory 造的所有 child wrapper **共享同一个
`BillingRunState` 实例** → call_index 全 run 单调、预算按全 run 累加熔断，行为与
现状一致。顺带说明：call_index 即便分裂也不会在 billing 后端碰撞（`price_llm_usage`
的 key 含 `spawn_id`），但共享 state 仍是更稳的语义。

### 4. service 层装配 — `src/services/agent_run_service.py`

Stage 4 现有 billing 装配处：先建一个 `BillingRunState`（由 platform 分支的
`budget_micro` / `cancel_controller` 构造），root wrapper 用它；factory 闭包也闭进
同一个 state。复用顶层 agent 已备好的 `llm_config` 与计费上下文，零新依赖：

```python
billing_state = BillingRunState(
    session_id=session_id, budget_micro=budget_micro,
    cancel_controller=cancel_controller,
)
# root wrapper 改为接收 billing_state（替代原 budget_micro/cancel_controller 直传）

def _make_subagent_provider_factory(profile_key: str) -> LLMProviderBundle:
    bundle = build_provider_bundle(llm_config, model_override=profile_key)
    wrapped = BillingLLMProvider(
        bundle.provider,
        run_context=BillingRunContext(session_id, task_id, invocation_id),
        model=bundle.model,
        billing_service=get_billing_service(),
        billing_mode="platform",
        run_state=billing_state,          # ← 与 root 共享
    )
    return replace(bundle, provider=wrapped)
```

- platform 分支挂 `subagent_provider_factory=_make_subagent_provider_factory`。
- **BYOK 分支不挂**（保持 `None`），落实 BYOK 忽略 exp 配置。

### 5. devshell 装配 — `matmaster/devshell/runner.py`

`DevRunner` 现在构造 `AgentRunRequest` 时不传 `ports`（runner.py:92），故默认
`subagent_provider_factory is None`，会静默忽略 exp.toml 的 `llm`。给它挂一个
**不含计费**的 factory，使本地能复现线上行为：

```python
def _dev_subagent_provider_factory(profile_key: str) -> LLMProviderBundle:
    return build_provider_bundle(llm_config, model_override=profile_key)
```

devshell 不走 `BillingLLMProvider`（无计费/熔断），factory 直接返回裸 bundle，
经 `AgentRunPorts(subagent_provider_factory=...)` 注入 `_request.ports`。

### 6. child 解析与回退 — `matmaster/core/exp.py::child_run_factory`

```python
def child_run_factory(exp_name, task, *, cancel_token=None, spawn_id=None):
    child_cfg = load_exp_config(exp_name)
    child_exp = Exp(child_cfg, allow_spawn=False, inherited_skill_cache=skill_cache)

    child_ctx = ctx
    factory = ctx.request.ports.subagent_provider_factory
    if child_cfg.llm and factory is not None:        # 有配置 且 非 BYOK
        try:
            bundle = factory(profile_key=child_cfg.llm)
        except KeyError:                             # 非法 profile_key
            logger.warning(
                "subagent %s: llm profile %r unresolvable, inheriting parent",
                exp_name, child_cfg.llm,
            )
            bundle = None
        if bundle is not None:
            child_ctx = ctx.model_copy(update={"request": ctx.request.model_copy(update={
                "llm_provider": bundle.provider,
                "llm_model": bundle.model,
                "llm_model_profile": bundle.model_profile,
                "llm_model_route": bundle.model_route,
                "context_limit": bundle.context_limit,
                "supports_vision": bundle.supports_vision,
                "vision_detail": bundle.vision_detail,
            })})

    return child_exp.run_stream(child_ctx, task, cancel_token=cancel_token,
                                spawn_id=spawn_id)
```

回退路径（均用父 `ctx`，即继承父 profile）：
- `child_cfg.llm is None`（未配置）；
- `factory is None`（BYOK，或 devshell 未装时）；
- `factory` 抛 `KeyError`（profile_key 非法）—— 记 warning 后回退。

`AgentRunRequest` / `AgentRunContext` 均 frozen，`model_copy` 是符合现有约定的
不可变更新方式。

### 7. exp toml — `matmaster/exps/*.toml`

按需给某些 exp 写 `llm = "matmaster/gpt-5.5"`；不写即继承。

注意：`llm` 字段**只在该 exp 作为 subagent 被 spawn 时生效**。某些 exp（如
`direct.toml`）同时作为 UI root mode，此时 root 的 profile 由 service 层
`build_provider_bundle(model_override=...)`（用户顶层模型选择）决定，**不读**
exp.llm。即 `direct.toml` 里写 `llm` 不会固定 direct 作为 root 时的模型。

## 持久化与可观测

`model_copy` 后 child 的 `request.llm_model_profile` 改变，流经 `build_runtime`
→ `AgentKernelSpec.llm_model_profile`（exp.py 现有）→ `RunResultEvent.model_profile`
（events.py:138）→ `PersistenceHandler` 落库（persistence_handler.py:63）。subagent
run 本就携带独立 `spawn_id`，故每个 subagent 的真实 profile 会自动各自持久化，无需
额外接线（搭上既有的 "persist model identity on run_result"）。

范围限定：这是**后台持久化可观测**。公共 SSE / replay payload 会隐藏 model
identity（event_payloads.py:369），前端不可见。本特性不改变该 withhold 策略。

## 改动清单

| 文件 | 改动 |
|---|---|
| `matmaster/config/exp.py` | `ExpConfig` 加 `llm: str \| None = None` |
| `matmaster/types/runtime_ports.py` | 加 `SubagentProviderFactory` Protocol + `AgentRunPorts.subagent_provider_factory` |
| `src/services/billing_llm_provider.py` | 抽出 `BillingRunState`（call_index / spent / guard），`BillingLLMProvider` 改持有共享 state |
| `src/services/agent_run_service.py` | 建共享 `BillingRunState`；platform 分支构造工厂闭包（闭进同一 state）并挂进 `AgentRunPorts`；BYOK 分支不挂 |
| `matmaster/devshell/runner.py` | 挂非计费 subagent provider factory，注入 `AgentRunPorts` |
| `matmaster/core/exp.py` | `child_run_factory` 内解析 child profile（含 `KeyError` 回退）并 `model_copy` 出 child_ctx |
| `matmaster/exps/*.toml` | 按需给某些 exp 写 `llm = "..."` |

核心新增：一个端口类型 + 一处工厂闭包 + 一段 child 解析；外加一处 billing
状态抽取（保住 run 级熔断语义）。沿用现有端口 / 不可变 request 模式，核心 `Exp`
层不沾计费。
