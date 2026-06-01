# AgentRunService Orchestrator V2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `AgentRunService` 从当前的大编排器收敛为生产入口编排层，把 root turn rendering、active skill 事件解析和 context runtime 生命周期下沉到 `Exp`，同时保留 API / Worker 分离语义。

**Architecture:** 按 O1 到 O5 分阶段执行，每个阶段都能独立通过 focused tests。service 继续负责环境、fanout、Bohrium、history wiring、terminal handling 与 cleanup；`Exp` 负责 runtime 构造、root turn rendering、user_turn_context writer 调用和 child runtime 生命周期。跨层能力只通过 `AgentRunPorts` 等窄端口传递，不通过 metadata、dict-bag 或反向 import。

**Tech Stack:** Python 3.11+ via `uv run`, frozen dataclass, Pydantic v2 models, pytest, pre-commit, MatMaster `AgentRunContext` / `AgentRunPorts` / `ContextAssembler` runtime stack.

---

## 0. Source Spec

Implementation source of truth:

- `docs/superpowers/specs/2026-06-01-agent-run-service-orchestrator-v2-design.md`

Current baseline facts from that spec:

- `AgentRunContext = ExecutionEnvironment + AgentRunRequest` is the `Exp` entry boundary.
- `AgentRunPorts` is the service-to-runtime capability boundary.
- `run_meta` / metadata must not carry callbacks, factories, sinks, barriers, service objects, or state bags.
- `AgentKernelSpec` and `AgentKernelResources` must not expose context assembly internals.
- `matmaster/core` must not import from `src/services`.
- `RuntimePorts` and child ports must stay narrow; no `extra`, `metadata`, `state`, `context`, `services`, `payload`, or `dict[str, Any]` fallback fields.
- Production API / Worker separation must remain valid; do not introduce same-process assumptions.

## 1. File Structure

### New Files

- `src/services/figure_coordinator.py`
  Service-layer response figure coordination. Owns `ResponseFiguresAccumulator`, lock, root/child tool-result recording, final flush, and figure upload config.

- `matmaster/context/skill_resolver.py`
  MatMaster-owned `SkillRegistryResolver`. Converts persisted `skill_hit` events into prompt-side `ActiveSkill` DTOs.

- `matmaster/context/user_turn_context.py`
  MatMaster-owned user-turn-context constants shared by core and service. Keeps `Exp` from importing `src.services.user_turn_context_service`.

- `tests/matmaster/services/test_figure_coordinator.py`
  Focused tests for response figure dispatch behavior.

- `tests/matmaster/context/test_skill_resolver.py`
  Moved resolver tests from `tests/matmaster/services/test_skill_resolver.py`.

- `tests/matmaster/context/test_active_mcp_replay.py`
  Moved active MCP replay tests from `tests/matmaster/services/test_active_mcp_replay.py`.

- `tests/matmaster/context/test_turn_intent.py`
  Core-context tests for `resolve_turn_intent`, including the separate intent query and `skill_hit` query.

- `tests/matmaster/core/test_exp_turn_preparation.py`
  Root run preparation tests for `Exp.run_stream`.

- `tests/matmaster/services/test_agent_run_service_orchestration_boundary.py`
  Final O4/O5 service-boundary assertions.

### Modified Files

- `src/services/agent_run_service.py`
  Remove local figure closures, image detail merge, `_active_skills` cache, service context assembly, service intent resolution, service user_turn_context write, and service resolver construction.

- `src/services/image_input_service.py`
  Add `resolve_image_detail()` and `enrich_turn_input_images()`.

- `src/services/user_turn_context_service.py`
  Import shared constants from `matmaster.context.user_turn_context`; keep durable DB write and AGENT.md loading.

- `src/services/skill_resolver.py`
  Temporary thin compatibility shell during O3, then deleted in O5 after imports are gone.

- `src/services/context_turn_intent.py`
  Temporary thin compatibility shell during O4, then deleted in O5 after imports are gone.

- `src/services/context_assembly_factory.py`
  Deleted in O5.

- `src/services/context_assembly_ports.py`
  Keep `AppUserInstructionsPort` if still referenced; remove service-only assembly event/job adapters when unused.

- `matmaster/types/runtime_ports.py`
  Add `UserTurnContextWriteRequest` and `UserTurnContextWriter` to `AgentRunPorts`.

- `matmaster/core/run_context.py`
  Add `AgentRunRequest.invocation_id` as a passive per-run identity value used by the typed writer request. This is not `run_meta` and does not carry service capability.

- `matmaster/types/runtime.py`
  Add `AgentRuntime.context_runtime` as a non-kernel-facing lifecycle field.

- `matmaster/core/runtime_context_assembly.py`
  Continue owning `ContextAssemblyRuntime`; no prebuilt runtime parameters are added.

- `matmaster/context/turn_intent.py`
  Extend existing `decide_turn_context_intent()` module with `TurnIntentResolution` and `resolve_turn_intent()`.

- `matmaster/core/exp.py`
  Create resolver internally after skill registry construction, expose `context_runtime` on `AgentRuntime`, resolve root intent before `build_runtime`, render/persist root turn after `build_runtime`, and remove `skill_resolver` from public runtime path in O4.

- Existing tests under `tests/matmaster/services/`, `tests/matmaster/core/`, `tests/matmaster/types/`.
  Update imports and expected call shapes after each stage.

## 2. Execution Rules

- Use `uv run pytest ...` for every verification command.
- Do not use system `python` or `pip`.
- Keep each task as a separate commit when executing.
- Do not stage or commit `docs/` unless the user explicitly asks.
- If unrelated local changes appear, leave them alone.
- Never add `prebuilt_skill_registry`, `prebuilt_skill_resolver`, `prebuilt_context_runtime`, or equivalent parameters.

## 3. Task Plan

### Task 1: Extract FigureCoordinator

**Files:**
- Create: `src/services/figure_coordinator.py`
- Create: `tests/matmaster/services/test_figure_coordinator.py`
- Modify: `src/services/agent_run_service.py`
- Verify: `tests/matmaster/services/test_figure_coordinator.py`, `tests/matmaster/services/test_agent_run_stream_response_figures.py`

- [ ] **Step 1: Write focused FigureCoordinator tests**

Create `tests/matmaster/services/test_figure_coordinator.py` with this content:

```python
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from matmaster.types.events import ResponseEvent, ToolResultEvent
from src.services.figure_coordinator import FigureCoordinator


class _Fanout:
    def __init__(self, *, dispatch_result: bool = True) -> None:
        self.dispatch_result = dispatch_result
        self.events = []
        self.flush_persistence_barrier = AsyncMock()

    async def dispatch(self, event):
        self.events.append(event)

    async def dispatch_and_wait_persistence(self, event):
        self.events.append(event)
        return self.dispatch_result


def _tool_result(*, spawn_id: str | None = None) -> ToolResultEvent:
    return ToolResultEvent(
        source="MatMaster",
        spawn_id=spawn_id,
        call_id="call-band",
        tool_name="Bash",
        result="done",
        payload={
            "figures": [
                {
                    "figure_id": "band",
                    "asset_url": "https://oss.example/band.png",
                    "caption": "band",
                    "importance": "primary",
                    "placement_hint": "sidebar_only",
                }
            ]
        },
    )


@pytest.mark.asyncio
async def test_record_tool_result_flushes_dirty_snapshot_after_barrier() -> None:
    fanout = _Fanout()
    coordinator = FigureCoordinator(
        fanout=fanout,
        session_id="sess-1",
        task_id="task-1",
    )

    await coordinator.record_tool_result(
        _tool_result(),
        include_spawned=False,
        reason="tool_result",
    )

    fanout.flush_persistence_barrier.assert_awaited_once()
    assert [getattr(event, "type", None) for event in fanout.events] == [
        "response_figures"
    ]


@pytest.mark.asyncio
async def test_record_tool_result_marks_emitted_only_after_dispatch_success() -> None:
    failed = _Fanout(dispatch_result=False)
    coordinator = FigureCoordinator(
        fanout=failed,
        session_id="sess-1",
        task_id="task-1",
    )

    await coordinator.record_tool_result(
        _tool_result(),
        include_spawned=False,
        reason="first_attempt",
    )
    await coordinator.flush_if_dirty("retry")

    assert [getattr(event, "type", None) for event in failed.events] == [
        "response_figures",
        "response_figures",
    ]


@pytest.mark.asyncio
async def test_root_stream_ignores_spawned_tool_results_by_default() -> None:
    fanout = _Fanout()
    coordinator = FigureCoordinator(
        fanout=fanout,
        session_id="sess-1",
        task_id="task-1",
    )

    await coordinator.record_tool_result(
        _tool_result(spawn_id="child-1"),
        include_spawned=False,
        reason="root_stream",
    )

    assert fanout.events == []


@pytest.mark.asyncio
async def test_child_event_sink_dispatches_child_event_and_promotes_figures() -> None:
    fanout = _Fanout()
    coordinator = FigureCoordinator(
        fanout=fanout,
        session_id="sess-1",
        task_id="task-1",
    )

    await coordinator.child_event_sink(_tool_result(spawn_id="child-1"))
    await coordinator.child_event_sink(
        ResponseEvent(
            source="MatMaster:direct",
            spawn_id="child-1",
            content="child answer",
        )
    )

    assert [getattr(event, "type", None) for event in fanout.events] == [
        "tool_result",
        "response_figures",
        "response",
    ]


def test_upload_config_is_available() -> None:
    fanout = _Fanout()
    coordinator = FigureCoordinator(
        fanout=fanout,
        session_id="sess-1",
        task_id="task-1",
    )

    assert coordinator.upload_config.session_id == "sess-1"
    assert coordinator.upload_config.task_id == "task-1"
    assert callable(coordinator.upload_config.upload_bytes)
```

