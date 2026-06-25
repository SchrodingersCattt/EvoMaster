-- 为 Bohrium 节点复用表增加 SKU 维度，避免不同机型复用同一 node。
-- 既有记录回填为当前平台默认 c2_m4 SKU（388）；若环境 BOHRIUM_SKU_ID 不同，请在执行前调整默认值。

ALTER TABLE `evo_bohrium_nodes`
    ADD COLUMN `sku_id` INT NOT NULL DEFAULT 388 COMMENT 'Bohrium 节点 SKU ID' AFTER `project_id`;

-- 同一 user/org/project/sku 只保留一个可复用节点槽位；迁移旧数据时保留最近使用/更新的记录。
DELETE n_old
FROM `evo_bohrium_nodes` n_old
JOIN `evo_bohrium_nodes` n_new
  ON n_old.`user_id` = n_new.`user_id`
 AND n_old.`org_id` = n_new.`org_id`
 AND n_old.`project_id` = n_new.`project_id`
 AND n_old.`sku_id` = n_new.`sku_id`
 AND (
     COALESCE(n_old.`last_used_at`, n_old.`updated_at`, n_old.`created_at`) <
     COALESCE(n_new.`last_used_at`, n_new.`updated_at`, n_new.`created_at`)
     OR (
         COALESCE(n_old.`last_used_at`, n_old.`updated_at`, n_old.`created_at`) =
         COALESCE(n_new.`last_used_at`, n_new.`updated_at`, n_new.`created_at`)
         AND n_old.`id` < n_new.`id`
     )
 );

ALTER TABLE `evo_bohrium_nodes`
    DROP INDEX `uk_user_org_project_node`,
    DROP INDEX `idx_user_org_project`,
    ADD UNIQUE KEY `uk_user_org_project_sku` (
        `user_id`, `org_id`, `project_id`, `sku_id`
    );

ALTER TABLE `evo_bohrium_nodes`
    ALTER COLUMN `sku_id` DROP DEFAULT;
