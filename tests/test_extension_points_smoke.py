"""接入路线接口验收：loop（RUN_END handler）/ schedule（扫表）仅靠 trigger_run 即可触发。"""

import asyncio
from unittest.mock import MagicMock


def test_loop_run_end_handler_can_drive_trigger_run():
    """loop 驱动器：RUN_END handler 收到 RunContext，调 trigger_run，不改其签名。"""
    from matmaster.core.hooks import RunContext

    stream_svc = MagicMock()
    stream_svc.trigger_run.return_value = MagicMock(status="enqueued")

    loop_state = {
        "sess-1": {"turn": 3, "next_prompt": "继续下一步", "should_continue": True}
    }

    async def on_run_end(ctx: RunContext) -> None:
        st = loop_state.get(ctx.session_id)
        if st and st["should_continue"]:
            stream_svc.trigger_run(
                ctx.session_id,
                st["next_prompt"],
                origin="loop",
                dedup_key=f"loop:{ctx.session_id}:{st['turn']}",
                delivery={"notify": False},
            )

    ctx = RunContext(task_id="t1", session_id="sess-1", reason="natural")
    asyncio.run(on_run_end(ctx))

    stream_svc.trigger_run.assert_called_once()
    kwargs = stream_svc.trigger_run.call_args.kwargs
    assert kwargs["origin"] == "loop"
    assert kwargs["dedup_key"] == "loop:sess-1:3"


def test_loop_handler_registers_on_run_end():
    """RUN_END 是可注册的 observe hook（接入点存在）。"""
    from matmaster.core.hooks import HookEvent

    assert hasattr(HookEvent, "RUN_END")


def test_schedule_tick_can_drive_trigger_run():
    """schedule 驱动器：扫到 due 行，调 trigger_run，不改其签名。"""
    stream_svc = MagicMock()
    stream_svc.trigger_run.return_value = MagicMock(status="enqueued")

    due_rows = [
        {
            "id": 7,
            "session_id": "sess-2",
            "prompt": "每日巡检",
            "fire_epoch": 1717459200,
        },
    ]

    def schedule_tick():
        for row in due_rows:
            stream_svc.trigger_run(
                row["session_id"],
                row["prompt"],
                origin="cron",
                dedup_key=f"sched:{row['id']}:{row['fire_epoch']}",
                delivery={"notify": True},
                workspace="/share/case",
            )

    schedule_tick()

    stream_svc.trigger_run.assert_called_once()
    kwargs = stream_svc.trigger_run.call_args.kwargs
    assert kwargs["origin"] == "cron"
    assert kwargs["dedup_key"] == "sched:7:1717459200"
    assert kwargs["workspace"] == "/share/case"


def test_completion_dispatcher_can_pass_claimed_workspace_to_trigger_run():
    stream_svc = MagicMock()
    stream_svc.trigger_run.return_value = MagicMock(status="enqueued")
    stream_svc.trigger_run(
        "sess-1",
        "Bohrium 作业 job-1 已完成，请读取结果并继续。",
        origin="bohrium_job",
        dedup_key="bohrium_job:sess-1:job-1:done",
        delivery={"notify": False},
        workspace="/share/project",
    )
    kwargs = stream_svc.trigger_run.call_args.kwargs
    assert kwargs["workspace"] == "/share/project"
    assert kwargs["origin"] == "bohrium_job"
