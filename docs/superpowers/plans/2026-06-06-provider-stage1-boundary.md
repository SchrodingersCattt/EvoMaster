# 阶段一：Provider 身份与协议边界收束 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 LLM provider 层的 provider 与真实 API transport 分层诚实化、误配置启动期 fail-fast，行为对现有 chat_completions / bedrock 路径等价。

**Architecture:** 引入两层轻量 registry（`PROVIDER_TRANSPORT` 协议查找表 + `PLATFORM_PROVIDERS` 平台 YAML 白名单），transport 由 provider 推出；factory 改为按 transport 显式分发；`OpenAIProvider` 改名 `ChatCompletionsProvider`；补全 `LLMProvider` Protocol 的 timeout/retry 属性并去掉 kernel 对私有 `_timeout` 的读取；BYOK 身份收束为 `provider=byok`。不动 kernel 主循环、message IR、provider_state、native transport、Bedrock 实现、reasoning 字段。

**Tech Stack:** Python ≥3.10、Pydantic v2、pytest、uv 环境、OpenAI SDK（chat.completions）、pre-commit。

**上位 spec：** `docs/superpowers/specs/2026-06-06-provider-stage1-boundary-design.md`

**全局约定：**
- 所有命令在仓库 uv 环境执行：`uv run pytest ...`。
- 任务顺序不可乱：Task 1（数据迁移）必须先于 Task 2（校验上线），否则现有 `provider: "openai"` 会被新校验打挂。
- 每个 Task 末尾 commit；commit 只含代码与测试，**不含任何 docs**（项目硬约束）。

---

## 文件结构

| 文件 | 责任 | 涉及 Task |
|---|---|---|
| `config/llm_config.yaml` | 平台 LLM 配置数据 | 1 |
| `matmaster/config/llm.py` | profile/route 配置模型、provider→transport 映射、校验 | 2, 3 |
| `matmaster/providers/chat_completions_provider.py`（由 `openai_provider.py` 改名） | Chat Completions / OpenAI 兼容 provider | 4 |
| `matmaster/providers/__init__.py` | provider 包导出 | 4 |
| `matmaster/providers/llm_factory.py` | route→provider 构造与分发 | 5, 8 |
| `scripts/lint_no_arguments_mutation.py` | E3 arguments 变异 lint 的 allowlist | 4 |
| `matmaster/types/llm_provider.py` | `LLMProvider` Protocol | 6 |
| `matmaster/providers/bedrock_provider.py` | Bedrock provider 的 timeout property 收口 | 6 |
| `matmaster/core/agent_llm_stream.py` | kernel 流式重试包装，去私有 `_timeout` | 7 |
| `tests/conftest.py`、`tests/matmaster/types/test_llm_provider.py` | Protocol 一致性 mock | 6 |
| 多个 `tests/matmaster/...` 夹具 | 随数据迁移更新 provider 值 | 1, 3 |

---

## Task 1: 数据迁移 — 显式 provider 值 `openai → litellm`

把所有显式写死的平台 provider 从 `openai` 改成 `litellm`。当前 factory 仍按 `provider == "bedrock"` 分发，`litellm != bedrock → OpenAIProvider`，**行为等价**。本任务纯数据迁移，不动任何逻辑。

**Files:**
- Modify: `config/llm_config.yaml`（7 处 `provider: "openai"`）
- Modify: `tests/matmaster/config/test_loader.py:24,29`
- Modify: `tests/matmaster/config/test_llm.py:328,329,371,376,398,405,417,474`
- Modify: `tests/matmaster/providers/test_llm_factory.py:23,46,102,218`
- Modify: `tests/matmaster/integration/test_llm_factory.py:86,100,146`

> 不改 `test_llm.py:42` 的 `assert p.provider == "openai"`（那是默认值断言，随 Task 2 的默认值变更一起改）。不改 `provider="bedrock"` 的行。
> 不改 `evaluation/` 下独立 eval runtime 的 config（与平台 LLMConfig 校验无关）。

- [ ] **Step 1: 迁移 `config/llm_config.yaml`**

把这 7 个 profile 的 `provider: "openai"` 全改为 `provider: "litellm"`：`sonnet` / `gemini-pro` / `gpt55` / `qwen_3_7_max` / `deepseek_v4_pro` / `deepseek_v4_pro_mm` / `opus_global`。`opus_bedrock` 的 `provider: "bedrock"` 不动。`prompt_cache.provider: "anthropic"`（PromptCacheConfig 内）不动。

