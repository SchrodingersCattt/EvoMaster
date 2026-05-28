# Two-Stage Tool Result Compression Design

- Date: 2026-05-17
- Status: Draft (awaiting user approval)
- Author: Kealdoom + Codex (brainstorming session)
- Related prior work:
  - [2026-05-17-inline-self-compaction-design.md](2026-05-17-inline-self-compaction-design.md)
  - [2026-05-09-compaction-checkpoint-context-design.md](2026-05-09-compaction-checkpoint-context-design.md)
  - [2026-05-11-preflight-current-input-compaction-design.md](2026-05-11-preflight-current-input-compaction-design.md)

## 1. Context & Motivation

MatMaster already has a healthy tool execution path:

- `FullToolRunner` is the central post-execution point for builtin and MCP
  tools.
- Every executor result is normalized into `ToolResult`.
- Each `ToolSpec` already carries `max_result_chars`.
- `dispatch_tool_calls` is the single place where tool results become both
  `ToolMessage` entries and `ToolResultEvent` events.

There is also an existing, implicit tool result storage prototype in
`FullToolRunner._truncate_result`: oversized `ToolResult.content` is written to
`{control_root}/.tool_results/{tool_call_id}.txt`, and the model-visible result
is replaced with a head/tail preview. The limitation is that this is only a
local helper:

- The storage record only appears in `ToolResult.meta`.
- `ToolResult.meta` is not propagated into `ToolResultEvent`.
- persisted chat history restores only `tool_result.result`, not metadata.
- context compaction has its own fallback truncation that does not persist full
  content.

As a result, the system has the right shape but not yet the right abstraction.
This design turns tool result compression into a first-class two-stage system.

## 2. Goals

1. **Prevent single tool calls from flooding the model context.**
   Large results should be persisted once, then represented by a stable,
   model-visible replacement string.

2. **Prevent parallel or adjacent tool results from exceeding provider limits
   in aggregate.**
   Even if each tool result is individually acceptable, a group of results from
   one assistant tool-use turn may exceed the practical input budget.

3. **Preserve prompt-cache stability.**
   Once a specific `tool_call_id` has been seen by the model in a particular
   form, later calls must not silently change that form.

4. **Keep compaction focused on summarization.**
   Tool result compression should happen before compaction. Summary-call
   preparation may still apply emergency truncation, but it should not be the
   primary tool result compression mechanism.

5. **Keep Phase 1 useful by itself.**
   Source-time storage must solve the common case without requiring the full
   Claude Code-style replacement state machine.

## 3. Non-Goals

- Do not change tool schemas or model-facing tool definitions.
- Do not make context compaction read full stored tool results by default.
- Do not expose absolute control-plane paths to the frontend.
- Do not rewrite historical `tool_result` events in place.
- Do not require Phase 1 to implement subagent/fork replacement inheritance.
- Do not use `run_meta` as a storage or service-object transport.
- Do not store filesystem paths in any `ToolResult` field. Paths are an
  implementation detail of `ToolResultStorage` and must remain encapsulated
  within storage service implementations.

## 4. Design Overview

The design has two stages.

### 4.1 Phase 1: Source-Time Tool Result Storage

Phase 1 runs immediately after tool execution and before a result is appended to
`state.messages`.

```text
tool executor
-> normalize ToolResult
-> error wrapping / hook rewrite
-> ToolResultStorage.process_result
-> ToolMessage.content = stable replacement
-> ToolResultEvent.result = stable replacement
-> ToolResultEvent.payload.tool_storage = public-safe storage record
```

The important invariant is:

```text
Once a ToolMessage enters state.messages, its content is already the canonical
model-visible form for that tool result.
```

This is different from Claude Code's query-time aggregate budget. Phase 1
normalizes the result at the source, so restored history and compaction can
consume the stable replacement string directly.

### 4.2 Phase 2: Query-Time Replacement State

Phase 2 runs before each main LLM call and before runtime compaction planning.
It scans the current model input view, detects groups of tool results whose
aggregate size exceeds a budget, and applies stable replacements using a
per-conversation state machine.

