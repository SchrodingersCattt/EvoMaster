"""``python -m playground.mat_master.service.server`` — same as legacy ``server.py`` __main__."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Project root on path so ``playground.mat_master.service.server.app:app`` resolves from any cwd.
_root = Path(__file__).resolve().parent.parent.parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import uvicorn

if __name__ == '__main__':
    force_reload = os.environ.get('RELOAD', '').lower() in ('1', 'true', 'yes')
    use_reload = force_reload or (sys.platform != 'win32')
    backend_port = int(os.environ.get('BACKEND_PORT', '50001'))
    uvicorn.run(
        'playground.mat_master.service.server.app:app',
        host='0.0.0.0',
        port=backend_port,
        reload=use_reload,
    )
