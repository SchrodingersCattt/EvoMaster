"""Retry wrapper for ``ClaudeSDKClient`` initialization timeouts."""

from __future__ import annotations

import asyncio
from typing import Any, TextIO

from evaluation.devshell_agent.sdk_logging import log_line


async def sdk_client_with_retry(
    options: Any,
    *,
    retries: int = 2,
    delay: float = 5.0,
    log_file: TextIO | None = None,
) -> Any:
    """Connect a ``ClaudeSDKClient``, retrying on initialization timeout."""
    from claude_agent_sdk import ClaudeSDKClient

    last_exc: Exception | None = None
    for attempt in range(1 + retries):
        try:
            client = ClaudeSDKClient(options=options)
            await client.__aenter__()
            return client
        except Exception as exc:
            if "Control request timeout" not in str(exc):
                raise
            last_exc = exc
            if attempt < retries:
                if log_file:
                    log_line(
                        f"SDK initialize timeout (attempt {attempt + 1}/{1 + retries}), "
                        f"retrying in {delay}s …",
                        log_file,
                    )
                await asyncio.sleep(delay)
    raise last_exc  # type: ignore[misc]
