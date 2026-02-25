-- Bohrium 节点复用表：按 user_id + org_id + project_id 缓存可复用节点，run 结束只更新 last_used_at 不销毁
CREATE TABLE IF NOT EXISTS `evo_bohrium_nodes` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '自增ID',
    `user_id` VARCHAR(255) NOT NULL COMMENT '用户ID（与 X-User-Id 一致）',
    `org_id` VARCHAR(255) NOT NULL COMMENT '组织ID（与 X-Org-Id 一致，用于销毁时拉取 access_key）',
    `project_id` INT NOT NULL COMMENT 'Bohrium 项目 ID',
    `node_id` INT NOT NULL COMMENT 'Bohrium 节点 ID',
    `last_used_at` DATETIME NULL COMMENT '最后一次被使用时间，用于空闲回收',
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    UNIQUE KEY `uk_user_org_project_node` (`user_id`, `org_id`, `project_id`, `node_id`),
    INDEX `idx_user_org_project` (`user_id`, `org_id`, `project_id`),
    INDEX `idx_last_used_at` (`last_used_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Bohrium 可复用节点缓存，多 session 可共享同一 node';
