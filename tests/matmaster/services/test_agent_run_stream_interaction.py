from __future__ import annotations

import asyncio
import json
import threading
import time
from collections import defaultdict, deque
from unittest.mock import MagicMock

import pytest

from matmaster.integration.interaction_bridge import InteractionBridge
from matmaster.types.events import RunResultEvent, ToolResultEvent
from src.dao.redis_dao import RedisDao

from .agent_run_stream_fixtures import _make_cancel_token, _patched_service


class _InMemoryRedisClient:
    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.lists: defaultdict[str, deque[str]] = defaultdict(deque)
        self.strings: dict[str, str] = {}

    def hset(self, key: str, mapping: dict[str, str]) -> None:
        self.hashes.setdefault(key, {}).update(mapping)

    def expire(self, key: str, ttl: int | str) -> None:
        return None

    def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.hashes.get(key, {}))

    def blpop(self, key: str, timeout: int) -> tuple[str, str] | None:
        if not self.lists[key]:
            return None
        return key, self.lists[key].popleft()

    def rpush(self, key: str, value: str) -> None:
        self.lists[key].append(value)

    def delete(self, key: str) -> None:
        self.hashes.pop(key, None)
        self.lists.pop(key, None)
        self.strings.pop(key, None)

    def exists(self, key: str) -> int:
        return int(key in self.hashes or key in self.lists or key in self.strings)

    def get(self, key: str) -> str | None:
        return self.strings.get(key)

    def set(
        self, key: str, value: str, nx: bool = False, ex: int | None = None
    ) -> bool:
        if nx and key in self.strings:
            return False
        self.strings[key] = value
        return True

    def eval(self, script: str, numkeys: int, *keys_and_args):
        keys = keys_and_args[:numkeys]
        args = keys_and_args[numkeys:]
        if numkeys == 2:
            registry_key, reply_key = keys
            envelope, terminal_ttl, reply_ttl = args
            if registry_key not in self.hashes:
                return 0
            if self.hashes[registry_key].get("state") != "pending":
                return 1
            self.hashes[registry_key]["state"] = "answered"
            self.rpush(reply_key, envelope)
            self.expire(registry_key, terminal_ttl)
            self.expire(reply_key, reply_ttl)
            return 2
        if numkeys == 1 and len(args) == 2:
            (registry_key,) = keys
            state, terminal_ttl = args
            if registry_key not in self.hashes:
                return 0
            if self.hashes[registry_key].get("state") != "pending":
                return 0
            self.hashes[registry_key]["state"] = state
            self.expire(registry_key, terminal_ttl)
            return 1
        if numkeys == 1 and len(args) == 1:
            (active_key,) = keys
            (request_id,) = args
            if self.strings.get(active_key) == request_id:
                self.delete(active_key)
                return 1
            return 0
        raise AssertionError("unexpected eval call")


class _BlockingRedisClient(_InMemoryRedisClient):
    def __init__(self) -> None:
        super().__init__()
        self.condition = threading.Condition()

    def blpop(self, key: str, timeout: int) -> tuple[str, str] | None:
        deadline = time.monotonic() + min(timeout, 1)
        with self.condition:
            while not self.lists[key]:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self.condition.wait(timeout=remaining)
            return key, self.lists[key].popleft()

    def rpush(self, key: str, value: str) -> None:
        with self.condition:
            super().rpush(key, value)
            self.condition.notify_all()


@pytest.fixture
def redis_dao(monkeypatch):
    client = _InMemoryRedisClient()
    dao = RedisDao()
    monkeypatch.setattr(dao, "get_command_client", lambda: client)
    monkeypatch.setattr(dao, "create_client", lambda: client)
    return dao


def _pending_record() -> dict[str, str]:
    return {
        "kind": "ask_question",
        "session_id": "s",
        "task_id": "t",
        "invocation_id": "i",
        "state": "pending",
        "expires_at": "",
    }


