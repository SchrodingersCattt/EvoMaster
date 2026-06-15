"""RedisDao.publish_user_wakeup 与 user_wakeup_channel 行为测试。"""

import json
from unittest.mock import MagicMock, patch


def test_user_wakeup_channel_format():
    from src.dao.redis_dao import user_wakeup_channel

    assert user_wakeup_channel("user-1") == "chat:user:user-1:wakeup"
    assert user_wakeup_channel(" user-2 ") == "chat:user:user-2:wakeup"


def test_publish_user_wakeup_uses_user_channel_and_serializes_payload():
    from src.dao.redis_dao import RedisDao

    dao = RedisDao()
    fake_client = MagicMock()
    payload = {
        "source": "System",
        "type": "session_wakeup",
        "reason": "trigger_enqueued",
        "session_id": "s1",
    }
    with patch.object(dao, "get_publish_client", return_value=fake_client):
        ok = dao.publish_user_wakeup("user-1", payload)

    assert ok is True
    fake_client.publish.assert_called_once()
    channel, message = fake_client.publish.call_args.args
    assert channel == "chat:user:user-1:wakeup"
    assert json.loads(message) == payload


def test_publish_user_wakeup_false_when_no_client():
    from src.dao.redis_dao import RedisDao

    dao = RedisDao()
    with patch.object(dao, "get_publish_client", return_value=None):
        ok = dao.publish_user_wakeup("user-1", {"session_id": "s1"})
    assert ok is False
