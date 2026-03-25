# Config Consolidation Design

> matmaster_config/ 配置整理：消除重叠、删除死配置、拆分 MCP、修复运行时注入

## Scope

本次改动作用于重构分支 `refactor/matmaster-playground-exp-agent-v2` 的 `matmaster_config/` 目录。
`test` 分支仍使用 `configs/mat_master/`，不受影响 -- 两个分支各自独立的配置目录，无共享。

当前代码中部分路径仍指向旧目录 `configs/mat_master/`（如 `direct.toml`、`cache_mcp_schemas.py`、`PlaygroundManager.get_or_create()`、`agent_run_service.py`）。这些路径修正包含在本次实施范围内。

## Problem

`matmaster_config/config.yaml`（620 行）存在三个问题：

1. **重叠**：`llm:` 段（115 行）与 `llm_config.yaml`（122 行）内容实质相同但结构有差异 -- `config.yaml` 用 `llm.{name}` 平铺，`llm_config.yaml` 用 `profiles.{name}` + `routes` + `default` 结构化。后者是前者的超集和升级，删除 `llm:` 段安全。
2. **死配置**：`mat_master:`、`llm_output:`、`logging:` 等段在重构后的 `matmaster/` 代码中零引用（仅旧 `evomaster/` 使用）
3. **MCP 运行时配置丢失**：web 路径下 `LazyMCPConnector` 收到空 dict，导致 `path_adaptor` 和 `calculation_executors` 不生效

## Audit Results

### Dead Config (zero references in matmaster/ + src/)

| Section | Lines | Reason | Safe to Remove |
|---------|-------|--------|----------------|
| `llm:` | ~115 | Redundant copy of `llm_config.yaml` | Yes -- `EvoMasterConfig.llm` has `default_factory=dict` |
| `mat_master:` (entire) | ~130 | crp, execution, planner, ask_human, monitor_job, skill_evolution, capabilities -- all evomaster-only | Yes -- not in `EvoMasterConfig` schema |
| `agents.general.max_turns` | 1 | Zero references | Yes -- `EvoMasterConfig.agents` is `dict[str, Any]` |
| `agents.general.tools` | 3 | Zero references | Yes |
| `agents.general.context` (entire, incl. compaction) | ~20 | Zero references | Yes |
| `agents.general.system_prompt_file` | 1 | Zero references | Yes |
| `agents.general.user_prompt_file` | 1 | Zero references | Yes |
| `skills:` | 3 | Duplicated in `direct.toml`; `skills_root` values disagree (`evomaster/skills` vs `playground/mat_master/skills`). Actual consumer is toml -> ExpConfig | Yes -- `EvoMasterConfig.skills` has `default_factory` |
| `llm_output:` | ~5 | Zero references | Yes -- `EvoMasterConfig.llm_output` has `default_factory` |
| `logging:` | ~8 | Zero references | Yes -- `LoggingConfig` fields all have defaults |
| `project_root`, `results_dir`, `debug` | 3 | Zero references | Yes -- all have defaults in `EvoMasterConfig` |

### Must Keep (EvoMasterConfig constraint)

| Section | Lines | Reason |
|---------|-------|--------|
| `env:` | ~20 | `EvoMasterConfig.env: EnvConfig` -- `EnvConfig` has required sub-fields (`cluster`, `docker`, `scheduler`) with NO defaults. Removing `env:` from YAML causes Pydantic validation error at `ConfigManager.load()`. Must retain as stub. |

### Active Config

| Section | Consumer | Access Pattern |
|---------|----------|----------------|
| `agents.general.llm` | `src/services/agent_run_service.py:290-295` | dict.get() -- service 层路由 LLM profile 的 key，其他 agents 字段已迁入 ExpConfig/toml |
| `mcp:` (runtime fields) | `matmaster/tools/lazy_mcp.py` via `configure_mcp_manager()` | dict.get() |
| `session:` | `matmaster/core/playground.py:157-178` | dict -> Pydantic |
| `playground:` | `matmaster/core/playground.py:289-311` | dict -> Pydantic |
| `workspace:` | `matmaster/core/playground.py:200` | attribute |
| `env:` | `EvoMasterConfig` Pydantic validation | Required by schema, not by matmaster code |

Note: `playground.py:375` 的 `validate_startup()` 校验 `"agents" in cfg`，清理后仍保留 `agents` 段所以不受影响。

### MCP Field Classification

| Field | Cache Gen | Runtime (execute) | Decision |
|-------|-----------|-------------------|----------|
| `config_file` | Used | Overridden by toml | **Delete** (toml provides) |
| `enabled` | Not used | Not used | **Delete** |
| `path_adaptor` | Used | **Required** -- creates CalculationPathAdaptor | **Keep** |
| `calculation_servers` | Used | **Required** -- determines which servers get path adaptor | **Keep** |
| `tool_include_only` | **Required** -- filters tool list | Not used (baked in cache) | **Keep** (cache gen) |
| `calculation_executors.*.executor` | Not used | **Required** -- path_adaptor.resolve_args() injects executor/creds | **Keep** |
| `calculation_executors.*.executor_map` | Not used | **Required** -- per-tool image dispatch | **Keep** |
| `calculation_executors.*.path_params_by_tool` | Not used | **Required** -- detect extra path params | **Keep** |
| `calculation_executors.*.sync_tools` | **Required** -- filters submit_* | Not used (baked in cache) | **Keep** (cache gen) |

