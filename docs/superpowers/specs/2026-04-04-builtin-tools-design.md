# Builtin Tools Rebuild Design Spec

> Replaces the deleted `matmaster/tools/builtin/` with fresh implementations following Claude Code tool naming and behavior.

## Decisions

| Decision | Choice |
|---|---|
| Session model | All tools use `session.*` API uniformly (no local fast-path) |
| Grep backend | `rg` preferred, `grep` fallback (runtime detection) |
| Agent params | MatMaster domain names (`exp_name`), CC structure (`description` + `prompt`) |
| MonitorJobTool | Out of scope, references commented out |
| SkillTool | Migrate into `builtin/`, rename to `Skill`, refactor to CC style |
| Architecture | One file per tool, shared `_path_safety.py` |
| Tool names | All use Claude Code names: Bash, Read, Edit, Write, Glob, Grep, WebSearch, WebFetch, Agent, TodoWrite, Skill |

## Directory Structure

```
matmaster/tools/builtin/
  __init__.py
  base.py
  _path_safety.py
  bash_tool.py
  read_tool.py
  edit_tool.py
  write_tool.py
  glob_tool.py
  grep_tool.py
  web_search_tool.py
  web_fetch_tool.py
  agent_tool.py
  todo_write_tool.py
  skill_tool.py
```

## 1. Infrastructure

### 1.1 base.py -- BuiltinTool ABC

Satisfies the existing `Tool` Protocol. Core contract:

- **Construction**: `__init__(*, session=None, workdir=None)`
- **Execution**: `execute(arguments)` -- async, delegates `_execute(arguments)` via `asyncio.to_thread`
- **Context execution**: `execute_with_context(arguments, exec_ctx)` -- default same as `execute()`, subclasses override for `stop_event` / `runner_state`
- **Validation**: `validate_input(arguments, runner_state)` -- return `None` to allow, `ToolDecision(deny)` to reject
- **Subclass contract**: `_execute(arguments) -> str | ToolResult` -- sync, `@abstractmethod`
- **Helpers**: `_require_session()` raises `RuntimeError` if session is None; `_stop_event_for_exec()` resolves cancellation signal

ClassVar defaults:

| Attribute | Default |
|---|---|
| `effect_level` | `"local_mutation"` |
| `fast_path_eligible` | `False` |
| `max_result_chars` | `0` |
| `plane` | `CONTROL_PLANE` |
| `state_mode` | `"stateless"` |
| `stop_mode` | `"cancellable"` |
| `exposed_to_model` | `True` |

### 1.2 _path_safety.py

Single function:

```python
def resolve_safe_path(user_path: str, workdir: str) -> str:
```

Logic:
1. Empty or `.` -> return workdir
2. Absolute path -> `posixpath.normpath`, verify within workdir, else fallback to workdir
3. Relative path -> join with workdir, then same check

Used by Glob and Grep. Write's boundary check lives in `validate_input` (needs `deny` not silent fallback).

### 1.3 __init__.py

Exports: `BuiltinTool`, `BashTool`, `ReadTool`, `EditTool`, `WriteTool`, `GlobTool`, `GrepTool`, `WebSearchTool`, `WebFetchTool`, `AgentTool`, `TodoWriteTool`, `SkillTool`.

## 2. File Tools

### 2.1 Read

```
name = "Read"
plane = SESSION_FS
effect_level = "none"
fast_path_eligible = True
max_result_chars = 12_000
resource = ResourceClaim("workspace", "shared_read")
```

Parameters:

| Param | Type | Required | Description |
|---|---|---|---|
| `file_path` | string | yes | Absolute path |
| `offset` | integer | no | Start line (0-indexed) |
| `limit` | integer | no | Number of lines to read, default reads to EOF (cap 2000) |

Behavior:
1. `session.is_file(path)` validation
2. `session.read_file(path)` to get full content, splitlines
3. Full-read mode (no offset/limit):
   - 2000 lines or fewer: cat -n format, `meta.mark_read = True`
   - Over 2000: error + 50-line preview + guidance to use offset/limit
4. Range mode (offset/limit specified):
   - Slice lines, cat -n format
   - Continuation hint if truncated
5. All output capped at 200K chars
6. `execute_with_context()`: successful full read adds path to `runner_state["read_files"]`

### 2.2 Edit

```
name = "Edit"
plane = SESSION_FS
effect_level = "local_mutation"
resource = ResourceClaim("workspace", "exclusive")
```

Parameters:

| Param | Type | Required | Description |
|---|---|---|---|
| `file_path` | string | yes | Absolute path |
| `old_string` | string | yes | Exact string to find |
| `new_string` | string | yes | Replacement string |
| `replace_all` | boolean | no | Default false |

