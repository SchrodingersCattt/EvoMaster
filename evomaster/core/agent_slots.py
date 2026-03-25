"""多 Agent 槽位容器（供 BasePlayground 使用）。"""

from __future__ import annotations


class AgentSlots:
    """多 Agent 槽位容器，支持 dict 式访问与属性访问（如 self.agents.planning_agent）。"""

    def __init__(self):
        self._slots: dict[str, object] = {}

    def __setitem__(self, name: str, agent: object) -> None:
        self._slots[name] = agent

    def __getitem__(self, name: str) -> object:
        return self._slots[name]

    def __getattr__(self, name: str) -> object:
        if name.startswith('_'):
            raise AttributeError(name)
        if name in self._slots:
            return self._slots[name]
        raise AttributeError(f"{type(self).__name__!r} has no attribute {name!r}")

    def __contains__(self, name: str) -> bool:
        return name in self._slots

    def get(self, name: str, default: object = None) -> object:
        """按名称获取 agent，不存在时返回 default。"""
        return self._slots.get(name, default)

    def get_random_agent(self) -> object | None:
        """返回任意一个已注册的 agent（兼容单 agent 调用方）。"""
        if not self._slots:
            return None
        return next(iter(self._slots.values()))
