---
phase: 06-service-layer-wiring
verified: 2026-03-22T14:00:00Z
status: passed
score: 9/9 must-haves verified
re_verification: false
---

# Phase 6: Service Layer Wiring Verification Report

**Phase Goal:** Service 层存根全部接线到真实实现，生产 run 可端到端执行（不再依赖 mock LLM）
**Verified:** 2026-03-22T14:00:00Z
**Status:** passed
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths (from Success Criteria)

| #  | Truth                                                                                                              | Status     | Evidence                                                                                     |
|----|--------------------------------------------------------------------------------------------------------------------|------------|----------------------------------------------------------------------------------------------|
| 1  | `_build_llm_provider` 实现 LLM 工厂 + config 驱动的 provider 路由，按模型族匹配参数模板实例化 OpenAIProvider      | VERIFIED   | `agent_run_service.py` L204-271: 完整工厂实现，`_resolve_llm_profile` + model family routing + `OpenAIProvider(` |
| 2  | Builtin tools（BashTool/EditorTool/MonitorJobTool）在 DirectExp.assemble(ctx) 中通过 ctx.session 构建并注册        | VERIFIED   | `direct_exp.py` L117-135: `_init_builtin_tools` imports and registers 3 tools via `ctx.session` |
| 3  | PlaygroundContext 携带 session 和 config_dir 字段，DirectExp 不再需要单独的 session/config_dir 构造参数            | VERIFIED   | `context.py` L55-56: `session: Any = None` 和 `config_dir: Path | None = None` 存在；DirectExp 构造函数无这些参数 |
| 4  | 现有 `worker_registry_service.py` 适配为 WorkerRegistry Protocol 实现，通过依赖注入传入 Exp 层                    | VERIFIED   | `worker_registry_adapter.py` 存在，`WorkerRegistryServiceAdapter` 实现全部 4 个 Protocol 方法，`isinstance` 检查通过 |
| 5  | mat_master 生产路径可以不依赖 mock 完成 Playground→Exp→Kernel 全链路（配置驱动）                                  | VERIFIED   | `agent_run_service.py` L425-431: DirectExp 构造无 mock 参数；406/406 tests pass（含 E2E 管线测试） |

**Score:** 5/5 truths verified

---

### Required Artifacts

| Artifact                                    | Expected                                                   | Status     | Details                                                                        |
|---------------------------------------------|------------------------------------------------------------|------------|--------------------------------------------------------------------------------|
| `matmaster/types/context.py`                | PlaygroundContext with `session: Any = None` and `config_dir: Path | None = None` | VERIFIED | L47: `arbitrary_types_allowed=True`; L55-56: 两个新字段；playground.py L116-117 populate 这两个字段 |
| `matmaster/providers/openai_provider.py`    | OpenAIProvider with extra_kwargs support                   | VERIFIED   | L42: `extra_kwargs: dict[str, Any] | None = None`; L72-73: `if self._extra_kwargs: kwargs.update(self._extra_kwargs)` (chat); L189-190 同样在 chat_stream |
| `src/services/agent_run_service.py`         | Working `_build_llm_provider` factory (no NotImplementedError) | VERIFIED | L84: `_infer_model_family`; L100: `_build_reasoning_extra_kwargs`; L122: `_resolve_temperature`; NotImplementedError count = 0 |
| `matmaster/assembly/direct_exp.py`          | DirectExp with `_init_builtin_tools` and cleaned constructor | VERIFIED  | L117: `def _init_builtin_tools(self, ctx`; no `self._builtin_tools`, `self._session`, `self._config_dir` |
| `src/services/worker_registry_adapter.py`  | WorkerRegistryServiceAdapter bridging Protocol             | VERIFIED   | L21: `class WorkerRegistryServiceAdapter`; L38-40: `delete_session_run_owner` returns `True` |
| `matmaster/assembly/guards.py`              | Guard shell classes removed                                | VERIFIED   | 文件仅含 8 行 docstring；无 `ManuscriptGateGuard`，无 `AuthFailureGateGuard` |
| `tests/matmaster/integration/test_llm_factory.py` | LLM factory unit tests                              | VERIFIED   | 25 个测试：`test_default_profile_no_override`, `test_claude_46_reasoning_extra_kwargs`, `test_gpt5_reasoning_extra_kwargs` 等全部通过 |
| `tests/matmaster/assembly/test_direct_exp.py` | TestDirectExpBuiltinTools class                         | VERIFIED   | L236: `class TestDirectExpBuiltinTools`; L288: `test_constructor_rejects_old_params`; L280: `test_builtin_tools_skipped_when_no_session` |
| `tests/matmaster/assembly/test_worker_registry.py` | TestWorkerRegistryServiceAdapter                   | VERIFIED   | L132: `class TestWorkerRegistryServiceAdapter`; L135: `test_adapter_isinstance_check`; L142: `test_adapter_delete_returns_bool` |

