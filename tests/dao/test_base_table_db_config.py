from __future__ import annotations

import pytest

from src.base import base_table as base_table_module
from src.base.base_table import BaseTable


class _Probe(BaseTable):
    table_name = "probe_tbl"

    def init_table(self) -> None:
        return None


def test_base_table_uses_injected_db_config() -> None:
    injected = {"host": "h", "port": 1, "user": "u", "database": "d"}
    t = _Probe(db_config=injected)
    assert t.db_config is injected


def test_base_table_defaults_to_global_db_config() -> None:
    from src.utils.constant import DB_CONFIG

    t = _Probe()
    assert t.db_config is DB_CONFIG


def test_base_table_initialization_logs_only_connection_error_type(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class _InitializingProbe(BaseTable):
        table_name = "probe_tbl"

    def fail_connect(**_kwargs):
        raise RuntimeError("database failed with secret-ak")

    monkeypatch.setattr(base_table_module.pymysql, "connect", fail_connect)
    caplog.set_level("ERROR", logger=base_table_module.__name__)

    with pytest.raises(RuntimeError, match="database failed"):
        _InitializingProbe(
            db_config={"host": "h", "port": 1, "user": "u", "database": "d"}
        )

    assert "error_type=RuntimeError" in caplog.text
    assert "database failed with secret-ak" not in caplog.text
    assert "secret-ak" not in caplog.text
