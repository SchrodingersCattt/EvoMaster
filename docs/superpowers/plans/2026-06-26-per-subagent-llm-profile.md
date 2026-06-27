# Per-subagent LLM profile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让每个 subagent（`matmaster/exps/*.toml` 定义的 exp）能在自己的 toml 里静态配置使用哪个 LLM profile，未配置则继承父 agent。

**Architecture:** 在 `ExpConfig` 加可选 `llm` 字段；在 `AgentRunPorts` 加一个 `subagent_provider_factory` 端口，由 service 层闭包注入（闭进 llm_config + 计费上下文），devshell 注入非计费版；`child_run_factory` 在 spawn 时按 child 的 `llm` 调端口换出 provider，未配置 / 端口缺失 / profile_key 非法均回退继承父 ctx。run 级计费状态抽出共享 `BillingRunState`，使 root 与所有 subagent wrapper 共享 call_index 与成本熔断。

**Tech Stack:** Python 3.12+, Pydantic v2, pytest, asyncio。

## Global Constraints

- 项目处于开发阶段，**禁止任何兼容 / 兜底 / 迁移内联逻辑**；偏好迁移而非兼容，改构造签名就直接改所有调用点。
- 除专有名词外注释用中文。
- `AgentRunRequest` / `AgentRunContext` 均 frozen，更新一律用 `model_copy`。
- `types` 层禁止反向依赖 `providers` 层；端口返回类型用 `Any`（与 `AgentRunRequest.llm_provider: Any` 一致）。
- 非法 profile_key 的语义是**回退继承父 profile + warning 日志**，不 fail-fast、不抛错给模型。
- 设计来源：`docs/superpowers/specs/2026-06-26-per-subagent-llm-profile-design.md`。

---

### Task 1: `ExpConfig.llm` 配置字段

**Files:**
- Modify: `matmaster/config/exp.py`（`ExpConfig`，约 69-93 行）
- Test: `tests/matmaster/config/test_exp.py`

**Interfaces:**
- Consumes: 无
- Produces: `ExpConfig.llm: str | None`（默认 `None`，profile_key 或 None）

- [ ] **Step 1: 写失败测试**

在 `tests/matmaster/config/test_exp.py` 末尾追加：

```python
from matmaster.config.exp import ExpConfig


def test_expconfig_llm_defaults_none():
    assert ExpConfig().llm is None


def test_expconfig_llm_accepts_profile_key():
    cfg = ExpConfig(llm="matmaster/gpt-5.5")
    assert cfg.llm == "matmaster/gpt-5.5"


def test_expconfig_rejects_unknown_field():
    # extra="forbid" 仍生效，llm 不削弱严格性
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ExpConfig(llmm="typo")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/matmaster/config/test_exp.py -k "llm or unknown_field" -v`
Expected: `test_expconfig_llm_accepts_profile_key` FAIL（`llm` 字段不存在，`extra="forbid"` 抛 ValidationError）

- [ ] **Step 3: 加字段**

在 `matmaster/config/exp.py` 的 `ExpConfig` 中，`developer_instructions: str = ""` 之后、`model_config` 之前加：

```python
    # subagent 专用：作为 subagent 被 spawn 时使用的 LLM profile_key。
    # None = 继承父 agent。作为 UI root mode 运行时不读此字段。
    llm: str | None = None
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/matmaster/config/test_exp.py -k "llm or unknown_field" -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add matmaster/config/exp.py tests/matmaster/config/test_exp.py
git commit -m "feat(config): add optional llm profile field to ExpConfig"
```

---

### Task 2: `SubagentProviderFactory` 端口

**Files:**
- Modify: `matmaster/types/runtime_ports.py`（`__all__`、新 Protocol、`AgentRunPorts` 约 165-185 行）
- Test: `tests/matmaster/types/test_runtime_ports.py`（已存在，**追加**）

**Interfaces:**
- Consumes: 无
- Produces:
  - `SubagentProviderFactory` Protocol：`__call__(self, *, profile_key: str) -> Any`
  - `AgentRunPorts.subagent_provider_factory: SubagentProviderFactory | None = None`

- [ ] **Step 1: 写失败测试**