validate_input:
1. `old_string` must be non-empty
2. `old_string != new_string`
3. Read-before-modify: path must be in `runner_state["read_files"]`

_execute:
1. `session.read_file(path)` for current content
2. `re.finditer(re.escape(old_string), content)` to find matches
3. Zero matches: try `old_string.strip()` fallback, still zero -> error
4. `replace_all=False`: multiple matches -> error with line numbers; single match -> replace
5. `replace_all=True`: `content.replace(old_string, new_string)`, return replacement count
6. `session.write_file(path, new_content)`
7. Return cat -n context snippet around edit location (+-4 lines, 16K truncation)

### 2.3 Write

```
name = "Write"
plane = SESSION_FS
effect_level = "local_mutation"
resource = ResourceClaim("workspace", "exclusive")
```

Parameters:

| Param | Type | Required | Description |
|---|---|---|---|
| `file_path` | string | yes | Absolute path |
| `content` | string | yes | Complete file content |

validate_input:
1. file_path non-empty
2. Workspace boundary: `PurePosixPath(normpath(path)).is_relative_to(workdir)`, violation -> deny
3. Read-before-modify: if `session.path_exists(path)` is True, path must be in `runner_state["read_files"]`

_execute:
1. `session.write_file(path, content)` -- session layer creates parent dirs
2. Return `"File written successfully to: {path}"`

## 3. Shell and Search Tools

### 3.1 Bash

```
name = "Bash"
plane = SESSION_SHELL
effect_level = "local_mutation"
max_result_chars = 30_000
resource = ResourceClaim("session", "exclusive")
```

Parameters:

| Param | Type | Required | Description |
|---|---|---|---|
| `command` | string | yes | Bash command |
| `timeout` | integer | no | Timeout in milliseconds, default 120000 |
| `description` | string | no | Purpose description for logging/audit |

_execute:
1. Strip command, error if empty
2. Convert timeout ms -> seconds for `session.exec_bash(command, timeout, stop_event)`
3. Output format: `stdout + stderr + \n[Current working directory: ...] + \n[Command finished with exit code ...]`

execute_with_context:
- Captures `exec_ctx.stop_event` into `self._stop_event`, then delegates to execute()

prompt():
- Returns tool usage guidance: do not use bash for cat/head/tail/sed/awk/find/ls/grep/rg/echo, use Read/Edit/Write/Glob/Grep instead.

No proxy-clear prefix injection (platform concern, not tool concern).

### 3.2 Glob

```
name = "Glob"
plane = SESSION_SHELL
effect_level = "none"
fast_path_eligible = True
max_result_chars = 8_000
resource = ResourceClaim("session", "shared_read")
```

Parameters:

| Param | Type | Required | Description |
|---|---|---|---|
| `pattern` | string | yes | Glob pattern (`**/*.py`, `src/**/*.ts`) |
| `path` | string | no | Search root, default workspace |

_execute:
1. `resolve_safe_path(path, workdir)` to determine search root
2. Build find command with exclusions (`.git`, `node_modules`, `__pycache__`, `.svn`)
3. `session.exec_bash(cmd, timeout=30)`
4. Limit 200 results via `head -200`
5. Empty -> `"No files matching pattern '{pattern}' found in {safe_path}"`

### 3.3 Grep

```
name = "Grep"
plane = SESSION_SHELL
effect_level = "none"
fast_path_eligible = True
max_result_chars = 20_000
resource = ResourceClaim("session", "shared_read")
```

Parameters:

| Param | Type | Required | Description |
|---|---|---|---|
| `pattern` | string | yes | Regex pattern |
| `path` | string | no | Search directory |
| `glob` | string | no | File filter (`*.py`, `*.{ts,tsx}`) |
| `output_mode` | string | no | `content` / `files_with_matches` (default) / `count` |
| `-A` | integer | no | Lines after match (content mode only) |
| `-B` | integer | no | Lines before match (content mode only) |
| `-C` | integer | no | Context lines (content mode only) |
| `-i` | boolean | no | Case insensitive |
| `-n` | boolean | no | Show line numbers, default true (content mode only) |
| `head_limit` | integer | no | Result cap, default 250 |
| `offset` | integer | no | Skip first N entries |

Backend detection:
- Instance-level cache `self._use_rg: bool | None = None`
- First execution: `session.exec_bash("which rg 2>/dev/null", timeout=5)` to detect, cache result

rg path (`self._use_rg = True`):
```
rg {flags} --glob "{glob}" "{pattern}" "{safe_path}" 2>/dev/null
```
Flags: `--files-with-matches` / default / `--count`; `--ignore-case`; auto-excludes `.git`/`node_modules`/`__pycache__`.

