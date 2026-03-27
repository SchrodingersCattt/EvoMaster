from __future__ import annotations

import shlex
import sys
import threading
import time

from evomaster.agent.session.local import LocalSession, LocalSessionConfig


def test_local_session_exec_bash_honors_stop_event(tmp_path) -> None:
    session = LocalSession(LocalSessionConfig(workspace_path=str(tmp_path), timeout=15))
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