在已存在的 `tests/matmaster/types/test_runtime_ports.py` 末尾**追加**以下内容（顶部已有的 `from matmaster.types.runtime_ports import ...` 行需补上 `SubagentProviderFactory`；若不便修改该行，则在追加块前单独 `from matmaster.types.runtime_ports import AgentRunPorts, SubagentProviderFactory`）：

```python
def test_subagent_provider_factory_defaults_none():
    assert AgentRunPorts().subagent_provider_factory is None


def test_subagent_provider_factory_settable():
    def fac(*, profile_key: str):
        return ("bundle", profile_key)

    ports = AgentRunPorts(subagent_provider_factory=fac)
    assert ports.subagent_provider_factory(profile_key="x") == ("bundle", "x")


def test_subagent_provider_factory_protocol_runtime_checkable():
    def fac(*, profile_key: str):
        return None

    assert isinstance(fac, SubagentProviderFactory)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/matmaster/types/test_runtime_ports.py -v`
Expected: FAIL（`ImportError: cannot import name 'SubagentProviderFactory'`）

- [ ] **Step 3: 实现端口**

在 `matmaster/types/runtime_ports.py`：

3a. `__all__` 列表中加入 `"SubagentProviderFactory"`（按字母序，放在 `"SubmitApprovalGate"` 之前一行）。

3b. 在 `InterruptChecker` Protocol 定义（约 135 行）之后加：

```python
@runtime_checkable
class SubagentProviderFactory(Protocol):
    """按 profile_key 物化一个 subagent 用的 LLM provider bundle。

    消费者：Exp 的 child_run_factory（child config 解析后、run_stream 前）。
    返回：每次调用返回**全新** bundle（其 ``.provider`` 已按当前 run 模式包装，
    平台模式下含计费）。严禁按 profile 缓存复用同一个 bundle——并发 spawn 同
    profile 的两个 subagent 会因此共用一个 async context manager 与 http session。
    返回类型用 ``Any`` 以免 types 层反向依赖 providers 层（实际为
    ``providers.llm_factory.LLMProviderBundle``）。
    profile_key 非法时实现内部抛 ``KeyError``，由消费者捕获并回退继承父 provider。
    """

    def __call__(self, *, profile_key: str) -> Any: ...
```

3c. 在 `AgentRunPorts` dataclass（约 165 行）中，`submit_approval_gate: SubmitApprovalGate | None = None` 之后加：

```python
    subagent_provider_factory: SubagentProviderFactory | None = None
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/matmaster/types/test_runtime_ports.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add matmaster/types/runtime_ports.py tests/matmaster/types/test_runtime_ports.py
git commit -m "feat(ports): add subagent_provider_factory port"
```

---

### Task 3: 共享 `BillingRunState` 重构

把 run 级计费状态（call_index / spent / guard）从 `BillingLLMProvider` 实例搬到一个可共享对象，并更新唯一调用点（`agent_run_service.py`）与迁移现有测试。重构后行为对单 provider 场景不变。

**Files:**
- Modify: `src/services/billing_llm_provider.py`
- Modify: `src/services/agent_run_service.py`（root wrapper 构造，约 447-460 行）
- Test: `tests/test_billing_llm_provider.py`（迁移 `TestCostGuard`）

**Interfaces:**
- Consumes: 无
- Produces:
  - `BillingRunState(*, session_id: str, budget_micro: int | None = None, cancel_controller: CancellationController | None = None)`，方法 `next_call_index() -> int`、`accumulate(cost_micro: int) -> None`，属性 `_spent_micro`、`_guard_tripped`。
  - `BillingLLMProvider.__init__(inner, *, run_context, model, billing_service, billing_mode="platform", run_state: BillingRunState)` —— 移除 `budget_micro` / `cancel_controller` 入参。

- [ ] **Step 1: 写失败测试（迁移 guard 测试到 BillingRunState）**

`tests/test_billing_llm_provider.py` 当前仅含 `_provider` + `TestCostGuard`（无其它使用）。用以下**完整文件**覆盖它（guard 逻辑现已属于 `BillingRunState`）：