- [ ] **Step 2: 迁移测试夹具里的显式 provider**

对上述测试文件，把 profile 级 `provider="openai"`（Python）与 `provider: "openai"`（YAML / `model_validate` 字典夹具）逐处改为 `litellm`；把因此受影响的断言一并改：
- `test_llm.py:398` `provider="openai",` → `provider="litellm",`
- `test_llm.py:405` `assert r.provider == "openai"` → `assert r.provider == "litellm"`
- `test_llm.py:417` `provider="openai",` → `provider="litellm",`（`test_resolve_route_llm_override_as_profile_key` 的期望 ResolvedLLMRoute）
- `test_llm.py:474` `provider="openai",`（TestSonnetRouteRegression 的 ResolvedLLMRoute）→ `provider="litellm",`
- `integration/test_llm_factory.py:86,100,146` 的 `"provider": "openai"` → `"litellm"`；若该文件有断言 `provider == "openai"`（如 `test_default_provider`），一并改为 `"litellm"`。
- 其余 `provider="openai"` / `provider: "openai"` → `litellm`（fixture 数据）。

- [ ] **Step 3: grep 守卫 — 确认平台测试/配置里无残留 `provider: openai`**

Run: `grep -rn 'provider": "openai"\|provider="openai"\|provider: "openai"' config tests matmaster --include="*.py" --include="*.yaml"`
Expected: 仅剩 `test_llm.py:42` 默认值断言一处（随 Task 2 改）；无其他残留。`evaluation/` 不在范围。

- [ ] **Step 4: 运行受影响测试确认全绿**

Run: `uv run pytest tests/matmaster/config/test_llm.py tests/matmaster/config/test_loader.py tests/matmaster/providers/test_llm_factory.py tests/matmaster/integration/test_llm_factory.py -q`
Expected: PASS（行为等价，无逻辑改动）。

- [ ] **Step 5: Commit**

```bash
git add config/llm_config.yaml tests/matmaster/config/test_llm.py tests/matmaster/config/test_loader.py tests/matmaster/providers/test_llm_factory.py tests/matmaster/integration/test_llm_factory.py
git commit -m "refactor(providers): migrate platform provider label openai->litellm"
```

---

## Task 2: 配置模型 — provider→transport 映射、`effective_transport()`、默认值、加载期校验

**Files:**
- Modify: `matmaster/config/llm.py`
- Test: `tests/matmaster/config/test_llm.py`

- [ ] **Step 1: 写失败测试 — `effective_transport` 与加载期 fail-fast**

在 `tests/matmaster/config/test_llm.py` 末尾追加：

```python
from matmaster.config.llm import PLATFORM_PROVIDERS, PROVIDER_TRANSPORT


class TestProviderTransport:
    def test_effective_transport_litellm(self) -> None:
        p = _profile(provider="litellm", model="m")
        assert p.effective_transport() == "chat_completions"

    def test_effective_transport_byok(self) -> None:
        p = _profile(provider="byok", model="m")
        assert p.effective_transport() == "chat_completions"

    def test_effective_transport_bedrock(self) -> None:
        p = _profile(provider="bedrock", model="m")
        assert p.effective_transport() == "bedrock_converse"

    def test_provider_default_is_litellm(self) -> None:
        assert _profile(model="m").provider == "litellm"

    def test_unknown_provider_in_config_fails_fast(self) -> None:
        with pytest.raises(ValueError, match="unknown provider"):
            LLMConfig(
                profiles={"p": _profile(provider="nope", model="m")},
                default="p",
            )

    def test_byok_provider_rejected_in_yaml_config(self) -> None:
        with pytest.raises(ValueError, match="unknown provider"):
            LLMConfig(
                profiles={"p": _profile(provider="byok", model="m")},
                default="p",
            )

    def test_byok_excluded_from_platform_providers(self) -> None:
        assert "byok" in PROVIDER_TRANSPORT
        assert "byok" not in PLATFORM_PROVIDERS
```

同时把现有 `test_defaults` 里的默认断言改为 litellm：
- `test_llm.py:42` `assert p.provider == "openai"` → `assert p.provider == "litellm"`

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/matmaster/config/test_llm.py::TestProviderTransport -q`
Expected: FAIL（`PROVIDER_TRANSPORT` / `effective_transport` 未定义）。

- [ ] **Step 3: 实现 — 常量、方法、默认值、校验**

在 `matmaster/config/llm.py` 的 `MODEL_FAMILY_DEFAULTS` 之后新增常量：

```python
# ── Provider → transport 映射（阶段一轻量 registry）─────────────────────────────

