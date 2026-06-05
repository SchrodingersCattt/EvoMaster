-- Bohrium 作业状态表。新环境执行本脚本创建（与 create_chat_tables.sql 同级，手动执行）。
-- 需要 MySQL 8.0.16+（CHECK 约束强制执行）。
CREATE TABLE `bohrium_jobs` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,

    `session_id` VARCHAR(255) COLLATE utf8mb4_bin NOT NULL,
    `invocation_id` VARCHAR(255) COLLATE utf8mb4_bin NULL,
    `spawn_id` VARCHAR(64) COLLATE utf8mb4_bin NULL,
    `user_id` VARCHAR(255) COLLATE utf8mb4_bin NOT NULL,
    `org_id` VARCHAR(255) COLLATE utf8mb4_bin NOT NULL,

    `job_id` VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
    `job_name` VARCHAR(255) NULL,
    `project_id` BIGINT UNSIGNED NOT NULL,
    `sandbox` TINYINT(1) NOT NULL DEFAULT 0,

    `input_dir` VARCHAR(1024) NOT NULL,
    `workspace` VARCHAR(1024) COLLATE utf8mb4_bin NOT NULL,
    `result_dir` VARCHAR(1024) NULL,

    `status` VARCHAR(32) COLLATE utf8mb4_bin NOT NULL DEFAULT 'submitted',

    `poll_count` INT UNSIGNED NOT NULL DEFAULT 0,
    `next_poll_at` TIMESTAMP NULL,
    `last_polled_at` TIMESTAMP NULL,

    `submitted_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `terminal_at` TIMESTAMP NULL,
    `handled_at` TIMESTAMP NULL,

    `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    UNIQUE KEY `uk_owner_job_id` (`user_id`, `org_id`, `sandbox`, `job_id`),
    KEY `idx_poll_due` (`next_poll_at`, `id`),
    KEY `idx_session_active` (`user_id`, `org_id`, `session_id`, `submitted_at`),
    KEY `idx_session_pending` (`user_id`, `org_id`, `session_id`, `handled_at`, `terminal_at`),

    CONSTRAINT `chk_sandbox` CHECK (`sandbox` IN (0, 1)),
    CONSTRAINT `chk_status` CHECK (`status` IN (
        'submitted', 'running', 'terminating', 'unknown',
        'finished', 'failed', 'stopped'
    )),
    CONSTRAINT `chk_workspace_share_path` CHECK (
        `workspace` = '/share' OR `workspace` LIKE '/share/%'
    ),
    CONSTRAINT `chk_active_poll` CHECK (
        (`status` IN ('submitted', 'running', 'terminating', 'unknown') AND `next_poll_at` IS NOT NULL)
        OR
        (`status` IN ('finished', 'failed', 'stopped') AND `next_poll_at` IS NULL)
    ),
    CONSTRAINT `chk_terminal_at` CHECK (
        (`status` IN ('submitted', 'running', 'terminating', 'unknown') AND `terminal_at` IS NULL)
        OR
        (`status` IN ('finished', 'failed', 'stopped') AND `terminal_at` IS NOT NULL)
    ),
    CONSTRAINT `chk_handled_requires_terminal` CHECK (
        `handled_at` IS NULL OR `terminal_at` IS NOT NULL
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='Bohrium 作业状态表';
