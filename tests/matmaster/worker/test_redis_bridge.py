"""Tests for the Redis-to-cancellation bridge used by the worker."""

from __future__ import annotations

import signal
import time
from unittest.mock import MagicMock, patch


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

        removed_context_key = "current_input" "_context"
        removed_boundary_key = "pre_query" "_scope_event_id"
        payload = {
            "session_id": "sid-1",
            "task_id": "task-1",
            "invocation_id": "inv-1",
            "user_prompt": "hello",
            "mode": "direct",
            "remote_workdir": "/share/case",
            "session_directory_source": "request",
            "bohrium_required": True,
            removed_context_key: {
                "user_text": "legacy only",
                removed_boundary_key: 99,
            },
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
            def __init__(
                self, controller, session_id, task_id, interval=0.5, **_kwargs
            ):
                bridge_events["controller"] = controller
                bridge_events["session_id"] = session_id
                bridge_events["task_id"] = task_id

            def start(self) -> None:
                bridge_events["started"] = True

            def stop(self) -> None:
                bridge_events["stopped"] = True

        with (
            patch.object(mod, "_drain_requested", True),
            patch.object(mod, "_current_session_id", None),
            patch.object(mod, "_active_controller", None, create=True),
            patch.object(mod, "get_redis_dao", return_value=redis_dao),
            patch.object(mod, "get_sessions_service", return_value=sessions_service),
            patch.object(mod, "get_agent_run_service", return_value=agent_run_service),
            patch.object(mod, "UserService") as user_service_cls,
            patch.object(mod, "get_worker_registry_service") as registry_fn,
            patch.object(mod, "notify_post_async"),
            patch.object(mod, "send_session_complete_email_async"),
            patch.object(mod, "LogContext") as log_context,
            patch.object(mod, "RedisCancellationBridge", FakeBridge),
            patch.object(mod, "get_worker_id", return_value="worker-1"),
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
        assert observed["remote_workdir"] == "/share/case"
        assert observed["bohrium_required"] is True
        assert observed["turn_input"] is None

    def test_run_worker_loop_resolves_byok_reference_before_run_agent(self) -> None:
        from matmaster.config.llm import LLMProfileConfig
        from src.models.byok import BYOKResolvedWorkerRun
        from src.worker import agent_worker as mod

        payload = {
            "session_id": "sid-1",
            "task_id": "task-1",
            "invocation_id": "inv-1",
            "user_prompt": "hello",
            "mode": "direct",
            "byok": {"config_id": 12, "version": 3},
        }
        redis_dao = MagicMock()
        redis_dao.create_client.return_value = True
        redis_dao.blpop_agent_run_job.side_effect = [payload]
        redis_dao.llen_agent_run_queue.return_value = 0

        sessions_service = MagicMock()
        sessions_service.get_session_user_id.return_value = "user-1"
        sessions_service.try_acquire_session_run.return_value = (True, None)

        byok_profile = LLMProfileConfig(
            provider="openai",
            model="model-a",
            api_key="sk-test",
            base_url="https://api.example.com/v1",
        )
        resolver = MagicMock()
        resolver.resolve_for_worker_run.return_value = BYOKResolvedWorkerRun(
            config_id=12,
            version=3,
            model="model-a",
            display_name="Research Proxy",
            profile=byok_profile,
        )

        observed: dict[str, object] = {}

        async def fake_run_agent(**kwargs):
            observed.update(kwargs)
            return (True, 0)

        agent_run_service = MagicMock()
        agent_run_service.init_playground_sync.return_value = None
        agent_run_service.run_agent = fake_run_agent

        class FakeBridge:
            def __init__(self, *_args, **_kwargs) -> None:
                return None

            def start(self) -> None:
                return None

            def stop(self) -> None:
                return None

        with (
            patch.object(mod, "_drain_requested", True),
            patch.object(mod, "_current_session_id", None),
            patch.object(mod, "_active_controller", None, create=True),
            patch.object(mod, "get_redis_dao", return_value=redis_dao),
            patch.object(mod, "get_sessions_service", return_value=sessions_service),
            patch.object(mod, "get_agent_run_service", return_value=agent_run_service),
            patch.object(mod, "get_byok_model_resolver", return_value=resolver, create=True),
            patch.object(mod, "UserService") as user_service_cls,
            patch.object(mod, "get_worker_registry_service") as registry_fn,
            patch.object(mod, "notify_post_async"),
            patch.object(mod, "send_session_complete_email_async"),
            patch.object(mod, "LogContext"),
            patch.object(mod, "RedisCancellationBridge", FakeBridge),
            patch.object(mod, "get_worker_id", return_value="worker-1"),
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

        resolver.resolve_for_worker_run.assert_called_once_with(
            user_id="user-1",
            config_id=12,
            expected_version=3,
            mode="direct",
            has_images=False,
        )
        assert observed["byok_profile"] is byok_profile
        assert observed["byok_config_id"] == 12
        assert observed["byok_config_version"] == 3
        assert observed["billing_mode"] == "byok"

    def test_run_worker_loop_byok_version_mismatch_emits_error_and_skips_run_agent(
        self,
    ) -> None:
        from src.services.byok_model_resolver import BYOKResolveError
        from src.worker import agent_worker as mod

        payload = {
            "session_id": "sid-1",
            "task_id": "task-1",
            "invocation_id": "inv-1",
            "user_prompt": "hello",
            "mode": "direct",
            "byok": {"config_id": 12, "version": 3},
        }
        redis_dao = MagicMock()
        redis_dao.create_client.return_value = True
        redis_dao.blpop_agent_run_job.side_effect = [payload]
        redis_dao.llen_agent_run_queue.return_value = 0

        sessions_service = MagicMock()
        sessions_service.get_session_user_id.return_value = "user-1"
        sessions_service.try_acquire_session_run.return_value = (True, None)

        resolver = MagicMock()
        resolver.resolve_for_worker_run.side_effect = BYOKResolveError(
            "自定义模型配置已变更，请重新发送消息。",
            http_status=409,
            error_code="byok_version_mismatch",
        )

        agent_run_service = MagicMock()
        agent_run_service.init_playground_sync.return_value = None
        agent_run_service.run_agent = MagicMock()

        class FakeBridge:
            def __init__(self, *_args, **_kwargs) -> None:
                return None

            def start(self) -> None:
                return None

            def stop(self) -> None:
                return None

        with (
            patch.object(mod, "_drain_requested", True),
            patch.object(mod, "_current_session_id", None),
            patch.object(mod, "_active_controller", None, create=True),
            patch.object(mod, "get_redis_dao", return_value=redis_dao),
            patch.object(mod, "get_sessions_service", return_value=sessions_service),
            patch.object(mod, "get_agent_run_service", return_value=agent_run_service),
            patch.object(mod, "get_byok_model_resolver", return_value=resolver, create=True),
            patch.object(mod, "UserService") as user_service_cls,
            patch.object(mod, "get_worker_registry_service") as registry_fn,
            patch.object(mod, "notify_post_async"),
            patch.object(mod, "send_session_complete_email_async"),
            patch.object(mod, "LogContext"),
            patch.object(mod, "RedisCancellationBridge", FakeBridge),
            patch.object(mod, "get_worker_id", return_value="worker-1"),
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

        agent_run_service.run_agent.assert_not_called()
        published = [call.args[1] for call in redis_dao.publish_stream_event.call_args_list]
        error_payload = next(item for item in published if item["type"] == "error")
        closed_payload = next(
            item for item in published if item["type"] == "stream_closed"
        )
        assert error_payload["content"] == "自定义模型配置已变更，请重新发送消息。"
        assert error_payload["error_code"] == "byok_version_mismatch"
        assert closed_payload["treat_as_failure"] is True
        assert closed_payload["end_reason"] == "byok_version_mismatch"

    def test_main_sigterm_handler_drains_without_cancelling_active_controller(
        self,
    ) -> None:
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

        with (
            patch.object(mod, "_current_session_id", "sid-1"),
            patch.object(mod, "_drain_requested", False),
            patch.object(mod, "_active_controller", active_controller, create=True),
            patch.object(mod, "setup_logging"),
            patch.object(mod.signal, "signal", side_effect=fake_signal),
            patch.object(mod.threading, "Thread", FakeThread),
            patch.object(mod, "_run_worker_loop", side_effect=fake_run_loop),
            patch.object(mod, "_publish_run_interrupted_deploy") as publish_mock,
            patch.object(mod, "get_worker_id", return_value="worker-1"),
        ):
            mod.main()

            assert mod._drain_requested is True

        assert captured["signal"] == signal.SIGTERM
        assert captured["thread_started"] is True
        publish_mock.assert_called_once_with("sid-1")
        active_controller.cancel.assert_not_called()