# (1) transport 查找表：所有真实 provider → 其 API 协议。含运行时 BYOK。
PROVIDER_TRANSPORT: dict[str, str] = {
    "litellm": "chat_completions",
    "bedrock": "bedrock_converse",
    "byok": "chat_completions",
}

# (2) 平台 YAML 白名单：允许出现在 llm_config.yaml profile 里的 provider。
# 不含 byok —— byok 只能由运行时凭证路径构造，写进静态配置应 fail-fast。
PLATFORM_PROVIDERS: frozenset[str] = frozenset({"litellm", "bedrock"})
```

把 `LLMProfileConfig.provider` 默认值由 `"openai"` 改为 `"litellm"`：

```python
    provider: str = "litellm"
```

在 `LLMProfileConfig` 的语义方法区（`effective_family` 附近）新增：

```python
    def effective_transport(self) -> str:
        """Provider 决定 transport。provider 合法性由 LLMConfig 加载期校验保证。"""
        return PROVIDER_TRANSPORT[self.provider]
```

在 `LLMConfig._validate_internal_references` 的现有 default / route 校验**之后**追加 profile provider 校验：

```python
        for profile_key, profile in self.profiles.items():
            if profile.provider not in PLATFORM_PROVIDERS:
                raise ValueError(
                    f"profile '{profile_key}' has unknown provider "
                    f"'{profile.provider}', allowed: {sorted(PLATFORM_PROVIDERS)}"
                )
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/matmaster/config/test_llm.py -q`
Expected: PASS（含新 `TestProviderTransport` 与既有用例）。

- [ ] **Step 5: Commit**

```bash
git add matmaster/config/llm.py tests/matmaster/config/test_llm.py
git commit -m "feat(config): add provider->transport map and platform-provider fail-fast"
```

---

## Task 3: `ResolvedLLMRoute` 携带 transport

**Files:**
- Modify: `matmaster/config/llm.py`
- Test: `tests/matmaster/config/test_llm.py`

- [ ] **Step 1: 写失败测试 — resolve_route 带出 transport**

在 `tests/matmaster/config/test_llm.py` 的 `TestProviderTransport` 里追加：

```python
    def test_resolve_route_carries_transport(self) -> None:
        cfg = LLMConfig(
            profiles={
                "lite": _profile(provider="litellm", model="m-lite"),
                "bed": _profile(provider="bedrock", model="m-bed"),
            },
            routes={
                "r-lite": LLMRouteConfig(profile="lite"),
                "r-bed": LLMRouteConfig(profile="bed"),
            },
            default="lite",
        )
        assert cfg.resolve_route(model_override="r-lite").transport == "chat_completions"
        assert cfg.resolve_route(model_override="r-bed").transport == "bedrock_converse"
```

并更新**三处** resolve_route 等值断言（`ResolvedLLMRoute(...)` 加了必填 `transport` 字段，不补会构造失败）：
- `test_llm.py:393-400` `test_resolve_route_hit`：在 `ResolvedLLMRoute(...)` 内补 `transport="chat_completions",`
- `test_llm.py:412-419` `test_resolve_route_llm_override_as_profile_key`：同样补 `transport="chat_completions",`
- `test_llm.py:471-476` `TestSonnetRouteRegression`：同样补 `transport="chat_completions",`

> 这些 cfg 夹具的 profile 在 Task 1 已迁为 `litellm` → `effective_transport()=="chat_completions"`。
> grep 兜底：`grep -n "ResolvedLLMRoute(" tests/matmaster/config/test_llm.py` 确认每个构造点都带 transport。

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/matmaster/config/test_llm.py::TestProviderTransport::test_resolve_route_carries_transport -q`
Expected: FAIL（`ResolvedLLMRoute` 无 `transport` 属性）。

- [ ] **Step 3: 实现 — 给 `ResolvedLLMRoute` 加字段、resolve_route 填充**

在 `matmaster/config/llm.py` 的 `ResolvedLLMRoute` dataclass 增加字段：

```python
@dataclass(frozen=True)
class ResolvedLLMRoute:
    """Runtime-resolved LLM routing result."""

    route_key: str | None
    profile_key: str
    provider: str
    transport: str
    model: str
```

