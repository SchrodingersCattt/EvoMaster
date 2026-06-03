# BYOK User LLM Config Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 增加用户级 BYOK OpenAI-compatible LLM 配置，让用户用自己的 `base_url + api_key + model` 运行 MatMaster agent。

**Architecture:** BYOK 配置持久化在 per-user DB 表中，API 侧只做鉴权、endpoint policy、能力和 quota 预检，Redis job 只保存 `config_id + version`。Worker 执行前用同一个 resolver 重新回查 DB、校验版本、校验 endpoint、解密 key、组装 `LLMProfileConfig`，再复用 provider factory 和 `AgentRunService`。

**Tech Stack:** Python 3.11+, uv, FastAPI, Pydantic v2, PyMySQL, Redis, Fernet from `cryptography`, pytest, existing MatMaster LLM config/provider/runtime modules.

---

## Decisions

- 只支持 OpenAI-compatible endpoint；不支持原生 Anthropic/Google SDK，不支持用户自有 Bedrock。
- BYOK 执行默认要求 `verification_status == "verified"`，不在主路径静默允许未验证配置。
- 删除第一版采用物理删除；删除、禁用、更新都会让已入队但未执行的 job 在 Worker 回查时 fail-fast。
- BYOK 保留 `check_quota(user_id)` 作为基础访问 gate，跳过平台 model-level quota 和 `use_quota(..., model_key=...)`。
- 不写入、不复用、不动态污染静态 `LLMConfig.routes`；BYOK 通过 `custom_llm_config_id` 显式流转。
- 前端设置页与选择器另行排期；本计划只实现后端契约、运行链路和测试。

## File Structure

- Modify `pyproject.toml`: add direct dependency `cryptography>=46.0.0`.
- Create `src/utils/secret.py`: BYOK enable flag, Fernet encrypt/decrypt, key hint, key version.
- Create `src/services/byok_redaction.py`: recursive payload/text/provider-error redaction.
- Create `src/services/byok_endpoint_policy.py`: HTTPS endpoint normalization and SSRF policy.
- Create `src/models/byok.py`: CRUD models, patch models, capability DTOs, safe output conversion, run reference DTOs.
- Create `src/sql/migrate_add_user_llm_config.sql`: external MySQL migration only.
- Create `src/dao/user_llm_config_table.py`: per-user CRUD, JSON serialization, `version = version + 1`.
- Modify `matmaster/config/llm.py`: add BYOK passthrough fields.
- Modify `matmaster/providers/llm_factory.py`: add `build_provider_from_profile(profile, model)`.
- Create `src/services/byok_model_resolver.py`: shared API preflight and Worker run resolver.
- Create `src/services/byok_verifier.py`: injectable `/test` verifier boundary.
- Create `src/apis/byok_api.py`; modify `src/apis/api_router.py`: `/api/v1/llm-configs`.
- Modify `src/models/chat.py`, `src/apis/chat_api.py`, `src/services/stream_service.py`, `src/worker/agent_worker.py`, `src/services/agent_run_service.py`.
- Add focused tests under `tests/utils/`, `tests/services/`, `tests/dao/`, `tests/apis/`, `tests/matmaster/config/`, `tests/matmaster/providers/`, `tests/matmaster/worker/`, `tests/matmaster/services/`, and `tests/test_chat_stream_direct.py`.

---

### Task 1: Secret And Redaction Foundation

**Files:**
- Modify: `pyproject.toml`
- Create: `src/utils/secret.py`
- Create: `src/services/byok_redaction.py`
- Create: `tests/utils/test_secret.py`
- Create: `tests/services/test_byok_redaction.py`

- [ ] **Step 1: Write failing tests**

Create `tests/utils/test_secret.py` with these tests:

```python
def test_encrypt_decrypt_round_trip(monkeypatch): ...
def test_hint_does_not_contain_full_plaintext(monkeypatch): ...
def test_enabled_requires_valid_fernet_key(monkeypatch): ...
def test_disabled_secret_service_rejects_encrypt(monkeypatch): ...
def test_key_version_comes_from_env(monkeypatch): ...
```

Use `Fernet.generate_key().decode()` in tests and reload `src.utils.secret` after env changes.

Create `tests/services/test_byok_redaction.py` with these tests:

```python
def test_redact_mapping_removes_secret_fields_recursively(): ...
def test_redact_text_masks_common_secret_patterns(): ...
def test_sanitize_provider_error_truncates_and_redacts(): ...
```

Assertions must cover `api_key`, `api_key_cipher`, `Authorization`, `secret`, `token`, and `sk-...` style text.

- [ ] **Step 2: Run red tests**

```bash
uv run pytest tests/utils/test_secret.py tests/services/test_byok_redaction.py -q
```

Expected: import failure for new modules.

- [ ] **Step 3: Implement secret utility**