grep fallback (`self._use_rg = False`):
```
grep -r {flags} --include="{glob}" "{pattern}" "{safe_path}" 2>/dev/null
```
Flags: `-l` / `-n` / `-c`; `-i`; `--exclude-dir=.git --exclude-dir=node_modules --exclude-dir=__pycache__`.

Both paths pipe through `tail -n +{offset+1} | head -{head_limit}`.

Empty output -> `"No matches for pattern '{pattern}' in {safe_path}"`.

## 4. Web Tools

### 4.1 WebSearch

```
name = "WebSearch"
plane = EXTERNAL_SERVICE
effect_level = "external_effect"
stop_mode = "best_effort"
resource = ResourceClaim("web", "counted", max_concurrent=3)
```

Parameters:

| Param | Type | Required | Description |
|---|---|---|---|
| `query` | string | yes | Search query |
| `allowed_domains` | array[string] | no | Whitelist domains |
| `blocked_domains` | array[string] | no | Blacklist domains |

_execute:
1. Empty query -> error
2. Resolve API key from `SEARCHAPI_API_KEY` / `SEARCHAPI_KEY` env vars; missing -> error
3. Domain filtering via query modifiers:
   - `allowed_domains` -> append `site:domain1 OR site:domain2`
   - `blocked_domains` -> append `-site:domain`
4. `httpx.Client(timeout=20)` to SearchApi.io, engine=google
5. Extract `organic_results`, take top 10: `{title, link, snippet}` each
6. Return `ToolResult(content=json.dumps({results: [...]}))`

No session dependency. Constructor takes no session.

### 4.2 WebFetch

```
name = "WebFetch"
plane = EXTERNAL_SERVICE
effect_level = "external_effect"
max_result_chars = 100_000
stop_mode = "best_effort"
resource = ResourceClaim("web", "counted", max_concurrent=3)
```

Parameters:

| Param | Type | Required | Description |
|---|---|---|---|
| `url` | string | yes | URL to fetch |
| `prompt` | string | no | Extraction hint (recorded, not used for LLM extraction this phase) |

_execute:
1. Empty url -> error
2. URL normalization: decode then re-encode path for special characters
3. Disk cache check: `{workdir}/.web_cache/{url_sha256_16}.json`, TTL 15min
4. `httpx.Client(timeout=15, follow_redirects=True)` to fetch
5. 403/429 -> retry once with alternate User-Agent
6. Content extraction by content-type:
   - HTML: BeautifulSoup cleanup (strip script/style/nav/footer/aside/noscript/iframe, noise-pattern class/id removal) -> markdownify to Markdown
   - PDF: PyMuPDF text extraction (optional dep, error if unavailable)
   - Other: raw text
7. Clean control characters, 50K char truncation
8. Write disk cache (max 200 entries, evict oldest)
9. `prompt` stored in `ToolResult.payload["prompt"]` for future extensibility
10. Return `ToolResult(content=extracted_text)`

Constructor: `__init__(*, workdir=None)`. No session needed.

## 5. Agentic Tools

### 5.1 Agent

```
name = "Agent"
plane = CONTROL_PLANE
effect_level = "local_mutation"
stop_mode = "non_cancellable"
resource = ResourceClaim("spawn", "counted", max_concurrent=2)
```

Parameters:

| Param | Type | Required | Description |
|---|---|---|---|
| `description` | string | yes | 3-5 word task summary |
| `prompt` | string | yes | Full task description; sub-agent has no conversation history |
| `exp_name` | string | no | Sub-agent type, maps to exp TOML name |

Constructor:
```python
def __init__(self, *, session=None, workdir=None,
             spawn_fn=None,          # async (exp_name, task, stop_event) -> str
             available_exps=None):   # list[tuple[name, description]]
```

- `spawn_fn` injected by Exp; async callback to launch sub-agent
- `available_exps` drives dynamic `exp_name` enum constraint and description text
- Recursion guard: child agents constructed with `spawn_fn=None`, tool not registered

Dynamic schema:
- If `available_exps` provided at construction, override instance-level `description` and `json_schema`
- `exp_name` field gets `enum: [name1, name2, ...]` constraint
- Description lists each exp's purpose

execute() override (native async, bypasses `_execute` + `to_thread`):
1. `spawn_fn is None` -> error (recursion depth limit)
2. Validate exp_name and prompt non-empty
3. `await self._spawn_fn(exp_name, prompt, self._stop_event)`
4. Return sub-agent output text

`_execute()` retained as ABC stub (`raise NotImplementedError`).

### 5.2 TodoWrite

```
name = "TodoWrite"
plane = CONTROL_PLANE
effect_level = "local_mutation"
resource = ResourceClaim("todo-store", "exclusive")
```

Parameters:

| Param | Type | Required | Description |
|---|---|---|---|
| `todos` | array[object] | yes | Complete todo list, full replacement |