- [ ] **Step 2: Run the focused test and confirm it fails**

Run:

```bash
uv run pytest tests/matmaster/services/test_figure_coordinator.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'src.services.figure_coordinator'`.

- [ ] **Step 3: Create FigureCoordinator**

Create `src/services/figure_coordinator.py` with this content:

```python
from __future__ import annotations

import asyncio
import logging

from matmaster.integration.fanout import RunEventFanout
from matmaster.types.events import BusEvent, ToolResultEvent
from matmaster.types.figures import FigureUploadConfig
from src.services.agent_run_bohrium_stage import _build_figure_upload_config
from src.services.response_figures_service import ResponseFiguresAccumulator

logger = logging.getLogger(__name__)


class FigureCoordinator:
    def __init__(
        self,
        *,
        fanout: RunEventFanout,
        session_id: str,
        task_id: str,
    ) -> None:
        self._fanout = fanout
        self._accumulator = ResponseFiguresAccumulator()
        self._lock = asyncio.Lock()
        self._upload_config = _build_figure_upload_config(
            session_id=session_id,
            task_id=task_id,
        )

    @property
    def upload_config(self) -> FigureUploadConfig:
        return self._upload_config

    async def child_event_sink(self, event: BusEvent) -> None:
        try:
            await self._fanout.dispatch(event)
            if isinstance(event, ToolResultEvent):
                await self.record_tool_result(
                    event,
                    include_spawned=True,
                    reason="child_tool_result",
                )
        except Exception:
            logger.warning(
                "child event sink failed for event type=%s",
                getattr(event, "type", "?"),
                exc_info=True,
            )

    async def flush_if_dirty(self, reason: str) -> None:
        async with self._lock:
            await self._flush_if_dirty_unlocked(reason)

    async def record_tool_result(
        self,
        event: ToolResultEvent,
        *,
        include_spawned: bool,
        reason: str,
    ) -> None:
        async with self._lock:
            self._accumulator.add_tool_result(
                event,
                include_spawned=include_spawned,
            )
            await self._flush_if_dirty_unlocked(reason)

    async def _flush_if_dirty_unlocked(self, reason: str) -> None:
        response_figures_event = self._accumulator.build_snapshot_event_if_dirty()
        if response_figures_event is None:
            return
        try:
            await self._fanout.flush_persistence_barrier()
            dispatched = await self._fanout.dispatch_and_wait_persistence(
                response_figures_event
            )
        except Exception:
            logger.warning(
                "response_figures dispatch failed reason=%s",
                reason,
                exc_info=True,
            )
            return

        if dispatched:
            self._accumulator.mark_snapshot_emitted()
            return

        logger.warning(
            "response_figures dispatch reported handler failure reason=%s",
            reason,
        )
```

- [ ] **Step 4: Run the focused FigureCoordinator tests**

Run:

```bash
uv run pytest tests/matmaster/services/test_figure_coordinator.py -q
```

Expected: PASS.

- [ ] **Step 5: Replace figure closures in AgentRunService**

Modify `src/services/agent_run_service.py` imports:

```python
from src.services.figure_coordinator import FigureCoordinator
```

Remove these imports when they become unused:

```python
from src.services.agent_run_bohrium_stage import _build_figure_upload_config
from src.services.response_figures_service import ResponseFiguresAccumulator
```

Replace the local accumulator / lock / child sink block with:

```python
            figure_coordinator = FigureCoordinator(
                fanout=fanout,
                session_id=session_id,
                task_id=task_id,
            )
            figure_upload_config = figure_coordinator.upload_config
```

Set the runtime port with:

```python
                        child_event_forward_sink=figure_coordinator.child_event_sink,
```

Replace final flush:

```python
                    if isinstance(event, RunResultEvent) and event.spawn_id is None:
                        await figure_coordinator.flush_if_dirty("final_flush")
```

Replace root tool result figure handling:

```python
                    if isinstance(event, ToolResultEvent):
                        await figure_coordinator.record_tool_result(
                            event,
                            include_spawned=False,
                            reason="tool_result",
                        )
```

- [ ] **Step 6: Run response figure regression tests**

Run:

```bash
uv run pytest \
  tests/matmaster/services/test_figure_coordinator.py \
  tests/matmaster/services/test_agent_run_stream_response_figures.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit Task 1**

```bash
git add src/services/figure_coordinator.py \
  tests/matmaster/services/test_figure_coordinator.py \
  src/services/agent_run_service.py
git commit -m "refactor: extract response figure coordinator"
```

### Task 2: Add ImageInputService TurnInput Helpers

**Files:**
- Modify: `src/services/image_input_service.py`
- Modify: `tests/services/test_image_input_service.py`
- Modify: `src/services/agent_run_service.py`
- Verify: `tests/services/test_image_input_service.py`, image-related agent stream tests

- [ ] **Step 1: Add tests for image detail and TurnInput enrichment**

Append these tests to `tests/services/test_image_input_service.py`:

```python
from matmaster.context.sources.turn_input import TurnInput


def test_resolve_image_detail_returns_none_without_images() -> None:
    config = LLMConfig(
        profiles={"plain": LLMProfileConfig(model="plain")},
        default="plain",
    )

    result = _service().resolve_image_detail(
        llm_config=config,
        images=(),
        llm_override=None,
        model_override=None,
        default_profile_key=None,
    )

    assert result is None


def test_resolve_image_detail_returns_profile_detail_for_images() -> None:
    config = LLMConfig(
        profiles={
            "vision": LLMProfileConfig(
                model="vision",
                supports_vision=True,
                vision_detail="high",
            )
        },
        default="vision",
    )

    result = _service().resolve_image_detail(
        llm_config=config,
        images=("https://oss.example.com/chat/a.png",),
        llm_override=None,
        model_override=None,
        default_profile_key=None,
    )

    assert result == "high"


def test_enrich_turn_input_images_builds_turn_input_when_missing() -> None:
    enriched = _service().enrich_turn_input_images(
        turn_input=None,
        user_prompt="inspect image",
        top_level_images=("https://oss.example.com/chat/a.png",),
        image_detail="low",
    )

    assert enriched.user_text == "inspect image"
    assert enriched.images == ("https://oss.example.com/chat/a.png",)
    assert enriched.attachments.image_detail == "low"


def test_enrich_turn_input_images_preserves_existing_turn_input_images() -> None:
    turn_input = TurnInput.from_values(
        user_text="from turn input",
        files=("https://oss.example.com/chat/a.cif",),
        images=("https://oss.example.com/chat/existing.png",),
        image_detail="auto",
        workspace_paths=("/workspace/note.md",),
        pre_turn_history_event_id=22,
    )

    enriched = _service().enrich_turn_input_images(
        turn_input=turn_input,
        user_prompt="ignored",
        top_level_images=("https://oss.example.com/chat/top.png",),
        image_detail="high",
    )

    assert enriched.user_text == "from turn input"
    assert enriched.files == ("https://oss.example.com/chat/a.cif",)
    assert enriched.images == ("https://oss.example.com/chat/existing.png",)
    assert enriched.workspace_paths == ("/workspace/note.md",)
    assert enriched.pre_turn_history_event_id == 22
    assert enriched.attachments.image_detail == "high"


def test_enrich_turn_input_images_preserves_existing_detail_when_no_new_detail() -> None:
    turn_input = TurnInput.from_values(
        user_text="from turn input",
        images=("https://oss.example.com/chat/existing.png",),
        image_detail="auto",
    )

    enriched = _service().enrich_turn_input_images(
        turn_input=turn_input,
        user_prompt="ignored",
        top_level_images=(),
        image_detail=None,
    )

    assert enriched.images == ("https://oss.example.com/chat/existing.png",)
    assert enriched.attachments.image_detail == "auto"
```

- [ ] **Step 2: Run the new tests and confirm they fail**

Run:

```bash
uv run pytest tests/services/test_image_input_service.py \
  -k "resolve_image_detail or enrich_turn_input_images" -q
