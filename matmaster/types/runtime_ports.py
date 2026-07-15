"""Narrow runtime capability ports for context/spec boundaries.

RuntimePorts are not metadata containers. They carry callable capabilities
that core runtime components invoke directly.
"""

from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import Any, Literal, NotRequired, Protocol, TypedDict, runtime_checkable

from matmaster.bohrium.types import BohriumRuntimeSnapshot
from matmaster.context.ports import (
    BohriumJobLedgerPort,
    SessionEvent,
    SessionEventQuery,
    WorkspaceJobsPort,
)
from matmaster.types.events import BusEvent
from matmaster.types.figures import FigureUploadConfig
from matmaster.types.messages import Message
from matmaster.types.submit_review import SubmitApprovalGate

__all__ = [
    "AgentRunPorts",
    "BohriumRuntimePort",
    "BohriumRuntimeSnapshot",
    "BusEventSink",
    "CheckpointSink",
    "CheckpointSinkFactory",
    "CompactionCheckpointPayload",
    "EmptySessionEventHistory",
    "FigureUploadPort",
    "InterruptChecker",
    "KernelRuntimePorts",
    "PlaygroundCompactionPort",
    "PreCompactionBarrier",
    "SessionEventHistoryPort",
    "SubagentProviderFactory",
    "SubmitApprovalGate",
    "ToolTimeoutNotice",
    "ToolTimeoutObserver",
    "UserTurnContextWriteRequest",
    "UserTurnContextWriter",
]


class CompactionCheckpointPayload(TypedDict):
    durability: str
    strategy: str
    covered_until_event_id: NotRequired[int]
    schema_version: NotRequired[str]
    render_version: NotRequired[str]
    user_instructions_text: NotRequired[str]
    user_instructions_hash: NotRequired[str]


@runtime_checkable
class BusEventSink(Protocol):
    def __call__(self, event: BusEvent) -> Awaitable[None] | None: ...


@runtime_checkable
class PreCompactionBarrier(Protocol):
    def __call__(self) -> Awaitable[None] | None: ...


@runtime_checkable
class CheckpointSink(Protocol):
    async def __call__(
        self,
        *,
        payload: CompactionCheckpointPayload,
        base_messages: list[dict[str, Any]],
    ) -> int | None: ...


@runtime_checkable
class CheckpointSinkFactory(Protocol):
    def __call__(self, *, spawn_id: str | None = None) -> CheckpointSink: ...


@runtime_checkable
class SessionEventHistoryPort(Protocol):
    async def load_events(
        self,
        query: SessionEventQuery,
    ) -> tuple[SessionEvent, ...]: ...

    def query_events(self) -> list[dict[str, Any]]: ...

    def all_events(self) -> list[dict[str, Any]]: ...

    def latest_checkpoint_covered_until_event_id(self) -> int | None: ...

    def latest_scope_event_id(self) -> int | None: ...


@dataclass(frozen=True)
class EmptySessionEventHistory:
    async def load_events(
        self,
        query: SessionEventQuery,
    ) -> tuple[SessionEvent, ...]:
        return ()

    def query_events(self) -> list[dict[str, Any]]:
        return []

    def all_events(self) -> list[dict[str, Any]]:
        return []

    def latest_checkpoint_covered_until_event_id(self) -> int | None:
        return None

    def latest_scope_event_id(self) -> int | None:
        return None


@dataclass(frozen=True)
class PlaygroundCompactionPort:
    history: SessionEventHistoryPort | None = None
    checkpoint_sink_factory: CheckpointSinkFactory | None = None
    pre_compaction_barrier: PreCompactionBarrier | None = None


@dataclass(frozen=True)
class FigureUploadPort:
    config: FigureUploadConfig | None = None


@dataclass(frozen=True)
class BohriumRuntimePort:
    snapshot: BohriumRuntimeSnapshot | None = None


@runtime_checkable
class InterruptChecker(Protocol):
    """Check and wait for user interrupt at checkpoint boundaries."""

    def has_hint(self) -> bool: ...

    async def wait_for_confirm(self, timeout: float) -> bool: ...

    def cleanup(self) -> None: ...


@runtime_checkable
class SubagentProviderFactory(Protocol):
    """按 profile_key 物化一个 subagent 用的 LLM provider bundle。

    消费者：Exp 的 child_run_factory，在 child config 解析后、run_stream 前调用。
    返回：每次调用返回全新 bundle，其中 provider 已按当前 run 模式包装，平台
    模式下含计费。严禁按 profile 缓存复用同一个 bundle，否则并发 spawn 同
    profile 的两个 subagent 会共用同一个 async context manager 与 HTTP session。
    返回类型用 Any 以免 types 层反向依赖 providers 层，实际为
    providers.llm_factory.LLMProviderBundle。profile_key 非法时实现内部抛
    KeyError，由消费者捕获并回退继承父 provider。
    """

    def __call__(self, *, profile_key: str) -> Any: ...


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


@dataclass(frozen=True)
class ToolTimeoutNotice:
    session_id: str
    task_id: str | None
    spawn_id: str | None
    tool_name: str
    tool_call_id: str
    turn: int
    result_content: str
    arguments_preview: str


@runtime_checkable
class ToolTimeoutObserver(Protocol):
    """Observe tool timeouts after tool execution.

    Consumer: service-layer observability integrations such as Feishu alerting.
    Timing: invoked from the POST_TOOL_CALL observer path after result rewrites.
    Return: ignored; the observer is for side effects only.
    Exceptions: implementations should swallow failures; callers also guard and log.
    """

    def __call__(self, notice: ToolTimeoutNotice) -> Awaitable[None] | None: ...


@dataclass(frozen=True)
class AgentRunPorts:
    """Narrow runtime capability ports carried by AgentRunRequest.

    The service layer injects these per run. It intentionally does *not* carry
    the Bohrium snapshot: that is physical execution info and lives on
    ``ExecutionEnvironment.bohrium`` instead. Stays a narrow capability
    contract -- only callable / sink / barrier / capability, never a metadata
    or ``dict[str, Any]`` bag.
    """

    child_event_forward_sink: BusEventSink | None = None
    compaction: PlaygroundCompactionPort = field(
        default_factory=PlaygroundCompactionPort
    )
    figure_upload: FigureUploadPort = field(default_factory=FigureUploadPort)
    interrupt_checker: InterruptChecker | None = None
    user_turn_context_writer: UserTurnContextWriter | None = None
    bohrium_job_ledger: BohriumJobLedgerPort | None = None
    workspace_jobs: WorkspaceJobsPort | None = None
    submit_approval_gate: SubmitApprovalGate | None = None
    tool_timeout_observer: ToolTimeoutObserver | None = None
    subagent_provider_factory: SubagentProviderFactory | None = None


@dataclass(frozen=True)
class KernelRuntimePorts:
    checkpoint_sink: CheckpointSink | None = None
    pre_compaction_barrier: PreCompactionBarrier | None = None
    interrupt_checker: InterruptChecker | None = None
