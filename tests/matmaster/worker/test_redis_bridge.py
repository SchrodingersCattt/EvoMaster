"""Tests for the Redis-to-cancellation bridge used by the worker."""

from __future__ import annotations

import signal
import time
from unittest.mock import AsyncMock, MagicMock, patch


class TestRedisCancellationBridge:
    def test_bridge_detects_stop_and_cancels(self) -> None:
        from matmaster.types.cancellation import CancellationController
        from src.worker.agent_worker import RedisCancellationBridge

        ctrl = CancellationController()
        mock_dao = MagicMock()
        mock_dao.is_stop_requested.side_effect = [False, True]

        bridge = RedisCancellationBridge(
            ctrl,
            "sid",
            "tid",
            interval=0.05,
            _dao_override=mock_dao,
        )
        bridge.start()
        time.sleep(0.3)
        bridge.stop()

        assert ctrl.token.is_cancelled is True

    def test_bridge_stops_cleanly_on_normal_completion(self) -> None:
        from matmaster.types.cancellation import CancellationController
        from src.worker.agent_worker import RedisCancellationBridge

        ctrl = CancellationController()
        mock_dao = MagicMock()
        mock_dao.is_stop_requested.return_value = False

        bridge = RedisCancellationBridge(
            ctrl,
            "sid",
            "tid",
            interval=0.05,
            _dao_override=mock_dao,
        )
        bridge.start()
        time.sleep(0.1)
        bridge.stop()

        assert bridge._thread is not None
        assert not bridge._thread.is_alive()
        assert ctrl.token.is_cancelled is False

    def test_bridge_stop_is_idempotent(self) -> None:
        from matmaster.types.cancellation import CancellationController
        from src.worker.agent_worker import RedisCancellationBridge

        ctrl = CancellationController()
        mock_dao = MagicMock()
        mock_dao.is_stop_requested.return_value = False

        bridge = RedisCancellationBridge(
            ctrl,
            "sid",
            "tid",
            interval=0.05,
            _dao_override=mock_dao,
        )
        bridge.start()
        bridge.stop()
        bridge.stop()


class TestAgentWorkerCancellationIntegration:
    def test_run_worker_loop_passes_cancel_token_and_cleans_up_controller(self) -> None:
        from src.worker import agent_worker as mod

        payload = {
            "session_id": "sid-1",
            "task_id": "task-1",
            "user_prompt": "hello",
            "mode": "direct",
        }
        redis_dao = MagicMock()
        redis_dao.create_client.return_value = True
        redis_dao.blpop_agent_run_job.side_effect = [payload]
        redis_dao.llen_agent_run_queue.return_value = 0

        sessions_service = MagicMock()
        sessions_service.get_session_user_id.return_value = "user-1"
        sessions_service.try_acquire_session_run.return_value = (True, None)

        observed: dict[str, object] = {}

        async def fake_run_agent(**kwargs):
            observed.update(kwargs)
            return (True, 0)

        agent_run_service = MagicMock()
        agent_run_service.init_playground_sync.return_value = None
        agent_run_service.run_agent = fake_run_agent

        bridge_events: dict[str, object] = {}

        class FakeBridge:
            def __init__(self, controller, session_id, task_id, interval=0.5, **_kwargs):
                bridge_events["controller"] = controller
                bridge_events["session_id"] = session_id
                bridge_events["task_id"] = task_id

            def start(self) -> None:
                bridge_events["started"] = True

            def stop(self) -> None:
                bridge_events["stopped"] = True

        with patch.object(mod, "_drain_requested", True), patch.object(
            mod, "_current_session_id", None
        ), patch.object(mod, "_active_controller", None, create=True), patch.object(
            mod, "get_redis_dao", return_value=redis_dao
        ), patch.object(
            mod, "get_sessions_service", return_value=sessions_service
        ), patch.object(
            mod, "get_agent_run_service", return_value=agent_run_service
        ), patch.object(
            mod, "UserService"
        ) as user_service_cls, patch.object(
            mod, "get_worker_registry_service"
        ) as registry_fn, patch.object(
            mod, "notify_post_async"
        ), patch.object(
            mod, "send_session_complete_email_async"
        ), patch.object(
            mod, "LogContext"
        ) as log_context, patch.object(
            mod, "RedisCancellationBridge", FakeBridge
        ), patch.object(
            mod, "get_worker_id", return_value="worker-1"
        ):
            user_service_cls.get_user_info_for_display.return_value = {
                "user_id": "u1",
                "nickname": "nick",
                "email": "user@example.com",
            }
            registry = MagicMock()
            registry.count_active_runs.return_value = 0
            registry_fn.return_value = registry

            mod._run_worker_loop()

        assert observed["cancel_token"] is bridge_events["controller"].token
        assert bridge_events["session_id"] == "sid-1"
        assert bridge_events["task_id"] == "task-1"
        assert bridge_events["started"] is True
        assert bridge_events["stopped"] is True
        assert mod._active_controller is None
        log_context.clear.assert_called()

    def test_main_sigterm_handler_drains_without_cancelling_active_controller(self) -> None:
        from src.worker import agent_worker as mod

        captured: dict[str, object] = {}
        active_controller = MagicMock()

        class FakeThread:
            def __init__(self, *args, **kwargs) -> None:
                captured["thread_args"] = kwargs

            def start(self) -> None:
                captured["thread_started"] = True

        def fake_signal(sig, handler):
            captured["signal"] = sig
            captured["handler"] = handler

        def fake_run_loop() -> None:
            handler = captured["handler"]
            handler(signal.SIGTERM, object())

        with patch.object(mod, "_current_session_id", "sid-1"), patch.object(
            mod, "_drain_requested", False
        ), patch.object(mod, "_active_controller", active_controller, create=True), patch.object(
            mod, "setup_logging"
        ), patch.object(
            mod.signal, "signal", side_effect=fake_signal
        ), patch.object(
            mod.threading, "Thread", FakeThread
        ), patch.object(
            mod, "_run_worker_loop", side_effect=fake_run_loop
        ), patch.object(
            mod, "_publish_run_interrupted_deploy"
        ) as publish_mock, patch.object(
            mod, "get_worker_id", return_value="worker-1"
        ):
            mod.main()

            assert mod._drain_requested is True

        assert captured["signal"] == signal.SIGTERM
        assert captured["thread_started"] is True
        publish_mock.assert_called_once_with("sid-1")
        active_controller.cancel.assert_not_called()
