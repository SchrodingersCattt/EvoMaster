-- 会话列表：按 project + directory + updated_at 分页（list / list/more）时辅助索引。
ALTER TABLE `evo_chat_sessions`
ADD INDEX `idx_user_project_dir_updated` (
  `user_id`,
  `project_id`,
  `session_directory`(255),
  `updated_at`
);