```python
"""BillingRunState 的 in-run 成本熔断（防线二）+ call_index 计数。

只测同步的成本累加 + 熔断触发逻辑，不触发真实 LLM / HTTP。
"""

from __future__ import annotations

from matmaster.types.cancellation import CancellationController
from src.services.billing_llm_provider import BillingRunState


def _state(budget_micro, controller):
    return BillingRunState(
        session_id="s", budget_micro=budget_micro, cancel_controller=controller
    )


class TestCostGuard:
    def test_trips_when_cumulative_over_budget(self):
        ctrl = CancellationController()
        st = _state(1000, ctrl)
        st.accumulate(600)
        assert ctrl.token.is_cancelled is False
        st.accumulate(600)
        assert ctrl.token.is_cancelled is True

    def test_trip_marks_cost_guard_cancel_reason(self):
        ctrl = CancellationController()
        st = _state(100, ctrl)
        st.accumulate(200)
        assert ctrl.token.is_cancelled is True
        assert ctrl.token.cancel_reason == "cost_guard"

    def test_no_trip_within_budget(self):
        ctrl = CancellationController()
        st = _state(1000, ctrl)
        st.accumulate(1000)
        assert ctrl.token.is_cancelled is False

    def test_no_budget_never_trips(self):
        ctrl = CancellationController()
        st = _state(None, ctrl)
        st.accumulate(10**9)
        assert ctrl.token.is_cancelled is False

    def test_no_controller_never_trips(self):
        st = _state(100, None)
        st.accumulate(10**9)
        assert st._guard_tripped is False

    def test_trips_only_once_keeps_accumulating(self):
        ctrl = CancellationController()
        st = _state(100, ctrl)
        st.accumulate(200)
        assert ctrl.token.is_cancelled is True
        st.accumulate(200)
        assert st._spent_micro == 400

    def test_ignores_non_positive(self):
        ctrl = CancellationController()
        st = _state(100, ctrl)
        st.accumulate(0)
        st.accumulate(-5)
        assert st._spent_micro == 0
        assert ctrl.token.is_cancelled is False

    def test_call_index_monotonic_shared(self):
        # 共享 state：两个 wrapper 取到的 call_index 全 run 单调
        st = _state(None, None)
        assert [st.next_call_index() for _ in range(3)] == [1, 2, 3]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_billing_llm_provider.py::TestCostGuard -v`
Expected: FAIL（`ImportError: cannot import name 'BillingRunState'`）

- [ ] **Step 3a: 实现 `BillingRunState` 并改 `BillingLLMProvider`**

在 `src/services/billing_llm_provider.py`，于 `class BillingLLMProvider` **之前**插入：

```python
class BillingRunState:
    """一个 run 内共享的计费状态：call_index 计数 + in-run 成本熔断累计。

    root / subagent / compaction 的所有 BillingLLMProvider 共享同一实例，使
    call_index 全 run 单调、成本熔断按全 run 累计触发（防止 per-wrapper 拆分后
    预算被绕松）。asyncio 单线程下计数 / 累加均为同步原子操作，无需锁。
    """

    def __init__(
        self,
        *,
        session_id: str,
        budget_micro: int | None = None,
        cancel_controller: CancellationController | None = None,
    ) -> None:
        self._session_id = session_id
        self._budget_micro = budget_micro
        self._cancel_controller = cancel_controller
        self._call_index = 0
        self._spent_micro = 0
        self._guard_tripped = False

    def next_call_index(self) -> int:
        self._call_index += 1
        return self._call_index

    def accumulate(self, cost_micro: int) -> None:
        """累加本次结算成本，超预算则触发 in-run 熔断（取消整个 run）。"""
        if cost_micro <= 0:
            return
        self._spent_micro += cost_micro
        self._maybe_trip_guard()

    def _maybe_trip_guard(self) -> None:
        if (
            self._guard_tripped
            or self._budget_micro is None
            or self._cancel_controller is None
        ):
            return
        if self._spent_micro <= self._budget_micro:
            return
        self._guard_tripped = True
        logger.warning(
            "in-run cost guard tripped session_id=%s spent_micro=%s budget_micro=%s, "
            "cancelling run",
            self._session_id,
            self._spent_micro,
            self._budget_micro,
        )
        try:
            self._cancel_controller.cancel(reason=COST_GUARD_CANCEL_REASON)
        except Exception:
            logger.warning(
                "cost guard cancel failed session_id=%s",
                self._session_id,
                exc_info=True,
            )
```

