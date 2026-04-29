"""飞书应用配置表（多租户）：每个 tenant_id 对应一组飞书凭据。"""

import logging
from dataclasses import dataclass
from typing import Any

from pymysql import Error

from src.base.base_table import BaseTable

logger = logging.getLogger(__name__)


@dataclass
class FeishuAppConfig:
    tenant_id: str
    app_id: str
    app_secret: str
    encrypt_key: str | None = None
    verify_token: str | None = None
    created_by: str | None = None


class FeishuAppConfigTable(BaseTable):
    table_name = 'evo_feishu_app_config'

    def get_by_tenant_id(self, tenant_id: str) -> FeishuAppConfig | None:
        row = self.find_one({'tenant_id': tenant_id.strip()})
        if not row:
            return None
        return _row_to_config(row)

    def upsert(self, cfg: FeishuAppConfig) -> bool:
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        f"""
                        INSERT INTO {self.table_name}
                            (tenant_id, app_id, app_secret, encrypt_key, verify_token, created_by, created_at, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW())
                        ON DUPLICATE KEY UPDATE
                            app_id       = VALUES(app_id),
                            app_secret   = VALUES(app_secret),
                            encrypt_key  = VALUES(encrypt_key),
                            verify_token = VALUES(verify_token),
                            updated_at   = NOW()
                        """,
                        (
                            cfg.tenant_id.strip(),
                            cfg.app_id.strip(),
                            cfg.app_secret.strip(),
                            cfg.encrypt_key.strip() if cfg.encrypt_key else None,
                            cfg.verify_token.strip() if cfg.verify_token else None,
                            cfg.created_by,
                        ),
                    )
                conn.commit()
                return True
        except Error as e:
            logger.warning('feishu app_config upsert failed: %s', e)
            return False

    def delete_by_tenant_id(self, tenant_id: str) -> bool:
        return self.delete({'tenant_id': tenant_id.strip()})


def _row_to_config(row: dict[str, Any]) -> FeishuAppConfig:
    return FeishuAppConfig(
        tenant_id=str(row['tenant_id']),
        app_id=str(row['app_id']),
        app_secret=str(row['app_secret']),
        encrypt_key=row.get('encrypt_key'),
        verify_token=row.get('verify_token'),
        created_by=row.get('created_by'),
    )


def get_feishu_app_config_table() -> FeishuAppConfigTable:
    return FeishuAppConfigTable()