在 `resolve_route` 的两条 `return ResolvedLLMRoute(...)` 分支分别补 `transport=profile.effective_transport()`：

```python
            return ResolvedLLMRoute(
                route_key=model_override,
                profile_key=route.profile,
                provider=profile.provider,
                transport=profile.effective_transport(),
                model=route.model or profile.model,
            )
```

```python
        return ResolvedLLMRoute(
            route_key=None,
            profile_key=profile_key,
            provider=profile.provider,
            transport=profile.effective_transport(),
            model=profile.model,
        )
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/matmaster/config/test_llm.py -q`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add matmaster/config/llm.py tests/matmaster/config/test_llm.py
git commit -m "feat(config): carry transport in ResolvedLLMRoute"
```

---

## Task 4: 改名 `OpenAIProvider → ChatCompletionsProvider`（clean rename）

纯命名收束，逻辑不变。包含文件改名、类改名、引用更新、lint allowlist 更新、测试文件改名。

**Files:**
- Rename: `matmaster/providers/openai_provider.py` → `matmaster/providers/chat_completions_provider.py`
- Modify: `matmaster/providers/__init__.py`
- Modify: `matmaster/providers/llm_factory.py`
- Modify: `scripts/lint_no_arguments_mutation.py`
- Rename + Modify: 4 个 provider 测试文件
- Modify: 引用 `OpenAIProvider` 的其余测试

- [ ] **Step 1: git mv 主文件并改类名**

```bash
git mv matmaster/providers/openai_provider.py matmaster/providers/chat_completions_provider.py
```

在 `chat_completions_provider.py` 内把类名 `OpenAIProvider` 改为 `ChatCompletionsProvider`（含 `class OpenAIProvider`、`__aenter__`/`__aexit__` 返回注解里的 `-> OpenAIProvider`、文件内自引用）。`AnthropicPromptCacheOptions` 等其余符号原样保留。

- [ ] **Step 2: 更新 `scripts/lint_no_arguments_mutation.py` 的 allowlist**

把 `ALLOWLIST_PREFIXES` 内的旧路径改为新路径：

```python
ALLOWLIST_PREFIXES = [
    "matmaster/providers/chat_completions_provider.py",
]
```

- [ ] **Step 3: 更新 `providers/__init__.py`**

```python
"""matmaster.providers -- Concrete LLM provider implementations."""

from .bedrock_provider import BedrockProvider
from .chat_completions_provider import ChatCompletionsProvider
from .llm_factory import build_provider

__all__ = ["BedrockProvider", "ChatCompletionsProvider", "build_provider"]
```

- [ ] **Step 4: 更新 `llm_factory.py` 的 import 与符号**

把 `from matmaster.providers.openai_provider import (AnthropicPromptCacheOptions, OpenAIProvider)` 改为从 `chat_completions_provider` 导入 `ChatCompletionsProvider`；把文件内所有 `OpenAIProvider` 类型注解 / 构造改为 `ChatCompletionsProvider`（含 `LLMProviderBundle.provider` 联合类型、`_build_openai_provider` 的返回与构造）。把内部 helper `_build_openai_provider` 重命名为 `_build_chat_completions_provider`（调用点同步）。

- [ ] **Step 5: 改名并更新 provider 测试文件**

```bash
git mv tests/matmaster/providers/test_openai_provider.py tests/matmaster/providers/test_chat_completions_provider.py
git mv tests/matmaster/providers/test_openai_provider_errors.py tests/matmaster/providers/test_chat_completions_provider_errors.py
git mv tests/matmaster/providers/test_openai_provider_tool_choice.py tests/matmaster/providers/test_chat_completions_provider_tool_choice.py
git mv tests/matmaster/providers/test_openai_provider_prompt_cache.py tests/matmaster/providers/test_chat_completions_provider_prompt_cache.py
```

在这 4 个文件以及 `tests/matmaster/providers/test_llm_factory.py`、`tests/matmaster/integration/test_tool_protocol_guardrails.py`、`tests/matmaster/devshell/test_devshell_mcp_skill_filter.py` 里，把 `from matmaster.providers.openai_provider import ...` 改为 `chat_completions_provider`，把符号 `OpenAIProvider` 改为 `ChatCompletionsProvider`。

- [ ] **Step 6: 更新引用旧名的 docstring/注释**

这几处在代码注释/docstring 里提旧名，grep 会被它们卡住，一并更新为 `ChatCompletionsProvider`：
- `matmaster/config/llm.py:164`（`build_extra_kwargs` docstring 提 `OpenAIProvider.__init__`）
- `matmaster/providers/bedrock_provider.py:7`（模块 docstring 的 `:class:`OpenAIProvider``）
- `matmaster/providers/llm_factory.py:95`（`_build_openai_provider` 的 docstring；该 helper 已在 Step 4 改名为 `_build_chat_completions_provider`，docstring 同步）
- `matmaster/providers/llm_factory.py:3`（模块 docstring 顶部 `resolve_route -> OpenAIProvider or BedrockProvider`）

- [ ] **Step 7: 全仓搜索残留旧名**

Run: `grep -rn "OpenAIProvider\|openai_provider" matmaster src tests scripts --include="*.py"`
Expected: 无输出（全部已迁移）。

- [ ] **Step 8: 运行 provider/ factory / lint 相关测试**

Run: `uv run pytest tests/matmaster/providers/ tests/scripts/test_lint_no_arguments_mutation.py -q`
Expected: PASS。

- [ ] **Step 9: 跑 pre-commit 的 lint hook 确认不被旧 allowlist 卡住**

Run: `uv run python scripts/lint_no_arguments_mutation.py`
Expected: 退出码 0、无违规输出（新路径已 allowlist）。

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -m "refactor(providers): rename OpenAIProvider to ChatCompletionsProvider"
```

