-- 用户级 BYOK OpenAI-compatible LLM 配置表。
-- 外部迁移脚本：不要在应用启动或 table init 中自动执行。
CREATE TABLE IF NOT EXISTS `user_llm_config` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '自增 ID',
    `user_id` VARCHAR(255) NOT NULL COMMENT '用户 ID',
    `display_name` VARCHAR(255) NOT NULL COMMENT '用户可见配置名',
    `base_url` VARCHAR(1024) NOT NULL COMMENT 'OpenAI-compatible HTTPS base_url',
    `model` VARCHAR(255) NOT NULL COMMENT '用户配置的模型名',
    `api_key_cipher` TEXT NOT NULL COMMENT 'Fernet 加密后的 API key',
    `api_key_hint` VARCHAR(64) NOT NULL COMMENT '非敏感 key 提示',
    `key_version` VARCHAR(64) NOT NULL DEFAULT 'v1' COMMENT '密钥加密版本',
    `params` JSON NULL COMMENT '白名单采样/生成参数',
    `extra_body` JSON NULL COMMENT 'OpenAI-compatible extra_body 扩展',
    `prompt_cache` JSON NULL COMMENT 'prompt cache 配置',
    `supports_streaming` TINYINT(1) NOT NULL DEFAULT 1 COMMENT '是否支持流式输出',
    `supports_tool_calling` TINYINT(1) NOT NULL DEFAULT 1 COMMENT '是否支持 tool calling',
    `supports_vision` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否支持图片输入',
    `verification_status` VARCHAR(32) NOT NULL DEFAULT 'unverified' COMMENT '验证状态',
    `verification_error` TEXT NULL COMMENT '最近一次验证错误（已脱敏）',
    `verified_at` DATETIME NULL COMMENT '最近一次验证通过时间',
    `is_enabled` TINYINT(1) NOT NULL DEFAULT 1 COMMENT '是否启用',
    `version` BIGINT UNSIGNED NOT NULL DEFAULT 1 COMMENT '配置版本，运行回查时校验',
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    UNIQUE KEY `uk_user_display_name` (`user_id`, `display_name`),
    INDEX `idx_user_id_id` (`user_id`, `id`),
    INDEX `idx_user_id_is_enabled` (`user_id`, `is_enabled`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='用户级 BYOK LLM 配置表';
