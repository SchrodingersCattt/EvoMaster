-- 飞书 open_id 与平台 user_id 绑定（HTTP 事件回调链路使用）
CREATE TABLE IF NOT EXISTS `evo_feishu_user_binding` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    `feishu_open_id` VARCHAR(128) NOT NULL COMMENT '飞书用户 open_id（应用维度）',
    `user_id` VARCHAR(255) NOT NULL COMMENT '平台用户 ID（与 X-User-Id 一致）',
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY `uk_feishu_open_id` (`feishu_open_id`),
    KEY `idx_user_id` (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='飞书用户绑定';
