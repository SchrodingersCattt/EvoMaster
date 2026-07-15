from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from matmaster.calculation_runtimes.types import SubmissionRequest

from .errors import CalculationPreflightError
from .selectors import (
    collect_path_selectors,
    rewrite_selected_paths,
    validate_selector_paths,
)

_JOB_CONTROL_TOOLS: frozenset[str] = frozenset(
    {
        "query_job_status",
        "get_job_results",
        "terminate_job",
    }
)

_DOCSTRING_PATH_RE = re.compile(
    r"(\w+)\s*\("
    r"\s*(?:Optional\[|List\[|Dict\[[\w,\s]*)?"
    r"Path"
    r"(?:\])*"
    r"\s*\)",
)

_URL_RE = re.compile(r'https?://[^\s,\'"<>)}\]]+')


def _has_remote_profile(executor_cfg: Any) -> bool:
    if not isinstance(executor_cfg, dict):
        return False
    machine = executor_cfg.get("machine")
    if not isinstance(machine, dict):
        return False
    remote_profile = machine.get("remote_profile")
    return isinstance(remote_profile, dict) and bool(remote_profile)


def _effective_sync_tools(server_cfg: Any) -> set[str]:
    if not isinstance(server_cfg, dict):
        return set()
    return set(server_cfg.get("sync_tools") or []) | set(_JOB_CONTROL_TOOLS)


def _path_keys_from_description(description: str | None) -> set[str]:
    if not description:
        return set()

    args_match = re.search(
        r"Args:\s*\n(.*?)(?=\n\s*(?:Returns?|Raises?|Examples?|Notes?)\s*:|$)",
        description,
        re.DOTALL,
    )
    if not args_match:
        return set()

    return set(_DOCSTRING_PATH_RE.findall(args_match.group(1)))


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _build_alias_map(description: str | None, param_name: str) -> dict[str, str]:
    if not description or not param_name:
        return {}

    pattern = re.compile(
        rf"^(\s+){re.escape(param_name)}\s*\(.*?\):\s*(.*?)"
        rf"(?=\n\1\w+\s*\(|\n\s*\n|\Z)",
        re.DOTALL | re.MULTILINE,
    )
    match = pattern.search(description)
    block = match.group(2) if match else description

    urls = _URL_RE.findall(block)
    if not urls:
        return {}

    alias_map: dict[str, str] = {}
    for url in urls:
        fname = url.rstrip("/").rsplit("/", 1)[-1]
        stem = fname.rsplit(".", 1)[0] if "." in fname else fname
        alias_map[_normalize(stem)] = url

    dict_re = re.compile(
        r"['\"]([^'\"]+)['\"]\s*:\s*['\"](" + r'https?://[^\'"]+' + r")['\"]"
    )
    for alias, url in dict_re.findall(block):
        alias_map[_normalize(alias)] = url

    return alias_map


def _resolve_model_aliases(
    args: dict[str, Any],
    tool_description: str | None,
    path_keys: set[str],
) -> dict[str, Any]:
    if not tool_description:
        return args

    resolved = dict(args)
    for key in path_keys:
        value = resolved.get(key)
        if not isinstance(value, str):
            continue
        value = value.strip()
        if not value:
            continue
        parsed = urlparse(value)
        if parsed.scheme in ("http", "https"):
            continue
        if "/" in value or "\\" in value:
            continue

        alias_map = _build_alias_map(tool_description, key)
        if not alias_map:
            continue

        normalized = _normalize(value)
        if normalized in alias_map:
            resolved[key] = alias_map[normalized]
            continue

        for alias_norm, url in alias_map.items():
            if normalized in alias_norm or alias_norm in normalized:
                resolved[key] = url
                break

    return resolved


def _top_level_path_keys(selectors: set[str]) -> set[str]:
    return {
        selector
        for selector in selectors
        if "." not in selector and "[]" not in selector
    }