```text
state.messages
-> apply_tool_result_budget(replacement_state)
-> runtime compaction planning
-> provider canonicalization
-> LLM call
```

The Phase 2 invariant is:

```text
For a given tool_call_id, the replacement decision is frozen after the first
budget pass that sees it.
```

This mirrors Claude Code's `seenIds` / `replacements` behavior while adapting
it to MatMaster's `AssistantMessage` / `ToolMessage` data model.

## 5. Phase 1 Detailed Design

### 5.1 New Module

Add a new module:

```text
matmaster/tools/tool_storage.py
```

The module owns:

- storage record dataclasses
- preview/replacement generation
- local filesystem persistence
- path/ref sanitization
- compatibility helpers for the existing `full_result_path` metadata

### 5.2 Core Types

Storage objects are split into a public ref and a service-internal entry. The
public ref is the only object that may appear on `ToolResult`,
`ToolResultEvent.payload`, persisted events, or DB records. The internal entry
never leaves the storage service.

```python
@dataclass(frozen=True)
class ToolStorageRef:
    """Public-safe, ToolResult-embeddable storage reference.

    This is the only storage object that may appear in ToolResult,
    ToolResultEvent.payload, persisted events, or DB records.
    """
    schema_version: Literal["tool_result_storage.v1"]
    tool_call_id: str
    tool_name: str
    storage_ref: str
    sha256: str
    original_chars: int
    replacement_chars: int
    truncated: bool


@dataclass(frozen=True)
class _LocalStorageEntry:
    """Storage-service-internal record. NEVER exposed via ToolResult.

    Carries the absolute path used by LocalToolResultStorage to resolve
    refs back to file content. Other storage implementations may define
    their own internal entry types (e.g. S3 keys, workspace handles).
    """
    storage_ref: str
    path: Path
    sha256: str
    tool_call_id: str
    tool_name: str
    original_chars: int


@dataclass(frozen=True)
class ToolResultReplacementRecord:
    schema_version: Literal["tool_result_replacement.v1"]
    tool_call_id: str
    tool_name: str
    replacement: str
    storage_ref: str
    original_chars: int
    replacement_chars: int
    sha256: str
    truncated: bool
```

`ToolStorageRef` is the public-safe handle. It is path-free by construction.
Consumers obtain a `ToolStorageRef` from `ToolResultEvent.payload["tool_storage"]`
or from `ToolResult.payload["tool_storage"]`, then pass it to
`storage.load_full(ref)` when they need the original content.

`_LocalStorageEntry` is the implementation-private form for
`LocalToolResultStorage`. It carries the absolute path but is never returned
from any public method and never embedded in `ToolResult`. Alternative storage
backends (object storage, workspace storage) define their own internal entry
type and never expose physical addresses to callers.

`ToolResultReplacementRecord` is the exact model-visible decision. Its
`replacement` string is stored instead of regenerated so resume behavior is not
affected by later template or path-format changes.

### 5.3 Public Payload Shape

`ToolResultEvent.payload` should include a public-safe storage reference when
content is replaced:

```json
{
  "tool_storage": {
    "schema_version": "tool_result_storage.v1",
    "tool_call_id": "call_123",
    "tool_name": "Bash",
    "storage_ref": "tool-result:call_123",
    "sha256": "sha256:...",
    "original_chars": 84210,
    "replacement_chars": 2370,
    "truncated": true
  }
}
```

This payload must not include absolute local paths. Absolute paths must not
appear in `ToolResult` at all — not in `payload`, not in `meta`, not in
`content`. Storage paths are encapsulated inside `ToolResultStorage`
implementations and accessed only via `storage.load_full(ref)`. Consumers
requiring full content obtain a `ToolStorageRef` from
`payload["tool_storage"]` and pass it to the storage service.

Existing payload keys, especially `payload["figures"]`, must be preserved.
Storage metadata is additive.

### 5.4 Storage Location

Use a `.matmaster`-scoped location:

```text
{control_root}/.matmaster/tool-results/{tool_call_id}/result.txt
{control_root}/.matmaster/tool-results/{tool_call_id}/metadata.json
```

