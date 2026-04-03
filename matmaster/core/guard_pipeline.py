"""GuardPipeline and LoopDetectionGuard for kernel tool call gating."""

from __future__ import annotations

import json
import time
from collections import deque

from matmaster.types.guards import Guard, GuardContext, GuardResult, RecentCall
from matmaster.types.messages import ToolCallData

LOOP_WINDOW: int = 5
LOOP_THRESHOLD: int = 2


class LoopDetectionGuard:
    """Detect repeated identical tool calls within a sliding window."""

    def __init__(
        self,
        window: int = LOOP_WINDOW,
        threshold: int = LOOP_THRESHOLD,
    ) -> None:
        self._window = window
        self._threshold = threshold

    def _fingerprint(self, tool_name: str, tool_args: dict) -> str:
        try:
            args_str = json.dumps(tool_args, sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError):
            args_str = str(tool_args)
        return f"{tool_name}|{args_str}"

    def evaluate(self, ctx: GuardContext) -> GuardResult:
        current_fp = self._fingerprint(ctx.tool_name, ctx.tool_args)
        window_calls = ctx.recent_calls[-self._window :]
        count = sum(
            1
            for rc in window_calls
            if (rc.fingerprint or self._fingerprint(rc.tool_name, rc.tool_args))
            == current_fp
        )
        if count >= self._threshold:
            return GuardResult(
                allowed=False,
                reason=(
                    f"Loop detected: '{ctx.tool_name}' called {count + 1} times "
                    f"with identical arguments in the last {self._window} calls."
                ),
                guidance=(
                    "Try a different approach or modify the arguments. "
                    "Repeating the same call will not produce different results."
                ),
            )
        return GuardResult(allowed=True)


class GuardPipeline:
    """Pipeline that chains LoopDetectionGuard with optional external guards."""

    def __init__(self, external_guards: list[Guard] | None = None) -> None:
        self._loop_guard = LoopDetectionGuard()
        self._guards: list[Guard] = [self._loop_guard]
        if external_guards:
            self._guards.extend(external_guards)
        self._recent_calls: deque[RecentCall] = deque(maxlen=LOOP_WINDOW)

    def evaluate(
        self,
        tool_call: ToolCallData,
        current_turn: int,
        max_turns: int,
    ) -> GuardResult:
        ctx = GuardContext(
            tool_name=tool_call.name,
            tool_args=tool_call.arguments,
            tool_call_id=tool_call.id,
            current_turn=current_turn,
            max_turns=max_turns,
            recent_calls=list(self._recent_calls),
        )
        for guard in self._guards:
            result = guard.evaluate(ctx)
            if not result.allowed:
                return result
        self._record_call(tool_call)
        return GuardResult(allowed=True)

    def _record_call(self, tool_call: ToolCallData) -> None:
        self._recent_calls.append(
            RecentCall(
                tool_name=tool_call.name,
                tool_args=tool_call.arguments,
                call_id=tool_call.id,
                timestamp=time.monotonic(),
                fingerprint=self._loop_guard._fingerprint(
                    tool_call.name,
                    tool_call.arguments,
                ),
            )
        )