Add `cryptography>=46.0.0` to `pyproject.toml`. Create `src/utils/secret.py` with these public names:

```python
class BYOKSecretError(RuntimeError): ...
def is_byok_enabled() -> bool: ...
def current_key_version() -> str: ...
def encrypt(plaintext: str) -> str: ...
def decrypt(token: str) -> str: ...
def hint(plaintext: str) -> str: ...
```

Rules:

- `MATMASTER_BYOK_ENABLED` truthy values are `1,true,yes,on`.
- `encrypt()` and `decrypt()` fail-fast with `BYOKSecretError` when BYOK is disabled or `MATMASTER_BYOK_FERNET_KEY` is missing/invalid.
- `hint("sk-1234567890abcdef")` returns a short non-sensitive shape like `sk-...cdef`.
- Cache the Fernet object with `lru_cache`; tests can reload the module after env changes.

- [ ] **Step 4: Implement redaction utility**

Create `src/services/byok_redaction.py` with:

```python
SECRET_FIELD_NAMES = {"api_key", "api_key_cipher", "authorization", "secret", "token", "access_token", "refresh_token"}
def redact_mapping(value: Any) -> Any: ...
def redact_text(text: object) -> str: ...
def sanitize_provider_error(error: object, *, max_chars: int = 512) -> str: ...
```

`redact_mapping()` must recurse through dict/list/tuple and preserve non-secret values. `sanitize_provider_error()` must redact first, then truncate with `…`.

- [ ] **Step 5: Run green tests and commit**

```bash
uv run pytest tests/utils/test_secret.py tests/services/test_byok_redaction.py -q
git add pyproject.toml src/utils/secret.py src/services/byok_redaction.py tests/utils/test_secret.py tests/services/test_byok_redaction.py
git commit -m "feat: add BYOK secret and redaction utilities"
```

---

### Task 2: Endpoint Policy And BYOK Models

**Files:**
- Create: `src/services/byok_endpoint_policy.py`
- Create: `src/models/byok.py`
- Create: `tests/services/test_byok_endpoint_policy.py`
- Create: `tests/services/test_byok_validation_and_redaction.py`

- [ ] **Step 1: Write failing endpoint policy tests**

Create `tests/services/test_byok_endpoint_policy.py` with DNS monkeypatching via `socket.getaddrinfo`.

Required cases:

```python
def test_allows_https_public_domain_and_normalizes(monkeypatch): ...
def test_rejects_unsafe_url_shapes(monkeypatch): ...
def test_rejects_dns_records_to_private_or_metadata(monkeypatch): ...
def test_redirect_target_uses_same_policy(monkeypatch): ...
```

Reject `http`, `ftp`, userinfo, query string, non-443 port, localhost, loopback, private IP, link-local IP, and `169.254.169.254`.

- [ ] **Step 2: Write failing model validation tests**

Create `tests/services/test_byok_validation_and_redaction.py`:

```python
def test_create_trims_required_strings_and_hides_secret_repr(): ...
def test_params_are_whitelisted_and_bounded(): ...
def test_extra_body_rejects_credentials_core_fields_and_non_objects(): ...
def test_patch_empty_object_is_allowed_but_empty_api_key_is_rejected(): ...
def test_to_config_out_drops_cipher(): ...
```

`to_config_out()` assertion must prove `api_key_cipher` and plaintext `api_key` are absent.

- [ ] **Step 3: Run red tests**

```bash
uv run pytest tests/services/test_byok_endpoint_policy.py tests/services/test_byok_validation_and_redaction.py -q
```

Expected: import failures for `byok_endpoint_policy` and `src.models.byok`.

- [ ] **Step 4: Implement endpoint policy**

Create `src/services/byok_endpoint_policy.py`:

```python
class BYOKEndpointPolicyError(ValueError): ...

class BYOKEndpointPolicy:
    def __init__(self, *, allowed_ports: set[int] | None = None) -> None: ...
    def validate_base_url(self, raw_url: str) -> str: ...
    def validate_redirect_target(self, target_url: str) -> str: ...
```

Use `urllib.parse.urlsplit`, `socket.getaddrinfo`, and `ipaddress.ip_address`. Return normalized HTTPS URL without trailing duplicate slashes, query, or fragment.

- [ ] **Step 5: Implement BYOK models**

Create `src/models/byok.py` with:

```python
class BYOKCapabilities(BaseModel): ...
class BYOKConfigCreate(BaseModel): ...
class BYOKConfigUpdate(BaseModel): ...
class BYOKConfigOut(BaseModel): ...
class BYOKRunReference(BaseModel):
    def to_job_payload(self) -> dict[str, int]: ...
class BYOKResolvedPreflight(BaseModel): ...
class BYOKResolvedWorkerRun(BaseModel): ...
def to_config_out(row: dict[str, Any]) -> BYOKConfigOut: ...
```

Validation rules:

- `display_name`, `base_url`, `model` are trimmed and non-empty when present.
- `api_key` uses `SecretStr` with `repr=False`.
- `params` only allows `temperature`, `max_tokens`, `top_p`, `frequency_penalty`, `presence_penalty`, `reasoning_effort`, `seed`, `stop`.
- `extra_body` must be a JSON object and must reject credential keys and core provider keys `messages`, `tools`, `stream`, `model`, `temperature`, `max_tokens`.
- JSON size limits: `params` 8 KiB, `extra_body` 32 KiB, `prompt_cache` 4 KiB.

- [ ] **Step 6: Run green tests and commit**

```bash
uv run pytest tests/services/test_byok_endpoint_policy.py tests/services/test_byok_validation_and_redaction.py -q
git add src/services/byok_endpoint_policy.py src/models/byok.py tests/services/test_byok_endpoint_policy.py tests/services/test_byok_validation_and_redaction.py
git commit -m "feat: add BYOK endpoint policy and models"
```

---

### Task 3: Migration Script And DAO

**Files:**
- Create: `src/sql/migrate_add_user_llm_config.sql`
- Create: `src/dao/user_llm_config_table.py`
- Create: `tests/dao/test_user_llm_config_table.py`

- [ ] **Step 1: Write failing DAO tests**

Create `tests/dao/test_user_llm_config_table.py` with mocked `get_connection()` like existing table tests.

Required tests:

```python
def test_create_scopes_user_and_serializes_json(): ...
def test_get_scopes_by_user_and_parses_json(): ...
def test_list_by_user_orders_by_updated_at_and_id(): ...
def test_update_increments_version_for_runtime_fields(): ...
def test_delete_scopes_by_user(): ...
```

The SQL assertions must include `WHERE user_id = %s AND id = %s` for single-row read/update/delete.

- [ ] **Step 2: Run red test**

```bash
uv run pytest tests/dao/test_user_llm_config_table.py -q
```

Expected: import failure for `src.dao.user_llm_config_table`.

- [ ] **Step 3: Add migration script**

Create `src/sql/migrate_add_user_llm_config.sql` using the schema in the spec:

- table name `user_llm_config`
- `api_key_cipher TEXT NOT NULL`
- `api_key_hint VARCHAR(64) NOT NULL`
- `key_version VARCHAR(64) NOT NULL DEFAULT 'v1'`
- JSON fields `params`, `extra_body`, `prompt_cache`
- capability fields `supports_streaming`, `supports_tool_calling`, `supports_vision`
- `verification_status`, `verification_error`, `verified_at`
- `is_enabled`, `version`, timestamps
- unique key `(user_id, display_name)`
- indexes `(user_id, id)` and `(user_id, is_enabled)`

Do not call this SQL from application startup or table initialization.

- [ ] **Step 4: Implement DAO**

Create `src/dao/user_llm_config_table.py`:

```python
class UserLLMConfigTable(BaseTable):
    table_name = "user_llm_config"
    def create(self, user_id: str, **fields: Any) -> int: ...
    def get(self, user_id: str, config_id: int) -> dict[str, Any] | None: ...
    def get_for_run(self, user_id: str, config_id: int) -> dict[str, Any] | None: ...
    def list_by_user(self, user_id: str) -> list[dict[str, Any]]: ...
    def update(self, user_id: str, config_id: int, **fields: Any) -> bool: ...
    def delete(self, user_id: str, config_id: int) -> bool: ...

@lru_cache(maxsize=1)
def get_user_llm_config_table() -> UserLLMConfigTable: ...
```

Serialize `params`, `extra_body`, `prompt_cache` with compact `json.dumps(..., ensure_ascii=False, separators=(",", ":"))`; parse them back on reads. `update()` must append `version = version + 1` and `updated_at = NOW()`.

- [ ] **Step 5: Run green test and commit**

```bash
uv run pytest tests/dao/test_user_llm_config_table.py -q
git add src/sql/migrate_add_user_llm_config.sql src/dao/user_llm_config_table.py tests/dao/test_user_llm_config_table.py
git commit -m "feat: add BYOK config table"
```

---

### Task 4: LLM Profile Passthrough And Factory Extraction

**Files:**
- Modify: `matmaster/config/llm.py`
- Modify: `matmaster/providers/llm_factory.py`
- Modify: `tests/matmaster/config/test_llm.py`
- Modify: `tests/matmaster/providers/test_llm_factory.py`

- [ ] **Step 1: Write failing config tests**

Append to `TestLLMProfileConfigMethods`:

```python
def test_build_extra_kwargs_merges_passthrough_params_and_extra_body(self): ...
def test_build_extra_kwargs_preset_without_passthrough_is_unchanged(self): ...
```

The first test must prove `passthrough_params={"seed": 42, "reasoning_effort": "high"}` overrides inferred `reasoning_effort`, and `passthrough_extra_body` merges into `extra_body`.

