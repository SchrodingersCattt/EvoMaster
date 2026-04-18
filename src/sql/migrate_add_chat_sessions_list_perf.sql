-- 会话列表：按 user_id + project_id 过滤并按 created_at 排序时走索引，避免全表/大范围扫描。
-- 与 list_sessions / list_sessions_for_project_with_workspace 查询对齐。
ALTER TABLE `evo_chat_sessions`
ADD INDEX `idx_user_project_created` (`user_id`, `project_id`, `created_at`);
