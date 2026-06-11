# Skill Slash Command Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 支持用户在首行输入 `/skill-name`，由后端确定性解析并跨 Redis 传递，在 Worker/Exp 中基于当前 `SkillRegistry` 确认，首轮 LLM 调用前注入完整 skill 文档。

**Architecture:** API/devshell 只做语法解析，不确认 skill 是否存在。跨进程只传 `SkillCommandCandidate` Pydantic DTO；Exp 先用历史 `skill_hit` build runtime，再用 build 后的 `SkillRegistry` 确认当前 slash candidate。未命中保持原文；首次命中清理当前轮正文、注入 runtime-only `invoked_skills` section、复用 `SkillTool` lazy MCP activation、yield `SkillHitEvent(source="slash")`；重复命中只清理正文，不重注入全文、不发 hit。

**Tech Stack:** Python 3.11+ via `uv run`, Pydantic v2, frozen dataclass, pytest/pytest-asyncio, FastAPI, Redis job payload, MatMaster `Exp` / `ContextAssembler` runtime boundary.

---

## File Structure

**Create**
- `matmaster/skills/invocation.py` — slash parser DTOs and helpers.
- `tests/matmaster/skills/test_slash_invocation_parser.py` — parser and `TurnInput` replacement tests.
- `matmaster/context/sources/invoked_skills.py` — invoked skill section renderer.
- `tests/matmaster/context/sources/test_invoked_skills.py` — renderer tests.
- `matmaster/core/skill_slash.py` — Exp-side candidate confirmation helper.
- `tests/matmaster/core/test_exp_skill_slash.py` — Exp confirmation, injection, dedup, MCP tests.
- `src/models/skills.py`, `src/apis/skills_api.py`, `tests/apis/test_skills_api.py` — backend-only command list endpoint.

**Modify**
- `src/services/stream_service.py`, `src/worker/agent_worker.py`, `src/services/agent_run_service.py`, `matmaster/core/run_context.py` — carry candidate through API Redis Worker service boundary.
- `matmaster/tools/builtin/skill_tool.py` — expose reusable `load_invoked_context()`.
- `matmaster/context/sections.py`, `matmaster/context/compositions.py`, `matmaster/context/assembly.py` — render invoked skills before current instruction.
- `matmaster/core/exp.py` — minimal wiring only; keep below 1000 lines.
- `matmaster/devshell/repl.py`, `matmaster/devshell/runner.py` — unknown slash reaches agent; runner uses shared Exp root path.
- `src/apis/api_router.py` — include skill command router.

**Line-count guard:** `matmaster/core/exp.py` is already 968 lines. Put logic in `matmaster/core/skill_slash.py`; after touching `exp.py`, run `uv run python .pre-commit/check_file_lines.py matmaster/core/exp.py matmaster/core/skill_slash.py`.

---

## Phase 1: Parser And Payload Boundary

### Task 1: Add Slash Invocation Parser

**Files:**
- Create: `matmaster/skills/invocation.py`
- Create: `tests/matmaster/skills/test_slash_invocation_parser.py`

- [ ] **Step 1: Write parser tests**

Create `tests/matmaster/skills/test_slash_invocation_parser.py` with these exact tests:

```python
from __future__ import annotations

import pytest

from matmaster.context.sources.turn_input import TurnInput
from matmaster.skills.invocation import (
    RESERVED_SLASH_COMMANDS,
    SkillCommandCandidate,
    parse_slash_skill_invocation,
    replace_turn_input_user_text,
)


@pytest.mark.parametrize(
    ("text", "name", "raw_command", "cleaned"),
    [
        ("/vasp", "vasp", "/vasp", ""),
        ("/vasp 帮我生成 INCAR", "vasp", "/vasp 帮我生成 INCAR", "帮我生成 INCAR"),
        ("/vasp\n帮我生成 INCAR", "vasp", "/vasp", "帮我生成 INCAR"),
        ("/VASP", "VASP", "/VASP", ""),
        ("/quantum_espresso", "quantum_espresso", "/quantum_espresso", ""),
        ("/operate-molecular-crystal", "operate-molecular-crystal", "/operate-molecular-crystal", ""),
    ],
)
def test_parse_valid_skill_candidate(text, name, raw_command, cleaned) -> None:
    result = parse_slash_skill_invocation(text)
    assert result.candidate == SkillCommandCandidate(
        name=name,
        raw_command=raw_command,
        cleaned_user_text=cleaned,
    )


@pytest.mark.parametrize(
    ("text", "ordinary"),
    [
        ("//vasp", "/vasp"),
        ("/share/work/POSCAR", "/share/work/POSCAR"),
        ("/vasp!", "/vasp!"),
        ("/vasp,", "/vasp,"),
        ("/", "/"),
        ("/中文", "/中文"),
        ("请用 /vasp 帮我生成 INCAR", "请用 /vasp 帮我生成 INCAR"),
    ],
)
def test_parse_non_command_inputs(text, ordinary) -> None:
    result = parse_slash_skill_invocation(text)
    assert result.candidate is None
    assert result.ordinary_user_text == ordinary


def test_reserved_commands_do_not_become_skill_candidates() -> None:
    for name in RESERVED_SLASH_COMMANDS:
        assert parse_slash_skill_invocation(f"/{name}").candidate is None


def test_replace_turn_input_user_text_preserves_attachments() -> None:
    turn_input = TurnInput.from_values(
        user_text="/vasp run",
        files=["https://oss.example.com/a.cif"],
        images=["https://oss.example.com/a.png"],
        workspace_paths=["/share/a/POSCAR"],
        pre_turn_history_event_id=42,
    )
    replaced = replace_turn_input_user_text(turn_input, "run")
    assert replaced.user_text == "run"
    assert replaced.files == ("https://oss.example.com/a.cif",)
    assert replaced.images == ("https://oss.example.com/a.png",)
    assert replaced.workspace_paths == ("/share/a/POSCAR",)
    assert replaced.pre_turn_history_event_id == 42
```