- [ ] **Step 2: Write failing factory tests**

Append to `TestBuildProvider`:

```python
def test_build_provider_from_profile_builds_byok_openai_provider(self): ...
def test_build_provider_bundle_reuses_profile_builder(self, llm_config): ...
```

Assert BYOK profile produces an `OpenAIProvider` with user `api_key`, `base_url`, `model`, `temperature`, `max_tokens`, `extra_kwargs`, and prompt cache options.

- [ ] **Step 3: Run red tests**

```bash
uv run pytest tests/matmaster/config/test_llm.py::TestLLMProfileConfigMethods tests/matmaster/providers/test_llm_factory.py::TestBuildProvider -q
```

Expected: missing passthrough fields and missing `build_provider_from_profile`.

- [ ] **Step 4: Implement config and factory changes**

In `LLMProfileConfig`, add:

```python
passthrough_params: dict[str, Any] | None = None
passthrough_extra_body: dict[str, Any] | None = None
```

At the end of `build_extra_kwargs()`:

```python
if self.passthrough_params:
    out.update(self.passthrough_params)
if self.passthrough_extra_body:
    extra_body.update(self.passthrough_extra_body)
if extra_body:
    out["extra_body"] = extra_body
return out or None
```

In `matmaster/providers/llm_factory.py`, add:

```python
def build_provider_from_profile(
    profile: LLMProfileConfig,
    model: str,
) -> OpenAIProvider | BedrockProvider: ...
```

Move the existing Bedrock/OpenAI construction logic into that function. `build_provider_bundle()` should still resolve route/profile identity, then call this helper and return the same `LLMProviderBundle` fields.

- [ ] **Step 5: Run green tests and commit**

```bash
uv run pytest tests/matmaster/config/test_llm.py::TestLLMProfileConfigMethods tests/matmaster/providers/test_llm_factory.py::TestBuildProvider -q
git add matmaster/config/llm.py matmaster/providers/llm_factory.py tests/matmaster/config/test_llm.py tests/matmaster/providers/test_llm_factory.py
git commit -m "feat: support BYOK LLM profile passthrough"
```

---

### Task 5: BYOK Model Resolver

**Files:**
- Create: `src/services/byok_model_resolver.py`
- Create: `tests/services/test_byok_model_resolver.py`

- [ ] **Step 1: Write failing resolver tests**

Create `tests/services/test_byok_model_resolver.py` using mocked table, endpoint policy and secret module.

Required tests:

```python
def test_preflight_returns_reference_without_secret(): ...
def test_worker_resolve_decrypts_and_builds_profile(): ...
def test_fail_fast_when_missing_disabled_unverified_or_version_mismatch(): ...
def test_direct_and_planner_require_streaming_and_tool_calling(): ...
def test_images_require_vision_support(): ...
def test_endpoint_policy_failure_is_mapped(): ...
```

Key assertions:

- preflight returns `BYOKRunReference(config_id=12, version=3, model="...")`
- preflight never calls `secret.decrypt`
- worker resolve calls `secret.decrypt(api_key_cipher)`
- worker profile maps `temperature`, `max_tokens`, remaining params into `passthrough_params`, `extra_body` into `passthrough_extra_body`

- [ ] **Step 2: Run red test**

```bash
uv run pytest tests/services/test_byok_model_resolver.py -q
```

Expected: import failure.

- [ ] **Step 3: Implement resolver**

Create `src/services/byok_model_resolver.py`:

```python
class BYOKResolveError(RuntimeError):
    def __init__(self, message: str, *, http_status: int = 400, error_code: str = "byok_invalid") -> None: ...

class BYOKModelResolver:
    def resolve_for_preflight(self, *, user_id: str, config_id: int, mode: str, has_images: bool) -> BYOKResolvedPreflight: ...
    def resolve_for_worker_run(self, *, user_id: str, config_id: int, expected_version: int, mode: str, has_images: bool) -> BYOKResolvedWorkerRun: ...

@lru_cache(maxsize=1)
def get_byok_model_resolver() -> BYOKModelResolver: ...
```

Validation semantics:

- missing row -> 404 `byok_not_found`
- `is_enabled=0` -> `byok_disabled`
- version mismatch -> 409 `byok_version_mismatch`
- non-verified -> `byok_unverified`
- direct/planner require streaming and tool calling
- images require `supports_vision`
- endpoint policy runs in both preflight and worker
- worker only decrypts after all non-secret checks pass

Profile defaults for BYOK:

```python
provider="openai"
timeout=600
stream_timeout=120
stream_idle_timeout=60
max_retries=2
retry_delay=1.0
vision_detail="high"
```

- [ ] **Step 4: Run green test and commit**

```bash
uv run pytest tests/services/test_byok_model_resolver.py -q
git add src/services/byok_model_resolver.py tests/services/test_byok_model_resolver.py
git commit -m "feat: resolve BYOK model configs"
```