## Design

### File Structure (final)

```
matmaster_config/
├── config.yaml              # ~45 lines: agents.general.llm, env stub, session, playground, workspace
├── llm_config.yaml          # ~120 lines: unchanged
├── mcp.yaml                 # ~240 lines: extracted from config.yaml mcp: section
├── mcp_config.json          # unchanged (prod endpoints)
├── mcp_config.test.json     # unchanged
└── mcp_config.uat.json      # unchanged
```

### config.yaml (after cleanup, ~45 lines)

```yaml
# MatMaster main config
# LLM: llm_config.yaml | MCP: mcp.yaml | MCP endpoints: mcp_config.{env}.json

agents:
  general:
    llm: "litellm"

# env: retained as stub -- EvoMasterConfig.env requires cluster/docker/scheduler
# sub-fields. These are not used by matmaster code but removing them causes
# Pydantic validation errors at ConfigManager.load().
env:
  cluster:
    debug_pool:
      type: "cpu"
      max_concurrent: 1
    train_pool:
      type: "cpu"
      max_concurrent: 1
  docker:
    base_image: "python:3.11-slim"
    registry: "docker.io"
    pull_policy: "if_not_present"
  scheduler:
    type: "local"
    queue_timeout: 300
    retry_failed: false
    max_retries: 1

session:
  type: "local"
  local:
    working_dir: "./playground/mat_master/workspace"
    timeout: 60
    gpu_devices: null
    cpu_devices: null
    symlinks: {}
  docker:
    image: "evomaster/base:latest"
    container_name: null
    use_existing_container: null
    working_dir: "/workspace"
    memory_limit: "64g"
    cpu_limit: 16.0
    gpu_devices: "0"
    network_mode: "host"
    volumes: {"./playground/mat_master/workspace": "/workspace"}
    env_vars: {}
    auto_remove: false
    timeout: 300

playground:
  cache_dir: ".cache/matmaster"
  archival:
    enabled: true
    oss_bucket: "${OSS_BUCKET_NAME}"
    oss_prefix: "matmaster_evo/chat_workspace"
    credential_ref: "env:aliyun-oss"

workspace: "./workspace"
```

### mcp.yaml (extracted, ~240 lines)

Top-level keys (no `mcp:` wrapper):

```yaml
# MCP service config
# Endpoint addresses defined in mcp_config.{env}.json

path_adaptor: "calculation"
calculation_servers:
  - "mat_sg"
  - "mat_dpa"
  - "mat_compdart"
  - "mat_doc"
  - "mat_abacus"
  - "mat_binary_calc"
  - "mat_struct_db"
  - "mat_nmr"
  - "mat_xrd"
  - "mat_electron_microscope"

tool_include_only:
  # ... unchanged from current config.yaml

calculation_executors:
  # ... unchanged from current config.yaml (all fields preserved)
```

Deleted fields: `config_file` (toml provides), `enabled` (zero references).

### llm_config.yaml

Unchanged.

### direct.toml fix

```toml
[skills]
enabled = true
skills_root = "playground/mat_master/skills"
cache_dir = "matmaster/cache"
config_dir = "matmaster_config"          # Fix: was "configs/mat_master"
mcp_config_file = "mcp_config.json"
```

### Bug fix: Exp self-loads MCP runtime config

**Problem:** `agent_run_service.py:316` passes `mcp=pg_ctx.run_meta.get("mcp_config")` which is always `None`. `LazyMCPConnector` receives empty dict, so `path_adaptor` and `calculation_executors` are not available at runtime.

**Root cause analysis:** `exp.py:117-124` passes `skills` dict to `_init_skill_tools(config=skills)` and `mcp` dict to `_init_mcp_tools(config=mcp)`. The `config` parameter in `_init_skill_tools` represents skill config, NOT MCP config. Self-loading MCP config must not depend on this parameter being falsy -- otherwise a non-empty `skills` dict would suppress the MCP self-load.

**Solution:** `_init_skill_tools()` always self-loads MCP runtime config from `config_dir`, independent of the `config` parameter. The `config` parameter is renamed to `skills_config` for clarity.

#### ExpSkillsConfig extension

```python
class ExpSkillsConfig(BaseModel):
    enabled: bool = False
    skills_root: str = ""
    cache_dir: str = ""
    config_dir: str = ""
    mcp_config_file: str = ""
    mcp_runtime_file: str = "mcp.yaml"   # New: runtime MCP config filename
```