This layout is an internal detail of `LocalToolResultStorage`. Callers never
read these paths directly. Path-to-ref mapping is private to the storage
implementation, so future migration to an alternative backend (object storage,
workspace storage) does not require any caller changes.

The previous `_truncate_result` implementation wrote
`meta["full_result_path"]`. This field is removed in the new design. Diagnostic
code that previously read `meta["full_result_path"]` must migrate to
`payload["tool_storage"]["storage_ref"]` and call `storage.load_full(ref)`.
Existing tests that assert on `meta["full_result_path"]` should be rewritten
to assert on storage round-trip behavior.

### 5.5 Replacement String

The replacement string should be structured, short, and explicit:

```text
<persisted-tool-result>
tool_name: Bash
tool_call_id: call_123
original_chars: 84210
stored_as: tool-result:call_123

Preview:
...
</persisted-tool-result>
```

The replacement is the only content sent to the model, persisted in
`ToolMessage.content`, and stored in the replacement record.

### 5.6 Processing Order

Phase 1 should eventually run after hook rewrites and before final event
emission:

```text
execute
-> normalize_tool_result
-> error wrap
-> POST_TOOL_CALL rewrite hook
-> ToolResultStorage.process_result
-> POST_TOOL_CALL observe hook
-> ToolMessage / ToolResultEvent
```

This avoids persisting unredacted or pre-rewrite content when hooks are used for
sanitization. If preserving the current behavior is safer for the first patch,
the implementation may keep storage before post-tool hooks, but the target
ordering above should be documented in code and tests.

### 5.7 Phase 1 Compatibility

Phase 1 replaces `FullToolRunner._truncate_result` with the storage service.
The existing semantics of `max_result_chars` remain:

- `max_result_chars == 0`: no source-time storage or truncation.
- `len(content) <= max_result_chars`: result passes through unchanged.
- `len(content) > max_result_chars`: full content is stored and model-visible
  content becomes the stable replacement string.

### 5.8 Storage Service Protocol

`ToolResultStorage` is a Protocol. The default implementation is
`LocalToolResultStorage`. Other implementations may target object storage,
workspace-scoped storage, or remote stores without changing callers.

```python
class ToolResultStorage(Protocol):
    async def store_full(
        self,
        *,
        content: str,
        tool_call_id: str,
        tool_name: str,
    ) -> ToolStorageRef:
        """Persist full content, return a path-free public ref."""
        ...

    async def load_full(self, ref: ToolStorageRef) -> str | None:
        """Resolve ref to full content. Returns None if missing or sha256
        mismatch. Implementations must not return partial content."""
        ...

    async def process_result(
        self,
        *,
        result: ToolResult,
        tool_call_id: str,
        tool_name: str,
        max_visible_chars: int,
    ) -> ToolResult:
        """High-level helper: if oversized, store full content and return
        a new ToolResult whose content is the replacement string and whose
        payload includes a ToolStorageRef."""
        ...
```

Callers (`FullToolRunner`, history restore, future diagnostic endpoints) must
use this protocol. They must not construct or read storage paths directly.
`LocalToolResultStorage.__init__` accepts a `root: Path` parameter; the
path-to-ref mapping is private to that instance and is never exported.

### 5.9 Service Injection

`ToolResultStorage` is constructed at experiment assembly time
(`matmaster/core/exp.py`) using the playground-provided `control_root`.
`FullToolRunner` receives the storage instance through its constructor and
stores it as a private attribute. The runner must not construct storage paths
from `topology.control_root` directly; that responsibility belongs to the
storage service.

```python
# Exp assembly
tool_storage = LocalToolResultStorage(
    root=playground_ctx.control_root / ".matmaster" / "tool-results",
)
runner = FullToolRunner(
    catalog=...,
    structural_validation=...,
    capability_policy=...,
    scheduler=...,
    topology=...,
    tool_storage=tool_storage,
    hook_executor=...,
)
```

This injection point is the single place where `control_root` and the storage
root directory are connected. Future backends override only the constructor
argument; everything downstream stays unchanged.

## 6. Phase 2 Detailed Design

### 6.1 Replacement State

Add a per-runtime state object:

```python
@dataclass
class ToolResultReplacementState:
    seen_ids: set[str] = field(default_factory=set)
    replacements: dict[str, str] = field(default_factory=dict)
```

State semantics:

- `tool_call_id in replacements`: this result was replaced before; reapply the
  exact same replacement string.
- `tool_call_id in seen_ids and not in replacements`: this result was previously
  sent without aggregate-budget replacement; keep it unchanged forever.
- `tool_call_id not in seen_ids`: fresh result; eligible for a new aggregate
  budget decision.

### 6.2 Eligibility and Budget Units

Phase 1 keeps the current `max_result_chars` meaning:

- positive value: source-time storage is enabled above that threshold.
- `0`: source-time storage is disabled for that tool.

Phase 2 uses a separate aggregate budget policy so source-time opt-out does not
silently become query-time opt-out forever. The first implementation should use
a global character budget plus an explicit tool-name skip set:

```python
@dataclass(frozen=True)
class ToolResultBudgetPolicy:
    aggregate_budget_chars: int
    skip_tool_names: frozenset[str] = frozenset()
```

Character counts are sufficient for the first implementation because tool
result replacement is a coarse budget guard. Token estimation remains the job
of compaction planning and provider-level prompt accounting.

Default policy:

- use character counts for candidate sizing.
- skip no tools by default.
- allow deployment config to add tools such as `Bohrium` to `skip_tool_names`
  if preserving exact raw output is more important than aggregate budget
  enforcement.

### 6.3 Budget Pass Placement

The budget pass must run before compaction:

```text
ensure tool_definitions cached
-> apply_tool_result_budget
-> run_runtime_compaction_if_needed
-> provider canonicalization
-> LLM call
```

This ordering matters because aggregate result replacement may bring the prompt
under the compaction threshold and avoid unnecessary summarization.

Preflight should use the same principle:

```text
restore history
-> reconstruct ToolResultReplacementState
-> apply_tool_result_budget
-> plan_preflight_compaction
```

### 6.4 Candidate Grouping

The first implementation should group tool results by MatMaster tool-use turn:

```text
AssistantMessage(tool_calls=[...])
-> immediately following ToolMessage entries for those tool_call_ids
```

Each group is budgeted independently.

Future provider-specific grouping may be added if
`canonicalize_messages_for_provider` merges a wider set of messages on the wire.
The grouping helper should therefore be isolated and tested directly.

### 6.5 Budget Algorithm

For each group:

1. Collect eligible `ToolMessage` candidates.
2. Skip messages with image or non-text payloads if they cannot be safely
   represented by text.
3. Partition candidates:
   - `must_reapply`: id exists in `replacements`.
   - `frozen`: id exists in `seen_ids` but not in `replacements`.
   - `fresh`: id has never been seen.
4. Reapply exact replacements for `must_reapply`.
5. Leave `frozen` unchanged.
6. If `frozen + fresh` exceeds the aggregate budget, select the largest fresh
   candidates for replacement until the group is within budget or no fresh
   candidates remain.
7. Add every fresh candidate to `seen_ids`.
8. Add newly replaced candidates to `replacements`.
9. Emit append-only replacement events for newly replaced candidates.

If frozen content alone exceeds the budget, the system should not change it.
That content has already been seen by the model in its current form, and
changing it would break prompt-cache stability. Compaction can handle that case
later.

### 6.6 Replacement Event

Add an internal event type:

```text
tool_result_replacement
```

Suggested content:

```json
{
  "schema_version": "tool_result_replacement.v1",
  "tool_call_id": "call_123",
  "tool_name": "Bash",
  "replacement": "<persisted-tool-result>...</persisted-tool-result>",
  "storage_ref": "tool-result:call_123",
  "original_chars": 84210,
  "replacement_chars": 2370,
  "sha256": "sha256:...",
  "reason": "aggregate_tool_result_budget"
}
```

This event is append-only. It does not mutate the original `tool_result` event.
It is persisted for backend restore, but it is not part of the public SSE stream
by default. It may be exposed later through authenticated diagnostics.

### 6.7 Resume Reconstruction

On history restore:

1. Load restored messages.
2. Load `tool_result_replacement` events in scope.
3. Build `ToolResultReplacementState`.
4. Mark all candidate tool ids in restored messages as seen.
5. Populate `replacements` from replacement events whose ids still appear in
   restored messages.
6. Apply replacements during the next query-time budget pass.

If a replacement event references a tool id no longer present after compaction,
it is inert.

### 6.8 Subagent / Fork Behavior

Phase 2 should define the target behavior even if the first implementation only
supports the root run:

- Cache-sharing forks should clone the parent's replacement state at fork time.
- Resumed subagents should reconstruct from their own replacement events plus
  inherited parent replacements when parent tool ids appear in the sidechain.
- Non-cache-sharing or summary-only subagents may run without persistence if
  their messages are not resumed later.

The first Phase 2 implementation may explicitly limit support to root runs, but
the state object and reconstruction API should not block later subagent support.

## 7. Compaction Integration

The inline self-compaction design should treat tool result compression as an
upstream concern.

Summary input preparation should follow this order:

```text
source-time storage replacement
-> query-time aggregate replacement
-> summary-call emergency guard
```

`prepare_messages_for_summary_call` should not be the primary tool result
truncation mechanism. It only handles exceptional cases:

- legacy histories without storage or replacement records
- tools skipped by aggregate budget policy
- aggregate budget disabled or unavailable
- summary request still over budget after query-time replacement
- provider-specific tool schema overhead leaving too little room

The summary call should not reload full stored tool results by default. It
summarizes the same model-visible history that the main LLM saw.

## 8. Data Flow

### 8.1 Phase 1 Source-Time Flow

```text
Tool executor returns full content
        |
        v
ToolResultStorage stores full content
        |
        v
replacement string becomes ToolResult.content
        |
        v
ToolMessage.content and ToolResultEvent.result receive replacement
        |
        v
DB history restores replacement as normal tool result content
```

### 8.2 Phase 2 Query-Time Flow

```text
Restored/current messages
        |
        v
collect tool result groups
        |
        v
partition by replacement state
        |
        v
reapply known replacements, freeze seen ids, replace selected fresh ids
        |
        v
emit tool_result_replacement events for new decisions
        |
        v
compaction planning and LLM call consume stable wire view
```

## 9. Error Handling

### 9.1 Storage Failure

If source-time storage fails:

- Log the filesystem error with sensitive values redacted.
- Return the original tool result unchanged.
- Do not emit a partial storage record.
- Do not mark the result as replaced.

This preserves correctness. The prompt may be larger, but the model does not
receive a broken file reference.

### 9.2 Metadata Write Failure

If `metadata.json` fails but `result.txt` succeeds:

- Prefer treating the whole storage operation as failed.
- Avoid emitting a storage reference without metadata.
- Leave the original tool result unchanged unless a future recovery path can
  guarantee consistency.

### 9.3 Replacement Event Failure

If Phase 2 creates a new aggregate replacement but the replacement event cannot
be persisted:

- The in-memory state may still apply the replacement for the current run.
- The failure should be logged.
- On resume, the replacement may not be reconstructed; this can reduce cache
  stability but should not corrupt history.

If stronger resume stability is required, Phase 2 may fail closed and skip new
aggregate replacements when event persistence is unavailable.

## 10. Security & Privacy

- Full tool results may contain credentials, tokens, paths, or scientific data.
- Stored result files and metadata should be written with owner-only
  permissions where supported.
- `ToolResult` must not include absolute control-plane paths in any field
  (`content`, `payload`, `meta`). Paths exist only inside `ToolResultStorage`
  implementations, which expose access exclusively through `load_full(ref)`.
  This invariant prevents control-plane filesystem layout from leaking into
  events, DB, SSE streams, or restored history.
- Logs must not print full tool result content.
- `sha256` values are allowed in metadata and public payloads because they
  support integrity checks without exposing content.

## 11. Testing Strategy

### 11.1 Phase 1 Tests

- Oversized `ToolResult` is stored under `.matmaster/tool-results`.
- Returned `ToolResult.content` is the stable replacement string.
- `ToolResult.meta` does not contain any field whose value is a filesystem
  path. In particular, `meta["full_result_path"]` is absent.
