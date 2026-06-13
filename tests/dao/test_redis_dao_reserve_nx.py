"""try_reserve_nx 三态语义：True=占位成功 / False=已被占位 / None=无 client 或异常。

区分 False 与 None 是 scheduler fail-closed（skipped_redis 计数 + 告警）的前提，
现有 mark_dedup_key_nx 双态无法区分，故新增而非改造。
"""

from __future__ import annotations

from src.dao.redis_dao import RedisDao


class _FakeClient:
    def __init__(self, result=True, exc=None):
        self.result = result
        self.exc = exc
        self.calls = []

    def set(self, key, value, nx=False, ex=None):
        if self.exc is not None:
            raise self.exc
        self.calls.append({"key": key, "value": value, "nx": nx, "ex": ex})
        return self.result


def _dao_with(monkeypatch, client):
    dao = RedisDao()  # __init__ 惰性，无副作用
    monkeypatch.setattr(dao, "get_command_client", lambda: client)
    return dao


def test_reserve_returns_true_on_first_set(monkeypatch):
    client = _FakeClient(result=True)
    dao = _dao_with(monkeypatch, client)
    assert dao.try_reserve_nx("k1", "1", ttl_sec=60) is True
    assert client.calls == [{"key": "k1", "value": "1", "nx": True, "ex": 60}]


def test_reserve_returns_false_when_already_held(monkeypatch):
    # redis-py 的 SET NX 未设上时返回 None
    dao = _dao_with(monkeypatch, _FakeClient(result=None))
    assert dao.try_reserve_nx("k1", "1", ttl_sec=60) is False


def test_reserve_returns_none_without_client(monkeypatch):
    dao = _dao_with(monkeypatch, None)
    assert dao.try_reserve_nx("k1", "1", ttl_sec=60) is None


def test_reserve_returns_none_on_exception(monkeypatch):
    dao = _dao_with(monkeypatch, _FakeClient(exc=RuntimeError("down")))
    assert dao.try_reserve_nx("k1", "1", ttl_sec=60) is None
