"""Process-wide session state and playground cache for the MatMaster web service."""

from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor

DIALOG_HISTORY_MAX_EVENTS = int(os.environ.get('CHAT_DIALOG_HISTORY_MAX_EVENTS', '500'))

logger = logging.getLogger(__name__)

# Pre-initialized playground (tools loaded at startup). Reused per run with set_run_dir(task_id).
_cached_pg = None
_playground_init_done = threading.Event()
_executor = ThreadPoolExecutor(max_workers=1)

SESSIONS: dict[str, dict] = {}
SESSION_ID_DEMO = 'demo_session'
RUN_ID_WEB = 'mat_master_web'
# Per-session cancel: session_id -> Event, read by agent thread
_run_stop_events: dict[str, threading.Event] = {}
# Pending cancel: session_ids that requested cancel before run registered (race fix)
_pending_cancel: set[str] = set()
