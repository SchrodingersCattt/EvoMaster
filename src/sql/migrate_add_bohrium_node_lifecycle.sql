-- Bohrium Node 槽位从“最后使用时间缓存”升级为 invocation lease 状态机。
-- 上线前先检查 node_id 是否重复；ADD UNIQUE INDEX 会在脏数据存在时显式失败。
ALTER TABLE `evo_bohrium_nodes`
    MODIFY COLUMN `node_id` INT NULL COMMENT 'Bohrium 节点 ID；creating 初期为空',
    ADD COLUMN `state` VARCHAR(32) NOT NULL DEFAULT 'ready'
        COMMENT 'creating/ready/stopping/paused/destroying/idle' AFTER `node_id`,
    ADD COLUMN `creating_invocation_id` VARCHAR(64) NULL AFTER `state`,
    ADD COLUMN `creating_lease_token` VARCHAR(64) NULL AFTER `creating_invocation_id`,
    ADD COLUMN `creating_lease_expires_at` DATETIME NULL AFTER `creating_lease_token`,
    ADD COLUMN `lifecycle_policy` VARCHAR(32) NOT NULL DEFAULT 'run_end'
        AFTER `creating_lease_expires_at`,
    ADD COLUMN `idle_timeout_seconds` INT NULL AFTER `lifecycle_policy`,
    ADD COLUMN `idle_expires_at` DATETIME NULL AFTER `idle_timeout_seconds`,
    ADD COLUMN `last_error` TEXT NULL AFTER `idle_expires_at`,
    ADD UNIQUE INDEX `uk_node_id` (`node_id`),
    ADD INDEX `idx_state_creating_expiry` (`state`, `creating_lease_expires_at`),
    ADD INDEX `idx_state_idle_expiry` (`state`, `idle_expires_at`);

CREATE TABLE `bohrium_node_leases` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    `node_slot_id` BIGINT UNSIGNED NOT NULL,
    `session_id` VARCHAR(255) NOT NULL,
    `invocation_id` VARCHAR(64) NOT NULL,
    `lease_token` VARCHAR(64) NOT NULL,
    `lease_expires_at` DATETIME NOT NULL,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY `uk_invocation_id` (`invocation_id`),
    INDEX `idx_slot_expiry` (`node_slot_id`, `lease_expires_at`),
    INDEX `idx_lease_expiry` (`lease_expires_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Bohrium Node invocation 共享租约';
