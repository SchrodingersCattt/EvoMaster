-- 会话表增加 Bohrium 任务提交确认偏好覆盖
-- 语义：NULL = 未设置/继承，1 = 需要确认，0 = 不需要确认。

ALTER TABLE `evo_chat_sessions`
ADD COLUMN `bohrium_submit_confirmation_required` TINYINT(1) NULL DEFAULT NULL
COMMENT 'Bohrium 任务提交是否需要确认；NULL=未设置/继承，1=需要，0=不需要'
AFTER `chat_mode`;
