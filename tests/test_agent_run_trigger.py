"""程序化触发原语测试：DeliverySpec / ChatSendRequest 扩展 / dedup / _prepare_run / _enqueue_run / trigger_run。"""

from unittest.mock import MagicMock, patch


def test_chat_send_request_trigger_fields_default():
    from src.models.chat import ChatSendRequest

    req = ChatSendRequest(content="hi")
    assert req.origin is None
    assert req.dedup_key is None
    assert req.on_busy == "skip"
    assert req.delivery is None


def test_chat_send_request_accepts_delivery_spec():
    from src.models.chat import ChatSendRequest, DeliverySpec

    req = ChatSendRequest(
        content="作业123已完成",
        origin="hpc_job",
        dedup_key="job:123:done",
        on_busy="skip",
        delivery={"notify": True},
    )
    assert req.origin == "hpc_job"
    assert req.dedup_key == "job:123:done"
    assert isinstance(req.delivery, DeliverySpec)
    assert req.delivery.notify is True


def test_delivery_spec_notify_defaults_true():
    from src.models.chat import DeliverySpec

    assert DeliverySpec().notify is True
    assert DeliverySpec(notify=False).notify is False


def test_dedup_key_exists_uses_prefixed_key():
    from src.dao.redis_dao import RedisDao

    dao = RedisDao()
    fake_client = MagicMock()
    fake_client.exists.return_value = 1
    with patch.object(dao, "get_command_client", return_value=fake_client):
        assert dao.dedup_key_exists("job:123:done") is True
    fake_client.exists.assert_called_once_with("chat:trigger:dedup:job:123:done")


def test_dedup_key_exists_false_when_no_client():
    from src.dao.redis_dao import RedisDao

    dao = RedisDao()
    with patch.object(dao, "get_command_client", return_value=None):
        assert dao.dedup_key_exists("job:123:done") is False


def test_mark_dedup_key_nx_sets_with_nx_and_ttl():
    from src.dao.redis_dao import RedisDao

    dao = RedisDao()
    fake_client = MagicMock()
    fake_client.set.return_value = True
    with patch.object(dao, "get_command_client", return_value=fake_client):
        assert dao.mark_dedup_key_nx("job:123:done", "trig_abc", ttl_sec=86400) is True
    fake_client.set.assert_called_once_with(
        "chat:trigger:dedup:job:123:done", "trig_abc", nx=True, ex=86400
    )


def test_mark_dedup_key_nx_returns_false_when_already_present():
    from src.dao.redis_dao import RedisDao

    dao = RedisDao()
    fake_client = MagicMock()
    fake_client.set.return_value = None  # NX 未设置成功 → redis 返回 None
    with patch.object(dao, "get_command_client", return_value=fake_client):
        assert dao.mark_dedup_key_nx("job:123:done", "trig_abc", ttl_sec=86400) is False


def test_internal_trigger_token_constant_importable():
    import src.utils.constant as constant

    # 常量必须存在；值取决于环境变量，未配置时为 None
    assert hasattr(constant, "INTERNAL_TRIGGER_TOKEN")


def test_run_handle_and_busy_and_trigger_result_shapes():
    from src.services.stream_service import Busy, RunHandle, TriggerResult

    handle = RunHandle(
        task_id="trig_x",
        invocation_id="inv_x",
        job={"session_id": "s1"},
        event={"source": "System", "type": "trigger"},
    )
    assert handle.task_id == "trig_x"
    assert handle.job["session_id"] == "s1"

    busy = Busy(reason="already_in_run")
    assert busy.reason == "already_in_run"

    res = TriggerResult(status="enqueued", task_id="trig_x", invocation_id="inv_x")
    assert res.status == "enqueued"
    assert res.reason is None
    assert res.dedup_key is None


def test_send_stream_context_has_job_field():
    from src.services.stream_service import SendStreamContext

    ctx = SendStreamContext(
        task_id="t",
        invocation_id="i",
        mode="direct",
        user_msg={},
        job={"session_id": "s1"},
    )
    assert ctx.job == {"session_id": "s1"}


def _make_service():
    from src.services.stream_service import ChatStreamService

    sessions_service = MagicMock()
    sessions_service.try_acquire_session_run.return_value = (True, None)
    events_service = MagicMock()
    events_service.get_latest_scope_event_id.return_value = 42
    service = ChatStreamService(
        sessions_service=sessions_service,
        events_service=events_service,
        deploy_state_service=MagicMock(),
    )
    return service, sessions_service, events_service


