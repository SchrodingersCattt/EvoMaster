-- 会话表增加 org_id、project_id 列，Bohrium 相关以库为准，需要时从库读，不依赖内存
ALTER TABLE `evo_chat_sessions`
ADD COLUMN `org_id` VARCHAR(255) NULL COMMENT 'Bohrium 组织 ID（与 X-Org-Id 对应）' AFTER `user_id`,
ADD COLUMN `project_id` BIGINT NULL COMMENT 'Bohrium 项目 ID' AFTER `org_id`;
