-- 会话表增加 session_title：用户为该会话自定义的标题（重命名）。
-- 为空（NULL）时前端回退到第一条用户消息 first_user_message 作为展示标题。
ALTER TABLE `evo_chat_sessions`
ADD COLUMN `session_title` VARCHAR(255) NULL COMMENT '用户自定义会话标题；NULL 时前端回退 first_user_message' AFTER `session_id`;
