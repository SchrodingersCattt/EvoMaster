"""Project root on sys.path + register mat_master playground (same as legacy server.py)."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

importlib.import_module('playground.mat_master.core.playground')
