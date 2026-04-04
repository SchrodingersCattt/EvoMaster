"""Tests for the Redis-to-cancellation bridge used by the worker."""

from __future__ import annotations

import time
from unittest.mock import MagicMock


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
