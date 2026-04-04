from __future__ import annotations

import shlex
import sys
import threading
import time

from matmaster.sessions.local import LocalSession
from matmaster.types.cancellation import CancellationController


def test_local_session_exec_bash_honors_cancel_token(tmp_path) -> None:
    session = LocalSession(str(tmp_path), timeout=15)
    session.open()
    ctrl = CancellationController()
    timer = threading.Timer(0.5, ctrl.cancel)
    timer.start()
    started_at = time.time()

    try:
        cmd = f'{shlex.quote(sys.executable)} -c ' f"\"import time; time.sleep(10)\""
        result = session.exec_bash(
            cmd,
            timeout=15,
            cancel_token=ctrl.token,
        )
    finally:
        timer.cancel()
        session.close()

    assert result['exit_code'] == 130
    assert time.time() - started_at < 3.0


def test_local_session_pre_check_skips_if_already_cancelled(tmp_path) -> None:
    session = LocalSession(str(tmp_path), timeout=15)
    session.open()
    ctrl = CancellationController()
    ctrl.cancel()

    try:
        result = session.exec_bash(
            "echo hello",
            timeout=5,
            cancel_token=ctrl.token,
        )
    finally:
        session.close()

    assert result['exit_code'] == 130
    assert "Cancelled" in result['stderr']


def test_local_session_timeout_still_works(tmp_path) -> None:
    session = LocalSession(str(tmp_path), timeout=15)
    session.open()

    try:
        cmd = f'{shlex.quote(sys.executable)} -c ' f"\"import time; time.sleep(10)\""
        result = session.exec_bash(cmd, timeout=1)
    finally:
        session.close()

    assert result['exit_code'] == 124
