---
name: session-analyzer
description: Analyze a MatMaster shared session from its share link. Extracts key decisions, conclusions, tool usage trace, and errors. Use when the user pastes a MatMaster share URL (e.g. matmaster.*.bohrium.com/matmaster/chat-evo/share/...).
skill_type: operator
---

# Session Analyzer

Fetch and analyze a shared MatMaster session to extract key information: user queries, agent reasoning, conclusions, tool usage patterns, and errors.

## When This Skill Activates

The user pastes a URL matching: `https://<host>/matmaster/chat-evo/share/<session_id>`

## Vendored CLI: `scripts/fetch_shared_session.py`

Stdlib-only (urllib + json). Resolve the skill directory at runtime:

```bash
SKILL_DIR=$(python3 -c "import matmaster.skills, pathlib; print(pathlib.Path(matmaster.skills.__file__).parent / 'session-analyzer')")
CLIENT="${SKILL_DIR}/scripts/fetch_shared_session.py"
python3 "$CLIENT" --help
```

### Modes

| Mode | Use case | Output |
|------|----------|--------|
| `summary` (default) | First pass — understand what happened | query + thought + response + run_result + failed tools + compact tool trace |
| `full` | Drill into details when summary is not enough | All events (excluding pings/status), content truncated at `--max-content-len` |
| `raw --event-types X,Y` | Targeted extraction | Only specified event types |

### Examples

```bash
# Default summary
python3 "$CLIENT" "https://matmaster.test.bohrium.com/matmaster/chat-evo/share/abc123"

# Full detail with larger truncation window
python3 "$CLIENT" "https://matmaster.test.bohrium.com/matmaster/chat-evo/share/abc123" --mode full --max-content-len 4000

# Only tool calls and results
python3 "$CLIENT" "https://matmaster.test.bohrium.com/matmaster/chat-evo/share/abc123" --mode raw --event-types tool_call,tool_result
```

## Standard Workflow

1. **First call**: Run with `--mode summary` to get an overview. Report to the user:
   - What they asked (user queries)
   - What the agent did (tool trace summary)
   - Key conclusions (response content)
   - Any errors encountered (failed tools)
   - Run statistics (turns, token usage if available)

2. **If user asks for details**: Re-run with `--mode full` or `--mode raw --event-types <specific>` to get deeper context on specific steps.

3. **If session is very long** (stats show > 50 tool calls): Warn the user that full mode will be large, suggest using `--event-types` filter or increasing `--max-content-len` selectively.

## Output Schema

Summary mode returns:
```json
{
  "mode": "summary",
  "session_id": "...",
  "source_url": "...",
  "stats": {
    "total_events": 100,
    "user_queries": 2,
    "responses": 4,
    "tool_calls": 42,
    "tool_results": 41
  },
  "tool_trace": ["Bash", "Bohrium x20", "Bash x3"],
  "failed_tools": [{"name": "Bohrium", "error": "..."}],
  "events": [
    {"source": "User", "type": "query", "content": "..."},
    {"source": "MatMaster", "type": "thought", "content": "..."},
    {"source": "MatMaster", "type": "response", "content": "..."}
  ]
}
```

## Hard Guards

- Never send messages to the shared session (the API only supports subscribe/read).
- If the session is not shared (403 or empty response), report this to the user clearly.
- Do not expose raw session IDs or internal API URLs to the user unless asked.
- Content truncation is a safety net — when presenting to the user, further summarize rather than dumping raw JSON.
