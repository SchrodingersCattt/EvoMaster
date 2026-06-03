# BYOK 用户自定义 LLM 配置设计

- Date: 2026-06-03
- Status: Draft, review 后修订
- Author: Kealdoom + Claude
- 影响范围:
  - 新增 `src/sql/migrate_add_user_llm_config.sql`
  - 新增 `src/dao/user_llm_config_table.py`
  - 新增 `src/models/byok.py`
  - 新增 `src/services/byok_model_resolver.py`
  - 新增 `src/services/byok_endpoint_policy.py`
  - 新增 `src/services/byok_redaction.py`
  - 新增 `src/utils/secret.py`
  - 新增 `src/apis/byok_api.py`
  - `src/apis/api_router.py` 挂载 BYOK API
  - `src/apis/chat_api.py` 增加 BYOK 预检、vision gate、quota 分流
  - `src/models/chat.py` 增加 `custom_llm_config_id`
  - `src/services/stream_service.py` 入队时只携带 BYOK 引用与版本
  - `src/worker/agent_worker.py` 执行前回查 BYOK 配置、校验版本、解密
  - `src/services/agent_run_service.py` 增加 BYOK profile 分支
  - `matmaster/config/llm.py` 增加透传字段并合并 `build_extra_kwargs`
  - `matmaster/providers/llm_factory.py` 抽出 `build_provider_from_profile`
  - 对应 `tests/`

## 1. 背景

当前所有预设 model 不是多家厂商直连，而是大多统一收口到一个 LiteLLM
代理:

- `config/llm_config.yaml` 里大多数 profile 都使用
  `api_key: "${LITELLM_PROXY_API_KEY}"` 与
  `base_url: "${LITELLM_PROXY_API_BASE}"`，彼此主要是 `model` 名不同。
- 例外是 `opus_bedrock`，它走 AWS Bedrock 凭证链。
- 静态 LLM 配置是三层结构:
  `profiles` 定义后端全参数，`routes` 将前端 `model` route key 映射到
  profile，`default` 定义默认 profile。
- 当前 `AgentRunService.run_agent()` 在每轮 Stage 4 调用
  `load_llm_config(_project_root / "config" / "llm_config.yaml")` 加载并
  校验这一份静态配置。它不是每个用户可变的配置源。

`OpenAIProvider` 本身已经接近 BYOK-ready。它的构造函数接受 `api_key`、
`base_url`、`model`，并通过 `extra_kwargs` 透传 OpenAI-compatible 请求参数。
因此本设计的难点不是 provider 调用，而是这些边界:

1. 目前没有 per-user 的 LLM 配置概念。
2. `LLMConfig.resolve_route()` 只在静态 `routes` 表里做精确查找，用户自定义
   model 不在这张表里。
3. 没有按用户存储、加密、轮换 LLM 密钥的设施。
4. API 进程与 Worker 进程隔着 Redis 队列，Job 是 JSON payload。用户密钥不应
   以明文或可解密密文快照进入 Redis。
5. 图片输入、模型配额、工具调用能力判断现在都在不同层触发，BYOK 需要统一
   模型解析语义，否则 API 侧和 Worker 侧会判断不一致。

## 2. 目标

- 让登录用户自带一个 OpenAI-compatible endpoint
  `base_url + api_key + model`，并在前端像选择预设 model 一样使用。
- 配置按 `user_id` 维度持久化，一个用户可维护多条配置。
- BYOK 通过显式的 `custom_llm_config_id` 贯穿调用链，不复用也不动态写入静态
  `routes` 表。
- API 侧与 Worker 侧都通过同一个 `BYOKModelResolver` 解析 BYOK 配置:
  API 侧只读取非明文配置和能力声明，Worker 侧执行前重新回查、校验版本、解密。
- Redis Job 只携带 BYOK 配置引用与版本，不携带 api_key 明文，也不携带可解密的
  `api_key_cipher`。
- api_key 在 DB 中以密文保存，不持久化、不入队、不记录日志，仅在用户提交和
  Worker 调用 provider 所需的内存路径中出现。
- 显式建模 `supports_streaming`、`supports_tool_calling`、`supports_vision` 等能力。
  direct/planner agent 主路径必须要求 streaming 与 tool calling 能力。
- 对用户提供的 `base_url` 做 SSRF 防护。BYOK endpoint 是服务端主动访问的 URL，
  不能只做格式校验。
- 参数模型采用扁平白名单参数 + 有大小限制的 `extra_body` JSON 透传口 +
  prompt cache 单独开关。
- 配置模型、加解密、endpoint policy、resolver、provider 构造均可单元测试。

## 3. 非目标

- 不支持原生 Anthropic / Google SDK 直连，只支持 OpenAI-compatible 形态。
- 不支持用户自有 Bedrock。
- 不做组织或租户级共享配置，只做个人用户级。
- 不基于 model 名自动推断 family 来给 BYOK model 配 reasoning、cache 或 vision。
  BYOK 的高级行为由用户显式参数和验证结果决定。
- 不在主代码内联迁移、兼容或兜底逻辑。建表与密钥轮换一律走外部脚本。
- 不让 BYOK 复用、动态修改或污染静态 `routes` 表。
- 不改变预设 model 的现有解析和调用行为。

## 4. 架构决策

