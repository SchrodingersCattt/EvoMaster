-- 为 evo_chat_sessions 表添加 status 列（已有库迁移用）
-- 新环境请直接使用 create_chat_tables.sql

ALTER TABLE `evo_chat_sessions`
ADD COLUMN `status` VARCHAR(32) NOT NULL DEFAULT 'idle'
  COMMENT '会话状态：idle=空闲/已结束，active=运行中（用于限流与前端展示）'
  AFTER `last_task_id`;

ALTER TABLE `evo_chat_sessions` ADD INDEX `idx_status` (`status`);
