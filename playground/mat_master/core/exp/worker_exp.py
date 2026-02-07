"""WorkerExp: single-shot experiment (mode='single').

Thin wrapper around BaseExp for one sub-task. Used by PI-Exp for dispatched
work and by MatMasterPlayground when mode='single'.
"""

from evomaster.core.exp import BaseExp


class WorkerExp(BaseExp):
    """Worker experiment: run the agent once for a single task/sub-task.

    Same semantics as BaseExp.run(): create TaskInstance, agent.run(task),
    return trajectory/status/steps. Used as the default when mat_master.mode
    is "single", and by PrincipalInvestigatorExp for each dispatched sub-task.
    """

    pass
