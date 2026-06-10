-- Add 'lost' terminal status to bohrium_jobs CHECK constraints.
-- This is an external/manual migration script. Run it BEFORE deploying code
-- that writes status='lost'; the runtime has no fallback when the CHECK
-- rejects the new value.
--
-- 'lost' semantics: an active job whose last successful poll (or submit, if
-- never polled) is older than BOHRIUM_POLL_LOST_AFTER_SECONDS is finalized
-- as lost (terminal + failure) by mark_poll_error and enters the delivery
-- queue like any other terminal job.

ALTER TABLE `bohrium_jobs`
    DROP CHECK `chk_status`,
    DROP CHECK `chk_active_poll`,
    DROP CHECK `chk_terminal_at`,
    ADD CONSTRAINT `chk_status` CHECK (`status` IN (
        'submitted', 'running', 'terminating', 'unknown',
        'finished', 'failed', 'stopped', 'lost'
    )),
    ADD CONSTRAINT `chk_active_poll` CHECK (
        (`status` IN ('submitted', 'running', 'terminating', 'unknown') AND `next_poll_at` IS NOT NULL)
        OR
        (`status` IN ('finished', 'failed', 'stopped', 'lost') AND `next_poll_at` IS NULL)
    ),
    ADD CONSTRAINT `chk_terminal_at` CHECK (
        (`status` IN ('submitted', 'running', 'terminating', 'unknown') AND `terminal_at` IS NULL)
        OR
        (`status` IN ('finished', 'failed', 'stopped', 'lost') AND `terminal_at` IS NOT NULL)
    );
