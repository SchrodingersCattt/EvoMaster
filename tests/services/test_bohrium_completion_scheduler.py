"""完成调度器：decide 无状态判定 + prompt 渲染 + tick 编排（假对象注入，不连库）。"""

from __future__ import annotations

import logging
from types import SimpleNamespace

from src.services.bohrium_completion_scheduler import (
    BohriumCompletionScheduler,
    Reason,
    SchedulerConfig,
    decide,
    render_prompt,
)


def _unit(**over):
    base = dict(
        user_id="u1",
        org_id="o1",
        session_id="s1",
        invocation_key="inv-1",
        workspace="/share/p",
        total=3,
        active=2,
        pending_terminal=1,
        failed_total=0,
        failed_handled=0,
        succeeded=1,
        max_pending_terminal_id=10,
    )
    base.update(over)
    return base


CFG = SchedulerConfig()  # segments=3, ttl=60, scan_limit=200


# ---------- decide ----------


def test_decide_none_when_no_pending():
    assert decide(_unit(pending_terminal=0), CFG) is None


def test_decide_final_when_all_terminal():
    assert decide(_unit(active=0, pending_terminal=1), CFG) is Reason.FINAL


def test_decide_final_preempts_first_failure_for_single_job_invocation():
    # 单 job invocation 直接失败：active==0 先命中，只发 final
    unit = _unit(
        total=1, active=0, pending_terminal=1, failed_total=1, failed_handled=0
    )
    assert decide(unit, CFG) is Reason.FINAL


def test_decide_first_failure_fast_lane():
    unit = _unit(total=3, active=2, pending_terminal=1, failed_total=1)
    assert decide(unit, CFG) is Reason.FIRST_FAILURE


def test_decide_first_failure_is_one_shot():
    # 已交付过失败（failed_handled>0）→ 不再走快车道；pending<step → None
    unit = _unit(
        total=9, active=6, pending_terminal=1, failed_total=2, failed_handled=1
    )
    assert decide(unit, CFG) is None


def test_decide_progress_threshold_is_ceil():
    # total=5, segments=3 → step=ceil(5/3)=2
    assert decide(_unit(total=5, active=3, pending_terminal=1), CFG) is None
    assert decide(_unit(total=5, active=3, pending_terminal=2), CFG) is Reason.PROGRESS


def test_progress_count_bounded_by_segments_with_ceil_total_5():
    """total=5 的完整生命周期：恰 2 次 progress（不退化成 4 次）+ 1 次 final。"""
    total, pending, progress_hits = 5, 0, 0
    for done in range(1, total + 1):
        pending += 1
        unit = _unit(total=total, active=total - done, pending_terminal=pending)
        reason = decide(unit, CFG)
        if reason is Reason.PROGRESS:
            progress_hits += 1
            pending = 0  # 成功 run 的 ack 翻篇
        elif reason is Reason.FINAL:
            pending = 0
    assert progress_hits == 2


def test_progress_count_bounded_by_segments_total_1000():
    """连续 ack 模拟：成功 progress 次数 ≤ segments，与 total 取大值无关。"""
    total, pending, progress_hits = 1000, 0, 0
    for done in range(1, total + 1):
        pending += 1
        unit = _unit(total=total, active=total - done, pending_terminal=pending)
        reason = decide(unit, CFG)
        if reason in (Reason.PROGRESS, Reason.FINAL):
            pending = 0
            if reason is Reason.PROGRESS:
                progress_hits += 1
    assert progress_hits <= CFG.progress_segments


# ---------- render_prompt ----------

_SUFFIX = "本轮交付为 session 级"


def test_render_final_prompt_has_counts_and_scope_suffix():
    prompt = render_prompt(
        Reason.FINAL,
        {"total": 10, "active": 0, "succeeded": 8, "failed_total": 2},
    )
    assert "成功 8/10" in prompt and "失败 2" in prompt
    assert _SUFFIX in prompt


def test_render_first_failure_prompt_carries_job_info_and_suffix():
    prompt = render_prompt(
        Reason.FIRST_FAILURE,
        {"total": 10, "active": 7, "succeeded": 2, "failed_total": 1},
        first_failed={"job_id": "j-9", "job_name": "dft-9", "status": "failed"},
    )
    assert "j-9" in prompt and "dft-9" in prompt and "7 个作业仍在运行" in prompt
    assert _SUFFIX in prompt


def test_render_first_failure_prompt_degrades_when_row_vanished():
    # §4d 查不到（竞态被并发 run ack）：降级文案，照常触发
    prompt = render_prompt(
        Reason.FIRST_FAILURE,
        {"total": 3, "active": 2, "succeeded": 0, "failed_total": 1},
        first_failed=None,
    )
    assert "unknown" in prompt and _SUFFIX in prompt