---

## Task 5: Factory 按 transport 显式分发

**Files:**
- Modify: `matmaster/providers/llm_factory.py`
- Test: `tests/matmaster/providers/test_llm_factory.py`

- [ ] **Step 1: 写失败测试 — 按 transport 分发**

在 `tests/matmaster/providers/test_llm_factory.py` 追加（夹具 `llm_config` 已是 litellm provider）：

```python
from matmaster.providers.bedrock_provider import BedrockProvider
from matmaster.providers.chat_completions_provider import ChatCompletionsProvider


class TestTransportDispatch:
    def test_litellm_route_builds_chat_completions(self, llm_config: LLMConfig) -> None:
        provider = build_provider(llm_config, model_override="claude-opus-4-6")
        assert isinstance(provider, ChatCompletionsProvider)

    def test_bedrock_transport_builds_bedrock(self) -> None:
        cfg = LLMConfig(
            profiles={
                "bed": LLMProfileConfig(
                    provider="bedrock",
                    model="arn:aws:bedrock:us-east-1:0:inference-profile/x",
                    context_limit=200_000,
                ),
            },
            routes={"bedrock-x": LLMRouteConfig(profile="bed")},
            default="bed",
        )
        provider = build_provider(cfg, model_override="bedrock-x")
        assert isinstance(provider, BedrockProvider)

    def test_dispatch_follows_transport_not_provider_name(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """判别性测试：把 litellm 的 transport 临时改成 bedrock_converse。
        旧实现按 provider=='bedrock' 分发 → 仍走 ChatCompletions（断言失败）；
        新实现按 resolved.transport 分发 → 走 Bedrock（断言通过）。
        """
        from matmaster.config import llm as llm_mod

        monkeypatch.setitem(llm_mod.PROVIDER_TRANSPORT, "litellm", "bedrock_converse")
        cfg = LLMConfig(
            profiles={
                "p": LLMProfileConfig(
                    provider="litellm", model="m", context_limit=200_000
                ),
            },
            routes={"r": LLMRouteConfig(profile="p")},
            default="p",
        )
        provider = build_provider(cfg, model_override="r")
        assert isinstance(provider, BedrockProvider)
```

- [ ] **Step 2: 运行确认现状（判别性测试应失败）**

Run: `uv run pytest tests/matmaster/providers/test_llm_factory.py::TestTransportDispatch -q`
Expected: `test_litellm_route_builds_chat_completions` / `test_bedrock_transport_builds_bedrock` PASS（旧实现对正常映射本就能过）；**`test_dispatch_follows_transport_not_provider_name` FAIL**（旧实现按 provider 名分发，litellm→ChatCompletions ≠ Bedrock）。这一红正是 Task 5 要消除的。

- [ ] **Step 3: 实现 — 用 `resolved.transport` 分发**