class CalculationPreflight:
    def __init__(self, calculation_executors: dict[str, Any] | None = None) -> None:
        self.calculation_executors = calculation_executors or {}

    def _path_selectors_from_tool_config(
        self,
        server_name: str,
        remote_tool_name: str,
        input_schema: dict[str, Any] | None,
    ) -> set[str]:
        server_cfg = self.calculation_executors.get(server_name)
        if not isinstance(server_cfg, dict):
            return set()
        mapping = server_cfg.get("path_params_by_tool")
        if not isinstance(mapping, dict):
            return set()
        raw = mapping.get(remote_tool_name)
        if raw is None and remote_tool_name.startswith("submit_"):
            raw = mapping.get(remote_tool_name[len("submit_") :])
        if raw is None:
            return set()
        if isinstance(raw, str):
            selectors = {raw}
        elif isinstance(raw, (list, tuple)) and all(
            isinstance(item, str) for item in raw
        ):
            selectors = set(raw)
        else:
            raise ValueError(
                f"Invalid path_params_by_tool entry for {server_name}.{remote_tool_name}"
            )

        validate_selector_paths(input_schema or {}, selectors)
        return selectors

    def requires_workspace_access(
        self,
        *,
        server_name: str,
        remote_tool_name: str,
        input_schema: dict[str, Any] | None,
        tool_description: str | None,
    ) -> bool:
        """Return whether preflight may materialize paths through the session."""
        return bool(
            collect_path_selectors(input_schema or {})
            | _path_keys_from_description(tool_description)
            | self._path_selectors_from_tool_config(
                server_name,
                remote_tool_name,
                input_schema,
            )
        )

    def _resolve_executor_template(
        self,
        server_name: str,
        remote_tool_name: str,
    ) -> dict[str, Any] | None:
        server_cfg = self.calculation_executors.get(server_name)
        if not isinstance(server_cfg, dict):
            return None
        sync_tools = _effective_sync_tools(server_cfg)
        if remote_tool_name in sync_tools:
            return {"type": "local", "env": {}}
        executor_map = server_cfg.get("executor_map")
        if isinstance(executor_map, dict):
            tool_executor = executor_map.get(remote_tool_name)
            if tool_executor is None and remote_tool_name.startswith("submit_"):
                tool_executor = executor_map.get(remote_tool_name[len("submit_") :])
            if isinstance(tool_executor, dict):
                return tool_executor
        executor_template = server_cfg.get("executor")
        if isinstance(executor_template, dict):
            return executor_template
        return None

    def _is_async_remote_tool(self, server_name: str, remote_tool_name: str) -> bool:
        server_cfg = self.calculation_executors.get(server_name)
        if not isinstance(server_cfg, dict):
            return False
        if remote_tool_name in _effective_sync_tools(server_cfg):
            return False

        executor_map = server_cfg.get("executor_map")
        if isinstance(executor_map, dict):
            tool_executor = executor_map.get(remote_tool_name)
            if tool_executor is None and remote_tool_name.startswith("submit_"):
                tool_executor = executor_map.get(remote_tool_name[len("submit_") :])
            if _has_remote_profile(tool_executor):
                return True
        return _has_remote_profile(server_cfg.get("executor"))

    @staticmethod
    def _validate_executor_profile(
        executor: dict[str, Any] | None,
        *,
        server_name: str,
        remote_tool_name: str,
    ) -> None:
        if not isinstance(executor, dict):
            raise ValueError(
                f"Missing executor for async tool '{server_name}_{remote_tool_name}'. "
                "Check calculation_executors config."
            )
        machine = executor.get("machine")
        if not isinstance(machine, dict):
            raise ValueError(
                f"Executor missing 'machine' for '{server_name}_{remote_tool_name}'."
            )
        remote_profile = machine.get("remote_profile")
        if not isinstance(remote_profile, dict):
            raise ValueError(
                f"Executor missing 'machine.remote_profile' for "
                f"'{server_name}_{remote_tool_name}'."
            )
        machine_type = remote_profile.get("machine_type")
        image_address = remote_profile.get("image_address")
        if not isinstance(machine_type, str) or not machine_type.strip():
            raise ValueError(
                f"Executor missing remote_profile.machine_type for "
                f"'{server_name}_{remote_tool_name}'."
            )
        if not isinstance(image_address, str) or not image_address.strip():
            raise ValueError(
                f"Executor missing remote_profile.image_address for "
                f"'{server_name}_{remote_tool_name}'."
            )

    def prepare_call(
        self,
        *,
        workspace_path: str,
        args: dict[str, Any],
        tool_name: str,
        remote_tool_name: str,
        server_name: str,
        input_schema: dict[str, Any] | None,
        tool_description: str | None,
        runtime: Any,
        session: Any,
    ) -> dict[str, Any]:
        try:
            if runtime is None:
                raise CalculationPreflightError(
                    f"Calculation runtime unavailable for server {server_name}."
                )

            server_cfg = self.calculation_executors.get(server_name) or {}
            sync_tools = _effective_sync_tools(server_cfg)
            if remote_tool_name.startswith("submit_"):
                base_name = remote_tool_name[len("submit_") :]
                if base_name in sync_tools:
                    raise CalculationPreflightError(
                        f"Tool '{tool_name}' is blocked: '{base_name}' is a sync tool."
                    )

            is_async_tool = self._is_async_remote_tool(server_name, remote_tool_name)
            if is_async_tool and not remote_tool_name.startswith("submit_"):
                raise CalculationPreflightError(
                    f"Async tool '{tool_name}' is blocked for LLM runtime. "
                    f"Use submit interface: '{server_name}_submit_*'."
                )

            schema_selectors = collect_path_selectors(input_schema or {})
            desc_selectors = _path_keys_from_description(tool_description)
            config_selectors = self._path_selectors_from_tool_config(
                server_name, remote_tool_name, input_schema
            )
            path_selectors = schema_selectors | desc_selectors | config_selectors

            request = SubmissionRequest(
                executor_template=self._resolve_executor_template(
                    server_name,
                    remote_tool_name,
                ),
                needs_storage=True,
                submission_mode="async" if is_async_tool else "sync",
            )
            submission = runtime.build_submission(request)
            if is_async_tool:
                self._validate_executor_profile(
                    submission.executor,
                    server_name=server_name,
                    remote_tool_name=remote_tool_name,
                )

            resolved = dict(args)
            resolved["executor"] = submission.executor
            resolved["storage"] = submission.storage

            top_level_path_keys = _top_level_path_keys(path_selectors)
            if top_level_path_keys and tool_description:
                resolved = _resolve_model_aliases(
                    resolved,
                    tool_description,
                    top_level_path_keys,
                )

            if not path_selectors:
                return resolved

            workspace_root = Path(workspace_path).resolve()
            return rewrite_selected_paths(
                resolved,
                selectors=path_selectors,
                schema=input_schema,
                rewrite_leaf=lambda selector, value, schema_leaf: runtime.materialize_input_path(
                    str(value),
                    selector=selector,
                    workspace_root=workspace_root,
                    session=session,
                ),
            )
        except CalculationPreflightError:
            raise
        except Exception as exc:
            raise CalculationPreflightError(str(exc)) from exc