def test_render_progress_prompt_has_terminal_ratio_and_suffix():
    prompt = render_prompt(
        Reason.PROGRESS,
        {"total": 9, "active": 3, "succeeded": 6, "failed_total": 0},
    )
    assert "已终态 6/9" in prompt and "仍在运行 3" in prompt
    assert _SUFFIX in prompt


# ---------- tick 编排（假对象注入） ----------


class _FakeJobsTable:
    """只实现 scheduler 用到的两个读方法；任何写方法被调用都会 AttributeError，
    这本身就是「enqueued 后不写任何持久状态」的守护。"""

    def __init__(self, units=(), first_failed=None):
        self.units = list(units)
        self.first_failed = first_failed
        self.scan_limits: list[int] = []
        self.first_failed_calls: list[dict] = []

    def scan_delivery_units(self, *, limit):
        self.scan_limits.append(limit)
        return list(self.units)

    def get_first_pending_failed(self, **kw):
        self.first_failed_calls.append(kw)
        return self.first_failed


class _FakeSessions:
    def __init__(self, *, session=None, status="idle"):
        self.session = (
            session if session is not None else {"user_id": "u1", "org_id": "o1"}
        )
        self.status = status

    def get_session(self, sid):
        return self.session

    def get_session_status(self, sid):
        return self.status


class _FakeRedis:
    def __init__(self, result=True):
        self.result = result
        self.calls: list[dict] = []

    def try_reserve_nx(self, key, value, ttl_sec):
        self.calls.append({"key": key, "value": value, "ttl_sec": ttl_sec})
        return self.result


class _FakeStream:
    def __init__(self, status="enqueued"):
        self.status = status
        self.calls: list[dict] = []

    def trigger_run(self, session_id, prompt, **kw):
        self.calls.append({"session_id": session_id, "prompt": prompt, **kw})
        return SimpleNamespace(status=self.status)


def _scheduler(units, *, table=None, sessions=None, redis=None, stream=None, cfg=None):
    table = table if table is not None else _FakeJobsTable(units)
    sessions = sessions if sessions is not None else _FakeSessions()
    redis = redis if redis is not None else _FakeRedis()
    stream = stream if stream is not None else _FakeStream()
    sched = BohriumCompletionScheduler(
        jobs_table=table,
        sessions_service=sessions,
        stream_service=stream,
        redis=redis,
        cfg=cfg or SchedulerConfig(),
    )
    return sched, table, sessions, redis, stream


def test_tick_triggers_final_with_notify_and_no_persistent_state():
    units = [_unit(active=0, pending_terminal=3, succeeded=3, total=3)]
    sched, table, _, redis, stream = _scheduler(units)

    summary = sched.tick()

    assert summary["scanned"] == 1 and summary["eligible"] == 1
    assert summary["triggered"] == 1 and summary["errors"] == 0
    call = stream.calls[0]
    assert call["session_id"] == "s1"
    assert call["origin"] == "bohrium_completion"
    assert call["workspace"] == "/share/p"
    assert call["delivery"] == {"notify": True}
    assert "dedup_key" not in call  # 占位已由 NX 接管
    assert redis.calls[0]["ttl_sec"] == 60


def test_tick_merges_session_units_single_trigger_with_primary_reason():
    units = [
        _unit(
            invocation_key="inv-a",
            active=0,
            pending_terminal=2,
            succeeded=2,
            total=2,
            max_pending_terminal_id=7,
        ),  # FINAL
        _unit(
            invocation_key="inv-b",
            active=1,
            pending_terminal=1,
            total=3,
            max_pending_terminal_id=12,
        ),  # PROGRESS(step=1)
    ]
    sched, _, _, redis, stream = _scheduler(units)

    summary = sched.tick()

    assert summary["triggered"] == 1 and len(stream.calls) == 1
    assert len(redis.calls) == 1  # 同 session 两单元只占一次位
    # NX key 用 session 内 max_pending_terminal_id 高水位
    assert redis.calls[0]["key"] == "bohrium_delivery:u1:o1:s1:12"
    # primary = FINAL：文案 + notify
    assert "全部 Bohrium 作业已结束" in stream.calls[0]["prompt"]
    assert stream.calls[0]["delivery"] == {"notify": True}


def test_tick_first_failure_fetches_job_info_into_prompt():
    units = [_unit(total=3, active=2, pending_terminal=1, failed_total=1, succeeded=0)]
    table = _FakeJobsTable(
        units, first_failed={"job_id": "j-9", "job_name": "dft", "status": "failed"}
    )
    sched, _, _, _, stream = _scheduler(units, table=table)

    sched.tick()

    assert table.first_failed_calls == [
        {
            "user_id": "u1",
            "org_id": "o1",
            "session_id": "s1",
            "invocation_key": "inv-1",
        }
    ]
    assert "j-9" in stream.calls[0]["prompt"]
    assert stream.calls[0]["delivery"] == {"notify": False}