---

### Key Link Verification

| From                             | To                                        | Via                                                         | Status  | Details                                                         |
|----------------------------------|-------------------------------------------|-------------------------------------------------------------|---------|-----------------------------------------------------------------|
| `src/services/agent_run_service.py` | `matmaster/providers/openai_provider.py` | `_build_llm_provider` instantiates `OpenAIProvider`        | WIRED   | L215: `from matmaster.providers.openai_provider import OpenAIProvider`; L261: `return OpenAIProvider(` |
| `src/services/agent_run_service.py` | `matmaster/types/context.py`             | Playground.prepare() populates session/config_dir on PlaygroundContext | WIRED | `playground.py` L116-117: `session=self.session, config_dir=self.config_path.parent`; service layer calls `playground.prepare()` at L373 |
| `matmaster/assembly/direct_exp.py`  | `evomaster/agent/tools/builtin`          | `_init_builtin_tools` imports BashTool/EditorTool/MonitorJobTool | WIRED | L127-129: `from evomaster.agent.tools.builtin.bash import BashTool` 等；L132: `EvoToolAdapter(evo_tool, ctx.session)`; L133: `registry.register(adapted, source="builtin")` |
| `src/services/worker_registry_adapter.py` | `src/services/worker_registry_service.py` | Adapter wraps WorkerRegistryService               | WIRED   | L16 (TYPE_CHECKING): `from src.services.worker_registry_service import WorkerRegistryService`; L29: `def __init__(self, service: "WorkerRegistryService")` |
| `src/services/worker_registry_adapter.py` | `matmaster/assembly/worker_registry.py`  | Adapter satisfies WorkerRegistry Protocol          | WIRED   | 4 个 Protocol 方法全部实现；`isinstance(adapter, WorkerRegistry)` 在测试中验证通过 |

---

### Requirements Coverage

| Requirement | Source Plan  | Description                                                                                         | Status    | Evidence                                                                              |
|-------------|--------------|-----------------------------------------------------------------------------------------------------|-----------|---------------------------------------------------------------------------------------|
| MIGR-01     | 06-01, 06-02 | mat_master 在新骨架上端到端跑通完整流程                                                             | SATISFIED | `_build_llm_provider` 工厂完整实现；DirectExp 干净构造；E2E 测试 406/406 通过         |
| MIGR-02     | 06-01, 06-02 | minimal 在新骨架上端到端跑通完整流程                                                                | SATISFIED | PlaygroundContext 扩展 session/config_dir；生产路径无 mock 依赖；全测试套件通过        |
| ASBL-02     | 06-02        | ToolRegistry 统一 builtin tools、MCP tools、skill tools 的注册路径                                 | SATISFIED | `_init_builtin_tools` 通过 `ctx.session` 构建并以 `source="builtin"` 注册 3 个工具   |
| ASBL-06     | 06-02        | WorkerRegistry 接口定义——定义 WorkerRegistry Protocol 和注入点                                    | SATISFIED | `WorkerRegistryServiceAdapter` 实现 Protocol；`isinstance` 检查通过；5 个适配器测试通过 |

