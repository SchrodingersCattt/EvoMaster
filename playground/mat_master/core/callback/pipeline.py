"""Composable before/after tool callback pipeline."""

from typing import Any, Callable

BeforeToolCallback = Callable[[Any], None]
AfterToolCallback = Callable[[Any, str, dict[str, Any]], tuple[str, dict[str, Any]]]


class ToolCallbackPipeline:
    """Composable before/after tool callback pipeline."""

    def __init__(self, logger) -> None:
        self.logger = logger
        self._before: list[BeforeToolCallback] = []
        self._after: list[AfterToolCallback] = []

    def register_before(self, callback: BeforeToolCallback) -> None:
        self._before.append(callback)

    def register_after(self, callback: AfterToolCallback) -> None:
        self._after.append(callback)

    def run_before(self, tool_call: Any) -> None:
        for cb in self._before:
            try:
                cb(tool_call)
            except Exception as e:
                self.logger.warning('before_tool callback failed: %s', e)

    def run_after(
        self, tool_call: Any, observation: str, info: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        obs = observation
        meta = dict(info or {})
        for i, cb in enumerate(self._after):
            cb_name = getattr(cb, '__name__', str(cb))
            try:
                self.logger.info(
                    '[flow] ToolCallbackPipeline.run_after callback %d/%d %s',
                    i + 1,
                    len(self._after),
                    cb_name,
                )
                obs, meta = cb(tool_call, obs, meta)
                self.logger.info(
                    '[flow] ToolCallbackPipeline.run_after callback %s done',
                    cb_name,
                )
            except Exception as e:
                self.logger.warning('after_tool callback failed: %s', e)
        return obs, meta
