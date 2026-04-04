# AGENTS.md — Project Conventions for AI Coding Assistants

This file provides project-level conventions and context for AI coding assistants.

---

## Error Handling

- **DAO/integration layer**: Do not catch and swallow exceptions. Let them propagate so callers can distinguish "no data" from "error".
- **Service layer**: Only catch exceptions where graceful degradation is explicitly needed; document the reason.

---

## bohr-agent-sdk and This Project

**bohr-agent-sdk** ([dptech-corp/bohr-agent-sdk](https://github.com/dptech-corp/bohr-agent-sdk)) is the Bohrium official scientific compute Agent SDK for wrapping computational programs as MCP services. This repository acts as the **MCP client / Agent side**, paired with MCP servers deployed via bohr-agent-sdk.

### Role Division

| Role | This project (EvoMaster) | bohr-agent-sdk |
|------|--------------------------|----------------|
| Position | MCP client: issues CallTool with executor/storage params | MCP Server: receives params, executes/submits jobs |
| Auth injection | Path Adaptor: `inject_bohrium_executor`, `get_bohrium_storage_config` (from session credentials) | Server side uses received executor/storage for `init_executor`, `init_storage` |
| Config | `mcp.calculation_executors`, `mcp.calculation_servers` in `config.yaml` | Server-side deployment and tool implementation |

### Data Flow (executor / storage)

1. **This repo**: Path Adaptor (`evomaster/adaptors/calculation/path_adaptor.py`) resolves executor templates from `calculation_executors`, injects `access_key`, `project_id`, `user_id` via `inject_bohrium_executor`; storage via `get_bohrium_storage_config`. Both are sent with tool args via `MCPTool`.
2. **MCP Server (bohr-agent-sdk)**: `CalculationMCPServer` receives `executor` and `storage` in CallTool arguments, calls `init_executor(executor)` and `init_storage(storage)` to dispatch the job.

### Related Conventions

- **executor types**: For `type == "dispatcher"` inject `machine.remote_profile` and `resources.envs`; for `type == "local"` inject `executor.env` BOHRIUM_ACCESS_KEY/PROJECT_ID.
- **Config structure**: executor templates come from `mcp_config.calculation_executors[server_name].executor`.
- **OSS object keys**: `evomaster/adaptors/calculation/oss_io.upload_file_to_oss` uses `{prefix}/{uuid}/{original_basename}` so the URL last segment matches the local basename.
- **Compatibility**: When modifying Path Adaptor, executor/storage structure, or auth injection, check compatibility with bohr-agent-sdk's `CalculationMCPServer`, `DispatcherExecutor`/`LocalExecutor`, and storage conventions.

---

## EvoMaster Upstream and This Project

**EvoMaster** ([sjtu-sai-agents/EvoMaster](https://github.com/sjtu-sai-agents/EvoMaster)) is the generic scientific agent framework. This repository embeds and extends the core with MatMaster business logic, Bohrium integration, and MCP calculation adapters.

| Dimension | This project (EvoMaster OSS) | EvoMaster upstream |
|-----------|------------------------------|--------------------|
| Position | Downstream application: EvoMaster core + MatMaster playground + MCP adapters | Upstream framework: Agent/Playground/Exp, Tools, Skills, Sessions |
| Code correspondence | `evomaster/` ← upstream `evomaster/`; `playground/mat_master/`, `evomaster/adaptors/` are custom | Upstream `evomaster/` + example playgrounds |

### Related Conventions

- **`evomaster/` directory**: Sourced from upstream but contains project customizations (e.g. `evomaster/adaptors/calculation/`, Bohrium/MCP logic). Modifications should preserve diff-ability with upstream.
- **Documentation**: For generic Agent/Playground/Exp/Tools/Skills behavior, reference the upstream [EvoMaster docs](https://github.com/sjtu-sai-agents/EvoMaster). For MatMaster-specific logic, this codebase and AGENTS.md are authoritative.

---

## Local Web Service

The local web interface is `playground/mat_master/service/server/` (start via `playground/mat_master/start_dev.sh` or `python -m playground.mat_master.service.server`, default port `50001`).

This is a lightweight FastAPI+WebSocket server for local development and debugging. It uses in-memory session state and a fixed workspace. It is the only backend in this OSS release (no Redis/MySQL/Worker queue).

---

## Python and Runtime

**The project runtime is managed by uv.**

- **Running/testing**: Use `uv run python` from the project root (or `source .venv/bin/activate` then `python`), not system PATH Python.
- **Examples**: `uv run python -c "from playground.mat_master.core.callback import MatToolCallbacks; print('OK')"` and `uv run pytest`.
- **Version**: `requires-python = ">=3.10"`.

---

## Code Style (pre-commit enforced)

1. **Formatting (Black)**: line width 88, `--skip-string-normalization`.
2. **Import sorting (isort `--profile black`)**: stdlib → third-party → local.
3. **Dead code (autoflake + pyupgrade)**: remove unused imports and variables; modernize syntax.
4. **Static checks (flake8 + flake8-bugbear)**: `max-line-length=88`, ignore E501, E203, B008, B036.
5. **File hygiene**: max 1000 lines per file; fix trailing whitespace, BOM, mixed line endings; format JSON.

---

## Other Conventions

- **Config directory**: Main config and MCP JSON live in `configs/mat_master/`. `ConfigManager.get_config_manager()` loads `config.yaml` from that directory by default.
- **Maintain this file**: When new conventions or architectural decisions arise, update AGENTS.md.
- **File line count**: Refactor source files exceeding 1000 lines into sub-modules.
- **Evaluation module**: Detailed conventions for `evaluation/` (question bank format, field rules, verify types, etc.) are in [`evaluation/AGENTS_evaluation.md`](evaluation/AGENTS_evaluation.md).