Each todo object:

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string | yes | User-defined unique identifier |
| `content` | string | yes | Task description |
| `status` | string | yes | `pending` / `in_progress` / `completed` |
| `priority` | string | no | `low` / `medium` / `high` |

_execute:
1. Read `{workdir}/.todos.json` for old todos (missing file -> empty list)
2. Validate each todo: must have id/content/status, status must be valid enum
3. Full replacement write to `.todos.json`
4. If all todos have status `completed` -> clear file
5. Return change summary: added N, updated N, removed N, completed N

Storage format (`.todos.json`):
```json
{"todos": [{"id": "1", "content": "...", "status": "pending", "priority": "high"}]}
```

Thread safety: file read/write protected by `threading.Lock` (`_execute` runs in `to_thread`).

### 5.3 Skill

```
name = "Skill"
plane = CONTROL_PLANE
effect_level = "local_mutation"
fast_path_eligible = False
```

Parameters:

| Param | Type | Required | Description |
|---|---|---|---|
| `skill` | string | yes | Skill name, kebab-case |
| `args` | string | no | Arguments for the skill |

Constructor:
```python
def __init__(self, *, session=None, workdir=None,
             skill_registry=None,   # SkillRegistry instance
             on_skill_hit=None):    # Callable[[str], None], MCP connection callback
```

execute() override (native async):
1. `skill_registry.get_skill(skill_name)` -> None raises error
2. `skill.get_full_info()` for SKILL.md body
3. `${SKILL_DIR}` variable replacement with actual path
4. Traverse `skill.meta_info.depends_on`, trigger MCP connection for each
5. If `args` is non-empty, append to return content
6. Return `"Base directory for this skill: {skill_dir}\n\n{body}"`

`_execute()` retained as ABC stub.

## 6. Integration Layer

### 6.1 Exp._init_builtin_tools() rewrite

New registration list:
```python
from matmaster.tools.builtin import (
    BashTool, ReadTool, WriteTool, EditTool,
    GlobTool, GrepTool, WebSearchTool, WebFetchTool,
    TodoWriteTool,
)

native_tools = [
    BashTool(session=ctx.session, workdir=exec_wd),
    ReadTool(session=ctx.session, workdir=exec_wd),
    WriteTool(session=ctx.session, workdir=exec_wd),
    EditTool(session=ctx.session, workdir=exec_wd),
    GlobTool(session=ctx.session, workdir=exec_wd),
    GrepTool(session=ctx.session, workdir=exec_wd),
    TodoWriteTool(workdir=ctx.workdir),
    WebSearchTool(),
    WebFetchTool(workdir=ctx.workdir),
]
```

Agent registered separately (needs spawn_fn):
```python
if ("Agent" in builtin_cfg or "*" in builtin_cfg) and ctx.session is not None:
    from matmaster.tools.builtin import AgentTool
    agent_tool = AgentTool(session=ctx.session, workdir=exec_wd,
                           spawn_fn=spawn_fn, available_exps=available_exps)
    registry.register(agent_tool, source="builtin")
```

Skill registered in `_init_skill_tools()` using new `builtin.SkillTool` class.

MonitorJobTool: comment out import and registration with `# TODO: rebuild MonitorJobTool`.

### 6.2 Downstream reference updates

| File | Change |
|---|---|
| `capability_policy.py` | `"execute_bash"` -> `"Bash"` |
| `agent.py:409` | `"use_skill"` -> `"Skill"`, `"skill_name"` -> `"skill"` |
| `tool_compiler.py:36` | `("list_dir", "glob", "grep")` -> `("Glob", "Grep")` |
| `eval_tooling_snapshot.py` | `_BUILTIN_WHEN_STAR` rewritten to new names; `"use_skill"` -> `"Skill"` |
| `devshell/runner.py:72` | `"execute_bash"` -> `"Bash"` in description text |
| `direct.toml` | `builtin = ["Bash", "Read", "Write", "Edit", "Glob", "Grep", "TodoWrite", "Agent", "WebSearch", "WebFetch"]` |
| `explore.toml` | tool names in builtin list and developer_instructions text |

## 7. Implementation Order

```
Phase 0: Infrastructure
    base.py + _path_safety.py + __init__.py (skeleton)

Phase 1: Core file/shell tools (parallel)
    read_tool.py, bash_tool.py, glob_tool.py, grep_tool.py

Phase 2: Write tools (depend on Read's runner_state)
    edit_tool.py, write_tool.py

Phase 3: Independent tools (parallel)
    web_search_tool.py, web_fetch_tool.py, agent_tool.py, todo_write_tool.py, skill_tool.py

Phase 4: Integration
    Exp, capability_policy, agent.py, tool_compiler, eval_tooling_snapshot,
    devshell/runner, direct.toml, explore.toml

Phase 5: Tests
    One test file per tool
```
