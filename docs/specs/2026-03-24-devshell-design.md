# MatMaster DevShell Design Spec

## Overview

MatMaster DevShell (`mm-devshell`) is a standalone CLI tool for testing the core agent chain (Playground -> Exp -> AgentKernel) without Redis, MySQL, or frontend dependencies. It provides an interactive REPL for verifying agent behavior, tool_call results, and skill dispatch.

## Approach

Reuse the Exp standard pipeline (assemble -> build_runtime -> run), replacing Playground with a manually constructed minimal PlaygroundContext. This ensures test behavior matches production while eliminating external service dependencies.

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
|  Exp.run(pg_context, task, bus)              |
|  - assemble() -> AgentRuntimeSpec           |
|  - build_runtime() -> AgentKernel           |
|  - kernel.run()                             |
+------------------+--------------------------+
                   | event stream
                   v
+---------------------------------------------+
|  MessageBus                                 |
|  +- DevStreamHook (injected into kernel)    |
|  |  -> real-time terminal output            |
|  +- EventLogger (bus consumer)              |
|     -> events.jsonl                         |
+---------------------------------------------+
```

Each user input triggers a new Exp.run() call, consistent with production behavior.

## Module Structure

```
matmaster/
  devshell/
    __init__.py
    __main__.py          # python -m matmaster.devshell entry (fallback)
    cli.py               # CLI arg parsing, mm-devshell entry function
    config.py            # DevConfig Pydantic model + YAML loading
    repl.py              # REPL loop, readline, builtin commands, Ctrl+C handling
    runner.py            # Per-run assembly: PlaygroundContext -> Exp.run() -> result
    stream_hook.py       # Hook protocol impl, formatted terminal output
    event_logger.py      # Bus consumer -> JSONL aggregation and persistence
```

### Module Responsibilities

| Module | Responsibility |
|--------|---------------|
| `cli.py` | Arg parsing + entry point. Routes to REPL or future batch runner |
| `config.py` | DevConfig definition, YAML loading, env var expansion |
| `repl.py` | REPL loop, readline, builtin command dispatch, Ctrl+C handling |
| `runner.py` | Per-run assembly: construct PlaygroundContext, create Exp, call run(), return result |
| `stream_hook.py` | Implements Hook protocol, formats events for terminal display |
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
  system_prompt: "You are a helpful assistant."  # optional override

# Session config
session:
  type: "local"  # local / docker / ssh

# Tools config
tools:
  builtin:
    - bash
    - editor
    - monitor_job
```

Design points:
- Flat structure, no nested Playground config schema
- Environment variable expansion via existing `${VAR}` mechanism
- DevConfig Pydantic model with sensible defaults; runs with zero config if env vars are set
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
| GuardBlock | `guard_blocked: {reason}` |
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
| Tool execution exception | Kernel handles normally (error string to LLM), terminal shows `tool_error` |
| Guard block | Terminal shows `guard_blocked`, kernel continues normal flow |
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

## pyproject.toml Registration

```toml
[project.scripts]
mm-devshell = "matmaster.devshell.cli:main"
```

## Dependencies

Only matmaster-internal and already-present dependencies:
- `matmaster.core.exp` (Exp pipeline)
- `matmaster.core.bus` (MessageBus)
- `matmaster.types.*` (PlaygroundContext, events, messages)
- `matmaster.providers.openai_provider` (OpenAIProvider)
- `evomaster.agent.session.*` (BaseSession, LocalSession)
- `pydantic` (DevConfig model)
- `pyyaml` (config loading)
- Standard library: `argparse`, `readline`, `pathlib`, `threading`, `signal`, `datetime`, `json`

No new external dependencies required.
