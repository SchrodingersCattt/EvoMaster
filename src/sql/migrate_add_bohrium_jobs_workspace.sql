-- Add workspace to an existing bohrium_jobs table.
-- This is an external/manual migration script. Runtime code must not infer,
-- backfill, or fall back when workspace is missing.
--
-- Operator flow:
-- 1. Add the nullable column.
-- 2. Manually populate every existing row with the correct submit-time
--    /share workspace, or delete rows that cannot be recovered.
-- 3. Verify the guard SELECT returns zero rows.
-- 4. Enforce NOT NULL and the CHECK constraint.

ALTER TABLE `bohrium_jobs`
    ADD COLUMN `workspace` VARCHAR(1024) COLLATE utf8mb4_bin NULL
    AFTER `input_dir`;

-- Manual recovery step, example only:
-- UPDATE `bohrium_jobs`
-- SET `workspace` = '/share/project'
-- WHERE `id` IN (...);

SELECT `id`, `session_id`, `job_id`, `workspace`
FROM `bohrium_jobs`
WHERE `workspace` IS NULL
   OR `workspace` = ''
   OR (`workspace` <> '/share' AND `workspace` NOT LIKE '/share/%');

ALTER TABLE `bohrium_jobs`
    MODIFY COLUMN `workspace` VARCHAR(1024) COLLATE utf8mb4_bin NOT NULL,
    ADD CONSTRAINT `chk_workspace_share_path` CHECK (
        `workspace` = '/share' OR `workspace` LIKE '/share/%'
    );