def test_tick_null_invocation_sentinel_unit_flows_through():
    units = [
        _unit(
            invocation_key="",
            total=1,
            active=1,
            pending_terminal=1,
            failed_total=1,
            succeeded=0,
        )
    ]
    table = _FakeJobsTable(units, first_failed=None)
    sched, _, _, _, stream = _scheduler(units, table=table)

    sched.tick()

    assert table.first_failed_calls[0]["invocation_key"] == ""
    assert len(stream.calls) == 1


def test_tick_identity_gate_skips_owner_changed_session():
    units = [_unit(active=0, pending_terminal=1)]
    sessions = _FakeSessions(session={"user_id": "u1", "org_id": "o-CHANGED"})
    sched, _, _, redis, stream = _scheduler(units, sessions=sessions)

    summary = sched.tick()

    assert summary["skipped_identity"] == 1 and summary["triggered"] == 0
    assert stream.calls == [] and redis.calls == []


def test_tick_status_gate_skips_busy_states():
    for status in ("active", "waiting"):
        units = [_unit(active=0, pending_terminal=1)]
        sched, _, _, _, stream = _scheduler(
            units, sessions=_FakeSessions(status=status)
        )
        summary = sched.tick()
        assert summary["skipped_busy"] == 1, status
        assert stream.calls == [], status


def test_tick_status_gate_failed_counts_and_warns_with_session_list(caplog):
    units = [_unit(active=0, pending_terminal=1)]
    sched, _, _, _, stream = _scheduler(units, sessions=_FakeSessions(status="failed"))

    with caplog.at_level(
        logging.WARNING, logger="src.services.bohrium_completion_scheduler"
    ):
        summary = sched.tick()

    assert summary["skipped_failed"] == 1 and stream.calls == []
    # 停摆唯一的发现通道：WARN + session 清单
    warn = [r for r in caplog.records if "stalled" in r.getMessage()]
    assert warn and "s1" in warn[0].getMessage()


def test_tick_nx_false_skips_as_busy():
    units = [_unit(active=0, pending_terminal=1)]
    sched, _, _, _, stream = _scheduler(units, redis=_FakeRedis(result=False))

    summary = sched.tick()

    assert summary["skipped_busy"] == 1 and stream.calls == []


def test_tick_nx_none_fail_closed_counts_redis_and_warns_once(caplog):
    # Redis 故障：禁止放行（放行只会产生孤儿 trigger 事件与排队通知）
    units = [
        _unit(session_id="s1", active=0, pending_terminal=1),
        _unit(
            session_id="s2", active=0, pending_terminal=1, max_pending_terminal_id=20
        ),
    ]
    sched, _, _, _, stream = _scheduler(units, redis=_FakeRedis(result=None))

    with caplog.at_level(
        logging.WARNING, logger="src.services.bohrium_completion_scheduler"
    ):
        summary = sched.tick()

    assert summary["skipped_redis"] == 2 and stream.calls == []
    # tick 级聚合一条 WARN，不逐 session 刷日志
    redis_warns = [r for r in caplog.records if "fail-closed" in r.getMessage()]
    assert len(redis_warns) == 1


def test_tick_trigger_busy_and_error_do_not_touch_ledger():
    units = [_unit(active=0, pending_terminal=1)]
    sched, _, _, _, _ = _scheduler(units, stream=_FakeStream(status="busy"))
    summary = sched.tick()
    assert summary["skipped_busy"] == 1 and summary["triggered"] == 0

    sched2, _, _, _, _ = _scheduler(units, stream=_FakeStream(status="error"))
    summary2 = sched2.tick()
    assert summary2["errors"] == 1 and summary2["triggered"] == 0
    # _FakeJobsTable 无任何写方法：触达 ledger 会直接 AttributeError 炸测试


def test_tick_swallows_scan_failure_and_returns_tick_failed():
    class _BoomTable:
        def scan_delivery_units(self, *, limit):
            raise RuntimeError("db down")

    sched = BohriumCompletionScheduler(
        jobs_table=_BoomTable(),
        sessions_service=_FakeSessions(),
        stream_service=_FakeStream(),
        redis=_FakeRedis(),
        cfg=SchedulerConfig(),
    )
    summary = sched.tick()
    assert summary["tick_failed"] == 1


def test_tick_session_exception_isolated_to_errors_bucket():
    # 单 session 异常进 errors 桶，不殃及同 tick 其他 session（tick 永不抛的另一半）
    units = [
        _unit(session_id="s1", active=0, pending_terminal=1),
        _unit(session_id="s2", active=0, pending_terminal=1),
    ]

    class _BoomOnS1Sessions(_FakeSessions):
        def get_session(self, sid):
            if sid == "s1":
                raise RuntimeError("session row gone")
            return super().get_session(sid)

    sched, _, _, _, stream = _scheduler(units, sessions=_BoomOnS1Sessions())

    summary = sched.tick()

    assert summary["errors"] == 1
    assert summary["triggered"] == 1
    assert [c["session_id"] for c in stream.calls] == ["s2"]
