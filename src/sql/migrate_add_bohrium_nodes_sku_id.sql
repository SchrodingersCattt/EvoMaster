-- 为 Bohrium 节点复用表增加 SKU 维度，避免不同机型复用同一 node。
-- 既有记录回填为当前平台默认 c2_m4 SKU（388）；若环境 BOHRIUM_SKU_ID 不同，请在执行前调整默认值。

ALTER TABLE `evo_bohrium_nodes`
    ADD COLUMN `sku_id` INT NOT NULL DEFAULT 388 COMMENT 'Bohrium 节点 SKU ID' AFTER `project_id`;

ALTER TABLE `evo_bohrium_nodes`
    DROP INDEX `uk_user_org_project_node`,
    DROP INDEX `idx_user_org_project`,
    ADD UNIQUE KEY `uk_user_org_project_sku_node` (
        `user_id`, `org_id`, `project_id`, `sku_id`, `node_id`
    ),
    ADD INDEX `idx_user_org_project_sku` (
        `user_id`, `org_id`, `project_id`, `sku_id`
    );

ALTER TABLE `evo_bohrium_nodes`
    ALTER COLUMN `sku_id` DROP DEFAULT;