在 `matmaster/providers/llm_factory.py` 的 `build_provider_bundle` 里，把 `if profile.provider == "bedrock":` 改为按 transport 分发。结构改为：

```python
    transport = resolved.transport
    if transport == "chat_completions":
        provider = _build_chat_completions_provider(
            profile,
            model=resolved.model,
            api_key=profile.api_key,
            base_url=profile.base_url,
            extra_kwargs=profile.build_extra_kwargs(),
        )
    elif transport == "bedrock_converse":
        region = (
            (profile.bedrock_region or "").strip()
            or os.environ.get("AWS_REGION")
            or os.environ.get("AWS_DEFAULT_REGION")
            or "us-east-1"
        )
        provider = BedrockProvider(
            model_id=resolved.model,
            region=region,
            temperature=profile.effective_temperature(),
            max_tokens=profile.max_tokens,
            timeout=profile.timeout,
            stream_timeout=profile.stream_timeout,
            stream_idle_timeout=profile.stream_idle_timeout,
            max_retries=profile.max_retries,
            retry_delay=profile.retry_delay,
        )
    else:
        raise ValueError(f"unsupported transport: {transport!r}")

    return LLMProviderBundle(
        provider=provider,
        model=resolved.model,
        model_profile=resolved.profile_key,
        model_route=resolved.route_key,
        provider_name=profile.provider,
        model_family=profile.effective_family(),
        context_limit=profile.context_limit,
        context_limit_source="profile",
    )
```

删除原来的 bedrock `if` 分支与其下重复的 `_build_openai_provider` 分支（合并为上面的单一 dispatch + 单一 return）。

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/matmaster/providers/test_llm_factory.py -q`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add matmaster/providers/llm_factory.py tests/matmaster/providers/test_llm_factory.py
git commit -m "feat(providers): dispatch factory by transport instead of provider name"
```

---

## Task 6: 补全 `LLMProvider` Protocol + provider property 收口 + Protocol mock 更新

这三件事必须**原子落地**：给 runtime_checkable Protocol 加数据成员后，缺属性的 mock 会立刻不满足 `isinstance`。

**Files:**
- Modify: `matmaster/types/llm_provider.py`
- Modify: `matmaster/providers/chat_completions_provider.py`
- Modify: `matmaster/providers/bedrock_provider.py`
- Modify: `tests/matmaster/types/test_llm_provider.py`
- Modify: `tests/conftest.py`
- Test: `tests/matmaster/types/test_llm_provider.py`

- [ ] **Step 1: 写失败测试 — 属性为具体 float / Protocol 仍满足**

在 `tests/matmaster/types/test_llm_provider.py` 追加：

```python
def test_chat_completions_provider_timeout_attrs_concrete() -> None:
    from matmaster.providers.chat_completions_provider import ChatCompletionsProvider

    p = ChatCompletionsProvider(model="m", api_key="k", base_url=None, timeout=300.0)
    assert p.stream_timeout == 300.0
    assert p.stream_idle_timeout == 300.0
    assert isinstance(p.max_retries, int)
    assert isinstance(p.retry_delay, float)
    assert isinstance(p, LLMProvider)


def test_complete_mock_still_satisfies_protocol() -> None:
    assert isinstance(CompleteLLMProvider(), LLMProvider)
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/matmaster/types/test_llm_provider.py::test_chat_completions_provider_timeout_attrs_concrete -q`
Expected: FAIL（`stream_timeout` 为 None 而非 300.0；且加属性后 `CompleteLLMProvider` isinstance 会变 False，下一步一并解决）。

- [ ] **Step 3: Protocol 增加四个成员（用 `@property` 而非 annotation-only）**

**必须用 property 声明**，不能用 annotation-only 数据成员。原因：`matmaster/validation.py` 的 `validate_async_protocol()` 会遍历 `__protocol_attrs__` 并对每个做 `inspect.getattr_static(protocol_cls, attr)`；annotation-only attr 在 class 上无实际属性对象 → `getattr_static` 抛 `AttributeError`，直接打断 `test_validation.py` 那批用例。而它对 `property` 有显式 `if isinstance(proto_static, property): continue` 跳过逻辑。property 声明同时匹配两个真实 provider 的实现形态。

在 `matmaster/types/llm_provider.py` 的 `LLMProvider` 类体里（async 方法之前）加：

```python
    @property
    def stream_timeout(self) -> float: ...

    @property
    def stream_idle_timeout(self) -> float: ...

    @property
    def max_retries(self) -> int: ...

    @property
    def retry_delay(self) -> float: ...
```