然后改 `BillingLLMProvider.__init__`：删除 `budget_micro` / `cancel_controller` 形参，新增 `run_state`，删除 `self._call_index` / `self._budget_micro` / `self._cancel_controller` / `self._spent_micro` / `self._guard_tripped` 五个实例字段，改为 `self._run_state = run_state`：

```python
    def __init__(
        self,
        inner: LLMProvider,
        *,
        run_context: BillingRunContext,
        model: str,
        billing_service: BillingService,
        billing_mode: str = "platform",
        run_state: BillingRunState,
    ) -> None:
        self._inner = inner
        self._run_context = run_context
        self._model = model
        self._billing_service = billing_service
        self._billing_mode = billing_mode
        self._run_state = run_state
        self._pending: set[asyncio.Task] = set()
        self._http_session: aiohttp.ClientSession | None = None
        self._spawn_id_var: ContextVar[str | None] = ContextVar(
            "billing_spawn_id",
            default=None,
        )
```

- [ ] **Step 3b: 把计数 / 累加委派给 run_state**

在同文件：

- 删除 `_next_call_index` 方法；将 `chat` / `chat_stream` 里的 `call_index = self._next_call_index()` 改为 `call_index = self._run_state.next_call_index()`。
- 删除 `_maybe_trip_guard` 方法（已移入 `BillingRunState`）。
- 将 `_accumulate_cost` 改为只解析定价 dict 后委派：

```python
    def _accumulate_cost(self, data: dict[str, Any] | None) -> None:
        """从定价响应解析本次成本，委派给共享 run_state 累加 / 熔断。"""
        if not data:
            return
        try:
            cost_micro = int(data.get("total_amount_settle_micro") or 0)
        except (TypeError, ValueError):
            return
        self._run_state.accumulate(cost_micro)
```

- [ ] **Step 3c: 更新唯一调用点 `agent_run_service.py`**

在 `src/services/agent_run_service.py`，把 root wrapper 构造（约 447-460 行）改为先建共享 state 再传入。将：

```python
            try:
                llm_provider = BillingLLMProvider(
                    llm_provider,
                    run_context=BillingRunContext(
                        session_id=session_id,
                        task_id=task_id,
                        invocation_id=invocation_id,
                    ),
                    model=llm_bundle.model,
                    billing_service=get_billing_service(),
                    billing_mode=billing_mode,
                    budget_micro=budget_micro,
                    cancel_controller=cancel_controller,
                )
            except Exception:
```

替换为：

```python
            billing_state = BillingRunState(
                session_id=session_id,
                budget_micro=budget_micro,
                cancel_controller=cancel_controller,
            )
            try:
                llm_provider = BillingLLMProvider(
                    llm_provider,
                    run_context=BillingRunContext(
                        session_id=session_id,
                        task_id=task_id,
                        invocation_id=invocation_id,
                    ),
                    model=llm_bundle.model,
                    billing_service=get_billing_service(),
                    billing_mode=billing_mode,
                    run_state=billing_state,
                )
            except Exception:
```

并更新 import：把 `from src.services.billing_llm_provider import BillingLLMProvider`（约 45 行附近，原 import 含 `BillingLLMProvider`）改为同时导入 `BillingRunState`。用 grep 定位：`grep -n "BillingLLMProvider" src/services/agent_run_service.py`。

