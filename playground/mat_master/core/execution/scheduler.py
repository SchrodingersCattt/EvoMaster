"""Unified Task Scheduler / Batch Executor.

Provides ``BatchExecutor`` — a thin, reusable layer built on
``concurrent.futures.ThreadPoolExecutor`` that:

* Runs a batch of independent ``ExecutionTask`` items concurrently.
* Guarantees result ordering matches input ordering.
* Wraps each task in a safe try/except so one failure never crashes the pool.
* Exposes optional per-batch ``rate_limit`` (token-bucket style) to throttle
  API calls or expensive I/O across all workers.

Both **Direct mode** (LLM tool calls) and **Planner mode** (DAG execution
window) feed tasks into this executor so that concurrency, throttling, and
error handling are implemented exactly once.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger("BatchExecutor")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ExecutionTask:
    """A single unit of work to be executed concurrently.

    Attributes:
        task_id:  Unique identifier (e.g. tool_call.id or step_id).
        func:     Callable that performs the work.  Signature must be
                  ``func(**kwargs) -> tuple[Any, dict]`` returning
                  ``(output, info)``.
        kwargs:   Keyword arguments forwarded to *func*.
        meta:     Arbitrary metadata carried through to the result.
    """

    task_id: str
    func: Callable[..., tuple[Any, dict]]
    kwargs: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutionResult:
    """Result produced by executing a single ``ExecutionTask``.

    Attributes:
        task_id:  Mirrors ``ExecutionTask.task_id``.
        status:   ``"success"`` or ``"failed"``.
        output:   The first element returned by the task callable (or an error
                  string on failure).
        info:     The second element returned by the task callable (or error
                  details on failure).
        error:    Exception message if the task failed; ``None`` otherwise.
        meta:     Passed through from the originating ``ExecutionTask``.
    """

    task_id: str
    status: str  # "success" | "failed"
    output: Any = None
    info: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Token-bucket rate limiter (optional)
# ---------------------------------------------------------------------------

class _TokenBucket:
    """Simple thread-safe token bucket for rate limiting.

    ``tokens_per_sec`` controls the refill rate; ``burst`` is the max tokens
    that can accumulate.  Each ``acquire()`` blocks until a token is available.
    """

    def __init__(self, tokens_per_sec: float, burst: int = 1):
        self._rate = tokens_per_sec
        self._burst = burst
        self._tokens = float(burst)
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        """Block until a token is available."""
        while True:
            with self._lock:
                now = time.monotonic()
                elapsed = now - self._last
                self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
                self._last = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
            # Avoid busy-waiting — sleep a fraction of the refill interval
            time.sleep(0.05)


# ---------------------------------------------------------------------------
# BatchExecutor
# ---------------------------------------------------------------------------

class BatchExecutor:
    """Execute a batch of ``ExecutionTask`` items concurrently.

    Parameters:
        max_workers:      Maximum threads in the pool.
        rate_limit:       Optional rate limit (calls per second).  ``None``
                          means unlimited.  Useful to prevent upstream API
                          throttling (e.g. OpenAI rate limits).

    Example::

        executor = BatchExecutor(max_workers=5, rate_limit=10.0)
        results = executor.execute_batch(tasks)
        for r in results:
            print(r.task_id, r.status, r.output)
    """

    def __init__(
        self,
        max_workers: int = 5,
        rate_limit: float | None = None,
    ):
        self.max_workers = max(1, max_workers)
        self._bucket: _TokenBucket | None = None
        if rate_limit is not None and rate_limit > 0:
            self._bucket = _TokenBucket(tokens_per_sec=rate_limit, burst=max(1, int(rate_limit)))

    # ----- public API -----

    def execute_batch(self, tasks: list[ExecutionTask]) -> list[ExecutionResult]:
        """Run *tasks* concurrently and return results in **input order**.

        If ``len(tasks) <= 1`` the call is executed synchronously (no thread
        overhead).
        """
        if not tasks:
            return []

        results_map: dict[str, ExecutionResult] = {}
        workers = min(self.max_workers, len(tasks))

        if workers <= 1:
            # Fast-path: sequential execution — no thread overhead
            for task in tasks:
                results_map[task.task_id] = self._safe_execute(task)
        else:
            logger.info(
                "Executing %d tasks in parallel (max_workers=%d)",
                len(tasks),
                workers,
            )
            with ThreadPoolExecutor(max_workers=workers) as pool:
                future_to_task = {
                    pool.submit(self._safe_execute, task): task
                    for task in tasks
                }
                for future in as_completed(future_to_task):
                    task = future_to_task[future]
                    try:
                        result = future.result()
                    except Exception as exc:
                        # Should not happen (_safe_execute catches everything)
                        logger.error(
                            "Critical executor error for %s: %s", task.task_id, exc
                        )
                        result = ExecutionResult(
                            task_id=task.task_id,
                            status="failed",
                            output=str(exc),
                            error=str(exc),
                            meta=task.meta,
                        )
                    results_map[task.task_id] = result

        # Return results in the same order as input tasks
        return [results_map[t.task_id] for t in tasks]

    # ----- internal -----

    def _safe_execute(self, task: ExecutionTask) -> ExecutionResult:
        """Execute a single task with error handling and optional rate limiting."""
        # Rate-limit gate
        if self._bucket is not None:
            self._bucket.acquire()

        try:
            output, info = task.func(**task.kwargs)
            return ExecutionResult(
                task_id=task.task_id,
                status="success",
                output=output,
                info=info if isinstance(info, dict) else {"raw": info},
                meta=task.meta,
            )
        except Exception as exc:
            logger.warning("Task %s failed: %s", task.task_id, exc)
            return ExecutionResult(
                task_id=task.task_id,
                status="failed",
                output=f"Error executing {task.task_id}: {exc}",
                info={"error": str(exc)},
                error=str(exc),
                meta=task.meta,
            )
