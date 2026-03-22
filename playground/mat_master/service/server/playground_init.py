"""Startup playground load and FastAPI lifespan."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import persistence, remote_workspace, state

logger = logging.getLogger(__name__)


def _init_playground_sync() -> None:
    """Load playground once: config, LLM, session, MCP tools, skills, agent."""
    try:
        from evomaster.core import get_playground_class

        from .bootstrap import PROJECT_ROOT
        from .paths import _get_run_id_web, _runs_dir

        config_path = PROJECT_ROOT / 'configs' / 'mat_master' / 'config.yaml'
        if not config_path.exists():
            raise FileNotFoundError(f"Config not found: {config_path}")
        pg = get_playground_class('mat_master', config_path=config_path)
        run_dir = _runs_dir() / _get_run_id_web()
        run_dir.mkdir(parents=True, exist_ok=True)
        pg.set_run_dir(run_dir)
        pg.setup()
        state._cached_pg = pg
        logger.info('Playground (tools, MCP, agent) initialized at startup.')
    except Exception as e:
        logger.exception('Playground init at startup failed: %s', e)
        state._cached_pg = None
    finally:
        state._playground_init_done.set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: load tools in a thread so server is ready only after tools are loaded."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _init_playground_sync)
    persistence._load_persisted_sessions()
    yield
    if state._cached_pg is not None:
        s = remote_workspace._get_session()
        if s is not None and hasattr(s, 'close'):
            try:
                s.close()
                logger.info('SSH session closed on shutdown.')
            except Exception:
                pass