- [ ] **Step 4: 两个 provider 的 timeout property 收口为具体 float**

在 `matmaster/providers/chat_completions_provider.py` 把两个 property 改为折进 `_timeout` 兜底：

```python
    @property
    def stream_timeout(self) -> float:
        return self._stream_timeout if self._stream_timeout is not None else self._timeout

    @property
    def stream_idle_timeout(self) -> float:
        return (
            self._stream_idle_timeout
            if self._stream_idle_timeout is not None
            else self._timeout
        )
```

在 `matmaster/providers/bedrock_provider.py` 做同样改动（注意 bedrock 的 `self._timeout` 是 int，作为 float 返回兼容）：

```python
    @property
    def stream_timeout(self) -> float:
        return self._stream_timeout if self._stream_timeout is not None else self._timeout

    @property
    def stream_idle_timeout(self) -> float:
        return (
            self._stream_idle_timeout
            if self._stream_idle_timeout is not None
            else self._timeout
        )
```

`max_retries` / `retry_delay` 两个 property 已是具体值，不动。

- [ ] **Step 5: 更新 Protocol 一致性 mock**

在 `tests/matmaster/types/test_llm_provider.py` 的 `CompleteLLMProvider` 类体加类级属性：

```python
class CompleteLLMProvider:
    """Mock that satisfies the async LLMProvider Protocol."""

    stream_timeout: float = 300.0
    stream_idle_timeout: float = 300.0
    max_retries: int = 3
    retry_delay: float = 1.0

    async def __aenter__(self) -> CompleteLLMProvider:
        ...
```

在 `tests/conftest.py` 的 `MockAsyncLLMProvider` 类体加同样四个类级属性：

```python
class MockAsyncLLMProvider:
    """Async mock satisfying LLMProvider Protocol for testing."""

    stream_timeout: float = 300.0
    stream_idle_timeout: float = 300.0
    max_retries: int = 3
    retry_delay: float = 1.0

    def __init__(
        ...
```

在 `tests/matmaster/types/test_runtime.py` 的 `_MockLLMProvider` 类体（line 36 起）加同样四个类级属性——`test_llm_provider_typed_as_protocol`（line 226/230）对它做 `isinstance(provider, LLMProvider)`，不补会变 False：

```python
class _MockLLMProvider:
    stream_timeout: float = 300.0
    stream_idle_timeout: float = 300.0
    max_retries: int = 3
    retry_delay: float = 1.0

    async def chat(
        ...
```

> 三个被 `isinstance(..., LLMProvider)` 检查的 mock 都要补：`CompleteLLMProvider`、`MockAsyncLLMProvider`、`_MockLLMProvider`。`test_validation.py` 那批用 `validate_async_protocol` 的 mock **无需改**（property 被 validate 跳过）。
> 不改 `BillingLLMProvider` / `UsageCollectingProvider`：二者 `__getattr__` 全量透传到 inner，运行时读属性即可；matmaster/src 内无运行时 `isinstance(provider, LLMProvider)`。

- [ ] **Step 6: 运行确认通过（含 validation/runtime 联动测试）**

Run: `uv run pytest tests/matmaster/types/test_llm_provider.py tests/matmaster/types/test_runtime.py tests/matmaster/test_validation.py -q`
Expected: PASS（property 声明使 `validate_async_protocol` 跳过这四个成员、不 crash；三个 mock 补属性后 isinstance 仍 True）。

- [ ] **Step 7: Commit**

```bash
git add matmaster/types/llm_provider.py matmaster/providers/chat_completions_provider.py matmaster/providers/bedrock_provider.py tests/matmaster/types/test_llm_provider.py tests/matmaster/types/test_runtime.py tests/conftest.py
git commit -m "feat(providers): declare timeout/retry on LLMProvider protocol, concretize properties"
```

---

## Task 7: kernel 去私有 `_timeout` 读取

纯重构：`call_llm_streaming` 改为直读 Protocol 公共属性。回归靠既有 AgentKernel 集成测试（经 conftest `MockAsyncLLMProvider`，Task 6 已补属性，走 `call_llm_streaming`）+ grep 校验私有读取已消除。不捏造新夹具。

**Files:**
- Modify: `matmaster/core/agent_llm_stream.py:262-267`

