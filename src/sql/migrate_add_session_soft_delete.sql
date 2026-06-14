-- 会话软删除：用户侧删除仅隐藏会话，保留会话行与 evo_chat_events 历史事件。
ALTER TABLE `evo_chat_sessions`
ADD COLUMN `deleted_at` DATETIME NULL COMMENT '软删除时间；NULL 表示未删除' AFTER `is_shared`,
ADD COLUMN `deleted_by` VARCHAR(255) NULL COMMENT '执行软删除的用户ID' AFTER `deleted_at`,
ADD INDEX `idx_deleted_at` (`deleted_at`),
ADD INDEX `idx_user_project_deleted_dir_updated` (
  `user_id`,
  `project_id`,
  `deleted_at`,
  `session_directory`(255),
  `updated_at`
);
