-- 修正上一版 Bohrium 节点复用表 SKU 迁移的唯一索引语义。
--
-- 适用状态：
--   已执行过 migrate_add_bohrium_nodes_sku_id.sql 的上一版，
--   表内已有 sku_id 列、uk_user_org_project_sku_node 五元组唯一索引、
--   idx_user_org_project_sku 普通索引。
--
-- 目标状态：
--   同一 user_id + org_id + project_id + sku_id 只保留一个可复用节点槽位。

-- 创建四元组唯一索引前，先清理上一版允许产生的重复槽位。
-- 保留最近使用/更新的一条；时间相同时保留 id 较大的记录。
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
    DROP INDEX `uk_user_org_project_sku_node`,
    DROP INDEX `idx_user_org_project_sku`,
    ADD UNIQUE KEY `uk_user_org_project_sku` (
        `user_id`, `org_id`, `project_id`, `sku_id`
    );
