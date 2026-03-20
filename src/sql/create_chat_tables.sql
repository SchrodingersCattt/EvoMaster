-- 创建聊天会话和事件相关的数据库表

-- 1. 聊天会话表
CREATE TABLE IF NOT EXISTS `evo_chat_sessions` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '自增ID',
    `user_id` VARCHAR(255) NULL COMMENT '用户ID',
    `session_id` VARCHAR(255) NOT NULL UNIQUE COMMENT '会话ID',
    `last_task_id` VARCHAR(255) NULL COMMENT '最后一个任务ID',
    `status` VARCHAR(32) NOT NULL DEFAULT 'idle' COMMENT '会话状态：idle=空闲/已结束，active=运行中（用于限流与前端展示）',
    `is_shared` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否已分享：0=否，1=是（分享后 stream 等可不鉴权）',
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    INDEX `idx_created_at` (`created_at`),
    INDEX `idx_user_id` (`user_id`),
    INDEX `idx_session_id` (`session_id`),
    INDEX `idx_status` (`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='聊天会话表';

-- 2. 聊天事件表
CREATE TABLE IF NOT EXISTS `evo_chat_events` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '事件ID',
    `session_id` VARCHAR(255) NOT NULL COMMENT '会话ID',
    `source` VARCHAR(50) NOT NULL COMMENT '事件来源：System|User|MatMaster',
    `type` VARCHAR(50) NOT NULL COMMENT '事件类型：status|query|thought|tool_call|tool_result|finish|error|cancelled|confirmation_request|confirmation_reply|planner_reply|exp_run|log_line等',
    `content` JSON NOT NULL COMMENT '事件内容（JSON格式）',
    `task_id` VARCHAR(255) NULL COMMENT '关联的任务ID',
    `invocation_id` VARCHAR(64) NULL COMMENT '本轮调用唯一标识，前端区分轮次',
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    INDEX `idx_session_id` (`session_id`),
    INDEX `idx_session_created` (`session_id`, `created_at`),
    INDEX `idx_task_id` (`task_id`),
    INDEX `idx_invocation_id` (`invocation_id`),
    FOREIGN KEY (`session_id`) REFERENCES `evo_chat_sessions`(`session_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='聊天事件表';

-- 3. 会话任务关联表（用于记录会话下的所有任务ID）
-- 暂时不使用此表
-- CREATE TABLE IF NOT EXISTS `chat_session_tasks` (
--     `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '记录ID',
--     `session_id` VARCHAR(255) NOT NULL COMMENT '会话ID',
--     `task_id` VARCHAR(255) NOT NULL COMMENT '任务ID',
--     `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
--     UNIQUE KEY `uk_session_task` (`session_id`, `task_id`),
--     INDEX `idx_session_id` (`session_id`),
--     INDEX `idx_task_id` (`task_id`),
--     FOREIGN KEY (`session_id`) REFERENCES `evo_chat_sessions`(`session_id`) ON DELETE CASCADE
-- ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='会话任务关联表';