---

### Task 6: BYOK CRUD And Test API

**Files:**
- Create: `src/services/byok_verifier.py`
- Create: `src/apis/byok_api.py`
- Modify: `src/apis/api_router.py`
- Create: `tests/apis/test_byok_api.py`

- [ ] **Step 1: Write failing API tests**

Create `tests/apis/test_byok_api.py` with `TestClient(app)` and dependency overrides for `get_user_llm_config_table()` and `get_byok_verifier()`.

Required tests:

```python
def test_create_returns_safe_payload_and_encrypts(monkeypatch): ...
def test_list_returns_current_user_configs(monkeypatch): ...
def test_patch_omitted_fields_are_not_updated(monkeypatch): ...
def test_patch_api_key_replaces_cipher_hint_and_key_version(monkeypatch): ...
def test_delete_scopes_current_user(monkeypatch): ...
def test_unsaved_test_does_not_persist(monkeypatch): ...
def test_saved_test_updates_verification_fields(monkeypatch): ...
def test_saved_test_sanitizes_provider_error(monkeypatch): ...
```

Assertions must prove responses never contain `api_key` or `api_key_cipher`.

- [ ] **Step 2: Run red test**

```bash
uv run pytest tests/apis/test_byok_api.py -q
```

Expected: `/api/v1/llm-configs` returns 404 or import failure.

- [ ] **Step 3: Implement verifier boundary**

Create `src/services/byok_verifier.py`:

```python
class BYOKVerificationError(RuntimeError): ...

class BYOKVerifier:
    async def verify_unsaved(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str,
        supports_vision: bool = False,
    ) -> dict[str, Any]: ...

@lru_cache(maxsize=1)
def get_byok_verifier() -> BYOKVerifier: ...
```

Implementation must use the shared provider path, keep verification requests small, sanitize provider errors, and return a dict with `status`, `supports_streaming`, `supports_tool_calling`, `supports_vision`, `error`.

- [ ] **Step 4: Implement API router**

Create `src/apis/byok_api.py`:

```python
router = APIRouter(tags=["BYOK LLM Configs"])
POST   ""                  -> create config
GET    ""                  -> list current user configs
GET    "/{config_id}"      -> get one config
PATCH  "/{config_id}"      -> patch explicit fields only
DELETE "/{config_id}"      -> physical delete
POST   "/test"             -> verify unsaved config without persistence
POST   "/{config_id}/test" -> verify saved config and update verification fields
```

Rules:

- all endpoints require `UserService.require_user_id`
- `_require_enabled()` checks `secret.is_byok_enabled()`
- create/update validate endpoint policy before saving `base_url`
- create/update encrypt plaintext key and save only cipher + hint
- `PATCH` uses `req.model_fields_set`; missing field means no update
- saved `/test` decrypts only inside the endpoint, calls verifier, sanitizes errors, updates verification fields, reloads row, returns `to_config_out(row)`

Modify `src/apis/api_router.py`:

```python
from src.apis import admin_chat_api, byok_api, chat_api, debug_api, feishu_api
api_router.include_router(byok_api.router, prefix="/llm-configs")
```

- [ ] **Step 5: Run green test and commit**

```bash
uv run pytest tests/apis/test_byok_api.py -q
git add src/services/byok_verifier.py src/apis/byok_api.py src/apis/api_router.py tests/apis/test_byok_api.py
git commit -m "feat: add BYOK config API"
```

---

### Task 7: Chat Request And API Preflight

**Files:**
- Modify: `src/models/chat.py`
- Modify: `src/apis/chat_api.py`
- Modify: `tests/test_chat_stream_direct.py`

- [ ] **Step 1: Write failing request tests**

Add to `tests/test_chat_stream_direct.py`:

```python
def test_chat_send_request_rejects_byok_with_model_or_llm(): ...
def test_chat_send_request_accepts_byok_alone(): ...
```

The first test must expect `ValidationError` for `custom_llm_config_id + model` and `custom_llm_config_id + llm`.

- [ ] **Step 2: Write failing API preflight tests**

Add tests proving:

```python
def test_chat_stream_byok_preflight_skips_model_quota(monkeypatch): ...
def test_chat_stream_byok_without_user_id_returns_401(monkeypatch): ...
def test_chat_stream_byok_with_images_does_not_call_static_vision_gate(monkeypatch): ...
```

Patch `get_byok_model_resolver()`, `check_quota`, `check_model_quota`, and `get_image_input_service()`. Assert `check_quota` still runs, `check_model_quota` is skipped, image URL validation still runs, and static `ensure_vision_supported()` is skipped for BYOK.

- [ ] **Step 3: Run red tests**

```bash
uv run pytest tests/test_chat_stream_direct.py -k "byok or custom_llm_config_id" -q
```

Expected: field missing or preflight not called.

