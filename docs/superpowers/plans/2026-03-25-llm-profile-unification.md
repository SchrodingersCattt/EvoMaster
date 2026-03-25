# LLM Profile Unification Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unify frontend and backend LLM profile naming, add missing `claude-sonnet-4-6` route, remove unused profiles.

**Architecture:** Rename backend profile `litellm` -> `opus`, add `sonnet` profile, remove `azure_gpt5`/`deepseek_reasoner`, update all config files and code references. Config-driven change with no new APIs or architectural shifts.

**Tech Stack:** Python (Pydantic, YAML), pytest

**Spec:** `docs/superpowers/specs/2026-03-25-llm-profile-unification-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `matmaster_config/llm_config.yaml` | Rewrite | Canonical LLM profiles + routes |
| `matmaster_config/config.yaml` | Edit L6 | Default agent LLM reference |
| `configs/mat_master/config.yaml` | Edit llm section | Legacy config sync for MonitorJobTool |
| `configs/mat_master/llm_config.yaml` | Rewrite | Legacy llm_config sync |
| `matmaster/config/llm.py` | Edit L9,11,18,169,179 | Pydantic defaults + docstring |
| `evomaster/agent/tools/builtin/monitor_job/_llm.py` | Edit L76 | Hardcoded fallback |
| `src/models/chat.py` | Edit L160 | Comment |
| `matmaster/config/loader.py` | Edit L10 | Docstring |
| `tests/matmaster/config/test_llm.py` | Edit | Profile key renames + add sonnet test |
| `tests/matmaster/config/test_loader.py` | Edit | Profile key renames |
| `tests/matmaster/config/test_config_consolidation.py` | Edit L28 | Assertion value |
| `tests/matmaster/providers/test_llm_factory.py` | Rewrite fixture | Replace azure with sonnet |
| `tests/matmaster/integration/test_llm_factory.py` | Rewrite fixture | Replace azure with sonnet |

---

## Chunk 1: Config Files

### Task 1: Rewrite matmaster_config/llm_config.yaml

**Files:**
- Modify: `matmaster_config/llm_config.yaml` (full rewrite)

- [ ] **Step 1: Rewrite the file**

Replace entire content with:

```yaml
# LLM 配置（独立文件）
# profiles: 定义每个 LLM 后端的完整参数
# routes: 前端 model_override 字符串 → 内部 profile 映射
# default: 未指定时使用的默认 profile key
#
# 鉴权凭据从 .env 读取，使用 ${VAR} 引用

profiles:
  opus:
    provider: "openai"
    model: "claude-opus-4-6"
    api_key: "${LITELLM_PROXY_API_KEY}"
    base_url: "${LITELLM_PROXY_API_BASE}"
    thinking_effort: "high"
    reasoning_protocol: "anthropic_adaptive_thinking"
    temperature_policy: "force_one_when_reasoning"
    temperature: 0.7
    timeout: 300
    stream_timeout: 20
    stream_idle_timeout: 30
    max_retries: 3
    retry_delay: 1.0

  sonnet:
    provider: "openai"
    model: "claude-sonnet-4-6"
    api_key: "${LITELLM_PROXY_API_KEY}"
    base_url: "${LITELLM_PROXY_API_BASE}"
    thinking_effort: "high"
    reasoning_protocol: "anthropic_adaptive_thinking"
    temperature_policy: "force_one_when_reasoning"
    temperature: 0.7
    timeout: 300
    stream_timeout: 20
    stream_idle_timeout: 30
    max_retries: 3
    retry_delay: 1.0

  haiku:
    provider: "openai"
    model: "claude-haiku-4-5"
    api_key: "${LITELLM_PROXY_API_KEY}"
    base_url: "${LITELLM_PROXY_API_BASE}"
    timeout: 120
    stream_timeout: 15
    stream_idle_timeout: 20
    max_retries: 3
    retry_delay: 1.0

  gemini:
    provider: "openai"
    model: "gemini-3-flash-preview"
    api_key: "${LITELLM_PROXY_API_KEY}"
    base_url: "${LITELLM_PROXY_API_BASE}"
    reasoning_protocol: "openai_reasoning_effort"
    thinking_effort: "high"
    timeout: 120
    stream_timeout: 15
    stream_idle_timeout: 20
    max_retries: 3
    retry_delay: 1.0

  compaction:
    provider: "openai"
    model: "gemini-3-flash-preview"
    api_key: "${LITELLM_PROXY_API_KEY}"
    base_url: "${LITELLM_PROXY_API_BASE}"
    temperature: 0.3
    max_tokens: 4096
    timeout: 120
    stream_timeout: 15
    stream_idle_timeout: 20
    max_retries: 2
    retry_delay: 1.0

