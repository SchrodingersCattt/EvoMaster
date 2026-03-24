# MatMaster DevShell Design Spec

## Overview

MatMaster DevShell (`mm-devshell`) is a standalone CLI tool for testing the core agent chain (Playground -> Exp -> AgentKernel) without Redis, MySQL, or frontend dependencies. It provides an interactive REPL for verifying agent behavior and tool_call results.

V1 scope: builtin tools only. MCP and skill dispatch are future extensions (skill system is still stub in Exp).

## Approach

Reuse the Exp standard pipeline (`build_runtime()` + `kernel.run()`), replacing Playground with a manually constructed minimal PlaygroundContext. DevRunner follows the same split-call pattern as production `AgentRunService` (build_runtime -> inject hooks -> kernel.run -> cleanup), NOT the convenience `Exp.run()` wrapper.

## Architecture

```
mm-devshell --workdir ./workspace --log-dir ./logs --config dev.yaml
  |
  v
+---------------------------------------------+
|  CLI Entry (mm-devshell)                    |
|  - Parse CLI args                           |
|  - Load DevConfig (simplified YAML)         |
|  - Construct LLMProvider (OpenAIProvider)    |
|  - Construct minimal PlaygroundContext       |
|    (workdir, local_session, llm_provider)    |
|  - Start REPL loop                          |
+------------------+--------------------------+
                   | per user input
                   v
+---------------------------------------------+
|  DevRunner (mirrors AgentRunService pattern) |
|  1. exp.build_runtime(pg_ctx, bus=bus)       |
|  2. spec.model_copy(update={hooks: [...]})   |
|  3. kernel.run(spec, task, history, stop)    |
|  4. Extract messages from KernelRunResult    |
|  5. Accumulate history                       |
|  6. runtime.cleanup()                        |
+------------------+--------------------------+
                   | event stream
                   v
+---------------------------------------------+
|  MessageBus                                 |
|  +- DevStreamHook (injected via model_copy) |
|  |  -> real-time terminal output            |
|  +- EventLogger (bus consumer)              |
|     -> events.jsonl                         |
+---------------------------------------------+
```

Each user input triggers a new build_runtime -> kernel.run cycle.

### Multi-Turn History Management

DevRunner maintains a session-level `history: list[Message]` across runs:

1. First run: history is empty, kernel.run() receives `history=None`
2. After each run completes: DevRunner reads `KernelRunResult.messages` (the full message transcript including AssistantMessage + ToolMessages produced during this run) and appends them to history
3. Next run: the accumulated history is passed to kernel.run(), giving the agent cross-turn context

**Required kernel change:** `AgentKernel.run()` currently returns `RunResultEvent` (which discards the internal `messages` list). Change return type to `KernelRunResult(event: RunResultEvent, messages: list[Message])`. This is a minimal, backwards-compatible change — production callers that only use the event can access it via `result.event`. The `_finish()` method receives `messages` already; it just needs to pass them through.

The `/history` command shows a summary of the accumulated history. History is not persisted to disk (lost on REPL exit); the JSONL event log serves as the durable record.

### Threading Model

REPL uses a background thread for agent execution:

1. REPL main thread: handles input, signal (SIGINT), and display
2. Per-run worker thread: executes the build_runtime -> kernel.run -> cleanup sequence (blocking call with LLM streaming + tool execution)
3. SIGINT handler (main thread): sets `stop_event` on the current run's threading.Event, which AgentKernel checks at each turn boundary
4. Main thread blocks on `worker_thread.join()` after dispatching, waking on SIGINT to set stop_event

This is the same pattern used by the production `AgentRunService` (ThreadPoolExecutor).

### Hook Architecture

Two hooks coexist, serving different purposes:

- **EventEmitterHook** (created by `Exp.build_runtime()` automatically when bus is provided): bridges kernel events to MessageBus. EventLogger consumes the bus for JSONL persistence.
- **DevStreamHook** (injected via `spec.model_copy(update={hooks: [*spec.hooks, DevStreamHook()]})` after `build_runtime()` returns): implements Hook protocol directly for real-time terminal output. This is the same injection pattern used by production `AgentRunService` (line 512).

**GuardBlock display:** Guard deny in the kernel (agent.py:129) skips all hooks (`continue` before `run_post_tool_call`). To surface guard blocks:

Required kernel change: add `run_guard_blocked(hooks, tc, guard_result)` call in the guard deny branch. Hook protocol gains an optional `on_guard_blocked(tool_call: ToolCallData, result: GuardResult) -> None` method (observation-only, all hooks called). DevStreamHook implements this to display `guard_blocked: {reason}`.

## Module Structure

```
matmaster/
  devshell/
    __init__.py
    __main__.py          # python -m matmaster.devshell entry (fallback)
    cli.py               # CLI arg parsing, mm-devshell entry function
    config.py            # DevConfig Pydantic model + YAML loading
    repl.py              # REPL loop, readline, builtin commands, Ctrl+C handling
    runner.py            # Per-run: build_runtime -> inject hooks -> kernel.run -> history
    stream_hook.py       # Hook protocol impl, formatted terminal output
    event_logger.py      # Bus consumer -> JSONL aggregation and persistence
```

### Module Responsibilities

| Module | Responsibility |
|--------|---------------|
| `cli.py` | Arg parsing + entry point. Routes to REPL or future batch runner |
| `config.py` | DevConfig definition, YAML loading, env var expansion |
| `repl.py` | REPL loop, readline, builtin command dispatch, Ctrl+C handling |
| `runner.py` | Per-run: build_runtime -> model_copy hooks -> kernel.run -> extract messages -> accumulate history -> cleanup |
| `stream_hook.py` | Implements Hook protocol (incl. on_guard_blocked), formats events for terminal display |
| `event_logger.py` | Consumes MessageBus events, aggregates streaming thoughts, writes JSONL |

## Configuration

File: `dev.yaml` (CLI-specific, simplified)

```yaml
# LLM config
llm:
  api_key: ${OPENAI_API_KEY}
  base_url: "https://api.openai.com/v1"
  model: "gpt-4o"

# Agent config
agent:
  name: "general"
  mode: "direct"
  max_turns: 20
  identity: "You are a helpful assistant."  # optional, overrides ContextBuilder default identity

# Session config
session:
  type: "local"  # local / docker / ssh

# Tools config (use "*" to register all builtin tools, matching Exp's registration logic)
tools:
  builtin:
    - "*"
```

Design points:
- Flat structure, no nested Playground config schema
- Environment variable expansion via `matmaster.config.loader._expand_env_vars()` (existing utility)
- DevConfig Pydantic model with sensible defaults; runs with zero config if env vars are set
- `tools.builtin` uses `"*"` wildcard to match `Exp.build_runtime()` registration logic (which checks `if "*" in builtin_cfg`)
- `agent.identity` override: requires a small change in `Exp.build_runtime()` to read an `identity` field from config and pass it to `ContextBuilder.build(identity=...)`. Currently `build_runtime()` does not forward this field. This is a targeted Exp enhancement. Note: this overrides only the identity section of the system prompt, not the full prompt.
- MCP/Skill config sections added later when `--mcp`/`--skills` flags are implemented

## CLI Interface

```
mm-devshell [OPTIONS]

Options:
  --workdir PATH      Workspace directory (required)
  --log-dir PATH      Event log directory (required)
  --config PATH       Config file path (default: dev.yaml)
  --session TYPE      Session type: local/docker/ssh (default: local)
  --mcp               Enable MCP tools (future)
  --skills            Enable skill tools (future)
  --batch PATH        Run batch scenarios from file (future)
  --verbose           Enable verbose output (hook/guard details)
```

## REPL Interaction

### Terminal Output Format

