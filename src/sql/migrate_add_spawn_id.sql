-- 聊天事件表增加 spawn_id：标记单次 spawn 调用，区分父/子 agent 事件并支持前端回放分组
ALTER TABLE `evo_chat_events`
ADD COLUMN `spawn_id` VARCHAR(64) NULL COMMENT '单次 spawn 调用唯一标识；NULL 表示父事件',
ADD INDEX `idx_session_spawn_id` (`session_id`, `spawn_id`);