- [ ] **Step 1: 建立回归基线 — 跑经 call_llm_streaming 的集成测试**

这些测试经 `AgentKernel._call_llm_streaming → call_llm_streaming` 跑通流式路径，是本次重构的回归网：

Run: `uv run pytest tests/matmaster/core/test_agent_kernel_stream.py tests/matmaster/core/test_agent_kernel_empty_response_sentinels.py tests/matmaster/core/test_agent_kernel_usage_events.py -q`
Expected: PASS（重构前基线）。

- [ ] **Step 2: 实现 — 改读公共属性**

把 `matmaster/core/agent_llm_stream.py:262-267` 改为：

```python
    provider = kernel_resources.llm_provider
    current_timeout = provider.stream_timeout
    max_retries = provider.max_retries
    retry_delay = provider.retry_delay
```

删除 `getattr(provider, "stream_timeout", None) or getattr(provider, "_timeout", 300.0)` 与对 `max_retries`/`retry_delay` 的 `getattr` 兜底。因 Task 6 保证 `stream_timeout` 永不为 None，行为等价。

- [ ] **Step 3: grep 校验私有读取已消除**

Run: `grep -n "_timeout" matmaster/core/agent_llm_stream.py`
Expected: 仅剩 `current_timeout` 等局部变量，无 `getattr(provider, "_timeout"` 私有读取。

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/matmaster/core/test_agent_kernel_stream.py tests/matmaster/core/test_agent_kernel_empty_response_sentinels.py tests/matmaster/core/test_agent_kernel_usage_events.py tests/matmaster/core/test_stream_drain.py -q`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add matmaster/core/agent_llm_stream.py
git commit -m "refactor(core): read provider timeout/retry from public protocol attrs"
```

---

## Task 8: BYOK 身份收束 `provider=byok`

**Files:**
- Modify: `matmaster/providers/llm_factory.py`（`build_byok_provider_bundle`）
- Test: `tests/matmaster/providers/test_byok_provider.py`

- [ ] **Step 1: 写失败测试 — BYOK 身份为 byok**

在 `tests/matmaster/providers/test_byok_provider.py` 追加：

```python
from matmaster.config.llm import PROVIDER_TRANSPORT
from matmaster.providers.chat_completions_provider import ChatCompletionsProvider
from matmaster.providers.llm_factory import build_byok_provider_bundle


def test_byok_bundle_identity_is_byok() -> None:
    bundle = build_byok_provider_bundle(
        model="user-model",
        api_key="sk-user",
        base_url="https://user.example/v1",
        credential_id="cred-1",
    )
    assert bundle.provider_name == "byok"
    assert isinstance(bundle.provider, ChatCompletionsProvider)
    assert PROVIDER_TRANSPORT["byok"] == "chat_completions"
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/matmaster/providers/test_byok_provider.py::test_byok_bundle_identity_is_byok -q`
Expected: FAIL（`provider_name == "openai"`）。

- [ ] **Step 3: 实现 — 临时 profile 与 bundle 改 byok**

在 `matmaster/providers/llm_factory.py` 的 `build_byok_provider_bundle` 里：
- 构造临时 `LLMProfileConfig(...)` 的 `provider="openai"` 改为 `provider="byok"`。
- 返回的 `LLMProviderBundle(...)` 的 `provider_name="openai"` 改为 `provider_name="byok"`。

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/matmaster/providers/test_byok_provider.py -q`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add matmaster/providers/llm_factory.py tests/matmaster/providers/test_byok_provider.py
git commit -m "feat(providers): converge BYOK identity to provider=byok"
```

---

## 收尾：全量回归

- [ ] **Step 1: 跑完整测试套件**

Run: `uv run pytest -q`
Expected: 全绿。

- [ ] **Step 2: 跑 pre-commit（确认 lint allowlist / 行数等钩子通过）**

Run: `uv run pre-commit run --all-files`
Expected: 全部 hook 通过（尤其 `lint-no-arguments-mutation` 不再因旧路径误报/漏报）。

- [ ] **Step 3: 验收对照 spec 完成标准**

逐条核对 `docs/superpowers/specs/2026-06-06-provider-stage1-boundary-design.md` §9：
- routes 解析出明确 provider + transport；未知 provider 启动期 fail-fast。
- 无 `OpenAIProvider` 残留；kernel 不读 `_timeout`。
- BYOK `provider_name=="byok"`。
- chat_completions / bedrock 行为等价。