def test_per_request_reply_isolation(redis_dao):
    redis_dao.write_pending_interaction("aq_A", _pending_record(), ttl=60)
    redis_dao.write_pending_interaction("aq_B", _pending_record(), ttl=60)
    redis_dao.answer_pending_interaction(
        "aq_A",
        '{"kind":"ask_question","request_id":"aq_A","payload":{"answers":{"q":"a"}}}',
    )

    assert redis_dao.blpop_interaction_reply("aq_B", timeout_sec=1) is None
    raw_a = redis_dao.blpop_interaction_reply("aq_A", timeout_sec=1)
    assert raw_a is not None and '"aq_A"' in raw_a


def test_answer_is_atomic_and_terminal(redis_dao):
    redis_dao.write_pending_interaction("aq_C", _pending_record(), ttl=60)

    assert (
        redis_dao.answer_pending_interaction(
            "aq_C", '{"kind":"ask_question","request_id":"aq_C","payload":{}}'
        )
        == "ok"
    )
    assert redis_dao.read_pending_interaction("aq_C")["state"] == "answered"
    assert (
        redis_dao.answer_pending_interaction(
            "aq_C", '{"kind":"ask_question","request_id":"aq_C","payload":{}}'
        )
        == "not_pending"
    )
    assert redis_dao.answer_pending_interaction("aq_missing", "{}") == "not_found"


def test_timeout_finalize_vs_answer_single_winner(redis_dao):
    redis_dao.write_pending_interaction("aq_D", _pending_record(), ttl=60)

    assert redis_dao.finalize_interaction("aq_D", "timeout") is True
    assert redis_dao.answer_pending_interaction("aq_D", "{}") == "not_pending"
    assert redis_dao.blpop_interaction_reply("aq_D", timeout_sec=1) is None
    assert redis_dao.finalize_interaction("aq_D", "cancelled") is False


def test_active_guard_setnx_and_compare_and_delete(redis_dao):
    assert redis_dao.acquire_active_interaction("sess", "aq_E") is True
    assert redis_dao.acquire_active_interaction("sess", "aq_F") is False
    assert redis_dao.get_active_interaction("sess") == "aq_E"
    redis_dao.release_active_interaction("sess", "aq_F")
    assert redis_dao.get_active_interaction("sess") == "aq_E"
    redis_dao.release_active_interaction("sess", "aq_E")
    assert redis_dao.get_active_interaction("sess") is None


class _BridgeDaoFake:
    def __init__(self, replies: list[str | None], finalize_result: bool = True) -> None:
        self.replies = deque(replies)
        self.finalize_result = finalize_result
        self.deleted: list[str] = []
        self.released: list[tuple[str, str]] = []

    def acquire_active_interaction(self, session_id: str, request_id: str) -> bool:
        return True

    def write_pending_interaction(
        self, request_id: str, record: dict, ttl: int
    ) -> None:
        return None

    def blpop_interaction_reply(self, request_id: str, timeout_sec: int) -> str | None:
        if not self.replies:
            return None
        return self.replies.popleft()

    def finalize_interaction(self, request_id: str, state: str) -> bool:
        return self.finalize_result

    def delete_interaction_reply(self, request_id: str) -> None:
        self.deleted.append(request_id)

    def release_active_interaction(self, session_id: str, request_id: str) -> None:
        self.released.append((session_id, request_id))


async def _noop_interaction_sink(event) -> None:
    return None


def _configure_immediate_interaction_reply(dao, request_id: str, payload: dict) -> None:
    envelope = json.dumps(
        {"kind": "ask_question", "request_id": request_id, "payload": payload},
        ensure_ascii=False,
    )
    dao.acquire_active_interaction.return_value = True
    dao.write_pending_interaction.return_value = None
    dao.blpop_interaction_reply.return_value = envelope
    dao.finalize_interaction.return_value = False
    dao.delete_interaction_reply.return_value = None
    dao.release_active_interaction.return_value = None


