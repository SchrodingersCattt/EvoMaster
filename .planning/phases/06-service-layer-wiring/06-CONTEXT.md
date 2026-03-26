# Phase 6: Service Layer Wiring - Context

**Gathered:** 2026-03-22
**Status:** Ready for planning

<domain>
## Phase Boundary

Service 层存根全部接线到真实实现，生产 run 可端到端执行。打通 `_build_llm_provider`（LLM 工厂 + provider 路由）、`_get_builtin_tools`（builtin tool 构建）、PlaygroundContext 优化（携带 session/config_dir），清理 DirectExp 与 service 层的交互杂质。

Guard 业务逻辑迁移不在范围内（manuscript gate 目标已弃用，auth failure gate 非管线打通必需）。WorkerRegistry 适配保留。

</domain>

<decisions>
## Implementation Decisions

### LLM 工厂与 Provider 路由
- **D-01:** 所有模型通过 LiteLLM 代理走 OpenAI 兼容接口，单一 base_url + api_key（`LITELLM_PROXY_API_BASE` / `LITELLM_PROXY_API_KEY`）
- **D-02:** `llm_override` 参数废弃（前端不再使用），`model_override` 是唯一的前端覆盖参数（如 `azure/gpt-5`）
- **D-03:** Config 驱动的 provider 路由，硬编码模型族到参数模板的映射。根据 `model_override` 匹配模型族，使用对应参数模板实例化 OpenAIProvider
- **D-04:** 模型族参数模板覆盖以下 provider 差异：
  - **Claude 4.6**（opus-4-6 / sonnet-4-6）: `anthropic_adaptive_thinking` 协议 — `thinking: {type: 'adaptive'} + output_config: {effort: ...}`，temperature 强制为 1
  - **Claude 4.5**（haiku-4-5）: 旧版 thinking 协议 — `thinking: {type: 'enabled', budget_tokens: N}`
  - **GPT-5 / Azure**: `reasoning_effort` 参数
  - **Gemini**: 无特殊 reasoning 参数
  - **通用 OpenAI 兼容**（qwen / cds）: 基础参数
- **D-05:** 参考 `evomaster/utils/llm.py` 中 `_MODEL_FAMILY_DEFAULTS`、`_infer_model_family_from_model`、`_build_reasoning_request_overrides` 等已有逻辑

### Builtin Tool 注册
- **D-06:** FinishTool 已在 Phase 1 明确弃用（终止条件为 LLM 无 tool_calls 或 max_turns）。Builtin tools 只有 3 个：BashTool、EditorTool、MonitorJobTool
- **D-07:** Builtin tools 保留 EvoMaster 实现（BashTool/EditorTool/MonitorJobTool），通过 EvoToolAdapter 包装为 matmaster Tool Protocol
- **D-08:** 工具构建在 DirectExp.assemble(ctx) 内部完成，通过 ctx.session 获取 session 进行 EvoToolAdapter 绑定。Service 层不再负责构建 builtin tools，`_get_builtin_tools()` 方法移除

### PlaygroundContext 优化
- **D-09:** PlaygroundContext 新增 `session: Any = None` 字段（需 `arbitrary_types_allowed=True`），Playground.prepare() 构建 session 后写入 ctx
- **D-10:** PlaygroundContext 新增 `config_dir: Path | None = None` 字段，携带 playground 配置目录路径
- **D-11:** DirectExp 构造参数移除 `session` 和 `config_dir`（改从 ctx 读取）。移除 `builtin_tools`（assemble 中自行构建）
- **D-12:** Service 层去掉 hacky 的 `playground.session if hasattr(...)` 和 `playground.config_path.parent if hasattr(...)` 访问

### Guard 处理
- **D-13:** 移除 ManuscriptGateGuard 和 AuthFailureGateGuard 的 shell 实现（`matmaster/assembly/guards.py` 中的两个 always-allow 壳）
- **D-14:** Phase 6 不注入任何业务 guard。ManuscriptGate 目标（finish tool）已弃用；AuthFailureGate 本质是 Hook 而非 Guard，且非管线打通必需
- **D-15:** Guard 注入机制（DirectExp 接受 guards 参数 → GuardPipeline 串联执行）已在 Phase 2-3 建成，保持可用即可

### WorkerRegistry 适配
- **D-16:** 现有 `worker_registry_service.py`（Redis 实现）适配为 WorkerRegistry Protocol 实现，通过依赖注入传入 Exp 层

### Claude's Discretion
- LLM 工厂中环境变量替换的具体实现方式（os.environ.get 或 config 层预处理）
- 模型族匹配的具体 pattern（前缀匹配 vs 子串匹配 vs 正则）
- OpenAIProvider 扩展参数的具体字段设计（reasoning_protocol、thinking_effort 等）
- WorkerRegistry Protocol 适配的具体桥接方式
- MonitorJobTool 是否需要条件注册（config 控制）

</decisions>

<specifics>
## Specific Ideas

- LiteLLM 代理支持的模型清单已确认（2026-03-22 查询）：Gemini 2.0/2.5/3 系列、Azure GPT-5 系列、Qwen3、Claude opus-4-6/sonnet-4-6/haiku-4-5、CDS 渠道 Claude
- `evomaster/utils/llm.py` 中 `_MODEL_FAMILY_DEFAULTS` 和 `_build_anthropic_adaptive_thinking_request` / `_build_openai_reasoning_effort_request` 是成熟的参考实现，可直接复用逻辑
- DirectExp.assemble(ctx) 已有 MCP/Skill 工具的 EvoToolAdapter 包装模式（`_init_skill_tools` / `_init_mcp_tools`），builtin tools 可以用相同模式

