"""Pytest configuration and fixtures."""
import os
import tempfile
from pathlib import Path

# Set LOG_FILE at import time, before any app modules are imported
if not os.getenv('LOG_FILE'):
    log_dir = Path(tempfile.gettempdir()) / 'matmaster_tests'
    log_dir.mkdir(parents=True, exist_ok=True)
    os.environ['LOG_FILE'] = str(log_dir / 'test.log')