| Event Type | Display |
|-----------|---------|
| ThoughtEvent (streaming) | Token-by-token print, no prefix |
| ToolCallEvent | `tool_call: {name}` + indented args |
| ToolResultEvent (success) | `tool_result:` + content (truncated if long) |
| ToolResultEvent (error) | `tool_error:` + content |
| GuardBlock (via on_guard_blocked hook) | `guard_blocked: {reason}` |
| RunResultEvent | Silent (natural finish needs no extra prompt) |
| ErrorEvent | `error: {message}` |

### Builtin Commands

| Command | Action |
|---------|--------|
| `/help` | Show command list |
| `/config` | Show current config |
| `/tools` | List registered tools |
| `/clear` | Clear screen |
| `/history` | Show conversation history summary for current session |
| `/verbose` | Toggle verbose mode (show hook triggers, guard evaluations) |

### Input Handling

- `Ctrl+C` during run: set stop_event, cancel current run, return to `>>>` prompt
- `Ctrl+C` at prompt: ignore (print hint to use Ctrl+D)
- `Ctrl+D`: exit REPL
- Empty input: ignore, re-prompt

## Event Logging

Log file path: `{log_dir}/events_{timestamp}.jsonl`

One file per REPL session. All runs within a session write to the same file.

### JSONL Format

```jsonl
{"ts": "2026-03-24T14:30:01.123", "run_id": "run-001", "turn": 1, "type": "thought", "content": "Let me help you..."}
{"ts": "2026-03-24T14:30:01.456", "run_id": "run-001", "turn": 1, "type": "tool_call", "tool": "editor.create", "args": {"path": "hello.py", "content": "print('Hello')"}}
{"ts": "2026-03-24T14:30:01.789", "run_id": "run-001", "turn": 1, "type": "tool_result", "tool": "editor.create", "content": "File created", "success": true}
{"ts": "2026-03-24T14:30:02.100", "run_id": "run-001", "turn": 1, "type": "guard_blocked", "tool": "bash", "reason": "Blocked by safety guard"}
{"ts": "2026-03-24T14:30:02.800", "run_id": "run-001", "turn": 1, "type": "run_result", "status": "natural", "reason": "stop"}
```

Design points:
- Streaming ThoughtEvent (start/streaming/end) merged into one complete thought record
- Each record is self-contained with run_id and turn for batch filtering
- assistant_state events skipped (matches production PersistenceHandler behavior)
- No file rotation (dev sessions are small)

## Error Handling

| Scenario | Handling |
|----------|---------|
| Config file missing / malformed | Error on startup, show specific field issue |
| API key missing | Check on startup, show which env var to set |
| LLM call failure (network/auth) | Print error in terminal, don't exit REPL, return to `>>>` |
| Tool execution exception | Kernel catches exception, converts to error string in ToolMessage (see Required Changes) |
| Guard block | Terminal shows `guard_blocked` via on_guard_blocked hook, kernel continues normal flow |
| Ctrl+C interrupts run | Set stop_event, wait for current turn to end, return to prompt |
| Ctrl+C at prompt | Ignore, print hint to use Ctrl+D |
| EventLogger write failure | Log warning, don't affect REPL main flow |
| Workdir doesn't exist | Auto-create |
| Log dir doesn't exist | Auto-create |

Core principle: REPL should not die. LLM failures and tool failures return to prompt. Only config-level errors block at startup.

## Batch Extension (Future)

Reserved via `cli.py` entry structure:

```python
def main():
    args = parse_args()
    if args.batch:
        run_batch(args)
    else:
        run_repl(args)
```

`runner.py`'s `DevRunner` class is REPL-agnostic: accepts a task string, returns a result. Both REPL and batch modes can call it.

## DevRunner Initialization Flow

DevRunner.__init__() performs one-time setup:

