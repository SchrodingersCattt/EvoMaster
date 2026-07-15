-- Bohrium 节点槽位：同一 user/org/project/sku 共享 Node，以 invocation lease 保护并发运行
CREATE TABLE IF NOT EXISTS `evo_bohrium_nodes` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '自增ID',
    `user_id` VARCHAR(255) NOT NULL COMMENT '用户ID（与 X-User-Id 一致）',
    `org_id` VARCHAR(255) NOT NULL COMMENT '组织ID（与 X-Org-Id 一致，用于销毁时拉取 access_key）',
    `project_id` INT NOT NULL COMMENT 'Bohrium 项目 ID',
    `sku_id` INT NOT NULL COMMENT 'Bohrium 节点 SKU ID',
    `node_id` INT NULL COMMENT 'Bohrium 节点 ID；creating 初期为空',
    `state` VARCHAR(32) NOT NULL DEFAULT 'ready' COMMENT 'creating/ready/stopping/paused/destroying/idle',
    `creating_invocation_id` VARCHAR(64) NULL,
    `creating_lease_token` VARCHAR(64) NULL,
    `creating_lease_expires_at` DATETIME NULL,
    `lifecycle_policy` VARCHAR(32) NOT NULL DEFAULT 'run_end',
    `idle_timeout_seconds` INT NULL,
    `idle_expires_at` DATETIME NULL,
    `last_error` TEXT NULL,
    `last_used_at` DATETIME NULL COMMENT '最后一次被使用时间，用于空闲回收',
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    UNIQUE KEY `uk_user_org_project_sku` (`user_id`, `org_id`, `project_id`, `sku_id`),
    UNIQUE KEY `uk_node_id` (`node_id`),
    INDEX `idx_last_used_at` (`last_used_at`),
    INDEX `idx_state_creating_expiry` (`state`, `creating_lease_expires_at`),
    INDEX `idx_state_idle_expiry` (`state`, `idle_expires_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Bohrium 可复用节点缓存，多 session 可共享同一 node';

CREATE TABLE IF NOT EXISTS `bohrium_node_leases` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    `node_slot_id` BIGINT UNSIGNED NOT NULL,
    `session_id` VARCHAR(255) NOT NULL,
    `invocation_id` VARCHAR(64) NOT NULL,
    `lease_token` VARCHAR(64) NOT NULL,
    `lease_expires_at` DATETIME NOT NULL,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY `uk_invocation_id` (`invocation_id`),
    INDEX `idx_slot_expiry` (`node_slot_id`, `lease_expires_at`),
    INDEX `idx_lease_expiry` (`lease_expires_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Bohrium Node invocation 共享租约';
