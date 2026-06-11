"""Worker 完成通知按 job.delivery 开关。"""

from src.worker.agent_worker import _should_notify_completion


def test_should_notify_when_delivery_absent():
    assert _should_notify_completion(None) is True


def test_should_notify_when_delivery_notify_true():
    assert _should_notify_completion({"notify": True}) is True


def test_should_not_notify_when_delivery_notify_false():
    assert _should_notify_completion({"notify": False}) is False


def test_should_notify_defaults_true_for_malformed_delivery():
    assert _should_notify_completion({}) is True
