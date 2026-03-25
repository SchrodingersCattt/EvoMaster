"""Tests for ReadTracker -- Read-Before-Modify protocol state."""

from __future__ import annotations

from matmaster.tools.builtin.read_tracker import ReadTracker


class TestReadTracker:
    """ReadTracker unit tests (pure, no mocks)."""

    def test_mark_and_check(self) -> None:
        tracker = ReadTracker()
        tracker.mark_read("/workspace/a.py")
        assert tracker.has_been_read("/workspace/a.py") is True

    def test_unread_file(self) -> None:
        tracker = ReadTracker()
        assert tracker.has_been_read("/workspace/b.py") is False

    def test_clear_resets(self) -> None:
        tracker = ReadTracker()
        tracker.mark_read("/workspace/a.py")
        tracker.clear()
        assert tracker.has_been_read("/workspace/a.py") is False

    def test_normpath_dot(self) -> None:
        tracker = ReadTracker()
        tracker.mark_read("/workspace/./foo.py")
        assert tracker.has_been_read("/workspace/foo.py") is True

    def test_normpath_dotdot(self) -> None:
        tracker = ReadTracker()
        tracker.mark_read("/workspace/sub/../foo.py")
        assert tracker.has_been_read("/workspace/foo.py") is True

    def test_multiple_files(self) -> None:
        tracker = ReadTracker()
        paths = ["/workspace/a.py", "/workspace/b.py", "/workspace/c.py"]
        for p in paths:
            tracker.mark_read(p)
        for p in paths:
            assert tracker.has_been_read(p) is True
