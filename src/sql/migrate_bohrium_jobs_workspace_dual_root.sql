-- 放开 bohrium_jobs.workspace 约束，从单根 /share 扩展到双根 /share + /personal。
-- 外部/手动迁移脚本：在已有 bohrium_jobs 表上执行；运行代码不内联本迁移逻辑。
-- 与 matmaster.types.session.REMOTE_ACCESS_ROOTS 保持同步。
--
-- 需要 MySQL 8.0.16+（CHECK 约束强制执行）。
-- Operator flow:
-- 1. DROP 旧的 /share-only 约束。
-- 2. ADD 新的双根约束（约束改名 chk_workspace_share_path -> chk_workspace_root_path）。

ALTER TABLE `bohrium_jobs`
    DROP CONSTRAINT `chk_workspace_share_path`;

ALTER TABLE `bohrium_jobs`
    ADD CONSTRAINT `chk_workspace_root_path` CHECK (
        `workspace` = '/share' OR `workspace` LIKE '/share/%'
        OR `workspace` = '/personal' OR `workspace` LIKE '/personal/%'
    );