@pytest.mark.asyncio
async def test_request_recovers_late_answer_after_blpop_timeout():
    dao = _BridgeDaoFake(
        [
            None,
            '{"kind":"ask_question","request_id":"aq_x","payload":{"answers":{"q":"a"}}}',
        ],
        finalize_result=False,
    )
    bridge = InteractionBridge(
        session_id="s",
        task_id="t",
        invocation_id="i",
        event_sink=_noop_interaction_sink,
        dao=dao,
    )

    out = await bridge.request(
        kind="ask_question", request_id="aq_x", payload={"questions": []}
    )

    assert out == {"answers": {"q": "a"}}
    assert dao.deleted == ["aq_x"]
    assert dao.released == [("s", "aq_x")]


@pytest.mark.asyncio
async def test_request_rejects_envelope_mismatch():
    dao = _BridgeDaoFake(
        ['{"kind":"ask_question","request_id":"aq_OTHER","payload":{}}']
    )
    bridge = InteractionBridge(
        session_id="s",
        task_id="t",
        invocation_id="i",
        event_sink=_noop_interaction_sink,
        dao=dao,
    )

    with pytest.raises(RuntimeError, match="mismatch"):
        await bridge.request(kind="ask_question", request_id="aq_x", payload={})


@pytest.mark.asyncio
async def test_stop_during_ask_question_wakes_blpop_immediately(monkeypatch):
    client = _BlockingRedisClient()
    dao = RedisDao()
    monkeypatch.setattr(dao, "get_command_client", lambda: client)
    monkeypatch.setattr(dao, "create_client", lambda: client)
    request_id = "aq_wake"
    bridge = InteractionBridge(
        session_id="sess-w",
        task_id="t",
        invocation_id="i",
        event_sink=_noop_interaction_sink,
        dao=dao,
    )

    async def fire_stop_after_active() -> None:
        deadline = time.monotonic() + 1
        while dao.get_active_interaction("sess-w") != request_id:
            if time.monotonic() > deadline:
                raise AssertionError("active interaction was not set")
            await asyncio.sleep(0.01)
        dao.finalize_interaction(request_id, "cancelled")
        dao.rpush_interaction_cancel(request_id)

    stopper = asyncio.create_task(fire_stop_after_active())
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(
            bridge.request(
                kind="ask_question",
                request_id=request_id,
                payload={"questions": []},
                timeout_seconds=1800,
            ),
            timeout=1,
        )
    await stopper
    assert dao.read_pending_interaction(request_id)["state"] == "cancelled"


@pytest.mark.asyncio
async def test_ask_question_bridge_events_go_through_fanout_and_persistence():
    async def ask_then_finish(ctx):
        await ctx.request.interaction_bridge.request(
            kind="ask_question",
            request_id="aq_1",
            payload={
                "questions": [
                    {
                        "question": "Q1",
                        "header": "H1",
                        "options": [
                            {"label": "A1", "description": "desc"},
                            {"label": "A2", "description": "desc"},
                        ],
                        "allow_freeform": True,
                        "multi_select": False,
                    }
                ],
                "metadata": {"scene": "test"},
                "origin": "tool:AskQuestion",
                "preview_format": "markdown",
            },
        )
        yield RunResultEvent(source="agent", status="completed", reason="natural")

    send_cb = MagicMock()

    async with _patched_service(ask_then_finish) as (svc, _, persist_events):
        _configure_immediate_interaction_reply(
            svc._test_redis_dao, "aq_1", {"answers": {"Q1": "A1"}, "annotations": {}}
        )
        await svc.run_agent(
            session_id="s1",
            user_prompt="hi",
            send_cb=send_cb,
            cancel_token=_make_cancel_token(),
            mode="direct",
            task_id="t1",
            invocation_id="inv-1",
        )

    payload = send_cb.call_args_list[0].args[0]
    assert payload["type"] == "interaction_request"
    assert payload["session_id"] == "s1"
    assert payload["task_id"] == "t1"
    assert payload["invocation_id"] == "inv-1"
    assert payload["content"]["request_id"] == "aq_1"
    assert payload["content"]["kind"] == "ask_question"
    assert payload["content"]["payload"]["metadata"] == {"scene": "test"}

    persisted = [
        event
        for event in persist_events
        if getattr(event, "type", None) == "interaction_request"
    ]
    assert len(persisted) == 1
    assert persisted[0].request_id == "aq_1"