# 路由表：前端 model_override 字符串 → 内部 profile
routes:
  "claude-opus-4-6":
    profile: opus
  "claude-sonnet-4-6":
    profile: sonnet
  "claude-haiku-4-5":
    profile: haiku
  "gemini-3-flash-preview":
    profile: gemini

default: "opus"
```

- [ ] **Step 2: Update matmaster_config/config.yaml**

Change line 6: `llm: "litellm"` -> `llm: "opus"`

- [ ] **Step 3: Commit**

```bash
git add matmaster_config/llm_config.yaml matmaster_config/config.yaml
git commit -m "config: rename litellm->opus, add sonnet, remove azure/deepseek profiles"
```

### Task 2: Sync legacy configs/mat_master/

**Files:**
- Modify: `configs/mat_master/config.yaml` (llm section + agents.general.llm)
- Modify: `configs/mat_master/llm_config.yaml` (full sync)

- [ ] **Step 1: Update configs/mat_master/config.yaml llm section**

In the `llm:` section (lines 10-115):
1. Rename `litellm:` block (lines 11-27) to `opus:`, remove `model_family` (L14) and `fallback_group` (L19)
2. Add `sonnet:` block after opus (see below)
3. Delete `azure:` block (lines 29-46)
4. Delete `deepseek:` block (lines 48-64)
5. In `haiku:` block (lines 67-80): remove `model_family` (L70), `fallback_group` (L73), `reasoning_protocol: ~` (L74), `thinking_effort: ~` (L75)
6. In `gemini:` block (lines 83-96): remove `model_family` (L86), `fallback_group` (L89)
7. In `compaction:` block (lines 99-113): remove `model_family` (L103), `fallback_group` (L106)
8. Change `default: "litellm"` to `default: "opus"` (line 115)
9. Change `agents.general.llm: "litellm"` to `"opus"` (line 122)
10. Update header comments (lines 2, 6, 9): remove Azure/DeepSeek references

The `sonnet:` block to insert after `opus:`:

```yaml
  sonnet:
    provider: "openai"
    model: "claude-sonnet-4-6"
    api_key: "${LITELLM_PROXY_API_KEY}"
    base_url: "${LITELLM_PROXY_API_BASE}"
    thinking_effort: "high"
    reasoning_protocol: "anthropic_adaptive_thinking"
    temperature_policy: "force_one_when_reasoning"
    temperature: 0.7
    timeout: 300
    stream_timeout: 90
    stream_idle_timeout: 60
    max_retries: 3
    retry_delay: 1.0
```

Note: timeout values in legacy config differ from matmaster_config (90/60 vs 20/30). Keep the legacy values as-is for MonitorJobTool compatibility.

- [ ] **Step 2: Copy matmaster_config/llm_config.yaml to configs/mat_master/llm_config.yaml**

```bash
cp matmaster_config/llm_config.yaml configs/mat_master/llm_config.yaml
```

- [ ] **Step 3: Commit**

```bash
git add configs/mat_master/config.yaml configs/mat_master/llm_config.yaml
git commit -m "config: sync legacy configs/mat_master/ with new profile names"
```

---

## Chunk 2: Python Code Changes

### Task 3: Update matmaster/config/llm.py

**Files:**
- Modify: `matmaster/config/llm.py:9,11,18,169,179`

- [ ] **Step 1: Update docstring (lines 8-18)**

Change:
```python
    llm:
      litellm:
        provider: "openai"
        model: "claude-opus-4-6"
        model_family: "claude-4.6"
        api_key: "${LITELLM_PROXY_API_KEY}"
        base_url: "${LITELLM_PROXY_API_BASE}"
        thinking_effort: "high"
        reasoning_protocol: "anthropic_adaptive_thinking"
        ...
      default: "litellm"