```

Expected: FAIL with missing `resolve_image_detail` / `enrich_turn_input_images`.

- [ ] **Step 3: Implement the helpers**

Modify imports in `src/services/image_input_service.py`:

```python
import logging
from typing import Any, Literal

from matmaster.context.sources.turn_input import TurnInput
```

Add near module constants:

```python
logger = logging.getLogger(__name__)
ImageDetail = Literal["low", "high", "auto"]
```

Add methods to `ImageInputService`:

```python
    def resolve_image_detail(
        self,
        *,
        llm_config: LLMConfig,
        images: tuple[str, ...],
        llm_override: str | None,
        model_override: str | None,
        default_profile_key: str | None,
    ) -> ImageDetail | None:
        if not images:
            return None
        profile = self.ensure_vision_supported(
            llm_config=llm_config,
            llm_override=llm_override,
            model_override=model_override,
            default_profile_key=default_profile_key,
        )
        return profile.vision_detail

    def enrich_turn_input_images(
        self,
        *,
        turn_input: TurnInput | None,
        user_prompt: str,
        top_level_images: tuple[str, ...],
        image_detail: ImageDetail | None,
    ) -> TurnInput:
        turn_input_images = turn_input.images if turn_input is not None else ()
        current_images = turn_input_images or top_level_images
        if (
            turn_input_images
            and top_level_images
            and turn_input_images != top_level_images
        ):
            logger.warning("run_agent image inputs differ; using TurnInput images")

        if turn_input is None:
            return TurnInput.from_values(
                user_text=user_prompt,
                files=(),
                images=current_images,
                image_detail=image_detail if current_images else None,
                workspace_paths=(),
                pre_turn_history_event_id=0,
            )

        if not current_images:
            return turn_input

        return TurnInput.from_values(
            user_text=turn_input.user_text,
            files=turn_input.files,
            images=current_images,
            image_detail=(
                image_detail
                if image_detail is not None
                else turn_input.attachments.image_detail
            ),
            workspace_paths=turn_input.workspace_paths,
            pre_turn_history_event_id=turn_input.pre_turn_history_event_id,
        )
```

- [ ] **Step 4: Replace inline image detail logic in AgentRunService**

In `src/services/agent_run_service.py`, replace the inline `turn_input_images` / `current_images` / `image_detail` block with:

```python
            image_service = get_image_input_service()
            top_level_images = tuple(images or ())
            turn_input_images = turn_input.images if turn_input is not None else ()
            current_images = turn_input_images or top_level_images
            image_detail = image_service.resolve_image_detail(
                llm_config=llm_config,
                images=tuple(current_images),
                llm_override=llm_override,
                model_override=model_override,
                default_profile_key=agent_default_llm,
            )
```

Replace the later `TurnInput.from_values(...)` branch with:

```python
            turn_input = image_service.enrich_turn_input_images(
                turn_input=turn_input,
                user_prompt=user_prompt,
                top_level_images=top_level_images,
                image_detail=image_detail,
            )
```

- [ ] **Step 5: Run image tests**

Run:

```bash
uv run pytest \
  tests/services/test_image_input_service.py \
  tests/matmaster/services/test_agent_run_stream_images.py \
  tests/matmaster/integration/test_image_input_e2e.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit Task 2**

```bash
git add src/services/image_input_service.py \
  tests/services/test_image_input_service.py \
  src/services/agent_run_service.py
git commit -m "refactor: centralize turn image enrichment"
```

### Task 3: Remove AgentRunService Active Skills Hot Cache

**Files:**
- Modify: `src/services/agent_run_service.py`
- Modify: `tests/matmaster/services/agent_run_stream_fixtures.py`
- Modify: `tests/matmaster/services/test_agent_run_stream.py`
- Verify: `tests/matmaster/services/test_lazy_mcp_replay.py`, `tests/matmaster/services/test_agent_run_stream.py`

- [ ] **Step 1: Add a cache-removal regression test**

Append to `tests/matmaster/services/test_agent_run_stream.py`:

```python
@pytest.mark.asyncio
async def test_run_agent_resolves_active_skills_from_events_without_hot_cache():
    run_result = RunResultEvent(source="agent", status="completed", reason="natural")

    async with _patched_service([run_result]) as (svc, _sse, _persist):
        svc._test_events_table.get_session_events.return_value = [
            {
                "id": 1,
                "type": "skill_hit",
                "source": "MatMaster",
                "content": {"skill_name": "mlip"},
            }
        ]

        ok, _elapsed, _usage = await svc.run_agent(
            session_id="sess-1",
            user_prompt="hello",
            send_cb=AsyncMock(),
            cancel_token=_make_cancel_token(),
            mode="direct",
            task_id="task-1",
            invocation_id="inv-active-skills",
        )

    assert ok is True
    assert not hasattr(svc, "_active_skills")
    assert svc._test_fake_exp.last_ctx.request.active_skills == frozenset({"mlip"})
    svc._test_events_table.get_session_events.assert_called_with(
        "sess-1",
        limit=ANY,
    )
```

- [ ] **Step 2: Run the focused regression and confirm it fails**

Run:

```bash
uv run pytest tests/matmaster/services/test_agent_run_stream.py \
  -k "active_skills_from_events_without_hot_cache" -q
```

Expected: FAIL because `AgentRunService` still has `_active_skills`.

- [ ] **Step 3: Remove the hot cache and make `_resolve_active_skill_names` stateless**

In `src/services/agent_run_service.py`, remove this from `__init__`:

```python
        self._active_skills: dict[str, frozenset[str]] = {}
```

Replace `_resolve_active_skill_names` with:

```python
    def _resolve_active_skill_names(
        self,
        session_id: str,
        events_table: Any,
        *,
        until_event_id: int | None = None,
    ) -> frozenset[str]:
        raw_events: list[dict] = []
        if events_table is not None:
            try:
                raw_events = events_table.get_session_events(
                    session_id,
                    limit=_DIALOG_HISTORY_MAX_EVENTS,
                )
            except Exception:
                logger.warning(
                    "active skill rehydrate: get_session_events failed for session_id=%s",
                    session_id,
                    exc_info=True,
                )

        events = decode_session_events(raw_events)
        if until_event_id is not None:
            events = tuple(event for event in events if event.id <= until_event_id)
        return frozenset(
            record.skill_name for record in scan_skill_hits(events) if record.skill_name
        )
```

Remove `_remember_skill_hit` and the `SkillHitEvent` branch from the event loop:

```python
                    if isinstance(event, SkillHitEvent):
                        _remember_skill_hit(event.skill_name)
```

Remove `SkillHitEvent` from imports if unused.

- [ ] **Step 4: Update fixtures**

In `tests/matmaster/services/agent_run_stream_fixtures.py`, remove:

```python
            svc._active_skills = {}
```

- [ ] **Step 5: Run service tests around active skills**

Run:

```bash
uv run pytest \
  tests/matmaster/services/test_agent_run_stream.py \
  tests/matmaster/services/test_lazy_mcp_replay.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit Task 3**

```bash
git add src/services/agent_run_service.py \
  tests/matmaster/services/agent_run_stream_fixtures.py \
  tests/matmaster/services/test_agent_run_stream.py
git commit -m "refactor: remove active skills hot cache"
```

### Task 4: Move SkillRegistryResolver Into matmaster.context

**Files:**
- Create: `matmaster/context/skill_resolver.py`
- Modify: `src/services/skill_resolver.py`
- Move: `tests/matmaster/services/test_skill_resolver.py` to `tests/matmaster/context/test_skill_resolver.py`
- Move: `tests/matmaster/services/test_active_mcp_replay.py` to `tests/matmaster/context/test_active_mcp_replay.py`
- Modify imports under `src/`, `tests/`
- Verify: moved resolver tests and lazy MCP replay tests

- [ ] **Step 1: Move tests to the target package and update imports**

Move the test files:

```bash
mkdir -p tests/matmaster/context
git mv tests/matmaster/services/test_skill_resolver.py tests/matmaster/context/test_skill_resolver.py
git mv tests/matmaster/services/test_active_mcp_replay.py tests/matmaster/context/test_active_mcp_replay.py
```

In both moved files, replace:

```python
from src.services.skill_resolver import SkillRegistryResolver
```

with:

```python
from matmaster.context.skill_resolver import SkillRegistryResolver
```

- [ ] **Step 2: Run moved tests and confirm they fail**

Run:

```bash
uv run pytest \
  tests/matmaster/context/test_skill_resolver.py \
  tests/matmaster/context/test_active_mcp_replay.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'matmaster.context.skill_resolver'`.

- [ ] **Step 3: Create the MatMaster resolver module**

Create `matmaster/context/skill_resolver.py`:

```python
"""SkillResolver implementation owned by the context layer."""

from __future__ import annotations

import logging
from typing import Any

from matmaster.context.ports import ActiveSkill, SessionEvent
from matmaster.context.scanner import scan_skill_hits

logger = logging.getLogger(__name__)


