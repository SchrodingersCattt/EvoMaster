"""Shared helpers for deployment/restart run interruption events."""

from __future__ import annotations

from typing import Any


def build_run_interrupted_message(reason: str) -> str:
    if reason == "restart":
        return "上一轮任务因服务重启中断，请重新发送以继续。"
    if reason == "deploy":
        return "上一轮任务因服务升级中断，请重新发送以继续。"
    return "上一轮任务因服务部署/重启中断，请重新发送以继续。"


def build_run_interrupted_meta(
    reason: str, reason_meta: dict[str, Any]
) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    current_version = reason_meta.get("current_version")
    previous_version = reason_meta.get("previous_version")
    if current_version:
        meta["current_version"] = current_version
    if previous_version:
        meta["previous_version"] = previous_version
    if reason_meta.get("note"):
        meta["reason_note"] = reason_meta["note"]
    if reason in ("restart", "deploy"):
        meta["treat_as_failure"] = True
    return meta


def build_run_interrupted_history_content(
    *, reason: str, reason_meta: dict[str, Any], last_user_content: str
) -> dict[str, Any]:
    return {
        "message": build_run_interrupted_message(reason),
        "reason": reason,
        "last_user_content": last_user_content,
        **build_run_interrupted_meta(reason, reason_meta),
    }