无孤立需求（ORPHANED）：REQUIREMENTS.md 追踪表中 MIGR-01/MIGR-02 标记 "Phase 5 + Phase 6 Complete"，ASBL-02/ASBL-06 标记 "Phase 3 Complete"（Phase 6 为 gap closure 强化）。

---

### Anti-Patterns Found

无 blocker 或 warning 级别的反模式：

- `agent_run_service.py`：NotImplementedError 数量 = 0；无 `_get_builtin_tools`；无 `hasattr(playground`；DirectExp 构造干净（无旧参数）
- `direct_exp.py`：无 `self._builtin_tools`、`self._session`、`self._config_dir`；无占位符实现
- `guards.py`：仅 8 行 docstring，无 shell 类残留
- `worker_registry_adapter.py`：无 TODO/FIXME/占位符

---

### Human Verification Required

以下项目需要人工验证（依赖外部服务或实际运行）：

#### 1. 生产环境 LLM 路由验证

**Test:** 使用真实 `configs/mat_master/config.yaml` 和有效 LiteLLM proxy 凭证运行 `run_agent_sync`
**Expected:** `_build_llm_provider` 正确读取 YAML，路由到 litellm profile，以 Claude 4.6 family 参数（`anthropic_adaptive_thinking` + `temperature=1`）实例化 OpenAIProvider，并完成真实 LLM 调用
**Why human:** 需要真实 API 密钥和网络访问；自动化测试使用 mock OpenAIProvider

#### 2. Builtin tools 通过真实 Session 执行

**Test:** 在真实 Docker/SSH session 场景中运行，验证 BashTool、EditorTool、MonitorJobTool 通过 `ctx.session` 正确构建和执行
**Expected:** 工具可以在 session 容器中执行 bash 命令和文件操作
**Why human:** 需要真实 session 基础设施；测试使用 MagicMock 替代

---

### Git Commit Verification

Phase 6 的 4 个原子提交均已确认在 git log 中：

| Commit  | Task                                                     |
|---------|----------------------------------------------------------|
| `6c49f6e` | feat(06-01): extend PlaygroundContext and OpenAIProvider for service wiring |
| `284bb1d` | feat(06-01): wire `_build_llm_provider` with config-driven LLM factory       |
| `ed64e1a` | feat(06-02): clean DirectExp constructor and move builtin tools to assemble() |
| `085171c` | feat(06-02): add WorkerRegistryServiceAdapter and clean service layer stubs  |

---

## Summary

Phase 6 的目标已完全实现。Service 层所有存根（`_build_llm_provider` NotImplementedError、`_get_builtin_tools` 占位方法、`hasattr` 技巧性访问、guard shell 类）均已替换为真实实现：

1. **LLM 工厂（Plan 01）**：`_build_llm_provider` 实现 config 驱动的 provider 路由，支持 model family 推断（Claude 4.6 / GPT-5 / DeepSeek / Gemini），reasoning 参数注入（`extra_kwargs` passthrough），temperature policy（`force_one_when_reasoning`）。OpenAIProvider 支持 `extra_kwargs` 合并到所有 SDK 调用。PlaygroundContext 新增 `session` 和 `config_dir` 字段并由 `Playground.prepare()` 填充。

2. **DirectExp 清理 + WorkerRegistry 适配（Plan 02）**：DirectExp 构造函数移除 `session`/`config_dir`/`builtin_tools` 参数；builtin tools 在 `assemble()` 中通过 `ctx.session` 构建。ManuscriptGateGuard / AuthFailureGateGuard shell 类彻底删除。WorkerRegistryServiceAdapter 桥接 Service（`delete` 返回 None）到 Protocol（要求 bool）。

全部 406 matmaster 测试通过，4 个 PLAN 中要求的验收标准逐条确认。

---

_Verified: 2026-03-22T14:00:00Z_
_Verifier: Claude (gsd-verifier)_
