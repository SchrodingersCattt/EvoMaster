"""完成调度器：decide 无状态判定 + prompt 渲染 + tick 编排（假对象注入，不连库）。"""

from __future__ import annotations

from src.services.bohrium_completion_scheduler import (
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