- [ ] **Step 2: Run parser tests red**

Run:

```bash
uv run pytest tests/matmaster/skills/test_slash_invocation_parser.py -q
```

Expected: FAIL with missing `matmaster.skills.invocation`.

- [ ] **Step 3: Implement parser and DTOs**

Create `matmaster/skills/invocation.py`:

```python
from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict

from matmaster.context.sources.turn_input import TurnInput

RESERVED_SLASH_COMMANDS = frozenset({"help", "skills", "stop", "clear"})
_COMMAND_NAME_RE = re.compile(r"^/([A-Za-z0-9][A-Za-z0-9_.:-]{0,127})(?=$|[ \t])")


class SkillCommandCandidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    raw_command: str
    cleaned_user_text: str = ""
    source: Literal["slash"] = "slash"


@dataclass(frozen=True)
class SlashSkillParseResult:
    candidate: SkillCommandCandidate | None
    ordinary_user_text: str
    raw_user_text: str


@dataclass(frozen=True)
class InvokedSkillContext:
    name: str
    base_dir: str
    full_body: str


def parse_slash_skill_invocation(text: str | None) -> SlashSkillParseResult:
    raw = (text or "").strip()
    if not raw:
        return SlashSkillParseResult(None, "", "")
    if raw.startswith("//"):
        return SlashSkillParseResult(None, raw[1:], raw)
    if not raw.startswith("/") or raw == "/":
        return SlashSkillParseResult(None, raw, raw)
    first_line, separator, remainder = raw.partition("\n")
    match = _COMMAND_NAME_RE.match(first_line)
    if match is None:
        return SlashSkillParseResult(None, raw, raw)
    name = match.group(1)
    if name in RESERVED_SLASH_COMMANDS:
        return SlashSkillParseResult(None, raw, raw)
    tail = first_line[match.end() :].lstrip(" \t")
    cleaned = f"{tail}\n{remainder}" if separator and tail else remainder if separator else tail
    candidate = SkillCommandCandidate(
        name=name,
        raw_command=first_line,
        cleaned_user_text=cleaned.strip(),
    )
    return SlashSkillParseResult(candidate, raw, raw)


def replace_turn_input_user_text(turn_input: TurnInput, user_text: str) -> TurnInput:
    return dataclasses.replace(
        turn_input,
        instruction=dataclasses.replace(
            turn_input.instruction,
            user_text=(user_text or "").strip(),
        ),
    )
```

- [ ] **Step 4: Run parser tests green and commit**

Run:

```bash
uv run pytest tests/matmaster/skills/test_slash_invocation_parser.py -q
git add matmaster/skills/invocation.py tests/matmaster/skills/test_slash_invocation_parser.py
git commit -m "feat(skills): add slash invocation parser"
```

Expected: pytest PASS, commit succeeds.

### Task 2: Carry Candidate Through API, Redis, Worker, Service

**Files:**
- Modify: `src/services/stream_service.py`
- Modify: `src/worker/agent_worker.py`
- Modify: `src/services/agent_run_service.py`
- Modify: `matmaster/core/run_context.py`
- Modify tests: `tests/test_chat_stream_direct.py`, `tests/test_chat_stream_reply_events.py`, `tests/matmaster/worker/test_redis_bridge.py`, `tests/matmaster/services/test_agent_run_stream.py`, `tests/matmaster/core/test_run_context.py`

- [ ] **Step 1: Write boundary tests**

Add these tests/assertions:

```python
# tests/test_chat_stream_direct.py
def test_prepare_send_message_preserves_raw_slash_and_records_candidate():
    ctx = service.prepare_send_message(
        "sess-1",
        ChatSendRequest(content="/vasp 帮我生成 INCAR"),
        user_id="user-1",
    )
    assert ctx.user_msg["content"] == "/vasp 帮我生成 INCAR"
    assert ctx.turn_input.user_text == "/vasp 帮我生成 INCAR"
    assert ctx.skill_command_candidate.name == "vasp"
    assert ctx.skill_command_candidate.cleaned_user_text == "帮我生成 INCAR"


@pytest.mark.asyncio
async def test_generate_send_stream_enqueues_skill_command_candidate():
    ctx = SendStreamContext(
        task_id="task-1",
        invocation_id="inv-1",
        mode="direct",
        user_msg={"source": "User", "type": "query", "content": "/vasp run"},
        request_event_queue=asyncio.Queue(),
        skill_command_candidate=SkillCommandCandidate(
            name="vasp",
            raw_command="/vasp run",
            cleaned_user_text="run",
        ),
    )
    # Use the same fake Redis + immediate stream close pattern as nearby tests.
    assert fake_redis.lpush_agent_run_job.call_args.args[0]["skill_command_candidate"] == {
        "name": "vasp",
        "raw_command": "/vasp run",
        "cleaned_user_text": "run",
        "source": "slash",
    }
```

```python
# tests/test_chat_stream_reply_events.py
assert "skill_command_candidate" in field_names

# tests/matmaster/worker/test_redis_bridge.py existing payload test
payload["skill_command_candidate"] = {
    "name": "vasp",
    "raw_command": "/vasp run",
    "cleaned_user_text": "run",
    "source": "slash",
}
assert observed["skill_command_candidate"].name == "vasp"

# tests/matmaster/services/test_agent_run_stream.py
candidate = SkillCommandCandidate(name="vasp", raw_command="/vasp run", cleaned_user_text="run")
await svc.run_agent(..., skill_command_candidate=candidate)
assert svc._test_fake_exp.last_ctx.request.skill_command_candidate == candidate

# tests/matmaster/core/test_run_context.py
request = AgentRunRequest(skill_command_candidate=candidate)
assert request.skill_command_candidate == candidate
```

- [ ] **Step 2: Run boundary tests red**

Run:

```bash
uv run pytest \
  tests/test_chat_stream_direct.py::test_prepare_send_message_preserves_raw_slash_and_records_candidate \
  tests/test_chat_stream_direct.py::test_generate_send_stream_enqueues_skill_command_candidate \
  tests/test_chat_stream_reply_events.py::test_send_stream_context_does_not_carry_unused_reply_queue \
  tests/matmaster/worker/test_redis_bridge.py \
  tests/matmaster/services/test_agent_run_stream.py::test_run_agent_passes_skill_command_candidate_to_exp \
  tests/matmaster/core/test_run_context.py::test_agent_run_request_accepts_skill_command_candidate \
  -q
```

Expected: FAIL on missing fields/signatures.

- [ ] **Step 3: Implement boundary plumbing**

Apply these code changes:

```python
# src/services/stream_service.py
from matmaster.skills.invocation import SkillCommandCandidate, parse_slash_skill_invocation

@dataclass
class SendStreamContext:
    skill_command_candidate: SkillCommandCandidate | None = None

slash_parse = parse_slash_skill_invocation(req.content)
user_content = slash_parse.raw_user_text
turn_user_text = (
    slash_parse.raw_user_text
    if slash_parse.candidate is not None
    else slash_parse.ordinary_user_text
)
turn_input = TurnInput.from_values(user_text=turn_user_text, ...)
skill_command_candidate=slash_parse.candidate

skill_candidate_payload = (
    ctx.skill_command_candidate.model_dump()
    if ctx.skill_command_candidate is not None
    else None
)
job["skill_command_candidate"] = skill_candidate_payload
```

```python
# matmaster/core/run_context.py
from matmaster.skills.invocation import SkillCommandCandidate
skill_command_candidate: SkillCommandCandidate | None = None

# src/worker/agent_worker.py
raw_skill_candidate = payload.get("skill_command_candidate")
skill_command_candidate = (
    SkillCommandCandidate.model_validate(raw_skill_candidate)
    if raw_skill_candidate is not None
    else None
)
run_agent_kwargs["skill_command_candidate"] = skill_command_candidate

# src/services/agent_run_service.py
async def run_agent(..., skill_command_candidate: SkillCommandCandidate | None = None, ...):
    AgentRunRequest(..., skill_command_candidate=skill_command_candidate, ...)
```

- [ ] **Step 4: Run boundary tests green and commit**

Run:

```bash
uv run pytest \
  tests/test_chat_stream_direct.py::test_prepare_send_message_preserves_raw_slash_and_records_candidate \
  tests/test_chat_stream_direct.py::test_generate_send_stream_enqueues_skill_command_candidate \
  tests/test_chat_stream_reply_events.py::test_send_stream_context_does_not_carry_unused_reply_queue \
  tests/matmaster/worker/test_redis_bridge.py \
  tests/matmaster/services/test_agent_run_stream.py::test_run_agent_passes_skill_command_candidate_to_exp \
  tests/matmaster/core/test_run_context.py::test_agent_run_request_accepts_skill_command_candidate \
  -q
git add src/services/stream_service.py src/worker/agent_worker.py src/services/agent_run_service.py matmaster/core/run_context.py tests/test_chat_stream_direct.py tests/test_chat_stream_reply_events.py tests/matmaster/worker/test_redis_bridge.py tests/matmaster/services/test_agent_run_stream.py tests/matmaster/core/test_run_context.py
git commit -m "feat(skills): carry slash skill candidate through run boundary"
```

Expected: pytest PASS, commit succeeds.

---

## Phase 2: Runtime Context Injection

### Task 3: Add Invoked Skill Section To Context Assembly

**Files:**
- Create: `matmaster/context/sources/invoked_skills.py`
- Create: `tests/matmaster/context/sources/test_invoked_skills.py`
- Modify: `matmaster/context/sections.py`, `matmaster/context/compositions.py`, `matmaster/context/assembly.py`, `tests/matmaster/context/test_assembly.py`

- [ ] **Step 1: Write context tests**

Add tests that assert:

```python
# tests/matmaster/context/sources/test_invoked_skills.py
source = InvokedSkillsSource((InvokedSkillContext("vasp", "/skills/vasp", "# VASP"),))
section = source.to_sections()[0]
assert section.key == "invoked_skills"
assert section.tag == "invoked_skills"
assert section.order == SectionOrder.INVOKED_SKILLS
assert section.views == frozenset({ContextView.RUNTIME})
assert "Base directory for this skill: /skills/vasp" in section.content
assert "Arguments" not in section.content
assert UserTurnContext.from_sources(source.to_sections()).render(ContextView.CHECKPOINT) == ""

# tests/matmaster/context/test_assembly.py
result = await assembler.assemble_turn(
    ContextAssemblyIntent.ANCHOR_TURN,
    TurnAssemblyRequest(
        session_id="sess-1",
        spawn_id=None,
        turn_input=TurnInput.from_values(user_text="帮我生成 INCAR"),
        user_instructions=UserInstructions(text="", hash=""),
        invoked_skills=(InvokedSkillContext("vasp", "/skills/vasp", "VASP body"),),
    ),
)
runtime = result.user_turn_context.render(ContextView.RUNTIME)
assert runtime.index("<invoked_skills>") < runtime.index("<current_instruction>")
```

- [ ] **Step 2: Run context tests red**

Run:

```bash
uv run pytest tests/matmaster/context/sources/test_invoked_skills.py tests/matmaster/context/test_assembly.py::test_assemble_turn_places_invoked_skills_before_current_instruction -q
```

Expected: FAIL on missing source and request field.

- [ ] **Step 3: Implement source and composition**

Create `matmaster/context/sources/invoked_skills.py`:

```python
from __future__ import annotations

from dataclasses import dataclass

from matmaster.context.sections import RUNTIME_ONLY_VIEWS, ContextSection, SectionOrder
from matmaster.skills.invocation import InvokedSkillContext


@dataclass(frozen=True)
class InvokedSkillsSource:
    skills: tuple[InvokedSkillContext, ...] = ()

    def to_sections(self) -> tuple[ContextSection, ...]:
        blocks = [
            (
                f"Skill: {skill.name}\n"
                f"Base directory for this skill: {skill.base_dir}\n\n"
                f"{skill.full_body.strip()}"
            ).strip()
            for skill in self.skills
        ]
        content = "\n\n---\n\n".join(block for block in blocks if block)
        if not content:
            return ()
        return (
            ContextSection(
                key="invoked_skills",
                tag="invoked_skills",
                content=content,
                order=SectionOrder.INVOKED_SKILLS,
                views=RUNTIME_ONLY_VIEWS,
            ),
        )
```

Apply structural edits:

```python
# sections.py
INVOKED_SKILLS = 900

# compositions.py
invoked_skills: tuple[InvokedSkillContext, ...] = ()
def _step_invoked_skills(inputs): return InvokedSkillsSource(inputs.invoked_skills).to_sections()
ANCHOR_COMPOSITION.steps = (
    _step_user_instructions,
    _step_session_sections,
    _step_invoked_skills,
    _step_turn_input,
    _step_session_jobs,
)

# assembly.py
class TurnAssemblyRequest:
    invoked_skills: tuple[InvokedSkillContext, ...] = ()
# assemble_turn() passes invoked_skills=request.invoked_skills
```

