from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Any

from src.base.base_table import BaseTable

logger = logging.getLogger(__name__)

_JSON_FIELDS = frozenset({"params", "extra_body", "prompt_cache"})


def _dump_json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _load_json(value: Any) -> dict[str, Any]:
    if value is None or value == "":
        return {}
    if isinstance(value, dict):
        return value
    parsed = json.loads(value)
    return parsed if isinstance(parsed, dict) else {}


def _prepare_fields(fields: dict[str, Any]) -> dict[str, Any]:
    prepared = dict(fields)
    for field in _JSON_FIELDS:
        if field in prepared:
            prepared[field] = _dump_json(prepared[field])
    return prepared


def _normalize_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    normalized = dict(row)
    for field in _JSON_FIELDS:
        normalized[field] = _load_json(normalized.get(field))
    return normalized


class UserLLMConfigTable(BaseTable):
    table_name = "user_llm_config"

    def create(self, user_id: str, **fields: Any) -> int:
        payload = {"user_id": user_id, **_prepare_fields(fields)}
        columns = ", ".join(payload)
        placeholders = ", ".join(["%s"] * len(payload))
        params = tuple(payload.values())
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"INSERT INTO {self.table_name} ({columns}) VALUES ({placeholders})",
                    params,
                )
                conn.commit()
                return int(cursor.lastrowid)

    def get(self, user_id: str, config_id: int) -> dict[str, Any] | None:
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"SELECT * FROM {self.table_name} WHERE user_id = %s AND id = %s LIMIT 1",
                    (user_id, config_id),
                )
                return _normalize_row(cursor.fetchone())

    def get_for_run(self, user_id: str, config_id: int) -> dict[str, Any] | None:
        return self.get(user_id, config_id)

    def list_by_user(self, user_id: str) -> list[dict[str, Any]]:
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"SELECT * FROM {self.table_name} WHERE user_id = %s ORDER BY updated_at DESC, id DESC",
                    (user_id,),
                )
                return [
                    normalized
                    for row in (cursor.fetchall() or [])
                    if (normalized := _normalize_row(row)) is not None
                ]

    def update(self, user_id: str, config_id: int, **fields: Any) -> bool:
        if not fields:
            return True
        payload = _prepare_fields(fields)
        set_clause = ", ".join(f"{field} = %s" for field in payload)
        sql = (
            f"UPDATE {self.table_name} SET {set_clause}, "
            "version = version + 1, updated_at = NOW() "
            "WHERE user_id = %s AND id = %s"
        )
        params = tuple(payload.values()) + (user_id, config_id)
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(sql, params)
                conn.commit()
                return cursor.rowcount > 0

    def delete(self, user_id: str, config_id: int) -> bool:
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"DELETE FROM {self.table_name} WHERE user_id = %s AND id = %s",
                    (user_id, config_id),
                )
                conn.commit()
                return cursor.rowcount > 0


@lru_cache(maxsize=1)
def get_user_llm_config_table() -> UserLLMConfigTable:
    return UserLLMConfigTable()