| # | 决策点 | 选择 | 放弃的候选 | 理由 |
|---|---|---|---|---|
| D1 | 绑定维度 | per `user_id` | 组织级、部署级 YAML | 面向终端用户自助，复用现有 `X-User-Id` 与 per-user 表先例 |
| D2 | 后端形态 | 仅 OpenAI-compatible，复用 `OpenAIProvider` | 原生 Anthropic/Google SDK、用户自有 Bedrock | provider 层改动最小，覆盖 OpenAI、Azure、vLLM、Ollama、OpenRouter 与各类代理 |
| D3 | 参数模型 | 扁平白名单参数 + 受限 `extra_body` + cache 开关 | 完全暴露内部 profile schema | 对用户透明，同时不把内部配置对象直接外泄 |
| D4 | 密钥链路 | DB 存密文，Redis 只传配置 id 与版本，Worker 回查 DB 后解密 | Redis 传明文、Redis 传密文快照 | 支持删除/禁用/换 key 立即影响在途任务，避免可解密 token 在 Redis 扩散 |
| D5 | 路由接入 | 显式 `custom_llm_config_id` | `model=byok:<id>` 魔法前缀、动态注入 `LLMConfig` | 一等公民参数，语义清楚，静态 route 表保持纯净 |
| D6 | 汇合点 | 抽出 `build_provider_from_profile(profile, model)` | BYOK 自带一套 provider 构造 | 预设与 BYOK 复用同一段 profile 到 provider 逻辑 |
| D7 | 配置传递 | Job 携带 `config_id + version`，Worker 执行前回查 DB | Job 携带发起时刻配置快照 | 保留版本一致性，支持撤销与配置语义变更即时失效 |
| D8 | 引用失效 | fail-fast，run 失败并明确报错 | 静默回退默认 model | 配置缺失、禁用、版本不匹配都是调用方状态变化，不做隐式兜底 |
| D9 | 加密方案 | `cryptography` Fernet，密钥来自环境变量 | 自行管理 AES-GCM nonce、明文 | 简单、带完整性校验、不易误用 |
| D10 | endpoint 安全 | create/update 与 Worker 执行前都校验 `base_url` | 只校验 URL 格式 | BYOK endpoint 是 SSRF 入口，执行前必须再校验一次 |
| D11 | 能力模型 | streaming/tool calling/vision 显式字段 + 验证状态 | 只存 `supports_vision`、只做普通 chat 测试 | agent 主路径需要 streaming 和 tool calling，普通连通性不足以证明可用 |
| D12 | 与 `llm/model` 关系 | `custom_llm_config_id` 与 `llm/model` 互斥 | BYOK 优先并忽略 `llm/model` | 避免 API quota、history、Worker 解析对同一请求产生不同解释 |
| D13 | 更新 API | 使用 `PATCH` 表达部分更新 | `PUT` 但缺省字段表示不改 | `api_key` 缺省不更新更符合 PATCH 语义 |
| D14 | 测试接口 | 提供显式 `/test`，保存不自动测试 | create/update 强制测试 | 避免费用、慢 endpoint 或临时不可用阻塞配置保存 |

## 5. 数据模型

### 5.1 建表脚本

新增 `src/sql/migrate_add_user_llm_config.sql`。这是外部迁移脚本，不在主代码内联
自动迁移逻辑。

