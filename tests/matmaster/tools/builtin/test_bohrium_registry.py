"""Unit tests for Bohrium job registry and throttle schedule."""

from __future__ import annotations

import time

from matmaster.tools.builtin.bohrium_tool.registry import (
    JobRecord,
    JobRegistry,
    next_interval,
)


class TestNextInterval:
    def test_first_interval_is_30s(self):
        assert next_interval(0) == 30

    def test_doubles_each_step(self):
        assert next_interval(1) == 60
        assert next_interval(2) == 120
        assert next_interval(3) == 240

    def test_caps_at_3600(self):
        assert next_interval(7) == 3600
        assert next_interval(10) == 3600
        assert next_interval(100) == 3600

    def test_poll_6_is_32_minutes(self):
        assert next_interval(6) == 1920


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
        assert 0 < remaining <= 30

    def test_second_poll_allowed_after_interval(self):
        reg = JobRegistry()
        reg.register("job-1")
        reg.update_poll("job-1", status="running", result='{"status":"Running"}')
        rec = reg.get("job-1")
        assert rec is not None
        rec.last_polled_at = time.monotonic() - 31
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
        throttled, remaining = reg.should_throttle("job-1")
        assert throttled is True
        assert remaining > 0
        rec = reg.get("job-1")
        assert rec is not None
        rec.last_polled_at = time.monotonic() - 481
        throttled, _ = reg.should_throttle("job-1")
        assert throttled is False
