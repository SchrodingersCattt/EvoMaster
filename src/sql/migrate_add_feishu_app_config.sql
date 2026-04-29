-- 飞书多租户应用配置表：每个租户存储自己的飞书自建应用凭据
CREATE TABLE IF NOT EXISTS `evo_feishu_app_config` (
    `tenant_id`    VARCHAR(64)  NOT NULL PRIMARY KEY COMMENT '租户标识，用于回调路径 /events/{tenant_id}',
    `app_id`       VARCHAR(128) NOT NULL COMMENT '飞书应用 App ID',
    `app_secret`   VARCHAR(256) NOT NULL COMMENT '飞书应用 App Secret',
    `encrypt_key`  VARCHAR(256) DEFAULT NULL COMMENT '事件订阅 Encrypt Key（可选）',
    `verify_token` VARCHAR(256) DEFAULT NULL COMMENT '事件订阅 Verification Token（可选）',
    `created_by`   VARCHAR(255) DEFAULT NULL COMMENT '创建人 user_id',
    `created_at`   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='飞书应用配置（多租户）';