@pytest.mark.asyncio
async def test_ask_question_tool_result_reaches_sse_before_run_result():
    async def ask_then_emit_tool_result(ctx):
        await ctx.request.interaction_bridge.request(
            kind="ask_question",
            request_id="aq_1",
            payload={
                "questions": [
                    {
                        "question": "Q1",
                        "header": "H1",
                        "options": [
                            {"label": "A1", "description": "desc"},
                            {"label": "A2", "description": "desc"},
                        ],
                        "allow_freeform": True,
                        "multi_select": False,
                    }
                ],
                "metadata": {"scene": "stream-order"},
                "origin": "tool:AskQuestion",
                "preview_format": "markdown",
            },
        )
        yield ToolResultEvent(
            source="agent",
            call_id="call_aq_1",
            tool_name="AskQuestion",
            result='"Q1"="A1"',
            status="success",
            payload={
                "request_id": "aq_1",
                "answers": {"Q1": "A1"},
                "annotations": {},
            },
        )
        yield RunResultEvent(source="agent", status="completed", reason="natural")

    send_cb = MagicMock()

    async with _patched_service(ask_then_emit_tool_result) as (svc, _, __):
        _configure_immediate_interaction_reply(
            svc._test_redis_dao, "aq_1", {"answers": {"Q1": "A1"}, "annotations": {}}
        )
        await svc.run_agent(
            session_id="s1",
            user_prompt="hi",
            send_cb=send_cb,
            cancel_token=_make_cancel_token(),
            mode="direct",
            task_id="t1",
            invocation_id="inv-1",
        )

    payloads = [call.args[0] for call in send_cb.call_args_list]
    payload_types = [payload["type"] for payload in payloads]

    ask_idx = payload_types.index("interaction_request")
    tool_result_idx = payload_types.index("tool_result")
    run_result_idx = payload_types.index("run_result")
    stream_closed_idx = payload_types.index("stream_closed")

    assert ask_idx < tool_result_idx < run_result_idx < stream_closed_idx
    assert payloads[tool_result_idx]["content"]["id"] == "call_aq_1"
    assert payloads[tool_result_idx]["content"]["name"] == "AskQuestion"
    assert payloads[tool_result_idx]["content"]["result"] == '"Q1"="A1"'


@pytest.mark.asyncio
async def test_submit_gate_constructed_only_when_enabled():
    async with _patched_service(
        [RunResultEvent(source="agent", status="completed", reason="natural")]
    ) as (svc, _, __):
        await svc.run_agent(
            session_id="s1",
            user_prompt="hi",
            send_cb=MagicMock(),
            cancel_token=_make_cancel_token(),
            mode="direct",
            task_id="t1",
            invocation_id="inv-1",
            submit_confirmation_enabled=False,
        )
        assert svc._test_fake_exp.last_ctx.request.ports.submit_approval_gate is None

    async with _patched_service(
        [RunResultEvent(source="agent", status="completed", reason="natural")]
    ) as (svc, _, __):
        await svc.run_agent(
            session_id="s1",
            user_prompt="hi",
            send_cb=MagicMock(),
            cancel_token=_make_cancel_token(),
            mode="direct",
            task_id="t1",
            invocation_id="inv-1",
            submit_confirmation_enabled=True,
        )

        from matmaster.integration.submit_approval_gate import BridgeSubmitApprovalGate

        gate = svc._test_fake_exp.last_ctx.request.ports.submit_approval_gate
        assert isinstance(gate, BridgeSubmitApprovalGate)
