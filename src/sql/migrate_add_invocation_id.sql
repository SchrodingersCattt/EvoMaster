-- 聊天事件表增加 invocation_id：标记本轮调用，前端区分轮次（刷新/历史回放时可按轮展示）
ALTER TABLE `evo_chat_events`
ADD COLUMN `invocation_id` VARCHAR(64) NULL COMMENT '本轮调用唯一标识，前端区分轮次' AFTER `task_id`,
ADD INDEX `idx_invocation_id` (`invocation_id`);