> 说明：`billing_state` 变量在 Task 5 会被 subagent factory 闭包复用，故定义在 try 之外。

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_billing_llm_provider.py -v`
Expected: PASS

再跑冒烟，确认服务模块仍可导入：
Run: `python -c "import src.services.agent_run_service"`
Expected: 无 ImportError / 无 TypeError

- [ ] **Step 5: 提交**

```bash
git add src/services/billing_llm_provider.py src/services/agent_run_service.py tests/test_billing_llm_provider.py
git commit -m "refactor(billing): extract shared BillingRunState for run-level counters"
```

---

### Task 4: `child_run_factory` profile 解析与回退

**Files:**
- Modify: `matmaster/core/exp.py`（新增模块级 `_resolve_child_run_ctx`；`child_run_factory` 调用它）
- Test: `tests/matmaster/core/test_exp.py`

**Interfaces:**
- Consumes: `ExpConfig.llm`（Task 1）、`AgentRunPorts.subagent_provider_factory`（Task 2）、`LLMProviderBundle`（含 `provider/model/model_profile/model_route/context_limit/supports_vision/vision_detail`）。
- Produces: `_resolve_child_run_ctx(ctx: AgentRunContext, child_cfg: ExpConfig) -> AgentRunContext`。

- [ ] **Step 1: 写失败测试**

在 `tests/matmaster/core/test_exp.py` 末尾追加（复用文件顶部已有的 `_make_ctx`、`ExpConfig`、`AgentRunPorts` 需 import）：

```python
from dataclasses import dataclass

from matmaster.types.runtime_ports import AgentRunPorts


@dataclass
class _FakeBundle:
    provider: object
    model: str
    model_profile: str
    model_route: str | None
    context_limit: int
    supports_vision: bool
    vision_detail: str | None


def _ctx_with_factory(factory):
    return _make_ctx().model_copy(
        update={
            "request": _make_ctx().request.model_copy(
                update={
                    "llm_provider": "PARENT",
                    "llm_model": "parent-model",
                    "llm_model_profile": "parent/profile",
                    "ports": AgentRunPorts(subagent_provider_factory=factory),
                }
            )
        }
    )


class TestResolveChildRunCtx:
    def test_no_llm_inherits_parent(self):
        from matmaster.core.exp import _resolve_child_run_ctx

        ctx = _ctx_with_factory(lambda *, profile_key: None)
        out = _resolve_child_run_ctx(ctx, ExpConfig(llm=None))
        assert out is ctx  # 未配置 → 原样继承

    def test_factory_none_inherits_parent(self):
        from matmaster.core.exp import _resolve_child_run_ctx

        ctx = _make_ctx()  # ports 默认无 factory
        out = _resolve_child_run_ctx(ctx, ExpConfig(llm="some/profile"))
        assert out is ctx

    def test_configured_llm_overrides_provider(self):
        from matmaster.core.exp import _resolve_child_run_ctx

        bundle = _FakeBundle(
            provider="CHILD",
            model="child-model",
            model_profile="child/profile",
            model_route="child/profile",
            context_limit=123,
            supports_vision=True,
            vision_detail="high",
        )
        ctx = _ctx_with_factory(lambda *, profile_key: bundle)
        out = _resolve_child_run_ctx(ctx, ExpConfig(llm="child/profile"))
        assert out is not ctx
        assert out.request.llm_provider == "CHILD"
        assert out.request.llm_model == "child-model"
        assert out.request.llm_model_profile == "child/profile"
        assert out.request.context_limit == 123
        assert out.request.supports_vision is True

    def test_keyerror_falls_back_to_parent(self):
        from matmaster.core.exp import _resolve_child_run_ctx

        def boom(*, profile_key):
            raise KeyError(profile_key)

        ctx = _ctx_with_factory(boom)
        out = _resolve_child_run_ctx(ctx, ExpConfig(llm="bad/key"))
        assert out is ctx  # 非法 key → 回退继承，不抛错
        assert out.request.llm_provider == "PARENT"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/matmaster/core/test_exp.py::TestResolveChildRunCtx -v`
Expected: FAIL（`ImportError: cannot import name '_resolve_child_run_ctx'`）

- [ ] **Step 3: 实现解析函数并接入 child_run_factory**

3a. 在 `matmaster/core/exp.py` 模块级（任意顶层函数位置，建议紧邻 `Exp` 类定义之前）加：

```python
logger = logging.getLogger(__name__)