def test_prepare_run_snapshots_boundary_before_writing_event():
    """历史边界必须在写发起事件之前快照（否则注入消息被算进自身历史）。"""
    from src.services.stream_service import RunHandle

    service, sessions_service, events_service = _make_service()
    call_order = []
    events_service.get_latest_scope_event_id.side_effect = lambda *a, **k: (
        call_order.append("snapshot") or 42
    )

    def writer(task_id, invocation_id):
        call_order.append("write_event")
        event = {"source": "System", "type": "trigger", "task_id": task_id}
        service._events_service.add_history_event("s1", event, user_id="owner-1")
        return event

    handle = service._prepare_run(
        "s1",
        user_id="owner-1",
        user_text="作业完成",
        files=None,
        images=None,
        workspace_paths=None,
        event_writer=writer,
        id_prefix="trig_",
        mode="direct",
        origin="hpc_job",
        delivery={"notify": True},
    )

    assert isinstance(handle, RunHandle)
    assert call_order == ["snapshot", "write_event"]
    assert handle.task_id.startswith("trig_")
    assert handle.invocation_id.startswith("inv_")
    assert handle.job["turn_input"]["pre_turn_history_event_id"] == 42
    assert handle.job["turn_input"]["user_text"] == "作业完成"
    assert handle.job["origin"] == "hpc_job"
    assert handle.job["delivery"] == {"notify": True}
    assert handle.job["user_prompt"] == "作业完成"
    assert handle.job["session_id"] == "s1"
    assert handle.job["task_id"] == handle.task_id


def test_prepare_run_returns_busy_when_lock_held():
    from src.services.stream_service import Busy

    service, sessions_service, events_service = _make_service()
    sessions_service.try_acquire_session_run.return_value = (False, "already_in_run")

    handle = service._prepare_run(
        "s1",
        user_id="owner-1",
        user_text="x",
        files=None,
        images=None,
        workspace_paths=None,
        event_writer=lambda t, i: {},
        id_prefix="trig_",
        mode="direct",
    )
    assert isinstance(handle, Busy)
    assert handle.reason == "already_in_run"
    events_service.add_history_event.assert_not_called()


def test_prepare_run_runs_pre_event_hook_after_lock_before_snapshot():
    service, sessions_service, events_service = _make_service()
    order = []
    sessions_service.try_acquire_session_run.side_effect = lambda sid: (
        order.append("lock") or (True, None)
    )
    events_service.get_latest_scope_event_id.side_effect = lambda *a, **k: (
        order.append("snapshot") or 42
    )

    service._prepare_run(
        "s1",
        user_id="owner-1",
        user_text="x",
        files=None,
        images=None,
        workspace_paths=None,
        event_writer=lambda t, i: {},
        id_prefix="sse_",
        mode="direct",
        pre_event_hook=lambda: order.append("hook"),
    )
    assert order == ["lock", "hook", "snapshot"]


def test_enqueue_run_pushes_job_and_sets_waiting():
    service, sessions_service, events_service = _make_service()
    fake_redis = MagicMock()
    fake_redis.lpush_agent_run_job.return_value = True
    job = {"session_id": "s1", "user_prompt": "hi", "llm": None, "model": None}

    with (
        patch("src.services.stream_service.get_redis_dao", return_value=fake_redis),
        patch("src.services.stream_service.notify_post_async"),
        patch(
            "src.services.stream_service.UserService.get_user_info_for_display",
            return_value={"user_id": "u", "nickname": "n", "email": "e"},
        ),
        patch(
            "src.services.stream_service.get_worker_registry_service",
            return_value=MagicMock(count_active_runs=MagicMock(return_value=0)),
        ),
    ):
        ok = service._enqueue_run("s1", job)

    assert ok is True
    sessions_service.set_session_status.assert_any_call("s1", "waiting")
    fake_redis.lpush_agent_run_job.assert_called_once_with(job)


def test_enqueue_run_rolls_back_on_lpush_failure():
    service, sessions_service, events_service = _make_service()
    fake_redis = MagicMock()
    fake_redis.lpush_agent_run_job.return_value = False
    job = {"session_id": "s1", "user_prompt": "hi", "llm": None, "model": None}

    with (
        patch("src.services.stream_service.get_redis_dao", return_value=fake_redis),
        patch("src.services.stream_service.notify_post_async"),
        patch(
            "src.services.stream_service.UserService.get_user_info_for_display",
            return_value={"user_id": "u", "nickname": "n", "email": "e"},
        ),
        patch(
            "src.services.stream_service.get_worker_registry_service",
            return_value=MagicMock(count_active_runs=MagicMock(return_value=0)),
        ),
    ):
        ok = service._enqueue_run("s1", job)

    assert ok is False
    sessions_service.set_session_status.assert_any_call("s1", "idle")
    fake_redis.delete_session_run_queued.assert_called_once_with("s1")


