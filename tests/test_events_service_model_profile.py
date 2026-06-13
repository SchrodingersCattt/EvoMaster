"""ChatEventsService.get_last_resolved_model_profile 透传与失败兜底测试。"""

import logging
from unittest.mock import MagicMock

from src.services.events_service import ChatEventsService


def test_delegates_to_dao():
    table = MagicMock()
    table.get_last_resolved_model_profile.return_value = "matmaster/qwen3.7-max"
    svc = ChatEventsService(events_table=table, sessions_service=MagicMock())

    assert svc.get_last_resolved_model_profile("s1") == "matmaster/qwen3.7-max"
    table.get_last_resolved_model_profile.assert_called_once_with("s1")


def test_returns_none_and_warns_on_dao_error(caplog):
    table = MagicMock()
    table.get_last_resolved_model_profile.side_effect = RuntimeError("db down")
    svc = ChatEventsService(events_table=table, sessions_service=MagicMock())

    with caplog.at_level(logging.WARNING):
        result = svc.get_last_resolved_model_profile("s1")

    assert result is None
    assert "get_last_resolved_model_profile" in caplog.text
