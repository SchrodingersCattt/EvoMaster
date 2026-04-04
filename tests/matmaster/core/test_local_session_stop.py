from __future__ import annotations

import shlex
import sys
import threading
import time

from matmaster.sessions.local import LocalSession


def test_local_session_exec_bash_honors_stop_event(tmp_path) -> None:
    session = LocalSession(str(tmp_path), timeout=15)
    session.open()
    stop_event = threading.Event()
    timer = threading.Timer(0.5, stop_event.set)
    timer.start()
    started_at = time.time()

    try:
        cmd = f'{shlex.quote(sys.executable)} -c ' f"\"import time; time.sleep(10)\""
        result = session.exec_bash(
            cmd,
            timeout=15,
            stop_event=stop_event,
        )
    finally:
        timer.cancel()
        session.close()

    assert result['exit_code'] == 130
    assert 'cancelled by stop request' in result['stderr'].lower()
    assert time.time() - started_at < 5


def test_local_session_accepts_is_set_only_stop_signal(tmp_path) -> None:
    class StopOnlySignal:
        def __init__(self) -> None:
            self._stop = False

        def is_set(self) -> bool:
            return self._stop

        def set(self) -> None:
            self._stop = True

    session = LocalSession(str(tmp_path), timeout=15)
    session.open()
    stop_event = StopOnlySignal()
    timer = threading.Timer(0.5, stop_event.set)
    timer.start()
    started_at = time.time()

    try:
        cmd = f'{shlex.quote(sys.executable)} -c ' f"\"import time; time.sleep(10)\""
        result = session.exec_bash(
            cmd,
            timeout=15,
            stop_event=stop_event,
        )
    finally:
        timer.cancel()
        session.close()

    assert result['exit_code'] == 130
    assert 'cancelled by stop request' in result['stderr'].lower()
    assert time.time() - started_at < 5
