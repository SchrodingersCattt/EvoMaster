---
name: session-analyzer
description: Analyze a MatMaster shared session from its share link. Extracts key decisions, conclusions, tool usage trace, and errors. Use when the user pastes a MatMaster share URL (e.g. matmaster.*.bohrium.com/matmaster/chat/share/..., legacy chat-evo/share links also accepted).
---

# Session Analyzer

Read-only extraction of key information from a shared MatMaster session via the public SSE API.

## Task Script: `scripts/fetch_shared_session.py`

Stdlib-only (urllib + json). Resolve at runtime:

```bash
SKILL_DIR=$(python3 -c "import matmaster.skills, pathlib; print(pathlib.Path(matmaster.skills.__file__).parent / 'session-analyzer')")
CLIENT="${SKILL_DIR}/scripts/fetch_shared_session.py"
```

### Modes

| Mode | Args | Output |
|------|------|--------|
| `summary` (default) | `"<url>"` | query + thought + response + run_result + failed tools + compact tool trace |
| `full` | `"<url>" --mode full --max-content-len 4000` | All events (excluding pings/status), per-field truncation |
| `raw` | `"<url>" --mode raw --event-types tool_call,tool_result` | Only specified event types |

Output is JSON with top-level keys: `stats`, `tool_trace`, `failed_tools`, `events`.

## Standard Workflow

1. Run `python3 "$CLIENT" "<share_url>" --mode summary`.

2. Present results as prose to the user:
   - User intent (from `events` where type=query)
   - Agent actions (from `tool_trace`)
   - Outcome and conclusions (from `events` where type=response)
   - Errors if any (from `failed_tools`)
   - Run stats (from `stats`)

3. If user asks for more detail on a specific step → re-run with `--mode full` or `--mode raw --event-types <specific>`.

4. If `stats.tool_calls > 50` → warn user that full mode output is large, suggest `--event-types` filter.

## Execution Rules

- This skill is read-only. Do not attempt to send messages to the analyzed session.
- If script returns HTTP 403 or empty events → tell user: "This session is not shared or does not exist."
- Do not expose raw session IDs or internal API URLs unless user asks.
- Present extracted information as prose paragraphs, not raw JSON dumps.