class SkillRegistryResolver:
    """Resolve typed session events into ActiveSkill DTOs."""

    def __init__(self, skill_registry: Any | None) -> None:
        self._registry = skill_registry

    def __call__(self, events: tuple[SessionEvent, ...]) -> tuple[ActiveSkill, ...]:
        if self._registry is None:
            return ()
        active: list[ActiveSkill] = []
        for record in scan_skill_hits(events):
            try:
                skill = self._registry.get_skill(record.skill_name)
            except Exception:
                logger.warning(
                    "active skill resolver: get_skill(%r) raised, skipping",
                    record.skill_name,
                    exc_info=True,
                )
                continue
            if skill is None:
                continue
            meta = skill.meta_info
            active.append(
                ActiveSkill(
                    name=meta.name,
                    description=meta.description or "",
                    mcp_server=meta.mcp_server,
                )
            )
        return tuple(active)
```

- [ ] **Step 4: Convert service resolver to a temporary compatibility shell**

Replace `src/services/skill_resolver.py` content with:

```python
"""Compatibility import for the moved SkillRegistryResolver."""

from __future__ import annotations

from matmaster.context.skill_resolver import SkillRegistryResolver

__all__ = ["SkillRegistryResolver"]
```

- [ ] **Step 5: Update production imports**

Replace imports with:

```bash
rg -n "src\\.services\\.skill_resolver|from src.services.skill_resolver import" src matmaster tests
```

Every in-repo production and test import should use:

```python
from matmaster.context.skill_resolver import SkillRegistryResolver
```

The compatibility shell remains only so a staged rollout does not break external imports during this PR.

- [ ] **Step 6: Run resolver and replay tests**

Run:

```bash
uv run pytest \
  tests/matmaster/context/test_skill_resolver.py \
  tests/matmaster/context/test_active_mcp_replay.py \
  tests/matmaster/services/test_lazy_mcp_replay.py -q
```

Expected: PASS.

- [ ] **Step 7: Confirm core does not import src.services**

Run:

```bash
rg -n "src\\.services" matmaster/core matmaster/context
```

Expected: no output.

- [ ] **Step 8: Commit Task 4**

```bash
git add matmaster/context/skill_resolver.py \
  src/services/skill_resolver.py \
  tests/matmaster/context/test_skill_resolver.py \
  tests/matmaster/context/test_active_mcp_replay.py \
  src/services/agent_run_service.py
git add -u tests/matmaster/services
git commit -m "refactor: move skill resolver into context layer"
```

### Task 5: Add UserTurnContext Runtime Port and Shared Constants

**Files:**
- Create: `matmaster/context/user_turn_context.py`
- Modify: `matmaster/core/run_context.py`
- Modify: `src/services/user_turn_context_service.py`
- Modify: `matmaster/types/runtime_ports.py`
- Modify: `tests/matmaster/core/test_run_context.py`
- Modify: `tests/matmaster/types/test_runtime_ports.py`
- Verify: runtime ports tests and user turn context service tests

- [ ] **Step 1: Add runtime port tests**

Append to `tests/matmaster/types/test_runtime_ports.py`:

```python
from matmaster.types.messages import UserMessage
from matmaster.types.runtime_ports import UserTurnContextWriteRequest


def test_user_turn_context_writer_port_defaults_to_none() -> None:
    ports = AgentRunPorts()

    assert ports.user_turn_context_writer is None


def test_user_turn_context_write_request_is_typed_dataclass() -> None:
    request = UserTurnContextWriteRequest(
        session_id="sess-1",
        task_id="task-1",
        invocation_id="inv-1",
        spawn_id=None,
        kind="anchor",
        message=UserMessage(content="hello"),
        user_instructions_hash="sha256:abc",
        transform="raw",
        render_version="user_context_render.v1",
        schema_version="user_turn_context.v1",
    )

    assert is_dataclass(request)
    assert request.kind == "anchor"
    assert request.message.content == "hello"
    with pytest.raises(FrozenInstanceError):
        request.kind = "continuation"


def test_agent_run_ports_has_no_service_bag_after_writer_port_added() -> None:
    ports = AgentRunPorts()

    assert not hasattr(ports, "payload")
    assert not hasattr(ports, "context")
    assert not hasattr(ports, "services")
    assert not hasattr(ports, "dict")
```

- [ ] **Step 2: Run runtime port tests and confirm they fail**

Run:

```bash
uv run pytest tests/matmaster/types/test_runtime_ports.py \
  -k "user_turn_context or service_bag" -q
```

Expected: FAIL with missing `UserTurnContextWriteRequest` or missing `user_turn_context_writer`.

- [ ] **Step 3: Create shared user_turn_context constants**

Create `matmaster/context/user_turn_context.py`:

```python
"""Shared user_turn_context constants.

Core runtime code may import this module. Durable DB writes stay in
src.services.user_turn_context_service.
"""

from __future__ import annotations

DEFAULT_TURN_TRANSFORM = "raw"
USER_CONTEXT_RENDER_VERSION = "user_context_render.v1"
USER_TURN_CONTEXT_SCHEMA_VERSION = "user_turn_context.v1"
```

Modify `src/services/user_turn_context_service.py` to import these constants:

```python
from matmaster.context.user_turn_context import (
    DEFAULT_TURN_TRANSFORM,
    USER_CONTEXT_RENDER_VERSION,
    USER_TURN_CONTEXT_SCHEMA_VERSION,
)
```

Remove the local duplicate constant assignments from `src/services/user_turn_context_service.py`.

- [ ] **Step 4: Add writer request and writer protocol to runtime ports**

Modify `matmaster/types/runtime_ports.py` imports:

```python
from typing import Any, Literal, NotRequired, Protocol, TypedDict, runtime_checkable

from matmaster.types.messages import Message
```

Add to `__all__`:

```python
    "UserTurnContextWriteRequest",
    "UserTurnContextWriter",
```

Add before `AgentRunPorts`:

```python
@dataclass(frozen=True)
class UserTurnContextWriteRequest:
    session_id: str
    task_id: str | None
    invocation_id: str | None
    spawn_id: str | None
    kind: Literal["anchor", "continuation"]
    message: Message
    user_instructions_hash: str | None
    transform: str
    render_version: str
    schema_version: str


@runtime_checkable
class UserTurnContextWriter(Protocol):
    async def __call__(self, request: UserTurnContextWriteRequest) -> None: ...
```

Add the field to `AgentRunPorts`:

```python
    user_turn_context_writer: UserTurnContextWriter | None = None
```

- [ ] **Step 5: Add invocation_id to AgentRunRequest**

Add this test to `tests/matmaster/core/test_run_context.py` inside `TestAgentRunRequest`:

```python
    def test_carries_invocation_id_as_runtime_request_identity(self) -> None:
        request = AgentRunRequest(invocation_id="inv-1")

        assert request.invocation_id == "inv-1"
        assert "invocation_id" in request.model_dump()
        assert "invocation_id" not in RunMetadata.model_fields
```

Add this field to `matmaster/core/run_context.py` inside `AgentRunRequest`, before `interaction_bridge`:

```python
    invocation_id: str | None = None
```

Run:

```bash
uv run pytest tests/matmaster/core/test_run_context.py \
  -k "invocation_id or AgentRunRequest" -q
```

Expected: PASS.

- [ ] **Step 6: Run type port and user-turn-context tests**

Run:

```bash
uv run pytest \
  tests/matmaster/types/test_runtime_ports.py \
  tests/matmaster/services/test_user_turn_context_service.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit Task 5**

```bash
git add matmaster/context/user_turn_context.py \
  matmaster/core/run_context.py \
  matmaster/types/runtime_ports.py \
  src/services/user_turn_context_service.py \
  tests/matmaster/core/test_run_context.py \
  tests/matmaster/types/test_runtime_ports.py
git commit -m "feat: add user turn context runtime port"
```

### Task 6: Move Turn Intent Resolution Into matmaster.context

**Files:**
- Modify: `matmaster/context/turn_intent.py`
- Modify: `src/services/context_turn_intent.py`
- Move/update: `tests/matmaster/services/test_context_turn_intent.py` to `tests/matmaster/context/test_turn_intent.py`
- Verify: context turn intent tests

- [ ] **Step 1: Move and expand turn intent tests**

Move the test file:

```bash
git mv tests/matmaster/services/test_context_turn_intent.py tests/matmaster/context/test_turn_intent.py
```

Replace imports at the top with:

```python
from matmaster.context.turn_intent import (
    _latest_anchor_hash_from_events,
    resolve_turn_intent,
)
```

Replace `resolve_turn_context_intent(...)` calls with:

```python
resolution = await resolve_turn_intent(
    instructions_hash="sha256:same",
    session_id="sess-1",
    spawn_id=None,
    events_port=port,
)
intent = resolution.intent
```

Append this test to prove intent and active skills use separate queries:

```python
@pytest.mark.asyncio
async def test_resolve_turn_intent_uses_separate_skill_hit_query() -> None:
    class SplitEventsPort:
        def __init__(self) -> None:
            self.queries = []

        async def load_events(self, query):
            self.queries.append(query)
            if query.event_types == ("user_turn_context", "history_checkpoint"):
                return (
                    _event(
                        "user_turn_context",
                        {
                            "kind": "anchor",
                            "user_instructions_hash": "sha256:same",
                        },
                        20,
                    ),
                )
            if query.event_types == ("skill_hit",):
                return (
                    _event("skill_hit", {"skill_name": "older-skill"}, 1),
                )
            return ()

    port = SplitEventsPort()

    resolution = await resolve_turn_intent(
        instructions_hash="sha256:same",
        session_id="sess-1",
        spawn_id=None,
        events_port=port,
        active_skill_event_limit=500,
    )

    assert resolution.intent == ContextAssemblyIntent.CONTINUATION_TURN
    assert resolution.active_skills == frozenset({"older-skill"})
    assert port.queries[0].event_types == (
        "user_turn_context",
        "history_checkpoint",
    )
    assert port.queries[0].limit == 50
    assert port.queries[0].order == "desc"
    assert port.queries[1].event_types == ("skill_hit",)
    assert port.queries[1].limit == 500
    assert port.queries[1].order == "asc"
```

- [ ] **Step 2: Run moved tests and confirm they fail**

Run:

```bash
uv run pytest tests/matmaster/context/test_turn_intent.py -q
```

Expected: FAIL because `resolve_turn_intent` is not defined in `matmaster.context.turn_intent`.

- [ ] **Step 3: Implement core turn intent resolution**

Replace `matmaster/context/turn_intent.py` with:

```python
from __future__ import annotations

from dataclasses import dataclass

from matmaster.context.assembly import ContextAssemblyIntent
from matmaster.context.ports import SessionEvent, SessionEventQuery, SessionEventsPort
from matmaster.context.scanner import scan_skill_hits

DEFAULT_INTENT_EVENT_LIMIT = 50
DEFAULT_ACTIVE_SKILL_EVENT_LIMIT = 500


@dataclass(frozen=True)
class TurnIntentResolution:
    intent: ContextAssemblyIntent
    active_skills: frozenset[str] = frozenset()


def decide_turn_context_intent(
    *,
    current_hash: str,
    latest_anchor_hash: str | None,
) -> ContextAssemblyIntent:
    if latest_anchor_hash is None or latest_anchor_hash != current_hash:
        return ContextAssemblyIntent.ANCHOR_TURN
    return ContextAssemblyIntent.CONTINUATION_TURN


async def resolve_turn_intent(
    *,
    events_port: SessionEventsPort,
    instructions_hash: str,
    session_id: str,
    spawn_id: str | None,
    active_skill_event_limit: int = DEFAULT_ACTIVE_SKILL_EVENT_LIMIT,
) -> TurnIntentResolution:
    intent_events = await events_port.load_events(
        SessionEventQuery(
            session_id=session_id,
            spawn_id=spawn_id,
            event_types=("user_turn_context", "history_checkpoint"),
            limit=DEFAULT_INTENT_EVENT_LIMIT,
            order="desc",
        )
    )
    latest_hash = _latest_anchor_hash_from_events(intent_events)
    skill_events = await events_port.load_events(
        SessionEventQuery(
            session_id=session_id,
            spawn_id=spawn_id,
            event_types=("skill_hit",),
            limit=active_skill_event_limit,
            order="asc",
        )
    )
    active_skills = frozenset(
        record.skill_name
        for record in scan_skill_hits(skill_events)
        if record.skill_name
    )
    return TurnIntentResolution(
        intent=decide_turn_context_intent(
            current_hash=instructions_hash,
            latest_anchor_hash=latest_hash,
        ),
        active_skills=active_skills,
    )


def _latest_anchor_hash_from_events(
    events: tuple[SessionEvent, ...],
) -> str | None:
    for event in events:
        if event.event_type == "user_turn_context":
            if event.content.get("kind") != "anchor":
                continue
            anchor_hash = event.content.get("user_instructions_hash")
            return anchor_hash if isinstance(anchor_hash, str) and anchor_hash else None
        if event.event_type == "history_checkpoint":
            checkpoint_hash = event.content.get("user_instructions_hash")
            return (
                checkpoint_hash
                if isinstance(checkpoint_hash, str) and checkpoint_hash
                else None
            )
    return None
```

- [ ] **Step 4: Convert service context intent to a compatibility shell**

Replace `src/services/context_turn_intent.py` with:

```python
from __future__ import annotations

from matmaster.context.turn_intent import (
    _latest_anchor_hash_from_events,
    resolve_turn_intent,
)


async def resolve_turn_context_intent(
    *,
    instructions_hash: str,
    session_id: str,
    spawn_id: str | None,
    events_port,
):
    resolution = await resolve_turn_intent(
        instructions_hash=instructions_hash,
        session_id=session_id,
        spawn_id=spawn_id,
        events_port=events_port,
    )
    return resolution.intent
```

- [ ] **Step 5: Run turn intent tests**

Run:

```bash
uv run pytest tests/matmaster/context/test_turn_intent.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit Task 6**

```bash
git add matmaster/context/turn_intent.py \
  src/services/context_turn_intent.py \
  tests/matmaster/context/test_turn_intent.py
git add -u tests/matmaster/services
git commit -m "refactor: move turn intent resolution into context layer"
```

### Task 7: Expose Context Runtime on AgentRuntime and Let Exp Own SkillResolver

**Files:**
- Modify: `matmaster/types/runtime.py`
- Modify: `matmaster/core/exp.py`
- Modify: `tests/matmaster/core/test_exp_runtime_v2.py`
- Verify: core runtime tests

- [ ] **Step 1: Add tests for AgentRuntime.context_runtime and internal resolver ownership**

Append to `tests/matmaster/core/test_exp_runtime_v2.py`:

```python
@pytest.mark.asyncio
async def test_build_runtime_exposes_context_runtime_outside_kernel_runtime(
    tmp_path: Path,
) -> None:
    from matmaster.config.exp import ExpConfig
    from matmaster.core.exp import Exp

    ctx = AgentRunContext(
        environment=ExecutionEnvironment(
            workdir=tmp_path,
            execution_workdir=str(tmp_path),
            session_type="local",
            cache_area=tmp_path / "cache",
        ),
        request=AgentRunRequest(llm_provider=_MockProvider()),
    )

    runtime = await Exp(ExpConfig(name="test")).build_runtime(ctx)

    assert runtime.context_runtime is not None
    assert runtime.context_runtime.assembler is not None
    assert not hasattr(runtime.kernel_runtime.spec, "context_runtime")
    assert not hasattr(runtime.kernel_runtime.spec, "context_assembler")
    assert not hasattr(runtime.kernel_runtime.resources, "context_runtime")
    assert not hasattr(runtime.kernel_runtime.resources, "context_assembler")


def test_build_runtime_signature_has_no_prebuilt_parameters() -> None:
    import inspect

    from matmaster.core.exp import Exp

    params = inspect.signature(Exp.build_runtime).parameters

    assert "prebuilt_skill_registry" not in params
    assert "prebuilt_skill_resolver" not in params
    assert "prebuilt_context_runtime" not in params
```

- [ ] **Step 2: Run the new tests and confirm the context_runtime test fails**

Run:

```bash
uv run pytest tests/matmaster/core/test_exp_runtime_v2.py \
  -k "context_runtime or prebuilt_parameters" -q
```

Expected: context runtime test FAILS because `AgentRuntime` does not expose `context_runtime`.

- [ ] **Step 3: Add non-kernel-facing context_runtime to AgentRuntime**

Modify `matmaster/types/runtime.py` `AgentRuntime`:

```python
@dataclass(frozen=True)
class AgentRuntime:
    """Runtime bundle returned by Exp.build_runtime().

    Holds the kernel, the assembled kernel_runtime, cleanup, and non-kernel
    context assembly lifecycle objects needed by Exp.run_stream.
    """

    kernel: Any
    kernel_runtime: AgentKernelRuntime
    cleanup: Callable[[], Any]
    context_runtime: Any | None = None
```

- [ ] **Step 4: Return context_runtime from Exp.build_runtime**

In `matmaster/core/exp.py`, change the return block:

```python
        return AgentRuntime(
            kernel=kernel,
            kernel_runtime=kernel_runtime,
            cleanup=self._run_cleanup_callbacks,
            context_runtime=runtime_context.context_runtime,
        )
```

- [ ] **Step 5: Make Exp construct SkillRegistryResolver internally**

In `matmaster/core/exp.py`, keep `build_runtime` parameters unchanged during this task, but set the resolver after `_init_skill_tools`:

```python
        from matmaster.context.skill_resolver import SkillRegistryResolver
        from matmaster.core.runtime_context_assembly import empty_skill_resolver

        self._skill_resolver = skill_resolver or empty_skill_resolver