- [ ] **Step 4: Implement request model field**

In `ChatSendRequest`:

```python
custom_llm_config_id: int | None = Field(default=None, description="用户自定义 BYOK LLM 配置 ID")

@model_validator(mode="after")
def validate_model_choice(self) -> "ChatSendRequest":
    if self.custom_llm_config_id is not None and ((self.llm or "").strip() or (self.model or "").strip()):
        raise ValueError("custom_llm_config_id cannot be used with llm or model")
    return self
```

- [ ] **Step 5: Implement chat API preflight**

In `chat_stream()`:

- compute `byok_preflight = None`
- when `req.custom_llm_config_id is not None`, require `user_id`
- call `get_byok_model_resolver().resolve_for_preflight(user_id=user_id, config_id=req.custom_llm_config_id, mode=mode, has_images=bool(req.images))`
- map `BYOKResolveError` to `BaseErrorResponse` with `data={"error_code": exc.error_code}`
- skip model-level quota when BYOK is active
- still run `validate_current_images`
- skip static `load_llm_config()` and `ensure_vision_supported()` for BYOK image requests
- call `stream_svc.prepare_send_message(..., byok_ref=byok_preflight.ref if byok_preflight else None)`

- [ ] **Step 6: Run green tests and commit**

```bash
uv run pytest tests/test_chat_stream_direct.py -k "byok or custom_llm_config_id" -q
git add src/models/chat.py src/apis/chat_api.py tests/test_chat_stream_direct.py
git commit -m "feat: preflight BYOK chat requests"
```

---

### Task 8: Stream Context And Redis Job Reference

**Files:**
- Modify: `src/services/stream_service.py`
- Modify: `tests/test_chat_stream_direct.py`

- [ ] **Step 1: Write failing stream tests**

Add tests:

```python
def test_prepare_send_message_records_byok_metadata_without_secret(): ...
async def test_generate_send_stream_enqueues_byok_reference_only(monkeypatch): ...
```

Assertions:

- `ctx.byok_ref.config_id == 12`
- `ctx.user_msg["requested_byok_config_id"] == 12`
- `job["byok"] == {"config_id": 12, "version": 3}`
- serialized job does not contain `api_key`, `api_key_cipher`, `sk-`

- [ ] **Step 2: Run red tests**

```bash
uv run pytest tests/test_chat_stream_direct.py -k "byok and (prepare_send_message or enqueue)" -q
```

Expected: `byok_ref` field/signature missing.

- [ ] **Step 3: Implement stream changes**

In `src/services/stream_service.py`:

```python
from src.models.byok import BYOKRunReference

@dataclass
class SendStreamContext:
    ...
    byok_ref: BYOKRunReference | None = None
```

Update `prepare_send_message(..., byok_ref: BYOKRunReference | None = None)`:

```python
if byok_ref is not None:
    user_msg["requested_byok_config_id"] = byok_ref.config_id
    if byok_ref.display_name:
        user_msg["requested_byok_display_name"] = byok_ref.display_name
    if byok_ref.model:
        user_msg["requested_model"] = byok_ref.model
```

In `generate_send_stream()`:

```python
if ctx.byok_ref is not None:
    job["byok"] = ctx.byok_ref.to_job_payload()
```

- [ ] **Step 4: Run green tests and commit**

```bash
uv run pytest tests/test_chat_stream_direct.py -k "byok and (prepare_send_message or enqueue)" -q
git add src/services/stream_service.py tests/test_chat_stream_direct.py
git commit -m "feat: enqueue BYOK config references"
```

---

### Task 9: Worker BYOK Resolution

**Files:**
- Modify: `src/worker/agent_worker.py`
- Modify: `tests/matmaster/worker/test_redis_bridge.py`

- [ ] **Step 1: Write failing worker tests**

Add tests:

```python
def test_run_worker_loop_resolves_byok_reference_before_run_agent(): ...
def test_run_worker_loop_byok_version_mismatch_emits_error_and_skips_run_agent(): ...
```

Use existing `_run_worker_loop()` mocking style. Patch `get_byok_model_resolver()` to return `BYOKResolvedWorkerRun`.

Assertions:

- `resolve_for_worker_run(user_id=session_user_id, config_id=12, expected_version=3, mode="direct", has_images=False)` is called
- `run_agent()` receives `byok_profile`, `byok_config_id=12`, `byok_config_version=3`, `billing_mode="byok"`
- resolver failure publishes `error` and `stream_closed` with `treat_as_failure=True`
- resolver failure does not call `run_agent()`

- [ ] **Step 2: Run red tests**

```bash
uv run pytest tests/matmaster/worker/test_redis_bridge.py -q
```

Expected: worker ignores `payload["byok"]`.

- [ ] **Step 3: Implement worker changes**

In `src/worker/agent_worker.py`:

- import `BYOKResolveError`, `get_byok_model_resolver`
- parse `raw_byok = payload.get("byok") if isinstance(payload.get("byok"), dict) else None`
- after `session_user_id` is known and before `run_agent()`, resolve BYOK if present
- on resolver failure, publish user-facing error and terminal `stream_closed`, set `run_success=False`, `fail_reason=exc.message`, and skip `run_agent()`
- on success, pass:

```python
byok_profile=resolved_byok.profile
byok_config_id=resolved_byok.config_id
byok_config_version=resolved_byok.version
billing_mode="byok"
```

Keep existing cleanup and session release behavior for both success and resolver-failure paths.

- [ ] **Step 4: Run green tests and commit**

```bash
uv run pytest tests/matmaster/worker/test_redis_bridge.py -q
git add src/worker/agent_worker.py tests/matmaster/worker/test_redis_bridge.py
git commit -m "feat: resolve BYOK configs in worker"
```

---

### Task 10: AgentRunService BYOK Branch

**Files:**
- Modify: `src/services/agent_run_service.py`
- Modify: `tests/matmaster/services/test_agent_run_stream.py`
- Modify: `tests/matmaster/services/test_agent_run_stream_images.py`

- [ ] **Step 1: Write failing service tests**

Extend run-agent signature tests to require:

```python
"byok_profile"
"byok_config_id"
"byok_config_version"
"billing_mode"
```

Add behavior tests:

```python
async def test_run_agent_uses_byok_profile_identity_and_skips_model_quota(monkeypatch): ...
async def test_run_agent_byok_uses_profile_vision_detail(monkeypatch): ...
```

Assertions:

- `build_provider_from_profile(byok_profile, byok_profile.model)` is called
- `AgentRunRequest.llm_model == byok_profile.model`
- `llm_model_profile == "byok:12"`
- `llm_model_route is None`
- `use_quota` is not awaited when `billing_mode == "byok"`
- BYOK image detail comes from `byok_profile.vision_detail`
- static `image_service.resolve_image_detail()` is not called for BYOK

- [ ] **Step 2: Run red tests**

```bash
uv run pytest tests/matmaster/services/test_agent_run_stream.py tests/matmaster/services/test_agent_run_stream_images.py -q
```

Expected: missing kwargs or preset provider path used.

- [ ] **Step 3: Implement BYOK branch**

Extend `AgentRunService.run_agent()` signature:

```python
byok_profile: LLMProfileConfig | None = None
byok_config_id: int | None = None
byok_config_version: int | None = None
billing_mode: Literal["platform", "byok"] = "platform"
```

In Stage 4:

```python
top_level_images = tuple(images or ())
current_images = image_service.select_current_images(turn_input, top_level_images)

if byok_profile is not None:
    if current_images and not byok_profile.supports_vision:
        raise ImageInputError("该自定义模型不支持图片输入，请切换模型。", http_status=400, error_code="byok_vision_required")
    image_detail = byok_profile.vision_detail if current_images else None
    llm_provider = build_provider_from_profile(byok_profile, byok_profile.model)
    llm_bundle = LLMProviderBundle(
        provider=llm_provider,
        model=byok_profile.model,
        model_profile=f"byok:{byok_config_id}",
        model_route=None,
        provider_name="openai",
        model_family=None,
    )
else:
    image_detail = image_service.resolve_image_detail(...)
    llm_bundle = build_provider_bundle(...)
    llm_provider = llm_bundle.provider
```

Quota post-processing:

```python
if user_id and billing_mode == "platform":
    await use_quota(user_id, model_key=model_override)
```

Do not change `AgentKernel`, `TurnInput`, runtime ports, or history checkpoint models.

- [ ] **Step 4: Run green tests and commit**

```bash
uv run pytest tests/matmaster/services/test_agent_run_stream.py tests/matmaster/services/test_agent_run_stream_images.py -q
git add src/services/agent_run_service.py tests/matmaster/services/test_agent_run_stream.py tests/matmaster/services/test_agent_run_stream_images.py
git commit -m "feat: run agents with BYOK profiles"
```

---

### Task 11: Cross-Path Security And Regression Tests

**Files:**
- Modify: `tests/test_chat_stream_direct.py`
- Modify: `tests/matmaster/worker/test_redis_bridge.py`
- Modify: `tests/matmaster/services/test_agent_run_stream.py`
- Modify: implementation files from earlier tasks if tests expose gaps.

- [ ] **Step 1: Add security regression tests**

Add or tighten tests for:

```python
def test_byok_redis_job_payload_never_contains_key_material(): ...
def test_byok_api_response_never_contains_cipher_or_plaintext(): ...
def test_worker_decrypt_failure_is_redacted_in_stream_error(): ...
def test_provider_verification_error_is_sanitized_before_persisting(): ...
```

- [ ] **Step 2: Add preset-path regression tests**

Add or tighten tests proving:

```python
def test_preset_model_still_uses_static_vision_gate(): ...
def test_preset_model_still_checks_model_quota(): ...
async def test_preset_model_success_still_uses_platform_quota(): ...
```

- [ ] **Step 3: Run red/green loop**

```bash
uv run pytest \
  tests/test_chat_stream_direct.py \
  tests/matmaster/worker/test_redis_bridge.py \
  tests/matmaster/services/test_agent_run_stream.py \
  tests/apis/test_byok_api.py \
  -q
```

Expected: PASS after fixing any exposed gaps.

- [ ] **Step 4: Commit**

```bash
git add src tests
git commit -m "test: cover BYOK security and preset regressions"
```

---

### Task 12: Final Verification

**Files:**
- Read: `docs/superpowers/specs/2026-06-03-byok-user-llm-config-design.md`
- Read: `docs/superpowers/plans/2026-06-03-byok-user-llm-config.md`
- Modify if needed: touched implementation/test files

- [ ] **Step 1: Run BYOK-focused suite**

```bash
uv run pytest tests/utils/test_secret.py tests/services/test_byok_redaction.py tests/services/test_byok_endpoint_policy.py tests/services/test_byok_validation_and_redaction.py tests/dao/test_user_llm_config_table.py tests/services/test_byok_model_resolver.py tests/apis/test_byok_api.py tests/matmaster/config/test_llm.py tests/matmaster/providers/test_llm_factory.py tests/test_chat_stream_direct.py tests/matmaster/worker/test_redis_bridge.py tests/matmaster/services/test_agent_run_stream.py tests/matmaster/services/test_agent_run_stream_images.py -q
```

Expected: PASS.

- [ ] **Step 2: Run pre-commit on touched files**

```bash
uv run pre-commit run --files pyproject.toml src/utils/secret.py src/services/byok_redaction.py src/services/byok_endpoint_policy.py src/models/byok.py src/sql/migrate_add_user_llm_config.sql src/dao/user_llm_config_table.py matmaster/config/llm.py matmaster/providers/llm_factory.py src/services/byok_model_resolver.py src/services/byok_verifier.py src/apis/byok_api.py src/apis/api_router.py src/models/chat.py src/apis/chat_api.py src/services/stream_service.py src/worker/agent_worker.py src/services/agent_run_service.py tests/utils/test_secret.py tests/services/test_byok_redaction.py tests/services/test_byok_endpoint_policy.py tests/services/test_byok_validation_and_redaction.py tests/dao/test_user_llm_config_table.py tests/services/test_byok_model_resolver.py tests/apis/test_byok_api.py tests/matmaster/config/test_llm.py tests/matmaster/providers/test_llm_factory.py tests/test_chat_stream_direct.py tests/matmaster/worker/test_redis_bridge.py tests/matmaster/services/test_agent_run_stream.py tests/matmaster/services/test_agent_run_stream_images.py
```

Expected: PASS.

- [ ] **Step 3: Search for credential leaks**

```bash
rg -n "api_key|api_key_cipher|Authorization|MATMASTER_BYOK_FERNET_KEY|byok" src matmaster tests -g '!uv.lock'
```

Expected: every occurrence is schema, validation, encryption, redaction, resolver, API, or test fixture code. No logger call prints plaintext key, cipher token, provider headers, or full provider error body.

- [ ] **Step 4: Check file length and final git status**

```bash
wc -l docs/superpowers/plans/2026-06-03-byok-user-llm-config.md
git status --short
```

Expected: plan file stays below 1000 lines. `git status --short` shows only intentional changes plus unrelated pre-existing user changes.

- [ ] **Step 5: Final commit**

```bash
git add pyproject.toml src matmaster tests
git commit -m "feat: add BYOK user LLM config support"
```

Expected: commit succeeds. Do not stage unrelated pre-existing user changes unless they are part of the BYOK implementation.

---

## Self-Review Checklist

- Spec coverage: Tasks 1-3 cover secret handling, endpoint policy, schema and DB persistence. Tasks 4-6 cover provider mapping, resolver, CRUD and `/test` API. Tasks 7-10 cover chat preflight, Redis reference-only queueing, Worker recheck/decrypt and `AgentRunService` BYOK provider branch. Tasks 11-12 cover security regressions and final validation.
- API/Worker separation: API chat preflight never decrypts and Redis never carries key material; Worker is the only chat-run path that decrypts after DB回查、版本校验和 endpoint policy.
- Runtime boundary: BYOK does not enter `run_meta`, `RuntimePorts`, static `routes`, `AgentKernel`, history checkpoint, or `TurnInput` semantics.
- Failure semantics: missing/disabled/unverified/version-mismatch/unsafe endpoint/capability-missing/decrypt-failure all fail-fast and never fall back to a default platform model.
- Preset path: when `custom_llm_config_id` is absent, static model route resolution, static vision gate, platform model quota and existing provider construction remain unchanged.
