"""WorkerExp: single-shot experiment (default capability).

Thin wrapper around BaseExp for one sub-task. Used by DirectSolver as default route.
"""

from evomaster.core.exp import BaseExp


class WorkerExp(BaseExp):
    """Worker experiment: run the agent once for a single task/sub-task.

    Same semantics as BaseExp.run(): create TaskInstance, agent.run(task),
    return trajectory/status/steps. Used by DirectSolver when route is default.
    """

    pass