```

Then after `_init_skill_tools(...)` has had the chance to set `self._skill_registry`, insert:

```python
        if skill_resolver is None:
            self._skill_resolver = SkillRegistryResolver(self._skill_registry)
```

This preserves O3 compatibility while establishing the O4 ownership path.

- [ ] **Step 6: Run core runtime tests**

Run:

```bash
uv run pytest tests/matmaster/core/test_exp_runtime_v2.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit Task 7**

```bash
git add matmaster/types/runtime.py \
  matmaster/core/exp.py \
  tests/matmaster/core/test_exp_runtime_v2.py
git commit -m "feat: expose context runtime on agent runtime"
```

### Task 8: Move Root Turn Rendering Into Exp

**Files:**
- Modify: `matmaster/core/exp.py`
- Modify: `src/services/agent_run_service.py`
- Modify: `tests/matmaster/core/test_exp_turn_preparation.py`
- Modify: `tests/matmaster/services/test_agent_run_stream.py`
- Modify: `tests/matmaster/services/agent_run_stream_fixtures.py`
- Verify: Exp preparation tests, service stream tests, context cutover tests

- [ ] **Step 1: Create Exp root turn preparation tests**

Create `tests/matmaster/core/test_exp_turn_preparation.py`:

```python
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from matmaster.config.exp import ExpConfig
from matmaster.context.ports import SessionEvent, UserInstructions, hash_user_instructions
from matmaster.context.sources.turn_input import TurnInput
from matmaster.core.exp import Exp
from matmaster.core.playground import ExecutionEnvironment
from matmaster.core.run_context import AgentRunContext, AgentRunRequest
from matmaster.types.cancellation import CancellationController
from matmaster.types.events import RunResultEvent
from matmaster.types.messages import LLMResponse, StreamChunk
from matmaster.types.runtime_ports import AgentRunPorts, PlaygroundCompactionPort


class _Provider:
    stream_timeout = 10.0
    max_retries = 1
    retry_delay = 0.0

    async def chat(self, messages, tools=None):
        return LLMResponse(content="mock", finish_reason="stop")

    async def chat_stream(self, messages, tools=None, *, timeout=None):
        yield StreamChunk(content="ok")
        yield StreamChunk(finish_reason="stop")


class _History:
    def __init__(self, events: tuple[SessionEvent, ...] = ()) -> None:
        self.events = events
        self.queries = []

    async def load_events(self, query):
        self.queries.append(query)
        if query.event_types == ("skill_hit",):
            return tuple(event for event in self.events if event.event_type == "skill_hit")
        return tuple(
            event
            for event in self.events
            if event.event_type in {"user_turn_context", "history_checkpoint"}
        )

    def query_events(self):
        return []

    def all_events(self):
        return []

    def latest_checkpoint_covered_until_event_id(self):
        return None

    def latest_scope_event_id(self):
        return None


def _ctx(
    tmp_path: Path,
    *,
    turn_input: TurnInput | None,
    user_instructions: UserInstructions | None = None,
    history: _History | None = None,
    writer: Any | None = None,
) -> AgentRunContext:
    return AgentRunContext(
        environment=ExecutionEnvironment(
            workdir=tmp_path,
            execution_workdir=str(tmp_path),
            session_type="local",
            cache_area=tmp_path / "cache",
        ),
        request=AgentRunRequest(
            invocation_id="inv-1",
            llm_provider=_Provider(),
            turn_input=turn_input,
            user_instructions=user_instructions,
            ports=AgentRunPorts(
                compaction=PlaygroundCompactionPort(history=history),
                user_turn_context_writer=writer,
            ),
        ),
    )


@pytest.mark.asyncio
async def test_root_run_missing_turn_input_fails_before_runtime_build(tmp_path: Path):
    exp = Exp(ExpConfig(name="test"))

    with pytest.raises(RuntimeError, match="turn_input is required"):
        async for _event in exp.run_stream(_ctx(tmp_path, turn_input=None)):
            pass


@pytest.mark.asyncio
async def test_root_run_renders_and_writes_user_turn_context(tmp_path: Path):
    calls = []

    async def writer(request):
        calls.append(request)

    instructions = UserInstructions(
        text="Use SI units.",
        hash=hash_user_instructions("Use SI units."),
        truncated=False,
    )
    ctx = _ctx(
        tmp_path,
        turn_input=TurnInput.from_values(user_text="hello"),
        user_instructions=instructions,
        history=_History(),
        writer=writer,
    )

    events = [event async for event in Exp(ExpConfig(name="test")).run_stream(ctx)]

    assert any(isinstance(event, RunResultEvent) for event in events)
    assert len(calls) == 1
    assert calls[0].kind == "anchor"
    assert calls[0].invocation_id == "inv-1"
    assert calls[0].user_instructions_hash == instructions.hash
    assert "Use SI units." in calls[0].message.content
    assert "hello" in calls[0].message.content


@pytest.mark.asyncio
async def test_writer_failure_propagates(tmp_path: Path):
    async def writer(_request):
        raise RuntimeError("write failed")

    ctx = _ctx(
        tmp_path,
        turn_input=TurnInput.from_values(user_text="hello"),
        history=_History(),
        writer=writer,
    )

    with pytest.raises(RuntimeError, match="write failed"):
        async for _event in Exp(ExpConfig(name="test")).run_stream(ctx):
            pass


@pytest.mark.asyncio
async def test_spawn_run_does_not_write_user_turn_context(tmp_path: Path):
    calls = []

    async def writer(request):
        calls.append(request)

    ctx = _ctx(
        tmp_path,
        turn_input=TurnInput.from_values(user_text="root"),
        history=_History(),
        writer=writer,
    )

    events = [
        event
        async for event in Exp(ExpConfig(name="test")).run_stream(
            ctx,
            "child task",
            spawn_id="child-1",
        )
    ]

    assert any(isinstance(event, RunResultEvent) for event in events)
    assert calls == []


@pytest.mark.asyncio
async def test_root_run_falls_back_when_history_and_instructions_are_missing(
    tmp_path: Path,
):
    calls = []

    async def writer(request):
        calls.append(request)

    ctx = _ctx(
        tmp_path,
        turn_input=TurnInput.from_values(user_text="hello"),
        user_instructions=None,
        history=None,
        writer=writer,
    )

    events = [event async for event in Exp(ExpConfig(name="test")).run_stream(ctx)]

    assert any(isinstance(event, RunResultEvent) for event in events)
    assert len(calls) == 1
    assert calls[0].user_instructions_hash == hash_user_instructions("")
```

- [ ] **Step 2: Run the new Exp tests and confirm they fail**

Run:

```bash
uv run pytest tests/matmaster/core/test_exp_turn_preparation.py -q
```

Expected: FAIL because `Exp.run_stream` still requires `task` and does not render root turns.

- [ ] **Step 3: Add RootTurnRender and writer helpers to Exp**

In `matmaster/core/exp.py`, add imports:

```python
from dataclasses import dataclass

from matmaster.context.assembly import ContextAssemblyIntent, ContextAssembler, TurnAssemblyRequest
from matmaster.context.ports import UserInstructions, hash_user_instructions
from matmaster.context.sections import ContextView
from matmaster.context.turn_intent import TurnIntentResolution, resolve_turn_intent
from matmaster.context.user_turn_context import (
    DEFAULT_TURN_TRANSFORM,
    USER_CONTEXT_RENDER_VERSION,
    USER_TURN_CONTEXT_SCHEMA_VERSION,
)
from matmaster.types.runtime_ports import (
    EmptySessionEventHistory,
    KernelRuntimePorts,
    UserTurnContextWriteRequest,
)
```

Add near module-level helpers:

```python
@dataclass(frozen=True)
class RootTurnRender:
    rendered_content: str
```

Add methods to `Exp`:

```python
    async def _render_and_persist_root_turn(
        self,
        *,
        ctx: AgentRunContext,
        intent: ContextAssemblyIntent,
        assembler: ContextAssembler,
        user_instructions: UserInstructions,
    ) -> RootTurnRender:
        if ctx.request.turn_input is None:
            raise RuntimeError("AgentRunRequest.turn_input is required for root run")
        assembly = await assembler.assemble_turn(
            intent=intent,
            request=TurnAssemblyRequest(
                session_id=ctx.environment.session_id,
                spawn_id=None,
                turn_input=ctx.request.turn_input,
                user_instructions=user_instructions,
            ),
        )
        message = assembly.user_turn_context.to_message(ContextView.RUNTIME)
        await self._write_user_turn_context_if_configured(
            ctx=ctx,
            intent=intent,
            message=message,
            user_instructions=user_instructions,
        )
        return RootTurnRender(rendered_content=message.content)

    async def _write_user_turn_context_if_configured(
        self,
        *,
        ctx: AgentRunContext,
        intent: ContextAssemblyIntent,
        message: Message,
        user_instructions: UserInstructions,
    ) -> None:
        writer = ctx.request.ports.user_turn_context_writer
        if writer is None:
            return
        await writer(
            UserTurnContextWriteRequest(
                session_id=ctx.environment.session_id,
                task_id=ctx.environment.metadata.task_id,
                invocation_id=ctx.request.invocation_id,
                spawn_id=None,
                kind="anchor" if intent.is_anchor_turn else "continuation",
                message=message,
                user_instructions_hash=(
                    user_instructions.hash if intent.is_anchor_turn else None
                ),
                transform=DEFAULT_TURN_TRANSFORM,
                render_version=USER_CONTEXT_RENDER_VERSION,
                schema_version=USER_TURN_CONTEXT_SCHEMA_VERSION,
            )
        )
```