```

To:
```python
    llm:
      opus:
        provider: "openai"
        model: "claude-opus-4-6"
        api_key: "${LITELLM_PROXY_API_KEY}"
        base_url: "${LITELLM_PROXY_API_BASE}"
        thinking_effort: "high"
        reasoning_protocol: "anthropic_adaptive_thinking"
        ...
      default: "opus"
```

- [ ] **Step 2: Update default field (line 169)**

Change: `default: str = "litellm"` -> `default: str = "opus"`

- [ ] **Step 3: Update legacy fallback (line 179)**

Change: `default = data.pop("default", "litellm")` -> `default = data.pop("default", "opus")`

- [ ] **Step 4: Commit**

```bash
git add matmaster/config/llm.py
git commit -m "refactor: update LLM default profile from litellm to opus"
```

### Task 4: Update monitor_job fallback and comments

**Files:**
- Modify: `evomaster/agent/tools/builtin/monitor_job/_llm.py:76`
- Modify: `src/models/chat.py:160,163`
- Modify: `src/services/stream_service.py:621`
- Modify: `matmaster/config/loader.py:10`

- [ ] **Step 1: Fix _llm.py fallback (line 76)**

Change:
```python
    llm_alias = (alias or llm_section.get('default') or 'litellm').strip()
```
To:
```python
    llm_alias = (alias or llm_section.get('default') or 'opus').strip()
```

- [ ] **Step 2: Update chat.py comment (line 159-160)**

Change:
```python
    llm: Optional[str] = (
        None  # 可选，本轮使用的 LLM 配置块（如 litellm/azure/deepseek），不传则用 agent 默认
    )
```
To:
```python
    llm: Optional[str] = (
        None  # 可选，本轮使用的 LLM 配置块（如 opus/sonnet/haiku），不传则用 agent 默认
    )
```

- [ ] **Step 3: Update chat.py model comment (line 162-163)**

Change:
```python
    model: Optional[str] = (
        None  # 可选，本轮使用的模型名（如 gemini-3-flash-preview、azure/gpt-5），覆盖所选 LLM 配置里的 model
    )
```
To:
```python
    model: Optional[str] = (
        None  # 可选，本轮使用的模型名（如 gemini-3-flash-preview、claude-sonnet-4-6），覆盖所选 LLM 配置里的 model
    )
```

- [ ] **Step 4: Update stream_service.py comment (line 619-621)**

Change:
```python
        model = (
            req.model or ''
        ).strip() or None  # 本轮模型名，如 gemini-3-flash-preview / azure/gpt-5
```
To:
```python
        model = (
            req.model or ''
        ).strip() or None  # 本轮模型名，如 gemini-3-flash-preview / claude-sonnet-4-6
```

- [ ] **Step 5: Update loader.py docstring (line 10)**

Change:
```python
    llm = load_llm_config("configs/mat_master/config.yaml")
```
To:
```python
    llm = load_llm_config("matmaster_config/llm_config.yaml")
```

- [ ] **Step 6: Commit**

```bash
git add evomaster/agent/tools/builtin/monitor_job/_llm.py src/models/chat.py src/services/stream_service.py matmaster/config/loader.py
git commit -m "refactor: update hardcoded litellm references and stale docstrings"
```

---

## Chunk 3: Test Updates

### Task 5: Update tests/matmaster/config/test_llm.py

**Files:**
- Modify: `tests/matmaster/config/test_llm.py`

- [ ] **Step 1: Update TestLLMConfigModelValidator (lines 148-158)**

Replace the `test_flat_yaml_dict` fixture:
```python
    def test_flat_yaml_dict(self) -> None:
        raw = {
            "opus": {"provider": "openai", "model": "claude-opus-4-6"},
            "sonnet": {"provider": "openai", "model": "claude-sonnet-4-6"},
            "default": "opus",
        }
        cfg = LLMConfig.model_validate(raw)
        assert cfg.default == "opus"
        assert "opus" in cfg.profiles
        assert "sonnet" in cfg.profiles
        assert cfg.profiles["opus"].model == "claude-opus-4-6"
```

- [ ] **Step 2: Update TestResolveProfile fixture (lines 172-178)**

```python
    @pytest.fixture()
    def llm_config(self) -> LLMConfig:
        return LLMConfig.model_validate({
            "opus": {"model": "claude-opus-4-6", "temperature": 0.7},
            "sonnet": {"model": "claude-sonnet-4-6", "temperature": 0.5},
            "default": "opus",
        })
