# LLM Profile 前后端统一设计

## 问题

前端定义了 4 个模型选项（gemini-3-flash-preview、claude-haiku-4-5、claude-sonnet-4-6、claude-opus-4-6），通过 `model` 字段传递到后端。后端 `llm_config.yaml` 的 routes 表缺少 `claude-sonnet-4-6` 的映射，导致前端选择 Balanced 时后端 `resolve_route()` 抛 KeyError。此外 profile 命名不一致（`litellm` 实际指 opus），存在前端未使用的冗余 profile（azure_gpt5、deepseek_reasoner）。

## 决策记录

- Profile 命名风格：短名 + routes 桥接（非模型标识符直接作 profile 名）
- `litellm` 重命名为 `opus`
- 删除 `azure_gpt5`、`deepseek_reasoner`（前端未使用，属有意 breaking change，见下方说明）
- YAML 中去掉 `model_family`（自动推断覆盖所有剩余模型）和 `fallback_group`
- `configs/mat_master/` 旧目录：同步配置变更（含 `config.yaml` 的 `llm` 节和 `llm_config.yaml`），不在此 spec 中删除整个目录（evomaster/、run.py、多个测试仍引用该目录，全面清理属独立任务）
- `MODEL_FAMILY_DEFAULTS` 和 `_infer_model_family()` 中 gpt-5/deepseek-reasoner 条目：有意保留，不影响运行，为未来重新启用留口

### Breaking Change 说明

移除 `azure/gpt-5`、`gpt-5`、`deepseek-reasoner` 路由和对应 profile 是有意 breaking change。后端 API 的 `model` 字段仍接受任意字符串，但未匹配路由的值会被 `resolve_route()` 拒绝并返回错误。当前只有前端 UI 使用这 4 个 model key，无外部 API 消费者依赖被删除的路由。如需恢复，在 `llm_config.yaml` 中重新添加 profile 和 route 即可。

## 变更范围

### 1. matmaster_config/llm_config.yaml

**Profiles（变更后）：**

| Profile | model | 操作 |
|---------|-------|------|
| `opus` | `claude-opus-4-6` | 由 `litellm` 重命名 |
| `sonnet` | `claude-sonnet-4-6` | 新增 |
| `haiku` | `claude-haiku-4-5` | 保留 |
| `gemini` | `gemini-3-flash-preview` | 保留 |
| `compaction` | `gemini-3-flash-preview` | 保留（内部用途） |

所有 profile 去掉 `model_family` 和 `fallback_group` 字段。

**sonnet profile 参数：**

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
  stream_timeout: 20
  stream_idle_timeout: 30
  max_retries: 3
  retry_delay: 1.0
```

**Routes（变更后）：**

```yaml
routes:
  "claude-opus-4-6":        { profile: opus }
  "claude-sonnet-4-6":      { profile: sonnet }
  "claude-haiku-4-5":       { profile: haiku }
  "gemini-3-flash-preview": { profile: gemini }
```

删除：`litellm/claude-opus-4-6`、`azure/gpt-5`、`gpt-5`、`deepseek-reasoner`

**Default：** `"opus"`

### 2. matmaster_config/config.yaml

`agents.general.llm: "litellm"` -> `"opus"`

### 3. configs/mat_master/config.yaml — llm 节

`MonitorJobTool`（`evomaster/agent/tools/builtin/monitor_job/_llm.py:68-81`）仍从此文件的 `llm` 节读取配置，且在新运行时中通过 `exp.py:346` 活跃使用。必须同步更新：

- `llm.litellm` 重命名为 `llm.opus`
- 新增 `llm.sonnet` 块
- 删除 `llm.azure`、`llm.deepseek`
- `llm.default: "litellm"` -> `"opus"`
- `agents.general.llm: "litellm"` -> `"opus"`

### 4. configs/mat_master/llm_config.yaml

同步变更（与 matmaster_config/llm_config.yaml 保持一致）。

### 5. evomaster/agent/tools/builtin/monitor_job/_llm.py

- 行 76：hardcoded fallback `'litellm'` -> `'opus'`

### 6. matmaster/config/llm.py

- 行 169：`default: str = "litellm"` -> `default: str = "opus"`
- 行 179：`_normalize_legacy_or_explicit_schema` 中 `data.pop("default", "litellm")` -> `data.pop("default", "opus")`
- 行 9, 18：docstring 示例中 profile 名更新

### 7. src/models/chat.py

- 行 160：注释 `（如 litellm/azure/deepseek）` 更新为 `（如 opus/sonnet/haiku）`

### 8. matmaster/config/loader.py

- 行 10：docstring 中 `configs/mat_master/config.yaml` 路径更新为 `matmaster_config/llm_config.yaml`

### 9. 测试文件

| 文件 | 变更 |
|------|------|
| `tests/matmaster/config/test_llm.py` | `"litellm"` -> `"opus"`，删 azure 相关，新增 `claude-sonnet-4-6` 路由解析测试 |
| `tests/matmaster/config/test_loader.py` | `"litellm"` -> `"opus"` |
| `tests/matmaster/config/test_config_consolidation.py` | `"litellm"` -> `"opus"` |
| `tests/matmaster/providers/test_llm_factory.py` | `"litellm"` -> `"opus"`，`"azure_gpt5"` 相关用例替换为 sonnet |
| `tests/matmaster/integration/test_llm_factory.py` | 同上 |

## 不变的部分

- 前端代码（model key 已正确）
- `_infer_model_family()` / `MODEL_FAMILY_DEFAULTS`（按 model 字符串推断，不依赖 profile 名；gpt-5/deepseek 条目有意保留）
- `llm_factory.py`、`agent_run_service.py`、`stream_service.py`（通过变量传递 profile key，无硬编码）
- `LLMProfileConfig.model_family` Pydantic 字段保留（作为未来显式覆盖口）

## 部署注意事项

- `matmaster_config/llm_config.yaml`、`matmaster_config/config.yaml`、`configs/mat_master/config.yaml` 三个配置文件必须同步更新
- 当前启动校验（`agent_run_service._validate_llm_configs`）在 `agents.general.llm` 与 profiles 不匹配时仅 log error，不会阻止启动。这意味着部分部署不会 fail-fast，而是在首次请求使用默认 profile 时才报错
- API 与 Worker 分离部署时，两端必须同时更新配置和代码
- 如需灰度部署，可临时在新 YAML 中保留 `litellm` 作为 `opus` 的别名 profile（参数相同），待全量切换后再移除

## 前后端映射关系（最终状态）

```
前端 UI        前端 model key           后端 route              后端 profile    后端 model
─────────────────────────────────────────────────────────────────────────────────────────
Standard       gemini-3-flash-preview   gemini-3-flash-preview  gemini          gemini-3-flash-preview
Lite           claude-haiku-4-5         claude-haiku-4-5        haiku           claude-haiku-4-5
Balanced       claude-sonnet-4-6        claude-sonnet-4-6       sonnet          claude-sonnet-4-6
Flagship       claude-opus-4-6          claude-opus-4-6         opus            claude-opus-4-6
```