- [ ] **Step 4: Change Exp run_stream root/child flow**

Change signature:

```python
    async def run_stream(
        self,
        ctx: AgentRunContext,
        task: str | None = None,
        *,
        history: list[Message] | None = None,
        cancel_token: CancellationToken | None = None,
        skills: dict[str, Any] | None = None,
        spawn_id: str | None = None,
    ) -> AsyncIterator[Any]:
```

At the beginning of `run_stream`, add:

```python
        resolution: TurnIntentResolution | None = None
        user_instructions: UserInstructions | None = None
        if spawn_id is None:
            if ctx.request.turn_input is None:
                raise RuntimeError("AgentRunRequest.turn_input is required for root run")
            events_port = ctx.request.ports.compaction.history or EmptySessionEventHistory()
            user_instructions = ctx.request.user_instructions or UserInstructions(
                text="",
                hash=hash_user_instructions(""),
                truncated=False,
            )
            resolution = await resolve_turn_intent(
                events_port=events_port,
                instructions_hash=user_instructions.hash,
                session_id=ctx.environment.session_id,
                spawn_id=None,
            )
            ctx = ctx.model_copy(
                update={
                    "request": ctx.request.model_copy(
                        update={"active_skills": resolution.active_skills}
                    )
                }
            )
        elif task is None:
            raise RuntimeError("task is required for spawn run")
```

Inside `runtime_scope`, before `kernel.run_stream`, add:

```python
            if spawn_id is None:
                if resolution is None or user_instructions is None:
                    raise RuntimeError("root turn resolution is missing")
                if runtime.context_runtime is None:
                    raise RuntimeError("context runtime is unavailable for root run")
                turn = await self._render_and_persist_root_turn(
                    ctx=ctx,
                    intent=resolution.intent,
                    assembler=runtime.context_runtime.assembler,
                    user_instructions=user_instructions,
                )
                task = turn.rendered_content
```

Keep the kernel call:

```python
            async for event in runtime.kernel.run_stream(
                runtime.kernel_runtime,
                task,
                history=history,
                cancel_token=cancel_token,
            ):
                yield event
```

- [ ] **Step 5: Remove skill_resolver from public Exp path**

In `matmaster/core/exp.py`:

- Remove `skill_resolver` from `build_runtime`, `runtime_scope`, and `run_stream` signatures.
- Remove `skill_resolver=...` arguments when calling `build_runtime` and child `run_stream`.
- In `build_runtime`, use:

```python
        from matmaster.context.skill_resolver import SkillRegistryResolver
        from matmaster.core.runtime_context_assembly import empty_skill_resolver

        self._skill_resolver = empty_skill_resolver
```

After `_init_skill_tools(...)`, use:

```python
        self._skill_resolver = SkillRegistryResolver(self._skill_registry)
```

This returns no active skill DTOs when `self._skill_registry is None`.

- [ ] **Step 6: Add durable writer factory in AgentRunService**

Add this private helper to `src/services/agent_run_service.py`:

```python
def _build_user_turn_context_writer(
    *,
    events_table: Any,
    session_id: str,
):
    async def _writer(request) -> None:
        payload = {
            "schema_version": request.schema_version,
            "kind": request.kind,
            "message": request.message.model_dump(mode="json"),
            "user_instructions_hash": request.user_instructions_hash,
            "transform": request.transform,
            "render_version": request.render_version,
        }
        await write_user_turn_context_event(
            events_table=events_table,
            session_id=session_id,
            task_id=request.task_id,
            invocation_id=request.invocation_id,
            spawn_id=request.spawn_id,
            payload=payload,
        )

    return _writer
```

- [ ] **Step 7: Remove Stage 5b rendering from AgentRunService**

In `src/services/agent_run_service.py`, delete the Stage 5b block that:

- builds `skill_resolver`
- builds `context_assembler`
- calls `resolve_turn_context_intent`
- calls `context_assembler.assemble_turn`
- builds `user_turn_payload`
- calls `write_user_turn_context_event`
- assigns `user_prompt = rendered_message.content`
- computes `active_skills`

After Task 2 image enrichment, keep only:

```python
            turn_input = image_service.enrich_turn_input_images(
                turn_input=turn_input,
                user_prompt=user_prompt,
                top_level_images=top_level_images,
                image_detail=image_detail,
            )
```

Set `invocation_id=invocation_id` and `active_skills=frozenset()` in `AgentRunRequest`:

```python
                    invocation_id=invocation_id,
                    active_skills=frozenset(),
```

Set the writer port:

```python
                        user_turn_context_writer=_build_user_turn_context_writer(
                            events_table=events_table,
                            session_id=session_id,
                        ),
```

Call `Exp.run_stream` without `task` and without `skill_resolver`:

```python
                exp.run_stream(
                    agent_run_ctx,
                    history=history,
                    cancel_token=cancel_token,
                )
```

- [ ] **Step 8: Update service fixtures**

In `tests/matmaster/services/agent_run_stream_fixtures.py`, update `_FakeExp.run_stream`:

```python
    async def run_stream(self, *args: Any, **kwargs: Any):
        self.last_ctx = args[0] if args else None
        self.last_task = args[1] if len(args) > 1 else None
        self.last_run_kwargs = kwargs
        if self.last_task is None and self.last_ctx is not None:
            turn_input = self.last_ctx.request.turn_input
            self.last_task = turn_input.user_text if turn_input is not None else None
        try:
            if callable(self._events):
                stream = self._events(self.last_ctx)
                async for event in stream:
                    yield event
            else:
                for event in self._events:
                    yield event
        finally:
            await self._run_cleanup_callbacks()
```

Remove assertions expecting service to pass `skill_resolver` to fake `Exp`.

- [ ] **Step 9: Run Exp preparation tests**

Run:

```bash
uv run pytest tests/matmaster/core/test_exp_turn_preparation.py -q
```

Expected: PASS.

- [ ] **Step 10: Run service stream tests**

Run:

```bash
uv run pytest \
  tests/matmaster/services/test_agent_run_stream.py \
  tests/matmaster/services/test_agent_run_stream_context_cutover.py \
  tests/matmaster/services/test_agent_run_stream_images.py \
  tests/matmaster/services/test_agent_run_stream_response_figures.py \
  tests/matmaster/services/test_lazy_mcp_replay.py \
  tests/matmaster/core/test_exp_runtime_v2.py -q
```

Expected: PASS.

- [ ] **Step 11: Commit Task 8**

```bash
git add matmaster/core/exp.py \
  src/services/agent_run_service.py \
  tests/matmaster/core/test_exp_turn_preparation.py \
  tests/matmaster/services/test_agent_run_stream.py \
  tests/matmaster/services/agent_run_stream_fixtures.py
git commit -m "refactor: move root turn rendering into exp"
```

### Task 9: Delete Service Context Assembly Adapters and Add Boundary Tests

**Files:**
- Delete: `src/services/context_assembly_factory.py`
- Delete: `src/services/context_turn_intent.py`
- Modify/Delete: `src/services/context_assembly_ports.py`
- Delete: `src/services/skill_resolver.py`
- Create: `tests/matmaster/services/test_agent_run_service_orchestration_boundary.py`
- Update tests importing deleted modules
- Verify: boundary tests and service/core suites

- [ ] **Step 1: Add final boundary tests**

Create `tests/matmaster/services/test_agent_run_service_orchestration_boundary.py`:

```python
from __future__ import annotations

import inspect
from pathlib import Path

from matmaster.core.exp import Exp
from matmaster.types.runtime import AgentKernelResources, AgentKernelSpec
from matmaster.types.runtime_ports import AgentRunPorts
from src.services.agent_run_service import AgentRunService


def _source_text(obj) -> str:
    path = Path(inspect.getsourcefile(obj) or "")
    assert path.exists()
    return path.read_text(encoding="utf-8")


def test_agent_run_service_no_longer_imports_context_assembly_adapters() -> None:
    text = _source_text(AgentRunService)

    forbidden = (
        "ContextAssembler",
        "build_context_assembler",
        "resolve_turn_context_intent",
        "write_user_turn_context_event(",
        "_active_skills",
        "_resolve_active_skill_names",
        "SkillRegistryResolver",
    )
    for value in forbidden:
        assert value not in text


def test_core_exp_does_not_import_src_services() -> None:
    text = _source_text(Exp)

    assert "src.services" not in text


def test_kernel_runtime_surface_has_no_context_assembly_fields() -> None:
    forbidden = {
        "context_runtime",
        "context_assembler",
        "assembly_ports",
        "user_turn_context_writer",
    }

    assert forbidden.isdisjoint(AgentKernelSpec.__dataclass_fields__)
    assert forbidden.isdisjoint(AgentKernelResources.__dataclass_fields__)


def test_agent_run_ports_has_writer_but_no_bag_fields() -> None:
    fields = set(AgentRunPorts.__dataclass_fields__)

    assert "user_turn_context_writer" in fields
    assert "extra" not in fields
    assert "metadata" not in fields
    assert "state" not in fields
    assert "context" not in fields
    assert "services" not in fields
    assert "payload" not in fields
```