```

Update all assertions in this class:
- `test_no_override_uses_default`: `key == "opus"`, `model == "claude-opus-4-6"`
- `test_no_override_with_custom_default_key`: `default_key="sonnet"`, `key == "sonnet"`, `model == "claude-sonnet-4-6"`
- `test_override_match_by_model_name`: `model_override="claude-sonnet-4-6"`, `key == "sonnet"`, `temperature == 0.5`
- `test_override_match_by_profile_key`: `model_override="sonnet"`, `key == "sonnet"`
- `test_override_fallback_to_default`: assertion `key == "opus"`

- [ ] **Step 3: Update TestLLMRouteConfig (lines 214-222)**

```python
    def test_route_with_model(self) -> None:
        r = LLMRouteConfig(profile="opus", model="claude-sonnet-4-6")
        assert r.profile == "opus"
        assert r.model == "claude-sonnet-4-6"

    def test_route_without_model(self) -> None:
        r = LLMRouteConfig(profile="opus")
        assert r.profile == "opus"
        assert r.model is None
```

- [ ] **Step 4: Update TestLLMConfigWithRoutes fixture (lines 228-241)**

```python
    @pytest.fixture()
    def cfg(self) -> LLMConfig:
        return LLMConfig.model_validate({
            "profiles": {
                "opus": {"provider": "openai", "model": "claude-opus-4-6"},
                "sonnet": {"provider": "openai", "model": "claude-sonnet-4-6"},
            },
            "routes": {
                "claude-opus-4-6": {"profile": "opus"},
                "claude-sonnet-4-6": {"profile": "sonnet"},
            },
            "default": "opus",
        })
```

Update all assertions in this class:
- `test_routes_parsed`: `len == 2`, `routes["claude-opus-4-6"].profile == "opus"`
- `test_resolve_route_hit`: `profile_key="opus"`
- `test_resolve_route_alias`: replace with sonnet route test: `model_override="claude-sonnet-4-6"`, assert `profile_key == "sonnet"`, `model == "claude-sonnet-4-6"`
- `test_resolve_route_llm_override_as_profile_key`: `llm_override="sonnet"`, assert `profile_key == "sonnet"`, `model == "claude-sonnet-4-6"`
- `test_resolve_route_default_path`: `profile_key == "opus"`
- `test_resolve_route_custom_default_key`: `default_key="sonnet"`, assert `profile_key == "sonnet"`, `model == "claude-sonnet-4-6"`
- `test_resolve_route_model_override_takes_precedence`: `model_override="claude-sonnet-4-6"`, `llm_override="opus"`, assert `route_key == "claude-sonnet-4-6"`, `profile_key == "sonnet"`
- `test_resolve_route_route_model_overrides_profile_model`: remove (no longer needed -- routes don't have model override in new schema)
- `test_resolve_route_route_without_model_uses_profile_model`: model comes from profile, assert `model == "claude-opus-4-6"`

- [ ] **Step 5: Update TestLLMConfigValidation (lines 299-315)**

```python
    def test_route_references_nonexistent_profile(self) -> None:
        with pytest.raises(ValueError, match="route.*references profile.*ghost"):
            LLMConfig.model_validate({
                "profiles": {"opus": {"model": "m1"}},
                "routes": {"r1": {"profile": "ghost"}},
                "default": "opus",
            })

    def test_default_references_nonexistent_profile(self) -> None:
        with pytest.raises(ValueError, match="default profile.*missing"):
            LLMConfig.model_validate({
                "profiles": {"opus": {"model": "m1"}},
                "default": "missing",
            })
```

- [ ] **Step 6: Update TestLLMConfigLegacyCompat (lines 318-344)**

Replace all `"litellm"` with `"opus"` in all three tests.

- [ ] **Step 7: Add sonnet route regression test**

Add new test class after `TestLLMConfigWithRoutes`:

```python
class TestSonnetRouteRegression:
    """Regression: claude-sonnet-4-6 must resolve without error."""

    def test_sonnet_route_resolves(self) -> None:
        cfg = LLMConfig.model_validate({
            "profiles": {
                "opus": {"provider": "openai", "model": "claude-opus-4-6"},
                "sonnet": {"provider": "openai", "model": "claude-sonnet-4-6"},
            },
            "routes": {
                "claude-opus-4-6": {"profile": "opus"},
                "claude-sonnet-4-6": {"profile": "sonnet"},
            },
            "default": "opus",
        })
        r = cfg.resolve_route(model_override="claude-sonnet-4-6")
        assert r == ResolvedLLMRoute(
            route_key="claude-sonnet-4-6",
            profile_key="sonnet",
            provider="openai",
            model="claude-sonnet-4-6",
        )
