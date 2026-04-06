"""Lightweight shell execution planning."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ShellPlan:
    mode: str
    reason: str


def plan_shell_command(command: str) -> ShellPlan:
    if "\n" in command:
        if "<<" in command:
            return ShellPlan(mode="script", reason="heredoc")
        return ShellPlan(mode="script", reason="multiline")
    return ShellPlan(mode="inline", reason="single_line")