- [ ] **Step 2: Run boundary tests and confirm they fail before deletion**

Run:

```bash
uv run pytest tests/matmaster/services/test_agent_run_service_orchestration_boundary.py -q
```

Expected: FAIL while deleted service modules or old imports still exist.

- [ ] **Step 3: Delete obsolete service modules**

Run:

```bash
git rm src/services/context_assembly_factory.py
git rm src/services/context_turn_intent.py
git rm src/services/skill_resolver.py
```

Inspect `src/services/context_assembly_ports.py`:

```bash
rg -n "AppUserInstructionsPort|AppSessionEventsPort|AppSessionJobsPort" src matmaster tests
```

If only `AppUserInstructionsPort` remains referenced, reduce the file to:

```python
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from matmaster.context.ports import UserInstructions
from src.services.user_turn_context_service import (
    USER_INSTRUCTIONS_MAX_BYTES,
    hash_user_instructions,
    truncate_utf8,
)

logger = logging.getLogger(__name__)


class AppUserInstructionsPort:
    async def load_user_instructions(
        self,
        workspace_root: Path,
    ) -> UserInstructions:
        path = workspace_root / ".matmaster" / "AGENT.md"
        try:
            raw = await asyncio.to_thread(path.read_text, encoding="utf-8")
        except FileNotFoundError:
            return UserInstructions(
                text="",
                hash=hash_user_instructions(""),
                truncated=False,
            )

        text, truncated = truncate_utf8(raw, USER_INSTRUCTIONS_MAX_BYTES)
        if truncated:
            logger.warning(
                "AGENT.md exceeds %d bytes; truncating user instructions",
                USER_INSTRUCTIONS_MAX_BYTES,
            )
        return UserInstructions(
            text=text,
            hash=hash_user_instructions(text),
            truncated=truncated,
        )
```

If `AppUserInstructionsPort` has no callers, delete `src/services/context_assembly_ports.py` too.

- [ ] **Step 4: Remove obsolete tests or move assertions to new boundaries**

Run:

```bash
rg -n "context_assembly_factory|context_turn_intent|src\\.services\\.skill_resolver|AppSessionEventsPort|AppSessionJobsPort" tests src matmaster
```

Update any remaining imports to:

```python
from matmaster.context.turn_intent import resolve_turn_intent
from matmaster.context.skill_resolver import SkillRegistryResolver
```

Delete tests whose only purpose was to test removed service adapter factories.

- [ ] **Step 5: Run boundary and focused suites**

Run:

```bash
uv run pytest \
  tests/matmaster/services/test_agent_run_service_orchestration_boundary.py \
  tests/matmaster/context/test_turn_intent.py \
  tests/matmaster/context/test_skill_resolver.py \
  tests/matmaster/context/test_active_mcp_replay.py \
  tests/matmaster/types/test_runtime_ports.py \
  tests/matmaster/core/test_exp_turn_preparation.py -q
```

Expected: PASS.

- [ ] **Step 6: Confirm deleted boundary strings are gone**

Run:

```bash
rg -n "build_context_assembler|resolve_turn_context_intent|src\\.services\\.skill_resolver|_active_skills|_resolve_active_skill_names" src/services/agent_run_service.py matmaster tests
```

Expected: no output for production paths. If tests still include these strings, they must be inside explicit deletion/boundary assertions only.

- [ ] **Step 7: Commit Task 9**

```bash
git add tests/matmaster/services/test_agent_run_service_orchestration_boundary.py
git add -u src/services tests
git commit -m "refactor: remove service context assembly adapters"
```

### Task 10: Final Verification

**Files:**
- No implementation files changed in this task.
- Verify: service/core/context/types suites and pre-commit.

- [ ] **Step 1: Run focused regression set**

Run:

```bash
uv run pytest \
  tests/matmaster/services/test_agent_run_stream.py \
  tests/matmaster/services/test_agent_run_stream_images.py \
  tests/matmaster/services/test_agent_run_stream_response_figures.py \
  tests/matmaster/services/test_lazy_mcp_replay.py \
  tests/matmaster/core/test_exp_runtime_v2.py \
  tests/matmaster/core/test_exp_turn_preparation.py \
  tests/matmaster/context/test_turn_intent.py \
  tests/matmaster/context/test_skill_resolver.py \
  tests/matmaster/types/test_runtime_ports.py \
  tests/matmaster/test_runtime_spec.py \
  tests/services/test_image_input_service.py -q
```

Expected: PASS.

- [ ] **Step 2: Run broader runtime suites**

Run:

```bash
uv run pytest tests/matmaster/services tests/matmaster/core tests/matmaster/context tests/matmaster/types -q
```

Expected: PASS.

- [ ] **Step 3: Run pre-commit**

Run:

```bash
uv run pre-commit run --all-files
```

Expected: PASS.

- [ ] **Step 4: Run final boundary searches**

Run:

```bash
rg -n "src\\.services" matmaster/core matmaster/context
```

Expected: no output.

Run:

```bash
rg -n "prebuilt_skill_registry|prebuilt_skill_resolver|prebuilt_context_runtime|prebuilt_" matmaster src tests
```

Expected: no output.

Run:

```bash
rg -n "_active_skills|_remember_skill_hit|_resolve_active_skill_names|build_context_assembler|resolve_turn_context_intent|ContextAssembler|SkillRegistryResolver" src/services/agent_run_service.py
```

Expected: no output.

Run:

```bash
rg -n "extra|metadata|state|context|services|payload|dict\\[str, Any\\]" matmaster/types/runtime_ports.py
```

Expected: occurrences only in comments or existing `CompactionCheckpointPayload`; no broad fallback field on `AgentRunPorts`, `FigureUploadPort`, `PlaygroundCompactionPort`, or `KernelRuntimePorts`.

- [ ] **Step 5: Review git diff**

Run:

```bash
git status --short
git diff --stat
git diff -- src/services/agent_run_service.py matmaster/core/exp.py matmaster/types/runtime_ports.py matmaster/types/runtime.py
```

Expected:

- `docs/` changes are not staged unless explicitly requested.
- `AgentRunService` no longer constructs context assembler, resolver, or user_turn_context payload.
- `Exp.run_stream` owns root turn render and writer call.
- `AgentKernelSpec` and `AgentKernelResources` have no context assembly internals.
- `AgentRunPorts` has typed `user_turn_context_writer` and no bag field.

- [ ] **Step 6: Commit final verification-only fixes if needed**

When Step 1 through Step 5 required formatting or import cleanup, commit only code and test changes:

```bash
git add -u matmaster src tests
git commit -m "test: verify agent run orchestrator boundary"
```

## 4. Spec Coverage Checklist

- O1 FigureCoordinator: Task 1.
- O1 image input enrichment helper: Task 2.
- O2 `_active_skills` hot cache removal: Task 3.
- O3 skill resolver migration into `matmaster`: Task 4.
- O4 `AgentRunPorts.user_turn_context_writer`: Task 5 and Task 8.
- O4 `AgentRuntime.context_runtime`: Task 7.
- O4 `resolve_turn_intent` core free function: Task 6.
- O4 root pre-runtime intent and active skills backfill: Task 8.
- O4 root render after `build_runtime`: Task 8.
- O4 service no longer passes `skill_resolver`: Task 8.
- O5 service context adapter deletion: Task 9.
- Boundary tests and final verification: Task 9 and Task 10.

## 5. Execution Handoff

After Task 10 passes, the implementation should satisfy the V2 spec completion definition:

- `AgentRunService` no longer directly constructs context assembler.
- `AgentRunService` no longer directly resolves turn intent.
- `AgentRunService` no longer directly writes user_turn_context event payload.
- `AgentRunService` no longer holds `_active_skills`.
- `AgentRunService` no longer constructs `SkillRegistryResolver`.
- `FigureCoordinator` owns response figure coordination.
- Root turn rendering lives inside `Exp.run_stream`.
- `AgentKernelSpec` and `AgentKernelResources` remain free of context assembly internals.
- Service-to-runtime capabilities flow through typed runtime ports.
- API / Worker separation remains valid.