```

- [ ] **Step 8: Run test_llm.py**

Run: `cd matmaster-evo && uv run pytest tests/matmaster/config/test_llm.py -v`
Expected: All PASS

- [ ] **Step 9: Commit**

```bash
git add tests/matmaster/config/test_llm.py
git commit -m "test: update test_llm.py for opus/sonnet profile rename"
```

### Task 6: Update tests/matmaster/config/test_loader.py

**Files:**
- Modify: `tests/matmaster/config/test_loader.py:14-33,48-49`

- [ ] **Step 1: Update _YAML_CONTENT fixture (lines 13-34)**

```python
_YAML_CONTENT = """\
llm:
  opus:
    provider: "openai"
    model: "claude-opus-4-6"
    temperature: 0.7
  sonnet:
    provider: "openai"
    model: "claude-sonnet-4-6"
    temperature: 0.5
  default: "opus"

agents:
  general:
    llm: "opus"
    max_turns: 200
    tools:
      builtin: ["*"]
      mcp: "*"
    context:
      max_tokens: 180000
"""
```

- [ ] **Step 2: Update assertions (lines 48-53)**

```python
    def test_from_yaml_path(self, yaml_file: Path) -> None:
        cfg = load_llm_config(yaml_file)
        assert isinstance(cfg, LLMConfig)
        assert cfg.default == "opus"
        assert cfg.profiles["opus"].model == "claude-opus-4-6"

    def test_from_string_path(self, yaml_file: Path) -> None:
        cfg = load_llm_config(str(yaml_file))
        assert "sonnet" in cfg.profiles
```

- [ ] **Step 3: Run and commit**

Run: `cd matmaster-evo && uv run pytest tests/matmaster/config/test_loader.py -v`
Expected: All PASS

```bash
git add tests/matmaster/config/test_loader.py
git commit -m "test: update test_loader.py for opus/sonnet profile rename"
```

### Task 7: Update tests/matmaster/config/test_config_consolidation.py

**Files:**
- Modify: `tests/matmaster/config/test_config_consolidation.py:28`

- [ ] **Step 1: Update assertion (line 28)**

Change:
```python
        assert cleaned_config["agents"]["general"]["llm"] == "litellm"
```
To:
```python
        assert cleaned_config["agents"]["general"]["llm"] == "opus"
```

- [ ] **Step 2: Run and commit**

Run: `cd matmaster-evo && uv run pytest tests/matmaster/config/test_config_consolidation.py -v`
Expected: All PASS

```bash
git add tests/matmaster/config/test_config_consolidation.py
git commit -m "test: update test_config_consolidation.py for opus profile rename"
```

### Task 8: Update tests/matmaster/providers/test_llm_factory.py

**Files:**
- Modify: `tests/matmaster/providers/test_llm_factory.py` (fixture rewrite)

- [ ] **Step 1: Rewrite fixture (lines 18-52)**

Replace the fixture with opus + sonnet profiles:

```python
@pytest.fixture()
def llm_config() -> LLMConfig:
    """LLMConfig with 2 profiles and 2 routes for testing."""
    return LLMConfig(
        profiles={
            "opus": LLMProfileConfig(
                provider="openai",
                model="claude-opus-4-6",
                api_key="sk-test-opus",
                base_url="http://litellm-proxy",
                thinking_effort="high",
                reasoning_protocol="anthropic_adaptive_thinking",
                temperature_policy="force_one_when_reasoning",
                temperature=0.7,
            ),
            "sonnet": LLMProfileConfig(
                provider="openai",
                model="claude-sonnet-4-6",
                api_key="sk-test-sonnet",
                base_url="http://litellm-proxy",
                thinking_effort="high",
                reasoning_protocol="anthropic_adaptive_thinking",
                temperature_policy="force_one_when_reasoning",
                temperature=0.7,
            ),
        },
        routes={
            "claude-opus-4-6": LLMRouteConfig(profile="opus"),
            "claude-sonnet-4-6": LLMRouteConfig(profile="sonnet"),
        },
        default="opus",
    )
