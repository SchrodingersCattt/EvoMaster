# Config Consolidation Design

> matmaster_config/ 配置整理：消除重叠、删除死配置、拆分 MCP、修复运行时注入

## Problem

`matmaster_config/config.yaml`（620 行）存在三个问题：

1. **重叠**：`llm:` 段（115 行）与 `llm_config.yaml`（122 行）几乎完全重复
2. **死配置**：`mat_master:`、`env:`、`llm_output:`、`logging:` 等段在重构后的 `matmaster/` 代码中零引用（仅旧 `evomaster/` 使用）
3. **MCP 运行时配置丢失**：web 路径下 `LazyMCPConnector` 收到空 dict，导致 `path_adaptor` 和 `calculation_executors` 不生效

## Audit Results

### Dead Config (zero references in matmaster/ + src/)

| Section | Lines | Reason |
|---------|-------|--------|
| `llm:` | ~115 | Redundant copy of `llm_config.yaml` |
| `mat_master:` (entire) | ~130 | crp, execution, planner, ask_human, monitor_job, skill_evolution, capabilities -- all evomaster-only |
| `agents.general.max_turns` | 1 | Zero references |
| `agents.general.tools` | 3 | Zero references |
| `agents.general.context` (entire, incl. compaction) | ~20 | Zero references |
| `agents.general.system_prompt_file` | 1 | Zero references |
| `agents.general.user_prompt_file` | 1 | Zero references |
| `env:` | ~20 | Zero references |
| `llm_output:` | ~5 | Zero references |
| `logging:` | ~8 | Zero references |
| `project_root`, `results_dir`, `debug` | 3 | Zero references |

### Active Config

| Section | Consumer | Access Pattern |
|---------|----------|----------------|
| `agents.general.llm` | `src/services/agent_run_service.py:290-295` | dict.get() |
| `mcp:` (runtime fields) | `matmaster/tools/lazy_mcp.py` via `configure_mcp_manager()` | dict.get() |
| `skills:` | `matmaster/core/exp.py` via `ExpConfig` | Pydantic |
| `session:` | `matmaster/core/playground.py:157-178` | dict -> Pydantic |
| `playground:` | `matmaster/core/playground.py:289-311` | dict -> Pydantic |
| `workspace:` | `matmaster/core/playground.py:200` | attribute |

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
├── config.yaml              # ~40 lines: agents.general.llm, skills, session, playground, workspace
├── llm_config.yaml          # ~120 lines: unchanged
├── mcp.yaml                 # ~240 lines: extracted from config.yaml mcp: section
├── mcp_config.json          # unchanged (prod endpoints)
├── mcp_config.test.json     # unchanged
└── mcp_config.uat.json      # unchanged
```

### config.yaml (after cleanup, ~40 lines)

```yaml
# MatMaster main config
# LLM: llm_config.yaml | MCP: mcp.yaml | MCP endpoints: mcp_config.{env}.json

agents:
  general:
    llm: "litellm"

skills:
  enabled: true
  skills_root: evomaster/skills

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

**Solution:** `Exp._init_skill_tools()` self-loads `mcp.yaml` from `config_dir` when no runtime config is injected.

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

#### Exp._init_skill_tools() change

```python
def _init_skill_tools(self, ctx, registry, config=None):
    skills_cfg = self._config.skills
    if not skills_cfg.enabled:
        return

    # Runtime MCP config: prefer injected, else self-load from config_dir
    if config:
        mcp_config = config
    else:
        mcp_runtime_path = Path(skills_cfg.config_dir) / skills_cfg.mcp_runtime_file
        if mcp_runtime_path.exists():
            import yaml
            mcp_config = yaml.safe_load(mcp_runtime_path.read_text()) or {}
        else:
            mcp_config = {}

    # Rest unchanged: reads path_adaptor, calculation_servers, etc. from mcp_config
    mcp_config_file = mcp_config.get("config_file", skills_cfg.mcp_config_file)
    # ...
```

### cache_mcp_schemas.py adaptation

Update to read `mcp.yaml` instead of `config.yaml`'s `mcp:` section:

```python
# Try mcp.yaml first, fallback to config.yaml mcp: key
mcp_yaml = config_dir / "mcp.yaml"
if mcp_yaml.exists():
    with open(mcp_yaml) as f:
        mcp_config = yaml.safe_load(f)
else:
    config_yaml = config_dir / "config.yaml"
    with open(config_yaml) as f:
        mcp_config = yaml.safe_load(f).get("mcp", {})
```

## Unchanged Components

- `llm_config.yaml` -- no changes
- `mcp_config.*.json` -- no changes (endpoint addresses)
- `matmaster/config/loader.py` -- no changes (mcp loading is in Exp)
- `matmaster/tools/lazy_mcp.py` -- no changes (receives dict, source-agnostic)
- `src/services/agent_run_service.py` -- no changes (`mcp=None` now handled by Exp)

## Constraints

- MCP servers must ONLY connect when a skill triggers them (lazy load contract preserved)
- `configure_mcp_manager()` runs during `LazyMCPConnector._ensure_manager()`, which is called on first `LazyMCPTool.execute()` -- lazy contract maintained
- Schema filtering (`tool_include_only`, `sync_tools`) is baked into cache at generation time, not re-applied at runtime
