import json
import logging
from functools import lru_cache
from typing import Any

import pymysql

from src.base.base_table import BaseTable

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class ModelPriceCatalogTable(BaseTable):
    """模型价格目录表。"""

    table_name = "evo_model_price_catalog"

    def get_active_price(
        self,
        *,
        provider: str,
        model: str,
        model_profile: str | None,
        model_route: str | None,
    ) -> dict[str, Any] | None:
        """取当前生效价格，优先 route/profile 精确匹配。"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT provider, model, model_profile, model_route, currency, unit,
                           input_price_micro_per_million,
                           output_price_micro_per_million,
                           cache_read_price_micro_per_million,
                           cache_write_price_micro_per_million,
                           version, effective_from, effective_to
                    FROM {self.table_name}
                    WHERE provider = %s
                      AND model = %s
                      AND status = 'active'
                      AND effective_from <= NOW()
                      AND (effective_to IS NULL OR effective_to > NOW())
                      AND (model_profile = '' OR model_profile = %s)
                      AND (model_route = '' OR model_route = %s)
                    ORDER BY
                      CASE WHEN model_route = %s THEN 2 ELSE 0 END
                      + CASE WHEN model_profile = %s THEN 1 ELSE 0 END DESC,
                      effective_from DESC,
                      id DESC
                    LIMIT 1
                    """,
                    (
                        provider,
                        model,
                        model_profile,
                        model_route,
                        model_route,
                        model_profile,
                    ),
                )
                row = cursor.fetchone()
                return dict(row) if row else None


class LLMUsageLedgerTable(BaseTable):
    """LLM 用量金额流水表。"""

    table_name = "evo_llm_usage_ledger"

    _insert_columns = (
        "idempotency_key",
        "billing_mode",
        "pricing_status",
        "user_id",
        "org_id",
        "project_id",
        "session_id",
        "task_id",
        "invocation_id",
        "spawn_id",
        "call_index",
        "call_kind",
        "provider",
        "model",
        "model_profile",
        "model_route",
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "uncached_input_tokens",
        "currency",
        "price_version",
        "input_price_micro_per_million",
        "output_price_micro_per_million",
        "cache_read_price_micro_per_million",
        "cache_write_price_micro_per_million",
        "input_amount_micro",
        "output_amount_micro",
        "cache_read_amount_micro",
        "cache_write_amount_micro",
        "total_amount_micro",
        "usage",
        "usage_vendor",
    )

    def insert_usage(self, row: dict[str, Any]) -> bool:
        """写入一条流水。幂等冲突视为成功且返回 False。"""
        payload = dict(row)
        payload["usage"] = json.dumps(payload.get("usage") or {}, ensure_ascii=False)
        usage_vendor = payload.get("usage_vendor")
        payload["usage_vendor"] = (
            json.dumps(usage_vendor, ensure_ascii=False)
            if usage_vendor is not None
            else None
        )
        columns = self._insert_columns
        placeholders = ", ".join(["%s"] * len(columns))
        col_sql = ", ".join(f"`{col}`" for col in columns)
        values = tuple(payload.get(col) for col in columns)
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        f"""
                        INSERT INTO {self.table_name}
                        ({col_sql})
                        VALUES ({placeholders})
                        """,
                        values,
                    )
                    conn.commit()
                    return cursor.rowcount > 0
        except pymysql.err.IntegrityError as exc:
            if exc.args and exc.args[0] == 1062:
                logger.info(
                    "llm usage ledger duplicate ignored idempotency_key=%s",
                    row.get("idempotency_key"),
                )
                return False
            raise

    def summarize_for_reconciliation(
        self,
        *,
        start_at: str | None = None,
        end_at: str | None = None,
        billing_mode: str = "dry_run",
    ) -> list[dict[str, Any]]:
        """按模型聚合 dry-run 流水，用于供应商账单对账。"""
        where = ["billing_mode = %s"]
        params: list[Any] = [billing_mode]
        if start_at:
            where.append("created_at >= %s")
            params.append(start_at)
        if end_at:
            where.append("created_at < %s")
            params.append(end_at)
        where_sql = " AND ".join(where)
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT provider, model, model_profile, model_route, currency,
                           COUNT(*) AS call_count,
                           SUM(input_tokens) AS input_tokens,
                           SUM(output_tokens) AS output_tokens,
                           SUM(cache_read_tokens) AS cache_read_tokens,
                           SUM(cache_write_tokens) AS cache_write_tokens,
                           SUM(uncached_input_tokens) AS uncached_input_tokens,
                           SUM(total_amount_micro) AS total_amount_micro
                    FROM {self.table_name}
                    WHERE {where_sql}
                    GROUP BY provider, model, model_profile, model_route, currency
                    ORDER BY total_amount_micro DESC
                    """,
                    tuple(params),
                )
                return [dict(row) for row in cursor.fetchall()]


@lru_cache(maxsize=1)
def get_model_price_catalog_table() -> ModelPriceCatalogTable:
    return ModelPriceCatalogTable()


@lru_cache(maxsize=1)
def get_llm_usage_ledger_table() -> LLMUsageLedgerTable:
    return LLMUsageLedgerTable()