def _make_trigger_service(owner="owner-1"):
    from src.services.stream_service import ChatStreamService

    sessions_service = MagicMock()
    sessions_service.get_session_user_id.return_value = owner
    sessions_service.try_acquire_session_run.return_value = (True, None)
    events_service = MagicMock()
    events_service.get_latest_scope_event_id.return_value = 10
    service = ChatStreamService(
        sessions_service=sessions_service,
        events_service=events_service,
        deploy_state_service=MagicMock(),
    )
    return service, sessions_service, events_service


def _trigger_patches(fake_redis):
    return (
        patch("src.services.stream_service.get_redis_dao", return_value=fake_redis),
        patch("src.services.stream_service.notify_post_async"),
        patch(
            "src.services.stream_service.UserService.get_user_info_for_display",
            return_value={"user_id": "u", "nickname": "n", "email": "e"},
        ),
        patch(
            "src.services.stream_service.get_worker_registry_service",
            return_value=MagicMock(count_active_runs=MagicMock(return_value=0)),
        ),
    )


def test_trigger_run_error_when_no_owner():
    from src.services.stream_service import TriggerResult

    service, sessions_service, events_service = _make_trigger_service(owner=None)
    res = service.trigger_run("s1", "作业完成", origin="hpc_job")
    assert isinstance(res, TriggerResult)
    assert res.status == "error"
    events_service.add_history_event.assert_not_called()


def test_trigger_run_enqueues_and_writes_system_event():
    service, sessions_service, events_service = _make_trigger_service()
    fake_redis = MagicMock()
    fake_redis.dedup_key_exists.return_value = False
    fake_redis.lpush_agent_run_job.return_value = True
    p1, p2, p3, p4 = _trigger_patches(fake_redis)
    with p1, p2, p3, p4:
        res = service.trigger_run(
            "s1",
            "作业123已完成，请下载并分析结果",
            origin="hpc_job",
            dedup_key="job:123:done",
            delivery=None,
        )
    assert res.status == "enqueued"
    assert res.task_id.startswith("trig_")
    written = events_service.add_history_event.call_args.args[1]
    assert written["source"] == "System"
    assert written["type"] == "trigger"
    assert written["content"] == {
        "text": "作业123已完成，请下载并分析结果",
        "origin": "hpc_job",
    }
    pushed = fake_redis.lpush_agent_run_job.call_args.args[0]
    assert pushed["origin"] == "hpc_job"
    fake_redis.mark_dedup_key_nx.assert_called_once()
    assert fake_redis.mark_dedup_key_nx.call_args.args[0] == "job:123:done"


def test_trigger_run_deduped_short_circuits():
    service, sessions_service, events_service = _make_trigger_service()
    fake_redis = MagicMock()
    fake_redis.dedup_key_exists.return_value = True
    p1, p2, p3, p4 = _trigger_patches(fake_redis)
    with p1, p2, p3, p4:
        res = service.trigger_run("s1", "x", origin="hpc_job", dedup_key="job:123:done")
    assert res.status == "deduped"
    events_service.add_history_event.assert_not_called()
    fake_redis.lpush_agent_run_job.assert_not_called()
    fake_redis.mark_dedup_key_nx.assert_not_called()


def test_trigger_run_busy_does_not_mark_dedup():
    service, sessions_service, events_service = _make_trigger_service()
    sessions_service.try_acquire_session_run.return_value = (False, "already_in_run")
    fake_redis = MagicMock()
    fake_redis.dedup_key_exists.return_value = False
    p1, p2, p3, p4 = _trigger_patches(fake_redis)
    with p1, p2, p3, p4:
        res = service.trigger_run("s1", "x", origin="loop", dedup_key="loop:1:3")
    assert res.status == "busy"
    fake_redis.lpush_agent_run_job.assert_not_called()
    fake_redis.mark_dedup_key_nx.assert_not_called()


def test_trigger_run_accepts_delivery_spec():
    from src.models.chat import DeliverySpec

    service, sessions_service, events_service = _make_trigger_service()
    fake_redis = MagicMock()
    fake_redis.dedup_key_exists.return_value = False
    fake_redis.lpush_agent_run_job.return_value = True
    p1, p2, p3, p4 = _trigger_patches(fake_redis)
    with p1, p2, p3, p4:
        res = service.trigger_run(
            "s1", "x", origin="hpc_job", delivery=DeliverySpec(notify=False)
        )
    assert res.status == "enqueued"
    pushed = fake_redis.lpush_agent_run_job.call_args.args[0]
    assert pushed["delivery"] == {"notify": False}