</specifics>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### 项目定义
- `.planning/PROJECT.md` — 项目愿景、核心价值、Key Decisions 表
- `.planning/REQUIREMENTS.md` — Phase 6 关联需求：MIGR-01, MIGR-02, ASBL-02, ASBL-03, ASBL-06
- `.planning/ROADMAP.md` — Phase 6 目标、成功标准、依赖关系

### Phase 1-5 上下文
- `.planning/phases/01-foundation-contracts/01-CONTEXT.md` — FinishTool 弃用决定、Guard 接口设计、TerminationPolicy 简化
- `.planning/phases/02-agent-kernel/02-CONTEXT.md` — 循环终止、Hook 扩展点、LLMProvider 边界
- `.planning/phases/03-exp-assembly-layer/03-CONTEXT.md` — ToolRegistry、DirectExp、WorkerRegistry Protocol
- `.planning/phases/04-playground-layer/04-CONTEXT.md` — Playground 职责边界、PlaygroundContext 字段、Exp MCP/Skill 初始化
- `.planning/phases/05-integration-quality/05-CONTEXT.md` — agent_run_service 重写、EventRouter、迁移策略

### Service 层（重构目标）
- `src/services/agent_run_service.py` — `_build_llm_provider`（L135, stub）、`_get_builtin_tools`（L140, stub）、DirectExp 构造（L242-260）
- `src/services/worker_registry_service.py` — Redis WorkerRegistry 现有实现

### LLM 参考实现
- `evomaster/utils/llm.py` — LLMConfig 定义（L101-163）、`_MODEL_FAMILY_DEFAULTS`（L217-237）、`_build_anthropic_adaptive_thinking_request`（L636-642）、`_build_openai_reasoning_effort_request`（L645-646）、`_infer_model_family_from_model`（L655-665）、`_resolve_model_profile`（L678-694）

### matmaster 框架核心
- `matmaster/types/context.py` — PlaygroundContext（需新增 session/config_dir 字段）
- `matmaster/types/llm_provider.py` — LLMProvider Protocol
- `matmaster/providers/openai_provider.py` — OpenAIProvider 实现（需扩展 reasoning 参数）
- `matmaster/assembly/direct_exp.py` — DirectExp（需重构构造参数和 assemble 中 builtin tool 构建）
- `matmaster/assembly/evomaster_tool_adapter.py` — EvoToolAdapter 包装模式
- `matmaster/assembly/guards.py` — shell guard 实现（待移除）
- `matmaster/engine/hooks.py` — Hook Protocol、BaseHook（Guard 语义的正确归属）

### EvoMaster 工具
- `evomaster/agent/tools/base.py` — BaseTool 抽象、EvoMaster ToolRegistry
- `evomaster/agent/tools/builtin/bash.py` — BashTool（session.exec_bash）
- `evomaster/agent/tools/builtin/editor.py` — EditorTool（session 文件操作）

### 配置文件
- `configs/mat_master/config.yaml` — LLM profile 定义、MCP/skill 配置

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `evomaster/utils/llm.py` `_MODEL_FAMILY_DEFAULTS` + reasoning builders — provider 路由的成熟参考逻辑
- `matmaster/assembly/direct_exp.py` `_init_skill_tools` / `_init_mcp_tools` — EvoToolAdapter 包装模式，builtin tools 可复用相同路径
- `matmaster/providers/openai_provider.py` OpenAIProvider — 已实现基础 chat/chat_with_retry/chat_stream，需扩展 reasoning 参数支持
- `matmaster/types/context.py` PlaygroundContext.with_bohrium() — frozen model 扩展字段的 model_copy 模式

### Established Patterns
- Pydantic frozen model + `model_copy(update={...})` 用于不可变契约扩展
- `@runtime_checkable` Protocol 用于接口定义（Guard、Hook、LLMProvider、WorkerRegistry）
- EvoToolAdapter 适配器模式：绑定 session + 包装 BaseTool → matmaster Tool Protocol
- DirectExp.assemble(ctx) 是能力装配的统一入口

### Integration Points
- PlaygroundContext — 新增 session/config_dir 字段后，成为 Exp 唯一的环境数据来源
- DirectExp.assemble(ctx) — builtin tool 构建从 service 层下沉到此处
- `_build_llm_provider()` — 从 stub 变为真实 LLM 工厂
- WorkerRegistry Protocol — 桥接现有 Redis 实现

</code_context>

<deferred>
## Deferred Ideas

- Guard 业务逻辑迁移（manuscript gate、auth failure gate、structure-retrieval gate 等）— 后续 milestone，且需重新设计为 Hook 而非 Guard
- 多 LLM provider 实现（Anthropic 原生、Google 原生）— 当前全走 LiteLLM OpenAI 兼容，无需独立 provider
- 旧代码清理（evomaster/、playground/mat_master/ 中的废弃模块）— Phase 7 或后续
- nanobot 风格 tools 重写（脱离 EvoMaster BaseTool 体系）— 长期方向，当前 EvoToolAdapter 足够

</deferred>

---

*Phase: 06-service-layer-wiring*
*Context gathered: 2026-03-22*