Do not add invoked skills to continuation or compacted compositions.

- [ ] **Step 4: Run context tests green and commit**

Run:

```bash
uv run pytest tests/matmaster/context/sources/test_invoked_skills.py tests/matmaster/context/test_assembly.py::test_assemble_turn_places_invoked_skills_before_current_instruction -q
git add matmaster/context/sections.py matmaster/context/compositions.py matmaster/context/assembly.py matmaster/context/sources/invoked_skills.py tests/matmaster/context/sources/test_invoked_skills.py tests/matmaster/context/test_assembly.py
git commit -m "feat(context): render invoked slash skills"
```

Expected: pytest PASS, commit succeeds.

### Task 4: Confirm Slash Candidate In Exp

**Files:**
- Modify: `matmaster/tools/builtin/skill_tool.py`
- Create: `matmaster/core/skill_slash.py`
- Modify: `matmaster/core/exp.py`
- Modify: `tests/matmaster/tools/builtin/test_skill_tool.py`
- Create: `tests/matmaster/core/test_exp_skill_slash.py`

- [ ] **Step 1: Write SkillTool and Exp tests**

Add to `tests/matmaster/tools/builtin/test_skill_tool.py`:

```python
context = SkillTool(skill_registry=make_registry(skill=skill)).load_invoked_context("test-skill")
assert context.name == "test-skill"
assert context.base_dir == "/skills/test-skill"
assert context.full_body == "# Test Skill\nUse it."

SkillTool(skill_registry=registry, on_skill_hit=callback).load_invoked_context("root")
assert [call.args[0] for call in callback.call_args_list] == ["root-server", "dep-server"]
```

Create `tests/matmaster/core/test_exp_skill_slash.py` with helpers:

```python
class _RecordingProvider:
    stream_timeout = 10.0
    max_retries = 1
    retry_delay = 0.0

    def __init__(self) -> None:
        self.tool_names_by_call: list[list[str]] = []

    async def __aenter__(self): return self
    async def __aexit__(self, *args): return None
    async def chat(self, messages, tools=None): return LLMResponse(content="ok", finish_reason="stop")
    async def chat_stream(self, messages, tools=None, *, timeout=None):
        self.tool_names_by_call.append([tool["name"] for tool in tools or []])
        yield StreamChunk(content="ok")
        yield StreamChunk(finish_reason="stop")
```

Create a tmp `vasp/SKILL.md` with `mcp_server: mat_vasp`, a `cache/mat_vasp.json` containing one `submit` schema, `mcp_config.json`, and `mcp.yaml`. Add three tests:

```python
# valid first slash
events = [event async for event in Exp(cfg).run_stream(ctx_with_candidate)]
assert [e.skill_name for e in events if isinstance(e, SkillHitEvent)] == ["vasp"]
assert any(e.source == "slash" for e in events if isinstance(e, SkillHitEvent))
assert "# VASP Skill" in writer_calls[0].message.content
assert "make INCAR" in writer_calls[0].message.content
assert "/vasp make INCAR" not in writer_calls[0].message.content
assert "mat_vasp_submit" in provider.tool_names_by_call[0]

# missing skill
assert not any(isinstance(e, SkillHitEvent) for e in events)
assert "/missing run" in writer_calls[0].message.content

# repeated slash with historical skill_hit
assert not any(isinstance(e, SkillHitEvent) for e in events)
assert "make INCAR" in writer_calls[0].message.content
assert "# VASP Skill" not in writer_calls[0].message.content
```

- [ ] **Step 2: Run Exp tests red**

Run:

```bash
uv run pytest tests/matmaster/tools/builtin/test_skill_tool.py tests/matmaster/core/test_exp_skill_slash.py -q
```

Expected: FAIL on missing helper and Exp slash logic.

- [ ] **Step 3: Implement SkillTool helper**

In `matmaster/tools/builtin/skill_tool.py`, add:

```python
def load_invoked_context(self, skill_name: str) -> InvokedSkillContext | None:
    name = (skill_name or "").lstrip("/")
    if self._registry is None:
        return None
    skill = self._registry.get_skill(name)
    if skill is None:
        return None
    body = skill.get_full_info()
    skill_dir = self._render_skill_dir(skill)
    body = body.replace("${SKILL_DIR}", skill_dir)
    self._maybe_hit_mcp(skill)
    for dep_name in skill.meta_info.depends_on:
        dep_skill = self._registry.get_skill(dep_name)
        if dep_skill is not None:
            self._maybe_hit_mcp(dep_skill)
    return InvokedSkillContext(name=name, base_dir=skill_dir, full_body=body)
```

