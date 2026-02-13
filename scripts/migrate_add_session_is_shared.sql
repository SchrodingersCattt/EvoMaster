-- 会话表增加「是否已分享」：分享后，该会话的 stream 等接口可不鉴权访问
ALTER TABLE `evo_chat_sessions`
ADD COLUMN `is_shared` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否已分享：0=否，1=是（分享后访问 stream 等可不鉴权)' AFTER `status`;