1. Load DevConfig from YAML (or defaults)
2. Create LLMProvider (OpenAIProvider with config.llm fields)
3. Create session based on config.session.type:
   - LocalSession: create instance
   - Set `session.config.workspace_path = workdir` BEFORE open() (mirrors Playground._sync_workspace_to_session_config(), line 90-96)
   - Call `session.open()` (session.open() uses workspace_path for environment init)
4. Construct PlaygroundContext (frozen Pydantic model):
   - workdir, session_type, cache_area (workdir/.cache), session, llm_provider
5. Build Exp config dict from DevConfig (agent name, mode, max_turns, tools, identity)
6. Initialize history list (empty)

DevRunner.run(task: str, stop_event: threading.Event) per-invocation:

1. Create MessageBus instance
2. Create Exp(config) with the prepared config dict
3. Call `runtime = exp.build_runtime(pg_ctx, bus=bus)`
4. Inject DevStreamHook via `spec = runtime.spec.model_copy(update={"hooks": [*runtime.spec.hooks, self.stream_hook]})`
5. Call `result = runtime.kernel.run(spec, task, history=self.history, stop_event=stop_event)`
6. Extract `result.messages` and append to `self.history`
7. Call `runtime.cleanup()` in finally block
8. Return result

## Required Kernel/Exp Changes

DevShell implementation requires these targeted changes to the core:

### 1. AgentKernel.run() return type (agent.py)

Current: returns `RunResultEvent` (discards internal messages list)
Change: return `KernelRunResult(event: RunResultEvent, messages: list[Message])`

```python
# New data class in matmaster/types/runtime.py
@dataclass(frozen=True)
class KernelRunResult:
    event: RunResultEvent
    messages: list[Message]
```

`_finish()` already receives `messages`; pass it through to the new return type. Production callers access `result.event` for the RunResultEvent.

### 2. Guard blocked hook point (agent.py:129)

Current: guard deny does `continue`, skipping all hooks
Change: add hook call before continue:

```python
if not guard_result.allowed:
    run_guard_blocked(spec.hooks, tc, guard_result)  # NEW
    blocked_content = f"BLOCKED: {guard_result.reason}"
    ...
    continue
```

Hook protocol addition (hooks.py):
```python
def on_guard_blocked(self, tool_call: ToolCallData, result: GuardResult) -> None: ...
```

Observation-only (all hooks called, no short-circuit).

### 3. Tool execution exception safety (agent.py:155-156)

Current: `tool_registry.execute()` can raise, no try/except in kernel
Change: wrap tool execution in try/except, convert exception to error ToolMessage:

```python
try:
    result = spec.tool_registry.execute(tc.name, tc.arguments)
except Exception as e:
    result = f"Error executing tool '{tc.name}': {type(e).__name__}: {e}"
    logger.exception("Tool execution failed: %s", tc.name)
```

This hardens the kernel for all callers, not just devshell.

### 4. Identity override in Exp.build_runtime() (exp.py:149)

Current: `builder.build(ctx, registry, mode=spec.mode)` — no identity param
Change: read `identity` from config and forward:

```python
identity = self._config.get("identity")
system_prompt = builder.build(ctx, registry, mode=spec.mode, identity=identity)
```

### 5. Packaging (pyproject.toml:57)

Current: `packages = ["evomaster", "playground"]`
Change: `packages = ["evomaster", "playground", "matmaster"]`

## pyproject.toml Registration

```toml
[project.scripts]
mm-devshell = "matmaster.devshell.cli:main"
```

## Dependencies

Only matmaster-internal and already-present dependencies:
- `matmaster.core.exp` (Exp pipeline)
- `matmaster.core.bus` (MessageBus)
- `matmaster.types.*` (PlaygroundContext, events, messages, KernelRunResult)
- `matmaster.providers.openai_provider` (OpenAIProvider)
- `evomaster.agent.session.*` (BaseSession, LocalSession)
- `pydantic` (DevConfig model)
- `pyyaml` (config loading)
- Standard library: `argparse`, `readline`, `pathlib`, `threading`, `signal`, `datetime`, `json`

No new external dependencies required.
