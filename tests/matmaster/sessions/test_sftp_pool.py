"""Tests for SFTPPool — SFTP connection pool with semaphore-based concurrency control."""
from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest

from matmaster.sessions.sftp_pool import SFTPPool


@pytest.fixture
def mock_transport():
    transport = MagicMock()
    # Each open_sftp_client() call returns a distinct mock
    transport.open_sftp_client.side_effect = lambda: MagicMock()
    return transport


class TestSFTPPoolAcquireRelease:
    def test_acquire_creates_new_client(self, mock_transport):
        pool = SFTPPool(mock_transport, max_size=2)
        sftp = pool.acquire()
        assert sftp is not None
        mock_transport.open_sftp_client.assert_called_once()

    def test_release_and_reuse(self, mock_transport):
        pool = SFTPPool(mock_transport, max_size=2)
        sftp = pool.acquire()
        sftp.stat.return_value = MagicMock()  # healthy
        pool.release(sftp)
        sftp2 = pool.acquire()
        assert sftp2 is sftp  # reused, not new
        assert mock_transport.open_sftp_client.call_count == 1

    def test_semaphore_limits_concurrency(self, mock_transport):
        pool = SFTPPool(mock_transport, max_size=1)
        sftp = pool.acquire()
        # Second acquire should block
        entered = threading.Event()
        acquired = threading.Event()
        def try_acquire():
            entered.set()  # 确认线程已启动
            pool.acquire()
            acquired.set()
        t = threading.Thread(target=try_acquire, daemon=True)
        t.start()
        entered.wait(timeout=1.0)  # 等线程进入 acquire
        import time; time.sleep(0.1)  # 让 semaphore.acquire 阻塞
        assert not acquired.is_set(), "Should block when pool exhausted"
        # Release first, now second should succeed
        sftp.stat.return_value = MagicMock()
        pool.release(sftp)
        t.join(timeout=2.0)
        assert acquired.is_set()

    def test_acquire_failure_releases_semaphore(self, mock_transport):
        mock_transport.open_sftp_client.side_effect = OSError("connection lost")
        pool = SFTPPool(mock_transport, max_size=1)
        with pytest.raises(OSError):
            pool.acquire()
        # Semaphore should be released; next acquire should not block
        mock_transport.open_sftp_client.side_effect = lambda: MagicMock()
        sftp = pool.acquire()  # should not hang
        assert sftp is not None


class TestSFTPPoolHealthCheck:
    def test_release_discards_dead_client(self, mock_transport):
        pool = SFTPPool(mock_transport, max_size=2)
        sftp = pool.acquire()
        sftp.stat.side_effect = OSError("channel closed")
        pool.release(sftp)
        sftp.close.assert_called_once()
        # Next acquire should create a new client, not reuse dead one
        sftp2 = pool.acquire()
        assert sftp2 is not sftp
        assert mock_transport.open_sftp_client.call_count == 2


class TestSFTPPoolCloseAll:
    def test_close_all_clears_pool(self, mock_transport):
        pool = SFTPPool(mock_transport, max_size=2)
        sftp1 = pool.acquire()
        sftp2 = pool.acquire()
        sftp1.stat.return_value = MagicMock()
        sftp2.stat.return_value = MagicMock()
        pool.release(sftp1)
        pool.release(sftp2)
        pool.close_all()
        sftp1.close.assert_called_once()
        sftp2.close.assert_called_once()