Make `execute()` call this helper and return `Base directory for this skill: ...`.

- [ ] **Step 4: Implement Exp-side helper**

Create `matmaster/core/skill_slash.py`:

```python
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from matmaster.context.sources.turn_input import TurnInput
from matmaster.skills.invocation import InvokedSkillContext, SkillCommandCandidate, replace_turn_input_user_text
from matmaster.types.events import SkillHitEvent


@dataclass(frozen=True)
class SlashSkillResolution:
    active_skills: frozenset[str]
    turn_input: TurnInput
    invoked_skills: tuple[InvokedSkillContext, ...] = ()
    events: tuple[SkillHitEvent, ...] = ()


def resolve_slash_skill_candidate(
    *,
    candidate: SkillCommandCandidate | None,
    skill_registry: Any | None,
    load_invoked_context: Callable[[str], InvokedSkillContext | None] | None,
    historical_active_skills: frozenset[str],
    turn_input: TurnInput,
) -> SlashSkillResolution:
    if candidate is None or skill_registry is None:
        return SlashSkillResolution(historical_active_skills, turn_input)
    if skill_registry.get_skill(candidate.name) is None:
        return SlashSkillResolution(historical_active_skills, turn_input)
    cleaned = replace_turn_input_user_text(turn_input, candidate.cleaned_user_text)
    active = historical_active_skills | frozenset({candidate.name})
    if candidate.name in historical_active_skills:
        return SlashSkillResolution(active, cleaned)
    if load_invoked_context is None:
        return SlashSkillResolution(active, cleaned)
    context = load_invoked_context(candidate.name)
    if context is None:
        return SlashSkillResolution(historical_active_skills, turn_input)
    return SlashSkillResolution(
        active_skills=active,
        turn_input=cleaned,
        invoked_skills=(context,),
        events=(SkillHitEvent(source="slash", skill_name=candidate.name),),
    )
```

- [ ] **Step 5: Wire Exp minimally**

Apply these edits to `matmaster/core/exp.py`:

```python
from matmaster.core.skill_slash import resolve_slash_skill_candidate

self._skill_context_loader: Callable[[str], Any | None] | None = None
self._skill_context_loader = None  # in build_runtime reset block
self._skill_context_loader = skill_tool.load_invoked_context  # in _init_skill_tools

# _render_and_persist_root_turn()
invoked_skills: tuple[Any, ...] = (),
TurnAssemblyRequest(..., invoked_skills=invoked_skills)
```

In root `run_stream()`, after `runtime_scope()` yields and before rendering root turn:

```python
slash = resolve_slash_skill_candidate(
    candidate=ctx.request.skill_command_candidate,
    skill_registry=self._skill_registry,
    load_invoked_context=self._skill_context_loader,
    historical_active_skills=resolution.active_skills,
    turn_input=ctx.request.turn_input,
)
ctx = ctx.model_copy(
    update={
        "request": ctx.request.model_copy(
            update={"active_skills": slash.active_skills, "turn_input": slash.turn_input}
        )
    }
)
for event in slash.events:
    yield event
```

Pass `invoked_skills=slash.invoked_skills` to `_render_and_persist_root_turn()`.

- [ ] **Step 6: Run line-count and Exp tests green, then commit**

Run:

```bash
uv run python .pre-commit/check_file_lines.py matmaster/core/exp.py matmaster/core/skill_slash.py
uv run pytest tests/matmaster/tools/builtin/test_skill_tool.py tests/matmaster/core/test_exp_skill_slash.py tests/matmaster/core/test_exp_turn_preparation.py tests/matmaster/core/test_exp_skill_replay.py tests/matmaster/context/test_assembly.py -q
git add matmaster/tools/builtin/skill_tool.py matmaster/core/skill_slash.py matmaster/core/exp.py tests/matmaster/tools/builtin/test_skill_tool.py tests/matmaster/core/test_exp_skill_slash.py
git commit -m "feat(skills): confirm slash invocations in exp"
```

Expected: line-count PASS, pytest PASS, commit succeeds.

---

## Phase 3: Devshell Reuse

### Task 5: Route Unknown Slash Text Through Agent

**Files:**
- Modify: `matmaster/devshell/repl.py`
- Modify: `matmaster/devshell/runner.py`
- Modify: `tests/matmaster/devshell/test_repl.py`
- Modify: `tests/matmaster/devshell/test_runner.py`

- [ ] **Step 1: Write devshell tests**

Update `tests/matmaster/devshell/test_repl.py` so `parse_command("/unknown") is None`. Add runner test:

```python
candidate = SkillCommandCandidate(name="vasp", raw_command="/vasp run", cleaned_user_text="run")
with patch.object(Exp, "run_stream", fake_run_stream):
    runner.run("/vasp run", skill_command_candidate=candidate)
assert observed["ctx"].request.skill_command_candidate == candidate
assert observed["ctx"].request.turn_input.user_text == "/vasp run"
```

- [ ] **Step 2: Run devshell tests red**

Run:

```bash
uv run pytest tests/matmaster/devshell/test_repl.py::TestBuiltinCommands tests/matmaster/devshell/test_runner.py::TestDevRunner::test_run_passes_skill_command_candidate_to_exp_run_stream -q
```

Expected: FAIL because unknown slash is swallowed and runner lacks candidate param.

- [ ] **Step 3: Implement devshell changes**

In `matmaster/devshell/repl.py`, make `parse_command()` return only builtins:

```python
if cmd not in BUILTIN_COMMANDS:
    return None
```

Parse slash before worker thread:

```python
slash_parse = parse_slash_skill_invocation(user_input)
agent_task = (
    slash_parse.raw_user_text
    if slash_parse.candidate is not None
    else slash_parse.ordinary_user_text
)
runner.run(..., task=agent_task, skill_command_candidate=slash_parse.candidate)
```

In `matmaster/devshell/runner.py`, add `skill_command_candidate` to `run()` and `build_run_context()`, put current `TurnInput.from_values(user_text=task)` into `AgentRunRequest`, and replace manual `runtime_scope()` driving with:

```python
return await drain_run_stream(
    exp.run_stream(ctx, history=self.history, cancel_token=cancel_token),
    on_event=_on_event,
)
```

- [ ] **Step 4: Run devshell tests green and commit**

Run:

```bash
uv run pytest tests/matmaster/devshell/test_repl.py tests/matmaster/devshell/test_runner.py tests/matmaster/devshell/test_devshell_mcp_skill_filter.py -q
git add matmaster/devshell/repl.py matmaster/devshell/runner.py tests/matmaster/devshell/test_repl.py tests/matmaster/devshell/test_runner.py
git commit -m "feat(devshell): route slash skills through agent"
```

Expected: pytest PASS, commit succeeds.

---

## Phase 4: Backend Command Candidate Endpoint

This repository has no frontend code. This phase adds only backend `GET /api/v1/skills/commands`; slash autocomplete UI belongs to the frontend repository.

### Task 6: Add Read-Only Skills Command API

**Files:**
- Create: `src/models/skills.py`
- Create: `src/apis/skills_api.py`
- Modify: `src/apis/api_router.py`
- Create: `tests/apis/test_skills_api.py`

- [ ] **Step 1: Write API test**

Create `tests/apis/test_skills_api.py`:

```python
def test_skill_commands_endpoint_lists_configured_skills(tmp_path, monkeypatch) -> None:
    skill_dir = tmp_path / "skills" / "vasp"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: vasp\ndescription: VASP helper\n---\nbody",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "src.apis.skills_api.load_exp_config",
        lambda _name: ExpConfig(
            skills=ExpSkillsConfig(enabled=True, skills_root=str(tmp_path / "skills"))
        ),
    )
    response = TestClient(app).get("/api/v1/skills/commands")
    assert response.status_code == 200
    assert response.json()["data"]["skills"] == [{"name": "vasp", "description": "VASP helper"}]
    assert "help" in response.json()["data"]["reserved_commands"]
```

- [ ] **Step 2: Run API test red**

Run:

```bash
uv run pytest tests/apis/test_skills_api.py -q
```

Expected: FAIL because router does not exist.

- [ ] **Step 3: Implement API**

Create `src/models/skills.py` with `SkillCommandItem`, `SkillCommandsData`, `SkillCommandsResponse`. Create `src/apis/skills_api.py`:

```python
router = APIRouter(tags=["Skills"])


@router.get("/commands", response_model=SkillCommandsResponse)
async def list_skill_commands() -> SkillCommandsResponse:
    exp_config = load_exp_config("direct")
    skills_config = exp_config.skills
    skills = []
    if skills_config.enabled:
        roots_raw = skills_config.skills_root
        roots = roots_raw if isinstance(roots_raw, list) else [roots_raw]
        registry = build_skill_registry(
            config_roots=roots,
            session=None,
            config_disabled=skills_config.disabled_skill_names,
        )
        skills = [] if registry is None else registry.get_all_skills()
    items = sorted(
        [
            SkillCommandItem(
                name=skill.meta_info.name,
                description=skill.meta_info.description or "",
            )
            for skill in skills
        ],
        key=lambda item: item.name,
    )
    return SkillCommandsResponse(
        data=SkillCommandsData(
            skills=items,
            reserved_commands=sorted(RESERVED_SLASH_COMMANDS),
        )
    )
```

Include router in `src/apis/api_router.py` under prefix `/skills`.

