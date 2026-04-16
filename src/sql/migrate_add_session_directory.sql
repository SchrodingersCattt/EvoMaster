-- 会话表增加 session_directory：用户为该会话选择/绑定的远端或逻辑工作目录（如 Bohrium /share 下路径）
ALTER TABLE `evo_chat_sessions`
ADD COLUMN `session_directory` VARCHAR(2048) NULL COMMENT '会话绑定的工作区目录路径' AFTER `project_id`;