def _resolve_child_run_ctx(
    ctx: AgentRunContext,
    child_cfg: ExpConfig,
) -> AgentRunContext:
    """按 child exp 的 ``llm`` 字段换出 provider；否则原样继承父 ctx。

    回退（返回原 ctx）三种情形：未配置 ``llm``、端口缺失（BYOK / devshell 未装）、
    profile_key 非法（factory 抛 KeyError）。命中配置时基于父 ctx 的 frozen
    request 做 ``model_copy`` 覆盖 llm 字段。
    """
    factory = ctx.request.ports.subagent_provider_factory
    if not child_cfg.llm or factory is None:
        return ctx
    try:
        bundle = factory(profile_key=child_cfg.llm)
    except KeyError:
        logger.warning(
            "subagent llm profile %r unresolvable, inheriting parent profile",
            child_cfg.llm,
        )
        return ctx
    return ctx.model_copy(
        update={
            "request": ctx.request.model_copy(
                update={
                    "llm_provider": bundle.provider,
                    "llm_model": bundle.model,
                    "llm_model_profile": bundle.model_profile,
                    "llm_model_route": bundle.model_route,
                    "context_limit": bundle.context_limit,
                    "supports_vision": bundle.supports_vision,
                    "vision_detail": bundle.vision_detail,
                }
            )
        }
    )