```

- [ ] **Step 2: Update test methods**

- `test_default_path`: unchanged (still asserts opus model + force_one)
- `test_route_hit`: `model_override="claude-sonnet-4-6"`, assert `_model == "claude-sonnet-4-6"`
- `test_route_alias`: remove (no aliases in new schema)
- `test_unknown_route_raises`: unchanged
- `test_llm_override_compat`: `llm_override="sonnet"`, assert `_model == "claude-sonnet-4-6"`
- `test_custom_default_key`: `default_profile_key="sonnet"`, assert `_model == "claude-sonnet-4-6"`
- `test_model_override_precedence`: `model_override="claude-sonnet-4-6"`, `llm_override="opus"`, assert `_model == "claude-sonnet-4-6"`

- [ ] **Step 3: Run and commit**

Run: `cd matmaster-evo && uv run pytest tests/matmaster/providers/test_llm_factory.py -v`
Expected: All PASS

```bash
git add tests/matmaster/providers/test_llm_factory.py
git commit -m "test: update test_llm_factory.py for opus/sonnet profile rename"
```

### Task 9: Update tests/matmaster/integration/test_llm_factory.py

**Files:**
- Modify: `tests/matmaster/integration/test_llm_factory.py:73-108,110-135`

- [ ] **Step 1: Rewrite TestEndToEndRouteToProvider fixture (lines 74-108)**

```python
    @pytest.fixture()
    def config(self) -> LLMConfig:
        return LLMConfig.model_validate({
            "profiles": {
                "opus": {
                    "provider": "openai",
                    "model": "claude-opus-4-6",
                    "api_key": "test-key",
                    "base_url": "https://test.example.com",
                    "thinking_effort": "high",
                    "reasoning_protocol": "anthropic_adaptive_thinking",
                    "temperature_policy": "force_one_when_reasoning",
                    "temperature": 0.7,
                    "timeout": 300,
                    "max_retries": 3,
                },
                "sonnet": {
                    "provider": "openai",
                    "model": "claude-sonnet-4-6",
                    "api_key": "test-key",
                    "base_url": "https://test.example.com",
                    "thinking_effort": "high",
                    "reasoning_protocol": "anthropic_adaptive_thinking",
                    "temperature_policy": "force_one_when_reasoning",
                    "temperature": 0.5,
                },
            },
            "routes": {
                "claude-opus-4-6": {"profile": "opus"},
                "claude-sonnet-4-6": {"profile": "sonnet"},
            },
            "default": "opus",
        })
```

- [ ] **Step 2: Update test methods**

- `test_route_azure_gpt5` -> rename to `test_route_sonnet`:
  ```python
  def test_route_sonnet(self, config: LLMConfig) -> None:
      provider = build_provider(config, model_override="claude-sonnet-4-6")
      assert provider._model == "claude-sonnet-4-6"
      assert provider._temperature == 1.0  # force_one_when_reasoning
  ```
- `test_route_alias_gpt5`: remove (no aliases)
- `test_route_claude`: unchanged conceptually, update `_model == "claude-opus-4-6"`
- `test_default_provider`: unchanged
- `test_unknown_route_errors`: unchanged
- `test_llm_override_compat`: `llm_override="sonnet"`, assert `_model == "claude-sonnet-4-6"`

- [ ] **Step 3: Run and commit**

Run: `cd matmaster-evo && uv run pytest tests/matmaster/integration/test_llm_factory.py -v`
Expected: All PASS

```bash
git add tests/matmaster/integration/test_llm_factory.py
git commit -m "test: update integration test_llm_factory.py for opus/sonnet rename"
```

### Task 10: Run full test suite

- [ ] **Step 1: Run all affected tests together**

```bash
cd matmaster-evo && uv run pytest tests/matmaster/config/ tests/matmaster/providers/test_llm_factory.py tests/matmaster/integration/test_llm_factory.py -v
```

Expected: All PASS

- [ ] **Step 2: If any failures, fix and re-run**
