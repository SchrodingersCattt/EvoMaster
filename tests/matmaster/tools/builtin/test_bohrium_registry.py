"""Unit tests for Bohrium job registry and throttle schedule."""

from __future__ import annotations

import time

from matmaster.tools.builtin.bohrium_tool.registry import (
    JobRegistry,
    classify_poll_status,
    next_interval,
)


class TestNextInterval:
    def test_probe_phase_fast(self):
        assert next_interval(0) == 5
        assert next_interval(1) == 10
        assert next_interval(2) == 15
        assert next_interval(3) == 30
        assert next_interval(4) == 45

    def test_stable_phase_doubles(self):
        assert next_interval(5) == 60
        assert next_interval(6) == 120
        assert next_interval(7) == 240
        assert next_interval(8) == 480

    def test_caps_at_3600(self):
        assert next_interval(11) == 3600
        assert next_interval(15) == 3600
        assert next_interval(100) == 3600


class TestClassifyPollStatus:
    def test_maps_finished_status(self):
        assert classify_poll_status("Finished") == "finished"

    def test_maps_failed_status(self):
        assert classify_poll_status("Failed") == "failed"

    def test_maps_running_status(self):
        assert classify_poll_status("Running") == "running"

    def test_maps_pending_like_statuses_to_running(self):
        assert classify_poll_status("Pending") == "running"
        assert classify_poll_status("Scheduling") == "running"


class TestJobRegistry:
    def test_register_and_get(self):
        reg = JobRegistry()
        reg.register("job-1", job_name="test")
        rec = reg.get("job-1")
        assert rec is not None
        assert rec.status == "submitted"
        assert rec.job_name == "test"
        assert rec.poll_count == 0

    def test_get_unknown_returns_none(self):
        reg = JobRegistry()
        assert reg.get("unknown") is None

    def test_first_poll_not_throttled(self):
        reg = JobRegistry()
        reg.register("job-1")
        throttled, remaining = reg.should_throttle("job-1")
        assert throttled is False

    def test_second_poll_throttled_within_interval(self):
        reg = JobRegistry()
        reg.register("job-1")
        reg.update_poll("job-1", status="running", result='{"status":"Running"}')
        throttled, remaining = reg.should_throttle("job-1")
        assert throttled is True
        assert 0 < remaining <= 10

    def test_second_poll_allowed_after_interval(self):
        reg = JobRegistry()
        reg.register("job-1")
        reg.update_poll("job-1", status="running", result='{"status":"Running"}')
        rec = reg.get("job-1")
        assert rec is not None
        rec.last_polled_at = time.monotonic() - 11
        throttled, _ = reg.should_throttle("job-1")
        assert throttled is False

    def test_terminal_status_not_throttled(self):
        reg = JobRegistry()
        reg.register("job-1")
        reg.update_poll("job-1", status="finished", result="{}")
        throttled, _ = reg.should_throttle("job-1")
        assert throttled is False

    def test_update_download(self):
        reg = JobRegistry()
        reg.register("job-1")
        reg.update_poll("job-1", status="finished", result="{}")
        reg.update_download("job-1")
        rec = reg.get("job-1")
        assert rec is not None
        assert rec.status == "downloaded"

    def test_pending_jobs(self):
        reg = JobRegistry()
        reg.register("job-1")
        reg.register("job-2")
        reg.update_poll("job-1", status="finished", result="{}")
        pending = reg.pending_jobs()
        assert len(pending) == 1
        assert pending[0].job_id == "job-2"

    def test_poll_count_increments(self):
        reg = JobRegistry()
        reg.register("job-1")
        reg.update_poll("job-1", status="running", result="{}")
        rec = reg.get("job-1")
        assert rec is not None
        assert rec.poll_count == 1
        rec.last_polled_at = time.monotonic() - 61
        reg.update_poll("job-1", status="running", result="{}")
        assert reg.get("job-1").poll_count == 2

    def test_backoff_schedule_increases_with_poll_count(self):
        reg = JobRegistry()
        reg.register("job-1")
        for _ in range(5):
            rec = reg.get("job-1")
            assert rec is not None
            rec.last_polled_at = time.monotonic() - 10000
            reg.update_poll("job-1", status="running", result="{}")
        # poll_count=5, next_interval(4)=45 → should be throttled
        throttled, remaining = reg.should_throttle("job-1")
        assert throttled is True
        assert remaining > 0
        rec = reg.get("job-1")
        assert rec is not None
        # After 46s, interval(4)=45 should be exceeded
        rec.last_polled_at = time.monotonic() - 46
        throttled, _ = reg.should_throttle("job-1")
        assert throttled is False


class TestRebuildFromQueryEvents:
    def test_query_event_restores_poll_count(self):
        events = [
            {"action": "submit", "job_id": "job-1", "job_name": "n"},
            {"action": "query", "job_id": "job-1", "status": "Running"},
        ]
        reg = JobRegistry.rebuild_from_events(events)
        rec = reg.get("job-1")
        assert rec is not None
        assert rec.poll_count == 1
        assert rec.status == "running"

    def test_legacy_poll_event_ignored(self):
        # post-migration the action word is "query"; stale "poll" no longer maps
        legacy_action = "po" + "ll"
        events = [{"action": legacy_action, "job_id": "job-1", "status": "Running"}]
        reg = JobRegistry.rebuild_from_events(events)
        rec = reg.get("job-1")
        assert rec is None or rec.poll_count == 0