```

> 注：`matmaster/core/exp.py` 顶部已 `import logging`（第 11 行）；若文件内已有 `logger = logging.getLogger(__name__)` 则不要重复定义，只加函数。先 `grep -n "^logger = " matmaster/core/exp.py` 确认。

3b. 改 `child_run_factory`（约 173-192 行），在构造 `child_exp` 后、`run_stream` 前解析 child_ctx：

```python
        def child_run_factory(
            exp_name: str,
            task: str,
            *,
            cancel_token: CancellationToken | None = None,
            spawn_id: str | None = None,
        ) -> AsyncIterator[Any]:
            from matmaster.config.loader import load_exp_config

            child_cfg = load_exp_config(exp_name)
            child_exp = Exp(
                child_cfg,
                allow_spawn=False,
                inherited_skill_cache=skill_cache,
            )
            child_ctx = _resolve_child_run_ctx(ctx, child_cfg)
            return child_exp.run_stream(
                child_ctx,
                task,
                cancel_token=cancel_token,
                spawn_id=spawn_id,
            )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/matmaster/core/test_exp.py::TestResolveChildRunCtx -v`
Expected: PASS

再跑整个 exp 测试确认无回归：
Run: `pytest tests/matmaster/core/test_exp.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add matmaster/core/exp.py tests/matmaster/core/test_exp.py
git commit -m "feat(exp): resolve per-subagent llm profile in child_run_factory"
```

---

### Task 5: service 层 subagent factory 装配

**Files:**
- Modify: `src/services/agent_run_service.py`（新增模块级 `make_subagent_provider_factory`；platform 分支挂端口）
- Test: `tests/test_subagent_provider_factory.py`（Create）

**Interfaces:**
- Consumes: `build_provider_bundle`（`matmaster/providers/llm_factory.py`）、`BillingLLMProvider` / `BillingRunState`（Task 3）、`AgentRunPorts.subagent_provider_factory`（Task 2）。
- Produces: `make_subagent_provider_factory(*, llm_config, run_context: BillingRunContext, billing_service, billing_state: BillingRunState) -> SubagentProviderFactory`。

- [ ] **Step 1: 写失败测试**

Create `tests/test_subagent_provider_factory.py`：

```python
"""service 层 subagent provider factory：换 profile + 共享 billing state。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from clients.billing.client import BillingRunContext
from matmaster.config.loader import load_llm_config
from src.services.agent_run_service import make_subagent_provider_factory
from src.services.billing_llm_provider import BillingLLMProvider, BillingRunState

_LLM_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "llm_config.yaml"


def _factory(state):
    return make_subagent_provider_factory(
        llm_config=load_llm_config(_LLM_CONFIG_PATH),
        run_context=BillingRunContext(session_id="s", task_id="t", invocation_id="i"),
        billing_service=MagicMock(),
        billing_state=state,
    )


def test_factory_returns_billing_wrapped_bundle_for_profile():
    state = BillingRunState(session_id="s")
    fac = _factory(state)
    bundle = fac(profile_key="matmaster/DeepSeek-v4-Pro")
    assert bundle.model_profile == "matmaster/DeepSeek-v4-Pro"
    assert isinstance(bundle.provider, BillingLLMProvider)


def test_factory_shares_billing_run_state():
    state = BillingRunState(session_id="s")
    fac = _factory(state)
    bundle = fac(profile_key="matmaster/DeepSeek-v4-Pro")
    assert bundle.provider._run_state is state


def test_factory_raises_keyerror_on_unknown_profile():
    import pytest

    fac = _factory(BillingRunState(session_id="s"))
    with pytest.raises(KeyError):
        fac(profile_key="matmaster/does-not-exist")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_subagent_provider_factory.py -v`
Expected: FAIL（`ImportError: cannot import name 'make_subagent_provider_factory'`）

- [ ] **Step 3: 实现 factory builder 并挂端口**

3a. 在 `src/services/agent_run_service.py` 模块级（靠近文件顶部其它 import 之后、`run_agent` 之外）加。先确认 `from dataclasses import replace` 已导入，否则补：

```python
from dataclasses import replace


def make_subagent_provider_factory(
    *,
    llm_config,
    run_context,
    billing_service,
    billing_state,
):
    """构造 subagent provider factory：按 profile_key 解析并包计费，共享 run_state。

    返回的 callable 每次都新建 bundle 与 BillingLLMProvider（连接池 / inner 必须
    per-profile 独立），但所有 wrapper 共享同一 ``billing_state``，使 call_index
    全 run 单调、成本熔断按全 run 累计。
    """
    from matmaster.providers.llm_factory import build_provider_bundle

    def factory(*, profile_key: str):
        bundle = build_provider_bundle(llm_config, model_override=profile_key)
        wrapped = BillingLLMProvider(
            bundle.provider,
            run_context=run_context,
            model=bundle.model,
            billing_service=billing_service,
            billing_mode="platform",
            run_state=billing_state,
        )
        return replace(bundle, provider=wrapped)

    return factory
```

3b. 在 platform 分支装配 `AgentRunPorts` 处（约 588 行起的 `ports=AgentRunPorts(...)`），加一个字段。注意 BYOK 分支不会执行到这套 platform billing（`billing_state` 仅 platform 分支定义），但 `AgentRunPorts` 是统一构造的——因此用条件值：仅 platform 模式挂 factory。

在 `ports=AgentRunPorts(` 之前先算出 factory（platform 才有）：

```python
            subagent_provider_factory = None
            if billing_mode == "platform":
                subagent_provider_factory = make_subagent_provider_factory(
                    llm_config=llm_config,
                    run_context=BillingRunContext(
                        session_id=session_id,
                        task_id=task_id,
                        invocation_id=invocation_id,
                    ),
                    billing_service=get_billing_service(),
                    billing_state=billing_state,
                )
```

> `billing_state` 来自 Task 3c（root wrapper 之前定义）。BYOK 分支 `billing_mode == "byok"` → factory 保持 None → child 回退继承父 BYOK provider，符合设计。

然后在 `AgentRunPorts(...)` 构造里新增一行：

```python
                        subagent_provider_factory=subagent_provider_factory,
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_subagent_provider_factory.py -v`
Expected: PASS

冒烟导入：
Run: `python -c "import src.services.agent_run_service"`
Expected: 无异常

- [ ] **Step 5: 提交**

```bash
git add src/services/agent_run_service.py tests/test_subagent_provider_factory.py
git commit -m "feat(service): wire platform subagent provider factory onto ports"
```

---

### Task 6: devshell 非计费 factory 装配

**Files:**
- Modify: `matmaster/devshell/runner.py`（新增模块级 `make_dev_subagent_provider_factory`；`build_run_context` 注入端口）
- Test: `tests/matmaster/devshell/test_runner_subagent_factory.py`（Create）

**Interfaces:**
- Consumes: `build_provider_bundle`、`AgentRunPorts.subagent_provider_factory`（Task 2）。
- Produces: `make_dev_subagent_provider_factory(llm_config) -> SubagentProviderFactory`。

- [ ] **Step 1: 写失败测试**

Create `tests/matmaster/devshell/test_runner_subagent_factory.py`：

```python
"""devshell 非计费 subagent provider factory。"""

from __future__ import annotations

from pathlib import Path

import pytest

from matmaster.config.loader import load_llm_config
from matmaster.devshell.runner import make_dev_subagent_provider_factory

_LLM_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "llm_config.yaml"


def test_dev_factory_returns_bare_bundle_for_profile():
    fac = make_dev_subagent_provider_factory(load_llm_config(_LLM_CONFIG_PATH))
    bundle = fac(profile_key="matmaster/DeepSeek-v4-Pro")
    assert bundle.model_profile == "matmaster/DeepSeek-v4-Pro"
    # devshell 不包计费：provider 不是 BillingLLMProvider
    from src.services.billing_llm_provider import BillingLLMProvider

    assert not isinstance(bundle.provider, BillingLLMProvider)


def test_dev_factory_raises_keyerror_on_unknown():
    fac = make_dev_subagent_provider_factory(load_llm_config(_LLM_CONFIG_PATH))
    with pytest.raises(KeyError):
        fac(profile_key="matmaster/nope")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/matmaster/devshell/test_runner_subagent_factory.py -v`
Expected: FAIL（`ImportError: cannot import name 'make_dev_subagent_provider_factory'`）

- [ ] **Step 3: 实现并注入**

3a. 在 `matmaster/devshell/runner.py` 模块级（顶层 import 之后）加：

```python
def make_dev_subagent_provider_factory(llm_config):
    """devshell 用的 subagent provider factory：解析 profile，不包计费。"""
    from matmaster.providers.llm_factory import build_provider_bundle

    def factory(*, profile_key: str):
        return build_provider_bundle(llm_config, model_override=profile_key)

    return factory
```

3b. 改 `build_run_context`（约 132-152 行），把 ports 统一包含 factory（`_llm_config` 为 None 的测试场景下不挂）：

```python
    def build_run_context(
        self,
        *,
        child_event_sink: Any = None,
    ) -> AgentRunContext:
        from matmaster.types.runtime_ports import AgentRunPorts

        subagent_factory = (
            make_dev_subagent_provider_factory(self._llm_config)
            if self._llm_config is not None
            else None
        )
        request = self._request.model_copy(
            update={
                "ports": AgentRunPorts(
                    child_event_forward_sink=child_event_sink,
                    subagent_provider_factory=subagent_factory,
                )
            }
        )
        return AgentRunContext(environment=self._environment, request=request)
```

> 原实现仅在 `child_event_sink is not None` 时才 model_copy；现在统一构造 ports（`child_event_forward_sink` 接受 None，与 service 层一致）。

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/matmaster/devshell/test_runner_subagent_factory.py -v`
Expected: PASS

冒烟导入：
Run: `python -c "import matmaster.devshell.runner"`
Expected: 无异常

- [ ] **Step 5: 提交**

```bash
git add matmaster/devshell/runner.py tests/matmaster/devshell/test_runner_subagent_factory.py
git commit -m "feat(devshell): wire non-billing subagent provider factory"
```

---

### Task 7: 全量回归 + 端到端确认

**Files:**
- Test: 全量

**Interfaces:**
- Consumes: 全部
- Produces: 无

- [ ] **Step 1: 跑全部受影响测试**

Run:
```bash
pytest tests/matmaster/config/test_exp.py \
       tests/matmaster/types/test_runtime_ports.py \
       tests/test_billing_llm_provider.py \
       tests/matmaster/core/test_exp.py \
       tests/test_subagent_provider_factory.py \
       tests/matmaster/devshell/test_runner_subagent_factory.py -q
```
Expected: 全 PASS

- [ ] **Step 2: 冒烟导入三处改动模块**

Run:
```bash
python -c "import src.services.agent_run_service, matmaster.devshell.runner, matmaster.core.exp"
```
Expected: 无异常

- [ ] **Step 3: （产品决定，非必做）给某个 exp 配 profile 做端到端验证**

由产品决定哪个 exp 用哪个 profile。示例：在 `matmaster/exps/explore.toml` 顶层加一行
`llm = "matmaster/gpt-5.5"`（profile_key 必须存在于 `config/llm_config.yaml`），
然后用 devshell spawn explore，确认其 run_result 的 `model_profile` 为该值、而父
agent 仍用默认 profile。本步是验证手段，**不**作为代码改动提交（除非产品确认要长期生效）。

- [ ] **Step 4: 若有 toml 改动则提交**

```bash
git add matmaster/exps/*.toml
git commit -m "chore(exps): assign per-subagent llm profile"
```
（无 toml 改动则跳过。）