Note: `mcp_runtime_file` defaults to `"mcp.yaml"` via Pydantic, not declared in toml.

#### Exp._init_skill_tools() change

```python
def _init_skill_tools(self, ctx, registry, skills_config=None):
    skills_cfg = self._config.skills
    if not skills_cfg.enabled:
        return

    # MCP runtime config: ALWAYS self-load from config_dir.
    # This is independent of skills_config -- MCP runtime config (path_adaptor,
    # calculation_executors) is a separate concern from skill routing config.
    from matmaster.config.loader import _load_raw
    mcp_runtime_path = Path(skills_cfg.config_dir) / skills_cfg.mcp_runtime_file
    if mcp_runtime_path.exists():
        mcp_config = _load_raw(mcp_runtime_path)
    else:
        raise FileNotFoundError(
            f"MCP runtime config not found: {mcp_runtime_path}. "
            f"Required when skills.enabled=true."
        )

    # mcp_config_file fallback to skills_cfg.mcp_config_file (from toml)
    # mcp.yaml does not contain config_file field -- this always hits the default
    mcp_config_file = mcp_config.get("config_file", skills_cfg.mcp_config_file)
    # ...
```

#### Exp.build_runtime() caller change

```python
# Before:
if skills:
    self._init_skill_tools(ctx, registry, skills)
elif self._config.skills.enabled:
    self._init_skill_tools(ctx, registry)

# After:
if skills or self._config.skills.enabled:
    self._init_skill_tools(ctx, registry, skills_config=skills)
```

### cache_mcp_schemas.py adaptation

Update to read `mcp.yaml` instead of `config.yaml`'s `mcp:` section.
No fallback -- `mcp.yaml` is the single source of truth; missing file is a hard error.

```python
mcp_yaml = config_dir / "mcp.yaml"
if not mcp_yaml.exists():
    logger.error("mcp.yaml not found at %s", mcp_yaml)
    sys.exit(1)
with open(mcp_yaml) as f:
    mcp_config = yaml.safe_load(f)
```

Also update `--config-dir` default from `"configs/mat_master"` to `"matmaster_config"`.

## Path Fixes in Scope

Besides config file changes, the following hardcoded paths need updating to `matmaster_config`:

| File | Line | Current | Target |
|------|------|---------|--------|
| `matmaster/exps/direct.toml` | 18 | `config_dir = "configs/mat_master"` | `"matmaster_config"` |
| `matmaster/tools/cache_mcp_schemas.py` | 28 | `--config-dir` default `"configs/mat_master"` | `"matmaster_config"` |
| `matmaster/core/playground.py` | 410-411 | `get_or_create()`: `configs/{pg_type}/config.yaml` | Route `mat_master` to `matmaster_config/config.yaml`; retain `configs/{pg_type}` for other types (e.g. `minimal`) |
| `matmaster/core/playground.py` | 369 | `validate_startup()`: `configs/{pg_type}/config.yaml` | Same routing as `get_or_create()` |
| `src/services/agent_run_service.py` | 126 | `configs/{pg_type}/llm_config.yaml` in `_validate_llm_configs()` | Route `mat_master` to `matmaster_config/llm_config.yaml` |

Implementation note: introduce a helper `_config_dir_for(pg_type)` in `PlaygroundManager` that returns `matmaster_config` for `mat_master` and `configs/{pg_type}` for others.

## Unchanged Components

- `llm_config.yaml` -- no changes
- `mcp_config.*.json` -- no changes (endpoint addresses)
- `matmaster/config/loader.py` -- no changes (mcp loading is in Exp)
- `matmaster/tools/lazy_mcp.py` -- no changes (receives dict, source-agnostic)
- `src/services/agent_run_service.py` -- orchestration logic unchanged (`mcp=None` now handled by Exp); only path constants updated

## Constraints

- MCP servers must ONLY connect when a skill triggers them (lazy load contract preserved)
- `configure_mcp_manager()` runs during `LazyMCPConnector._ensure_manager()`, which is called on first `LazyMCPTool.execute()` -- lazy contract maintained
- Schema filtering (`tool_include_only`, `sync_tools`) is baked into cache at generation time, not re-applied at runtime
- `mcp.yaml` missing is a hard error (both CLI and runtime) -- consistent failure mode

## Testing

- Existing `test_lazy_mcp_loading` E2E test needs update to cover Exp self-loading `mcp.yaml` path (always-load branch)
- Verify `cache_mcp_schemas.py` works with `--config-dir matmaster_config` pointing to new layout
- Verify `path_adaptor` is correctly initialized when `LazyMCPTool.execute()` fires (mcp_config no longer empty)
- Update `tests/matmaster/config/test_exp.py` -- `TestExpSkillsConfig.test_defaults()` must cover new `mcp_runtime_file` field
- Verify `EvoMasterConfig(**config_dict)` passes with cleaned config.yaml (env stub present, dead sections removed)