- [ ] **Step 4: Run API test green and commit**

Run:

```bash
uv run pytest tests/apis/test_skills_api.py -q
git add src/models/skills.py src/apis/skills_api.py src/apis/api_router.py tests/apis/test_skills_api.py
git commit -m "feat(skills): expose slash command candidates"
```

Expected: pytest PASS, commit succeeds.

---

## Final Verification

- [ ] **Step 1: Run focused suite**

Run:

```bash
uv run pytest \
  tests/matmaster/skills/test_slash_invocation_parser.py \
  tests/test_chat_stream_direct.py::test_prepare_send_message_preserves_raw_slash_and_records_candidate \
  tests/test_chat_stream_direct.py::test_generate_send_stream_enqueues_skill_command_candidate \
  tests/test_chat_stream_reply_events.py::test_send_stream_context_does_not_carry_unused_reply_queue \
  tests/matmaster/worker/test_redis_bridge.py \
  tests/matmaster/services/test_agent_run_stream.py::test_run_agent_passes_skill_command_candidate_to_exp \
  tests/matmaster/core/test_run_context.py::test_agent_run_request_accepts_skill_command_candidate \
  tests/matmaster/context/sources/test_invoked_skills.py \
  tests/matmaster/context/test_assembly.py::test_assemble_turn_places_invoked_skills_before_current_instruction \
  tests/matmaster/tools/builtin/test_skill_tool.py \
  tests/matmaster/core/test_exp_skill_slash.py \
  tests/matmaster/devshell/test_repl.py \
  tests/matmaster/devshell/test_runner.py \
  tests/apis/test_skills_api.py \
  -q
```

Expected: PASS.

- [ ] **Step 2: Run regression clusters**

Run:

```bash
uv run pytest \
  tests/test_chat_stream_direct.py \
  tests/test_chat_stream_planner.py \
  tests/test_chat_stream_session_directory.py \
  tests/matmaster/services/test_agent_run_stream.py \
  tests/matmaster/services/test_lazy_mcp_replay.py \
  tests/matmaster/core/test_exp_turn_preparation.py \
  tests/matmaster/core/test_exp_skill_replay.py \
  tests/matmaster/core/test_exp_skills.py \
  tests/matmaster/context/test_turn_intent.py \
  tests/matmaster/context/test_session.py \
  tests/matmaster/devshell/test_devshell_mcp_skill_filter.py \
  -q
```

Expected: PASS.

- [ ] **Step 3: Run hooks and scans**

Run:

```bash
uv run python .pre-commit/check_file_lines.py \
  matmaster/core/exp.py \
  matmaster/core/skill_slash.py \
  matmaster/skills/invocation.py \
  matmaster/context/sources/invoked_skills.py \
  docs/superpowers/plans/2026-06-03-skill-slash-command.md
uv run pre-commit run --files \
  matmaster/skills/invocation.py \
  matmaster/context/sources/invoked_skills.py \
  matmaster/context/sections.py \
  matmaster/context/compositions.py \
  matmaster/context/assembly.py \
  matmaster/core/skill_slash.py \
  matmaster/core/exp.py \
  matmaster/tools/builtin/skill_tool.py \
  matmaster/core/run_context.py \
  src/services/stream_service.py \
  src/worker/agent_worker.py \
  src/services/agent_run_service.py \
  matmaster/devshell/repl.py \
  matmaster/devshell/runner.py \
  src/models/skills.py \
  src/apis/skills_api.py \
  src/apis/api_router.py
rg -n "run_meta\\[.*skill|current_user_images|_active_skills|skill_name.*fallback|SkillCommandInvocation|body_after_command|arguments.*cleaned" matmaster src tests
git diff --check
git status --short
```

Expected: line-count PASS; pre-commit PASS; residual scan finds no slash implementation relying on `run_meta`, hot cache, compatibility fallback, or removed invocation shapes; `git diff --check` prints nothing.

---

## Self-Review

**Spec coverage:** Task 1 covers grammar and parser; Task 2 covers raw message preservation plus Redis payload; Task 3 covers runtime-only section order; Task 4 covers final registry confirmation, active skill merge, synthetic slash hit, dedup, and MCP activation; Task 5 covers devshell; Task 6 covers backend command candidates. Frontend autocomplete is outside this repository.

**No open implementation gaps:** each task names files, tests, commands, expected results, and concrete code shapes. There is no main-code Redis migration shim, no `run_meta` carrier, no runtime port expansion, and no process-local active skill cache.

**Type consistency:** cross-process DTO is `SkillCommandCandidate`; parser result is `SlashSkillParseResult`; context DTO is `InvokedSkillContext`; Exp helper returns `SlashSkillResolution`; synthetic event is `SkillHitEvent(source="slash", skill_name=candidate.name)`.