- `ToolResult.payload["tool_storage"]` is a `ToolStorageRef` dict with no
  `path` key and no other field that exposes a filesystem location.
- `storage.load_full(ref)` round-trips the original content and verifies the
  sha256 against the stored value.
- `storage.load_full(ref)` returns `None` when the underlying file is missing
  or when sha256 does not match.
- Storage service is the only object that knows the on-disk path; tests must
  obtain content via `load_full`, not by constructing paths from
  `tool_call_id`.
- Existing `payload["figures"]` survives storage replacement.
- History restore reconstructs `ToolMessage.content` as the replacement string.
- Compaction summary input consumes replacement text, not full stored text.

### 11.2 Phase 2 Tests

- Fresh candidates under aggregate budget are marked seen and left unchanged.
- Fresh candidates over aggregate budget are replaced largest-first.
- Previously replaced candidates are re-applied byte-for-byte.
- Previously seen but unreplaced candidates are frozen and never replaced later.
- Replacement events are emitted only for newly replaced candidates.
- Resume reconstruction rebuilds `seen_ids` and `replacements`.
- Replacement events for ids absent from restored messages are ignored.
- Query-time budget pass runs before runtime compaction planning.

### 11.3 Regression Tests

- Tool call / tool result pairing remains valid after replacement.
- Empty tool result content remains model-readable.
- Non-text or image payloads are not converted into invalid text references.
- Runtime compaction fallback does not orphan `ToolMessage` entries.
- Inline summary calls do not mutate `state.messages` during emergency
  truncation.

## 12. Rollout Plan

### Phase 1 Rollout

1. Add `matmaster/tools/tool_storage.py`.
2. Replace `FullToolRunner._truncate_result` with `ToolResultStorage`.
3. Propagate `payload["tool_storage"]` through `ToolResultEvent`.
4. Rewrite existing truncation tests: replace assertions on
   `meta["full_result_path"]` with `storage.load_full(ref)` round-trip checks.
   Update path expectations to use the new `.matmaster/tool-results` location
   through storage service helpers, not direct path construction.
5. Add history restore and compaction integration tests.

### Phase 2 Rollout

1. Add `ToolResultReplacementState`.
2. Add candidate grouping and aggregate budget helpers.
3. Add query-time `apply_tool_result_budget` before compaction planning.
4. Add append-only `tool_result_replacement` event persistence.
5. Add restore reconstruction.
6. Add root-run tests first.
7. Add subagent/fork inheritance in a follow-up patch.

## 13. Resolved Design Decisions

1. `max_result_chars == 0` remains a Phase 1 source-time storage opt-out. Phase
   2 uses an independent aggregate budget policy with an explicit skip set.
2. Phase 2 counts characters, not estimated tokens, in the first implementation.
   This matches the coarse nature of result-size budgeting and avoids extra
   tokenizer dependencies in the query-time fast path.
3. `tool_result_replacement` events are backend-persisted internal events by
   default. They should not appear in public SSE streams unless a future
   diagnostics surface explicitly opts in.
4. Stored tool result files should be cleaned up best-effort when a session is
   deleted, but cleanup failure must not block session deletion.
5. Authenticated retrieval of full stored tool results is out of scope for the
   first implementation. The storage format should not prevent adding such an
   endpoint later.
6. Absolute storage paths are not part of the `ToolResult` data contract. They
   live inside `ToolResultStorage` implementations only. This decision is
   stricter than an earlier draft, which permitted paths in
   `ToolResult.meta` as a server-side field. The stricter rule prevents path
   leakage through any future serialization or persistence change and avoids
   creating two parallel access paths to the same resource
   (`load_full(ref)` vs direct path read).

## 14. Implementation Principles

- Phase 1 should not depend on Phase 2.
- Phase 2 should reuse Phase 1 replacement records when possible.
- The model-visible replacement string is a durable behavioral artifact.
- Event history remains append-only.
- Compaction consumes stable history; it does not own tool result storage.
- If storage or replacement fails, prefer larger valid context over broken
  references.