```sql
CREATE TABLE `user_llm_config` (
    `id`                    BIGINT       PRIMARY KEY AUTO_INCREMENT,
    `user_id`               VARCHAR(255) NOT NULL,
    `display_name`          VARCHAR(128) NOT NULL,
    `base_url`              VARCHAR(1024) NOT NULL,
    `model`                 VARCHAR(255) NOT NULL,
    `api_key_cipher`        TEXT          NOT NULL,
    `api_key_hint`          VARCHAR(64)  NOT NULL,
    `key_version`           VARCHAR(64)  NOT NULL DEFAULT 'v1',
    `params`                JSON         NULL,
    `extra_body`            JSON         NULL,
    `prompt_cache`          JSON         NULL,
    `supports_streaming`    TINYINT(1)   NOT NULL DEFAULT 0,
    `supports_tool_calling` TINYINT(1)   NOT NULL DEFAULT 0,
    `supports_vision`       TINYINT(1)   NOT NULL DEFAULT 0,
    `verification_status`   VARCHAR(32)  NOT NULL DEFAULT 'unverified',
    `verification_error`    VARCHAR(512) NULL,
    `verified_at`           DATETIME     NULL,
    `is_enabled`            TINYINT(1)   NOT NULL DEFAULT 1,
    `version`               INT          NOT NULL DEFAULT 1,
    `created_at`            DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`            DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
                                           ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY `uk_user_display_name` (`user_id`, `display_name`),
    KEY `idx_user_id_id` (`user_id`, `id`),
    KEY `idx_user_enabled` (`user_id`, `is_enabled`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

字段说明:

- `display_name`: 用户给配置起的名字，前端选择器显示。
- `base_url`: OpenAI-compatible endpoint base URL。写入和执行前都要通过
  `BYOKEndpointPolicy` 校验。
- `api_key_cipher`: Fernet token，密文。明文 api_key 不落库。
- `key_version`: 当前密钥版本，例如 `v1`。MVP 不在主代码做多 key 解密，但该字段给
  外部轮换脚本、审计和排障留出空间。
- `api_key_hint`: 写入明文 key 时生成的不可敏感提示，例如 `sk-...abcd`。列表和详情
  直接展示该字段，不为展示而解密。
- `params`: 扁平参数 JSON，key 和大小受 API schema 约束。
- `extra_body`: 厂商特定参数透传 JSON，允许范围宽，但必须限制总大小、深度和字符串长度。
- `prompt_cache`: `NULL` 表示关闭；非 `NULL` 时存 `PromptCacheConfig` 的 dict 形态。
- `supports_streaming`: 是否通过 streaming 验证。
- `supports_tool_calling`: 是否通过 tool calling 验证。direct/planner agent 必须要求它。
- `supports_vision`: 是否允许当前用户把图片输入发给该 BYOK model。
- `verification_status`: `unverified | verified | failed`。
- `version`: 配置语义版本。用户更新配置（key、base_url、model、params、capabilities、
  enabled）时递增；主密钥轮换不算配置语义更新，不递增 `version`，见 6.4。Job 携带提交时
  版本，Worker 执行前若版本不一致则 fail-fast。
- 若 MySQL 版本支持，迁移脚本可给 JSON 字段增加 `CHECK JSON_TYPE(...) = 'OBJECT'`，
  避免 `params`、`extra_body`、`prompt_cache` 存入 array 或 scalar。
- `display_name`、`base_url`、`model` 写入前必须 trim，trim 后不得为空。
- `(user_id, display_name)` 唯一约束会受 collation 影响。产品层应把大小写敏感性说清楚，
  API 层建议按 DB collation 行为做同名检测与错误提示。

### 5.2 DAO

新增 `src/dao/user_llm_config_table.py`，`UserLLMConfigTable(BaseTable)`。建议通过
singleton service 复用 DAO 实例，避免每次请求新建 `BaseTable` 时触发 `init_table()`
的 `information_schema` 检查。

接口:

- `create(user_id, *, display_name, base_url, model, api_key_cipher, api_key_hint, key_version, params, extra_body, prompt_cache, supports_streaming, supports_tool_calling, supports_vision) -> int`
- `get(user_id, config_id) -> dict | None`
- `get_for_run(user_id, config_id) -> dict | None`
- `list_by_user(user_id) -> list[dict]`
- `update(user_id, config_id, **fields) -> bool`
- `delete(user_id, config_id) -> bool`

约束:

- 所有读写都以 `user_id` 为限定条件，防止跨用户访问。
- DAO 不负责掩码逻辑。DAO 可返回 DB row，service/API 层通过 `to_config_out(row)` 丢弃
  `api_key_cipher`，只把 `api_key_hint` 放进响应。
- `update` 修改任意运行相关字段时递增 `version`，包括 key、base_url、model、params、
  capabilities、enabled 状态。
- `delete` 可以物理删除；如果产品希望保留审计，可改为 `is_enabled=0` 的软删除，但本设计
  默认不要求主代码做兼容兜底。

## 6. 加密与密钥管理

### 6.1 特性开关与密钥来源

新增 `src/utils/secret.py`，封装 Fernet:

- `MATMASTER_BYOK_ENABLED=true` 时启用 BYOK API 与 chat BYOK 参数。
- 启用 BYOK 时，API 进程与 Worker 进程都必须配置同一个
  `MATMASTER_BYOK_FERNET_KEY`。
- 进程启动或首次加载 BYOK secret service 时校验密钥存在且格式合法，缺失即 fail-fast。
- 未启用 BYOK 时，CRUD API 返回 404 或 503，chat 请求中携带 `custom_llm_config_id`
  返回 4xx。这样没有启用 BYOK 的部署不会因为新增模块而被迫配置密钥。

```python
from cryptography.fernet import Fernet

def encrypt(plaintext: str) -> str: ...
def decrypt(token: str) -> str: ...
def hint(plaintext: str) -> str: ...
```

### 6.2 加解密边界

- 写入: API 在 CRUD 端点用 `encrypt()` 把 api_key 转成密文，存 `api_key_cipher`；
  同时用 `hint()` 生成 `api_key_hint`。
- 入队: API 不解密，也不把 `api_key_cipher` 放进 Redis Job。Job 只带
  `custom_llm_config_id` 与 `version`。
- Worker: 取 Job 后根据 session owner 的 `user_id` 与 `config_id` 回查 DB，确认
  `is_enabled=1` 且 `version` 匹配，然后用 `decrypt()` 还原明文 api_key，构造 provider。

明文 api_key 不持久化、不入队、不记录日志，仅在用户提交和 Worker 调用 provider 所需的
内存路径中出现。Python 字符串生命周期受运行时管理，不能承诺内存中立即擦除，因此文档
不使用“短暂出现后必然清零”这类无法验证的说法。

### 6.3 掩码回显

- 列表与详情 API 返回 `api_key_hint`，不解密、不回传完整明文。
- 创建和更新时可以基于用户刚提交的明文生成响应中的 hint。
- 前端修改 api_key 时整条覆盖，不支持在已有密文上局部编辑。

### 6.4 密钥轮换

区分两种轮换，关键差异是是否递增 `version`:

- 主密钥轮换: 更换 `MATMASTER_BYOK_FERNET_KEY`，用旧主密钥逐条解密、用新主密钥重新
  加密、回写 `api_key_cipher`。api_key 明文不变，只递增 `key_version`，不递增
  `version`。因为 Worker 执行前回查 DB 本就会拿到新密文并用新主密钥解密，在途任务应
  无感继续；若此时递增 `version`，会让所有在途 Job 版本不匹配而被白白 fail-fast。
- 用户更换 api_key: 用户提交新明文走正常 update，递增 `version`。这是配置语义变更，
  在途引用失效是预期行为。

主密钥轮换走外部脚本，主代码不实现多密钥并存或解密回退。

长期如安全要求提高，可演进到 KMS/Vault 管理主密钥、envelope encryption、按环境和用途
隔离密钥。MVP 只要求表里保留 `key_version`，不在主代码中引入多 key 兼容路径。

### 6.5 日志与脱敏红线

新增 `src/services/byok_redaction.py`，集中处理 BYOK 相关日志、异常和调试 payload 脱敏。

要求:

- `BYOKConfigCreate.api_key` 使用 Pydantic `SecretStr`，或至少在 model config 中避免
  repr/log 直接输出。
- API access log 不记录 request body。
- 所有异常与日志禁止包含 `api_key`、`api_key_cipher`、`Authorization`、provider request
  headers 或完整 provider error body。
- Redis Job payload、provider 初始化参数、`LLMProfileConfig` 相关日志必须 redaction。
- Sentry、OpenTelemetry、structured log 若接入，需要对 `api_key`、`api_key_cipher`、
  `authorization`、`secret`、`token` 等字段做字段级脱敏。
- Worker 解密失败只记录 `user_id`、`config_id`、`key_version`、错误类别，不记录 token。
- provider 返回错误体写入 `verification_error` 前必须截断并脱敏。

## 7. Endpoint 安全策略

新增 `src/services/byok_endpoint_policy.py`，用于 CRUD 写入和 Worker 执行前校验
`base_url`。两处都要校验，防止配置写入后环境、DNS 或记录内容变化导致执行时 SSRF。

最低规则:

- 只允许 `https://`。
- 禁止 URL 中出现 username/password/userinfo。
- 禁止 localhost、loopback、private、link-local、reserved、multicast IP。
- 禁止云厂商 metadata 地址，例如 `169.254.169.254`。
- host 必须可解析；解析出的所有 A/AAAA 记录都必须通过 IP 安全检查。
- 禁止 query string 携带 token。`base_url` 应是干净 endpoint，不是认证载体。
- 限制端口。默认只允许 443；若后续需要自定义端口，必须单独配置 allowlist。
- 标准化保存 URL，例如去掉末尾重复 `/`，不允许路径中包含明显的跳转参数。
- 发起验证请求或 provider 请求时禁止自动跟随未经校验的 redirect；如底层 SDK 发生
  redirect，redirect 后目标也必须通过同一套 endpoint policy。
- 可选增加部署级 allowlist，例如只允许指定域名后缀或公网地址段。若要支持内网 vLLM/Ollama，
  必须是部署级受控能力，不能默认开放给所有用户。

执行前再校验的原因:

- 用户可能在任务排队后修改 DNS 解析。
- DB 中老配置可能来自策略收紧前。
- Worker 才是真正发起外部请求的进程，执行前必须拥有最后一道防线。

## 8. 配置 Schema 与参数映射

### 8.1 参数模型

`src/models/byok.py` 定义 `BYOKConfigCreate`、`BYOKConfigUpdate`、`BYOKConfigOut`、
`BYOKRunReference` 等模型。

`params` 是扁平 JSON，API 层白名单校验 key。建议第一版支持:

- `temperature`
- `max_tokens`
- `top_p`
- `frequency_penalty`
- `presence_penalty`
- `reasoning_effort`
- `seed`
- `stop`

约束:

- `temperature`: number，`0 <= x <= 2`。
- `top_p`: number，`0 <= x <= 1`。
- `max_tokens`: int，`1 <= x <= BYOK_MAX_OUTPUT_TOKENS`。系统上限由部署配置控制。
- `frequency_penalty`: number，`-2 <= x <= 2`。
- `presence_penalty`: number，`-2 <= x <= 2`。
- `reasoning_effort`: enum，第一版建议只允许 `low | medium | high`；若某 endpoint 需要
  其它取值，放进 `extra_body`，并由用户自担兼容风险。
- `seed`: int，范围按 Pydantic / provider 可接受 int 约束。
- `stop`: string 或 list[string]，限制元素数量、单个字符串长度和总长度。
- `params` 序列化后不超过 8 KiB。
- `extra_body` 序列化后不超过 32 KiB。
- `prompt_cache` 序列化后不超过 4 KiB。
- JSON 最大嵌套深度建议不超过 8。
- 单个字符串长度建议不超过 8 KiB。
- `extra_body` 必须是 JSON object，不允许 array、string、number、boolean 或 null。
- `extra_body` 允许厂商特定字段，但禁止携带明显凭据字段，例如 `api_key`、
  `authorization`、`secret`、`token`。认证只允许通过 DB 中加密 key 注入。
- `extra_body` 不得覆盖核心 provider 字段，例如 `messages`、`tools`、`stream`、
  `model`、`temperature`、`max_tokens`。这些字段由 MatMaster 的消息、工具和 profile
  映射控制。

prompt cache 单独建模，因为它不是简单顶层参数，而是 provider 在消息 block 上添加
`cache_control` 断点。它沿用现有 `PromptCacheConfig` 和
`AnthropicPromptCacheOptions` 路径。

prompt cache 是高级兼容选项，仅适用于支持相应 `cache_control` 协议的 endpoint。
不支持时不自动降级，按 endpoint 返回错误 fail-fast。前端应提示这不是通用 OpenAI 参数。

### 8.2 能力模型

BYOK 配置至少记录:

```python
class BYOKCapabilities(BaseModel):
    supports_streaming: bool = False
    supports_tool_calling: bool = False
    supports_vision: bool = False
    verification_status: Literal["unverified", "verified", "failed"] = "unverified"
    verification_error: str | None = None
    verified_at: datetime | None = None
```

第一版可以允许用户手动填写能力，但 agent 执行路径必须按字段 gate:

- direct/planner run 需要 `supports_streaming=True`。
- direct/planner run 需要 `supports_tool_calling=True`。
- 当前轮有图片输入时需要 `supports_vision=True`。

能力验证不在 create/update 内联，统一走 §11 的 `/test` 端点：保存前用 `/test`，已保存
用 `/{id}/test`，验证分层见该节。验证失败不自动删除配置，只置
`verification_status=failed`。是否允许未验证配置执行由产品策略决定；本设计建议 agent
主路径只允许 `verified` 或管理员明确放开的配置。

### 8.3 `LLMProfileConfig` 透传字段扩展

在 `matmaster/config/llm.py` 的 `LLMProfileConfig` 增加两个可选字段:

```python
passthrough_params: dict[str, Any] | None = None
passthrough_extra_body: dict[str, Any] | None = None
```

`build_extra_kwargs()` 在现有 thinking / reasoning 推导逻辑之后追加合并，用户透传
优先:

```python
if self.passthrough_params:
    out.update(self.passthrough_params)
if self.passthrough_extra_body:
    extra_body.update(self.passthrough_extra_body)
if extra_body:
    out["extra_body"] = extra_body
return out or None
```

预设 profile 不设这两个字段，行为不变。第一版 `passthrough_params` 与
`passthrough_extra_body` 只由 BYOK resolver 构造出的 profile 使用；若未来要在预设
YAML profile 中使用，必须单独 review，并补充覆盖现有 reasoning 推导的测试。
BYOK profile 的 `reasoning_protocol` 默认为 `None`，推导段为空，透传段直接生效。

### 8.4 映射对照

Worker 回查 DB 并解密后，将 BYOK 配置组装成一个 `LLMProfileConfig`:

| 来源 | `LLMProfileConfig` 字段 | 最终用途 |
|---|---|---|
| DB `base_url` | `base_url` | `OpenAIProvider.base_url` |
| DB `model` | `model` | `OpenAIProvider.model` |
| 解密后的 api_key | `api_key` | `OpenAIProvider.api_key` |
| DB `key_version` | 不进入 profile | 解密与审计元数据 |
| `params.temperature` | `temperature` | `OpenAIProvider.temperature` |
| `params.max_tokens` | `max_tokens` | `OpenAIProvider.max_tokens` |
| `params` 其余键 | `passthrough_params` | `extra_kwargs` 顶层 |
| `extra_body` | `passthrough_extra_body` | `extra_kwargs["extra_body"]` |
| `prompt_cache` | `prompt_cache` | `prompt_cache_options` |
| `supports_vision` | `supports_vision` | API 和 Worker 的 image capability gate |
| 固定默认 | `provider="openai"`、`timeout`、`max_retries` 等 | provider 构造参数 |

BYOK 的超时/重试第一版用保守默认:

- `timeout=600`
- `stream_timeout=120`
- `stream_idle_timeout=60`
- `max_retries=2`
- `retry_delay=1.0`

是否暴露给用户见开放问题。

## 9. 路由接入与工厂重构

### 9.1 `custom_llm_config_id` 流转

显式参数贯穿调用链:

- `src/models/chat.py` 的 `ChatSendRequest` 增加
  `custom_llm_config_id: int | None = None`。
- `ChatSendRequest` 或 API/service 层校验:
  `custom_llm_config_id` 非空时，`llm` 与 `model` 必须为空。
- `src/apis/chat_api.py` 在入队前调用 `BYOKModelResolver.resolve_for_preflight()`:
  - 校验 BYOK 功能是否启用。
  - 校验 `(user_id, custom_llm_config_id)` 存在。
  - 校验 `is_enabled=1`。
  - 校验 endpoint policy。
  - 校验 direct/planner 所需能力。
  - 若本轮有图片，校验 `supports_vision`。
  - 返回 `config_id`、`version`、display name、model、capabilities，不返回密文。
- `src/services/stream_service.py` 在 `SendStreamContext` 中携带
  `byok_ref: BYOKRunReference | None`。
- Redis Job 新增 `byok` 字段，只包含引用:

```json
{
  "byok": {
    "config_id": 12,
    "version": 3
  }
}
```

- `src/worker/agent_worker.py` 从 payload 取 `byok`，根据当前 session owner 的
  `user_id` 调用 `BYOKModelResolver.resolve_for_worker_run()`:
  - 回查 DB。
  - 校验 `is_enabled=1`。
  - 校验当前 `version` 与 Job 中版本一致。
  - 执行 endpoint policy。
  - 解密 api_key。
  - 构造 `LLMProfileConfig`。
- Worker 将 `byok_profile`、`byok_config_id`、`byok_config_version` 传给
  `AgentRunService.run_agent()`。

### 9.2 解析优先级

BYOK 与预设模型互斥，不做同时传参后的优先级覆盖。

```text
custom_llm_config_id 非空:
  要求 llm is None 且 model is None
  走 BYOK

custom_llm_config_id 为空:
  model_override > llm_override > agent default > llm_config.default
```

这样 API quota、history metadata、Worker provider 构造都不会对同一请求产生不同解释。

### 9.3 工厂重构

把 `matmaster/providers/llm_factory.py` 中 profile 到 provider 的构造逻辑抽为:

```python
def build_provider_from_profile(
    profile: LLMProfileConfig,
    model: str,
) -> OpenAIProvider | BedrockProvider:
    ...
```

预设路径:

```text
resolve_route -> get_profile -> build_provider_from_profile
```

BYOK 路径:

```text
BYOKModelResolver -> LLMProfileConfig -> build_provider_from_profile
```

`AgentRunService.run_agent()` 中的关键分支:

```python
if byok_profile is not None:
    if current_images and not byok_profile.supports_vision:
        raise ImageInputError(...)
    image_detail = byok_profile.vision_detail if current_images else None
    provider = build_provider_from_profile(byok_profile, byok_profile.model)
    bundle = LLMProviderBundle(
        provider=provider,
        model=byok_profile.model,
        model_profile=f"byok:{byok_config_id}",
        model_route=None,
        provider_name="openai",
        model_family=None,
    )
else:
    image_detail = image_service.resolve_image_detail(
        llm_config=llm_config,
        images=current_images,
        llm_override=llm_override,
        model_override=model_override,
        default_profile_key=agent_default_llm,
    )
    bundle = build_provider_bundle(...)
```

run 元数据中:

- `llm_model`: 实际 model 名。
- `llm_model_profile`: `byok:<id>`。
- `llm_model_route`: `None`。

历史回放只展示模型身份，不重新调用 provider。配置后续删除不影响回放展示。

## 10. 数据流

```text
[设置页 CRUD]
  POST /api/v1/llm-configs
    { display_name, base_url, model, api_key, params, extra_body,
      prompt_cache, supports_streaming, supports_tool_calling, supports_vision }
    -> UserService.require_user_id -> user_id
    -> BYOKEndpointPolicy.validate(base_url)
    -> validate params / extra_body / prompt_cache size
    -> secret.encrypt(api_key) -> api_key_cipher
    -> secret.hint(api_key) -> api_key_hint
    -> key_version = current BYOK key version
    -> UserLLMConfigTable.create(user_id, ...)

[发消息: API 预检]
  POST /api/v1/chat/sessions/{sid}/stream
    { content, custom_llm_config_id }
    -> custom_llm_config_id 与 llm/model 互斥校验
    -> BYOKModelResolver.resolve_for_preflight(user_id, config_id)
       -> owner/enabled/version/capabilities/endpoint policy
    -> quota 分流
       -> BYOK 跳过 model-level platform quota
       -> 可保留 global quota 或单独 BYOK run limit
    -> 若 req.images 非空:
       -> validate_current_images
       -> BYOK capabilities.supports_vision gate

[入队]
  prepare_send_message
    -> user_msg 记录 requested_byok_config_id / requested_model
    -> SendStreamContext.byok_ref = {config_id, version}
  generate_send_stream
    -> job["byok"] = {"config_id": 12, "version": 3}
    -> Redis LPUSH

[worker]
  BLPOP -> payload
    -> session_user_id = sessions_service.get_session_user_id(session_id)
    -> byok = payload.get("byok")
    -> 若存在:
       -> BYOKModelResolver.resolve_for_worker_run(
            user_id=session_user_id,
            config_id=byok["config_id"],
            expected_version=byok["version"],
          )
       -> 当前版本不一致 / 禁用 / 删除 -> fail-fast
       -> decrypt api_key
       -> byok_profile = LLMProfileConfig(...)
    -> run_agent(..., byok_profile=byok_profile, byok_config_id=config_id)

[run_agent]
  BYOK:
    -> image gate from byok_profile.supports_vision
    -> build_provider_from_profile(byok_profile, byok_profile.model)
  预设:
    -> load_llm_config
    -> resolve_route
    -> build_provider_bundle
  -> AgentRunRequest(llm_provider, llm_model, llm_model_profile, ...)
  -> kernel_resources.llm_provider.chat_stream(messages, tools)
```

## 11. API 契约

新增 `src/apis/byok_api.py`，挂在 `api_router` 下，实际路径为
`/api/v1/llm-configs`。全部经 `UserService.require_user_id` 鉴权。

- `POST   /api/v1/llm-configs`: 新建配置，返回不含密钥的配置。
- `GET    /api/v1/llm-configs`: 列出当前用户配置。
- `GET    /api/v1/llm-configs/{id}`: 读取单条配置。
- `PATCH  /api/v1/llm-configs/{id}`: 部分更新配置；字段缺省表示不改，`api_key`
  传新值表示整条覆盖。
- `DELETE /api/v1/llm-configs/{id}`: 删除配置。
- `POST   /api/v1/llm-configs/test`: 测试尚未保存的配置，不落库，用于前端保存前验证。
- `POST   /api/v1/llm-configs/{id}/test`: 测试已保存配置，成功或失败都更新 `verification_*` 字段。

测试动作分层:

1. 基础连通性: endpoint 可访问且通过 endpoint policy。
2. 鉴权: api_key 有效。
3. 模型存在: model 可调用。
4. agent 必需能力: streaming 与 tool calling。
5. vision: 若用户声明 `supports_vision=true`，执行最小 vision 请求或标记为未验证。

create/update 不强制自动测试，避免费用、慢 endpoint 或临时不可用阻塞配置保存。前端可以在
保存后提示用户手动测试连接。

`PATCH` 请求语义:

- 字段缺省: 不更新。
- 字段传 `null`: 只有明确声明 nullable 的字段允许清空，例如 `prompt_cache`。
- `api_key`: 只允许传新明文并整条覆盖，不允许返回、局部修改或传空字符串。
- 修改 key、base_url、model、params、extra_body、prompt_cache、capabilities、enabled
  状态都会递增 `version`。

`BYOKConfigOut` 返回:

- `id`
- `display_name`
- `base_url`
- `model`
- `api_key_hint`
- `key_version`
- `params`
- `extra_body`
- `prompt_cache`
- `supports_streaming`
- `supports_tool_calling`
- `supports_vision`
- `verification_status`
- `verification_error`: 脱敏、截断后的最近验证错误。
- `verified_at`
- `is_enabled`
- `version`
- `created_at`
- `updated_at`

`ChatSendRequest` 增加:

```python
custom_llm_config_id: int | None = None
```

请求约束:

- `custom_llm_config_id` 非空时，`llm` 和 `model` 必须为空。
- `custom_llm_config_id` 只对发送消息有效；subscribe-only 请求不需要解析 BYOK。

前端设置页做配置增删改查。Chat model 选择器中把用户 BYOK 配置作为一个独立分组，与预设
模型并列。

## 12. 配额、计费与用量记录

BYOK 不消耗平台提供的模型额度，但仍占用平台 Worker、Redis 队列、SSE 连接、会话存储、
工具执行和 Bohrium 等资源。

第一版建议:

- API 侧保留 `check_quota(user_id)` 作为平台访问权限或基础风控 gate，具体文案避免写成
  纯 LLM 免费额度。
- BYOK 请求跳过 `check_model_quota(user_id, model_route_key)`，因为没有平台 model route。
- BYOK 成功路径不调用 `use_quota(user_id, model_key=model_override)` 扣减平台模型级 quota。
- 如需限制 BYOK 滥用，新增独立的 BYOK run limit 或 concurrency limit，不复用平台模型
  quota key。
- run result 中仍记录 provider 返回的 token usage，但 usage 只作为展示和审计，不直接映射
  平台模型成本。
- 限制每用户 BYOK 配置数量，第一版建议 5-20 条，由部署配置控制。
- 限制 BYOK run 并发，至少不得超过平台对单用户会话并发的现有限制。
- `max_tokens`、timeout、retry、stream idle timeout 都使用系统上限，不暴露无限制运行参数。
- `/test` 接口需要 rate limit，防止反复测试消耗平台出网和 Worker/API 资源。

`AgentRunService` 需要显式知道本轮 `billing_mode`:

```python
billing_mode: Literal["platform", "byok"]
```

预设模型为 `platform`，BYOK 为 `byok`。

## 13. 失败语义

以下情况直接 fail-fast，不回退默认 model，不静默关 cache:

- `custom_llm_config_id` 与 `llm/model` 同时传入。
- `custom_llm_config_id` 指向的配置不存在、不属于当前 `user_id`、`is_enabled=0`。
- Worker 执行前发现当前 `version` 与 Job 中版本不一致。
- `MATMASTER_BYOK_ENABLED` 未启用但请求使用 BYOK。
- `MATMASTER_BYOK_FERNET_KEY` 缺失或非法。
- `decrypt()` 失败。
- `base_url` 未通过 endpoint policy。
- `params` 含白名单外 key。
- `params`、`extra_body`、`prompt_cache` 超过大小或结构限制。
- direct/planner run 所需的 streaming 或 tool calling 能力缺失。
- 当前轮有图片但 `supports_vision=False`。
- `supports_vision=True` 但 endpoint 实际不支持图片时，不在 MatMaster 内自动降级为文本
  请求，按 provider 错误返回，并可在测试接口中更新验证失败状态。

endpoint 侧运行期错误，例如 401、timeout、connection failed、bad request，复用
`OpenAIProvider` 现有错误映射，作为正常 LLM 调用错误返回。

用户可见错误文案建议:

- 配置不存在或已删除: `该自定义模型配置不存在，请重新选择模型。`
- 配置已禁用: `该自定义模型配置已禁用，请启用后重试。`
- 配置已更新: `该自定义模型配置已更新，请重新发送本轮消息。`
- endpoint 不安全: `该模型 endpoint 不符合安全要求，请使用公开 HTTPS 地址。`
- 能力不足: `该自定义模型未通过工具调用/流式输出验证，不能用于 Agent 模式。`

## 14. 测试计划

### 14.1 加密

新增 `tests/.../test_secret.py`:

- `encrypt` 到 `decrypt` 往返还原明文。
- 错误密钥或损坏 token 解密失败。
- `hint` 不包含完整明文。
- BYOK enabled 但 env key 缺失时 fail-fast。
- 写入路径带上当前 `key_version`。
- 主密钥轮换脚本重新加密 `api_key_cipher` 时只递增 `key_version`，不递增 `version`；
  用户更换 api_key 才递增 `version`。

### 14.2 Endpoint policy

新增 `tests/.../test_byok_endpoint_policy.py`:

- 允许合法 HTTPS 公网域名。
- 拒绝 `http://`。
- 拒绝 `file://`、`ftp://`、`gopher://` 等非 HTTPS scheme。
- 拒绝 localhost、127.0.0.1、`::1`。
- 拒绝 private/link-local/reserved IP。
- 拒绝 `169.254.169.254`。
- 拒绝带 userinfo 的 URL。
- 拒绝 query string 中疑似 token 的 URL。
- DNS 解析到 private IP 时拒绝。
- redirect 到 private、loopback、metadata 或非 HTTPS 目标时拒绝。
- Worker 执行前再次调用 endpoint policy，不能只依赖写入时校验。

### 14.3 参数与脱敏安全

新增 `tests/.../test_byok_validation_and_redaction.py`:

- 超出范围的 `temperature`、`top_p`、`max_tokens`、penalty 参数被拒绝。
- 未知 `params` key 被拒绝。
- `extra_body` 非 object 被拒绝。
- 过大的 `params`、`extra_body`、`prompt_cache` 被拒绝。
- `extra_body` 中携带 `api_key`、`authorization`、`secret`、`token` 等凭据字段被拒绝。
- `extra_body` 覆盖 `messages`、`tools`、`stream`、`model` 等核心字段被拒绝。
- API response 不包含 `api_key` 或 `api_key_cipher`。
- Redis Job payload redaction 后不包含密钥字段。
- provider 构造失败、Worker 解密失败、测试接口失败日志不包含明文 key、密文 token
  或 Authorization header。
- provider error 写入 `verification_error` 前会脱敏并截断。

### 14.4 DAO

新增 `tests/.../test_user_llm_config_table.py`:

- CRUD 正常路径。
- `(user_id, display_name)` 唯一约束冲突。
- 跨 `user_id` 读取/更新/删除返回空或失败。
- service/API 层的 `to_config_out(row)` 丢弃 `api_key_cipher`，只返回 `api_key_hint`。
- `key_version` 可写入和读出，但不进入 provider profile。
- update 后 `version` 递增。

### 14.5 BYOK 模型与 resolver

新增 `tests/.../test_byok_model_resolver.py`:

- API preflight 只返回非密钥字段。
- Worker resolve 回查 DB、校验版本、解密并构造 `LLMProfileConfig`。
- 禁用、删除、版本不匹配都 fail-fast。
- endpoint policy 在 API 与 Worker 两侧都会调用。
- direct/planner 缺少 streaming/tool calling 时拒绝。
- 有图片但缺少 vision 时拒绝。
- 有图片且用户声明 vision，但 endpoint 实际失败时按 provider 错误返回，不自动降级。

### 14.6 配置模型

扩展 `tests/matmaster/config/test_llm.py`:

- `passthrough_params` 合并进 `build_extra_kwargs()` 顶层。
- `passthrough_extra_body` 合并进 `extra_body`。
- 用户透传覆盖推导值。
- 未设透传字段的预设 profile 行为不变。
- 预设 YAML profile 若未来启用 passthrough 字段，必须单独补测试覆盖 reasoning/cache
  推导覆盖关系。

### 14.7 工厂

扩展 `tests/matmaster/providers/test_llm_factory.py`:

- `build_provider_from_profile` 对预设 profile 构造出与原
  `build_provider_bundle` 一致的 provider。
- BYOK profile 构造出带正确 `api_key`、`base_url`、`model`、`extra_kwargs`
  的 `OpenAIProvider`。
- `prompt_cache` 非空时带 `AnthropicPromptCacheOptions`，为空时不带。

### 14.8 API 与 stream service

扩展 `tests/apis` 与 `tests/test_chat_stream_direct.py`:

- `PATCH /api/v1/llm-configs/{id}` 缺省字段不更新，`api_key` 只允许整条覆盖。
- `POST /api/v1/llm-configs/test` 测试未保存配置且不落库。
- `POST /api/v1/llm-configs/{id}/test` 测试已保存配置并更新验证字段。
- `custom_llm_config_id` 与 `llm/model` 同时传时报 4xx。
- BYOK 请求跳过 model-level quota。
- BYOK 图片请求走 BYOK `supports_vision` gate，不走静态 `LLMConfig.resolve_route()`。
- `prepare_send_message` 在 user history 中记录 BYOK 请求元数据，但不记录密钥。
- `generate_send_stream` 入队 Job 只包含 `byok.config_id` 与 `byok.version`，
  不包含 `api_key_cipher`。

### 14.9 Worker 集成

扩展 `tests/.../test_agent_worker.py`:

- Job 携带 BYOK reference，Worker 回查 DB、解密、传入 `run_agent`。
- 版本不匹配时发送明确错误事件并关闭 stream。
- 禁用或删除后新 run fail-fast，已入队但未执行的 run 因 Worker 回查 DB 也 fail-fast。
- Job 缺失 `byok` 时走现有预设路径。

### 14.10 AgentRunService

扩展 `tests/.../test_agent_run_service.py`:

- `byok_profile` 存在时走 BYOK bundle。
- `model_profile == "byok:<id>"`，`model_route is None`。
- BYOK 图片 detail 来自 `byok_profile.vision_detail`。
- BYOK 成功路径不扣减平台模型级 quota。
- 预设路径行为不变。

### 14.11 验证命令

```bash
uv run pytest \
  tests/matmaster/config/test_llm.py \
  tests/matmaster/providers/test_llm_factory.py \
  tests/matmaster/services/test_agent_run_stream_images.py \
  tests/matmaster/services/test_agent_run_stream.py \
  tests/test_chat_stream_direct.py \
  tests/apis/
```

新增测试按现有 `tests/` 镜像约定落位。

## 15. 实施顺序

1. `src/utils/secret.py` 与 `src/services/byok_redaction.py`: Fernet 封装、feature flag、
   `SecretStr`/日志/异常脱敏、单测。
2. `src/services/byok_endpoint_policy.py`: URL、DNS、IP、redirect 安全策略与单测。
3. `src/models/byok.py`: CRUD schema、参数类型/范围/大小限制、capabilities、PATCH
   语义、脱敏输出模型。
4. `src/sql/migrate_add_user_llm_config.sql`: 表结构、`api_key_hint`、`key_version`、
   JSON object 约束建议。
5. `src/dao/user_llm_config_table.py`: per-user CRUD、version 递增、DAO 与 response
   脱敏职责分离。
6. `matmaster/config/llm.py`: 增加透传字段，扩展 `build_extra_kwargs()`。
7. `matmaster/providers/llm_factory.py`: 抽出 `build_provider_from_profile()`。
8. `src/apis/byok_api.py` 与 `src/apis/api_router.py`: CRUD API、`PATCH`、未保存/已保存
   `/test` API、rate limit。
9. `src/services/byok_model_resolver.py`: API preflight 与 Worker run resolve，共用能力
   gate、endpoint policy 和 profile 组装逻辑。
10. `src/models/chat.py`: 增加 `custom_llm_config_id`，并定义与 `llm/model` 冲突时返回 4xx。
11. `src/apis/chat_api.py`: BYOK preflight、vision gate、quota 分流。
12. `src/services/stream_service.py`: `SendStreamContext.byok_ref` 与 Redis Job reference，
    确认 payload 不含密钥或密文。
13. `src/worker/agent_worker.py`: Worker 回查 DB、版本校验、endpoint policy、解密、构造
    profile。
14. `src/services/agent_run_service.py`: BYOK profile 分支、image detail、billing mode。
15. 运行 §14 验证命令和新增安全回归测试。
16. 前端设置页与选择器另行排期。

## 16. 性能说明

BYOK 增加的主要同步成本是 DB 回查:

- API preflight 一次 indexed read。
- Worker 执行前一次 indexed read。

在有连接池的情况下，单次查询通常是毫秒级。当前 `BaseTable.get_connection()` 每次会
`pymysql.connect()` 并关闭连接，因此实际成本会被连接建立放大。为了避免无意义放大:

- `UserLLMConfigTable` 不应在每个请求中反复实例化。
- 后续可给 DAO 层引入连接池；BYOK 本身不要求主链路绕过 DB 查询。
- 不应为了省这两次查询把可解密密文塞进 Redis。安全与撤销语义优先。

相对 agent run 的模型首 token 和完整执行时间，这两次 indexed read 的成本可接受。

## 17. 边界说明

API/Worker 分离:

- API 入队前做权限、能力、vision、quota 预检，但不解密密钥。
- Redis Job 只携带 BYOK 配置引用与版本。
- Worker 是最终执行方，必须重新回查 DB、校验版本、执行 endpoint policy、解密。
- run 元数据只记录 `llm_model` 与 `byok:<id>` 标识，不记录任何凭据。

与既有机制的关系:

- 预设 model 路径行为不变。
- BYOK 故意比预设路径更严格: 预设 profile 改 YAML 后，在途任务会用新值（Worker 重新
  `load_llm_config`），不做版本拦截；BYOK 是用户自管的凭据，用 `version` 做乐观并发，
  配置语义变更即让在途引用 fail-fast。这个不一致是有意为之。
- 不动 `AgentKernel`、`Message`、历史 checkpoint 与 runtime ports。
- 不把 BYOK 能力塞进 `run_meta` 或 `RuntimePorts`。
- 不动静态 `routes` 表，BYOK 不写入也不读取它。
- `TurnInput` 仍是当前轮文本、附件和图片 detail 的 canonical carrier。BYOK 只改变
  模型能力判断和 provider 构造，不改变 `TurnInput` 优先于 top-level `images` 的语义。

## 18. 开放问题

- 是否要求 BYOK 配置必须 `verification_status=verified` 才能执行，还是允许用户手动确认后
  使用 unverified 配置。
- 是否暴露 `timeout`、`stream_timeout`、`stream_idle_timeout`、`max_retries` 给用户。
- BYOK 是否需要独立 run limit。第一版可以保留全局 access quota，但不扣平台模型级 quota。
- 是否采用软删除以保留审计记录。若采用软删除，`delete` 改成 `is_enabled=0` 并递增 version。
- 前端选择器中 BYOK 分组与预设模型的排序和展示。
