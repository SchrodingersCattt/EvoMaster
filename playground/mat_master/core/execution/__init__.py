"""MatMaster Unified Execution Layer (TaskExecutionLayer).

Provides a shared ``BatchExecutor`` that decouples *decision* (Planner / Direct
mode) from *execution* (thread-pool based true I/O concurrency).

Usage::

    from ..execution import BatchExecutor, ExecutionTask

    tasks = [
        ExecutionTask(task_id="t1", func=my_fn, kwargs={"x": 1}),
        ExecutionTask(task_id="t2", func=my_fn, kwargs={"x": 2}),
    ]
    results = BatchExecutor(max_workers=4).execute_batch(tasks)
"""

from .scheduler import BatchExecutor, ExecutionResult, ExecutionTask

__all__ = ["BatchExecutor", "ExecutionResult", "ExecutionTask"]
