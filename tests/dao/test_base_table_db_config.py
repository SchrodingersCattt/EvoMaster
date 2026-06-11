from __future__ import annotations

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
