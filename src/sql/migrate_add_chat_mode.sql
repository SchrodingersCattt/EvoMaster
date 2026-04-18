-- 会话表增加 chat_mode：本会话内用户偏好的对话模式 direct|planner（与 POST stream 的 mode 一致）
ALTER TABLE `evo_chat_sessions`
ADD COLUMN `chat_mode` VARCHAR(32) NULL DEFAULT NULL COMMENT '会话偏好模式: direct|planner' AFTER `session_directory`;
