"""Path adaptor: Bohrium HTTPS storage and executor/sync logic for calculation MCP tools.

All calculation-related MCP tools must receive OSS (or HTTP) links for path arguments.
If the caller passes a local path, the adaptor uploads the file to OSS and passes
the resulting URL.

Path detection uses two layers (in order):
1. **Schema-driven**: ``"format": "path"`` in the JSON Schema (works when MCP SDK
   preserves Pydantic's format annotation).
2. **Description fallback**: parse the tool docstring for ``param_name (Path):``
   patterns (handles SDKs that convert ``Path → str`` and strip the format).

Model alias resolution: short model names like ``"DPA2.4-7M"`` are automatically
resolved to their full OSS URLs by matching against URLs found in the parameter's
description text.
"""

import logging
import re
from pathlib import Path
from typing import Any, Dict, Optional, Set
from urllib.parse import urlparse

from evomaster.env import get_bohrium_storage_config, inject_bohrium_executor

from .oss_io import upload_file_to_oss

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# JSON Schema ``format`` values that indicate a filesystem path.
# Pydantic v2 emits:
#   pathlib.Path          → "format": "path"
#   pydantic.FilePath     → "format": "file-path"
#   pydantic.DirectoryPath→ "format": "directory-path"
# ---------------------------------------------------------------------------
_PATH_FORMATS: frozenset[str] = frozenset({
    'path',
    'file-path',
    'directory-path',
})


# ---------------------------------------------------------------------------
# Executor helpers
# ---------------------------------------------------------------------------
def _has_remote_profile(executor_cfg: Any) -> bool:
    """Return True if executor config contains machine.remote_profile."""
    if not isinstance(executor_cfg, dict):
        return False
    machine = executor_cfg.get("machine")
    if not isinstance(machine, dict):
        return False
    remote_profile = machine.get("remote_profile")
    return isinstance(remote_profile, dict) and bool(remote_profile)


# ---------------------------------------------------------------------------
# Layer 1: schema-driven detection
# ---------------------------------------------------------------------------

def _has_path_format(prop_schema: dict) -> bool:
    """Return *True* if a single JSON-Schema property describes a Path type.

    Handles:
    * Direct: ``{"type": "string", "format": "path"}``
    * Optional / Union: ``{"anyOf": [{"type": "string", "format": "path"}, {"type": "null"}]}``
    * ``oneOf`` variant of the above.
    """
    fmt = prop_schema.get('format', '')
    if fmt in _PATH_FORMATS:
        return True
    for branch_key in ('anyOf', 'oneOf'):
        branches = prop_schema.get(branch_key)
        if branches and isinstance(branches, list):
            for branch in branches:
                if isinstance(branch, dict) and branch.get('format', '') in _PATH_FORMATS:
                    return True
    return False


def _path_keys_from_schema(input_schema: Optional[Dict[str, Any]]) -> Set[str]:
    """Derive path-typed parameter names from a JSON Schema ``"format"`` field."""
    if not input_schema or not isinstance(input_schema, dict):
        return set()

    props = input_schema.get('properties') or {}
    out: Set[str] = set()

    for key, spec in props.items():
        if not isinstance(spec, dict):
            continue
        if _has_path_format(spec):
            out.add(key)
            continue
        items = spec.get('items')
        if isinstance(items, dict) and _has_path_format(items):
            out.add(key)
            continue
        for branch_key in ('anyOf', 'oneOf'):
            branches = spec.get(branch_key)
            if branches and isinstance(branches, list):
                for branch in branches:
                    if not isinstance(branch, dict):
                        continue
                    if _has_path_format(branch):
                        out.add(key)
                        break
                    b_items = branch.get('items')
                    if isinstance(b_items, dict) and _has_path_format(b_items):
                        out.add(key)
                        break
    return out


# ---------------------------------------------------------------------------
# Layer 2: description (docstring) fallback
# ---------------------------------------------------------------------------

# Matches:  param_name (Path):   param_name (Optional[Path]):
#           param_name (List[Path]):  param_name (Dict[str, Path]):
_DOCSTRING_PATH_RE = re.compile(
    r'(\w+)\s*\('                          # param_name (
    r'\s*(?:Optional\[|List\[|Dict\[[\w,\s]*)?'  # optional wrapper
    r'Path'                                # the keyword Path
    r'(?:\])*'                             # closing brackets
    r'\s*\)',                              # )
)


def _path_keys_from_description(description: Optional[str]) -> Set[str]:
    """Parse tool description (docstring) for Path-typed parameter names.

    Only searches the **Args** section so that return-value Path annotations
    (e.g. ``phonon_band_plot (Optional[Path])``) are excluded.
    """
    if not description:
        return set()

    # Isolate the Args section; stop at Returns / Raises / Examples / Notes / end
    args_match = re.search(
        r'Args:\s*\n(.*?)(?=\n\s*(?:Returns?|Raises?|Examples?|Notes?)\s*:|$)',
        description,
        re.DOTALL,
    )
    if not args_match:
        return set()

    args_section = args_match.group(1)
    return set(_DOCSTRING_PATH_RE.findall(args_section))


# ---------------------------------------------------------------------------
# Model alias resolution
# ---------------------------------------------------------------------------

_URL_RE = re.compile(r'https?://[^\s,\'"<>)}\]]+')


def _normalize(text: str) -> str:
    """Lowercase, strip non-alphanumeric for fuzzy matching."""
    return re.sub(r'[^a-z0-9]', '', text.lower())


def _build_alias_map(description: Optional[str], param_name: str) -> Dict[str, str]:
    """Build a map of normalised-short-name → full OSS URL for a parameter.

    Parses the parameter's description block for HTTP(S) URLs and associates
    each URL's filename stem with the URL.  Also picks up dict-style aliases
    like ``'DPA2.4-7M': "https://..."`` commonly found in DPA docstrings.
    """
    if not description or not param_name:
        return {}

    # Find the block for this specific parameter in the Args section.
    # Uses a backreference (\1) to stop at the *next* param at the same
    # indent level, or at a blank line / end-of-string.
    pattern = re.compile(
        rf'^(\s+){re.escape(param_name)}\s*\(.*?\):\s*(.*?)'
        rf'(?=\n\1\w+\s*\(|\n\s*\n|\Z)',
        re.DOTALL | re.MULTILINE,
    )
    m = pattern.search(description)
    block = m.group(2) if m else description

    urls = _URL_RE.findall(block)
    if not urls:
        return {}

    alias_map: Dict[str, str] = {}
    for url in urls:
        # Stem of the URL filename:  .../dpa-2.4-7M.pt → dpa-2.4-7M
        fname = url.rstrip('/').rsplit('/', 1)[-1]
        stem = fname.rsplit('.', 1)[0] if '.' in fname else fname
        alias_map[_normalize(stem)] = url

    # Also pick up explicit dict keys like  'DPA2.4-7M': "url"
    dict_re = re.compile(r"['\"]([^'\"]+)['\"]\s*:\s*['\"](" + r'https?://[^\'"]+' + r")['\"]")
    for alias, url in dict_re.findall(block):
        alias_map[_normalize(alias)] = url

    return alias_map


def _resolve_model_aliases(
    args: Dict[str, Any],
    tool_description: Optional[str],
    path_keys: Set[str],
) -> Dict[str, Any]:
    """Replace short model names with their full OSS URLs.

    Only processes string arguments that:
    - are in *path_keys* (i.e. known Path-typed params),
    - are not already a URL or obvious local file path,
    - match an alias found in the tool description.
    """
    if not tool_description:
        return args

    out = dict(args)
    for key in path_keys:
        val = out.get(key)
        if not isinstance(val, str):
            continue
        val = val.strip()
        if not val:
            continue
        # Already a URL → skip
        parsed = urlparse(val)
        if parsed.scheme in ('http', 'https'):
            continue
        # Looks like a real local file path (has slash or common extension like .cif/.poscar)
        if '/' in val or '\\' in val:
            continue

        alias_map = _build_alias_map(tool_description, key)
        if not alias_map:
            continue

        norm = _normalize(val)
        # Exact normalised match
        if norm in alias_map:
            logger.info("Model alias resolved: %s → %s (param=%s)", val, alias_map[norm], key)
            out[key] = alias_map[norm]
            continue
        # Substring match (e.g. "DPA2.4-7M" matches "dpa247m" in the stem)
        for alias_norm, url in alias_map.items():
            if norm in alias_norm or alias_norm in norm:
                logger.info("Model alias fuzzy-resolved: %s → %s (param=%s)", val, url, key)
                out[key] = url
                break

    return out


# ---------------------------------------------------------------------------
# Path resolution helpers
# ---------------------------------------------------------------------------

def _is_local_path(value: Any) -> bool:
    if not value or not isinstance(value, str):
        return False
    value = value.strip()
    if not value:
        return False
    parsed = urlparse(value)
    if parsed.scheme in ('http', 'https'):
        return False
    if value.lower().startswith('local://'):
        return False
    return True


def _workspace_path_to_local(value: str, workspace_root: Path) -> Path:
    """Map /workspace/... or relative path to actual local Path under workspace_root."""
    value = value.strip().replace('\\', '/')
    if value.startswith('/workspace/'):
        rel = value[len('/workspace/'):].lstrip('/')
        return (workspace_root / rel).resolve()
    if value.startswith('/workspace'):
        rel = value[len('/workspace'):].lstrip('/')
        return (workspace_root / (rel or '.')).resolve()
    path = Path(value)
    if not path.is_absolute():
        return (workspace_root / path).resolve()
    return path


def _resolve_one(value: str, workspace_root: Path) -> str:
    """If value is a local path, upload to OSS and return the OSS URL."""
    if not _is_local_path(value):
        return value
    path = _workspace_path_to_local(value, workspace_root)
    if not path.exists():
        raise FileNotFoundError(
            f"Path argument file not found: {path}. "
            "For calculation MCP tools, input files must exist in workspace "
            "so they can be uploaded to OSS and passed as URL."
        )
    if not path.is_file():
        raise ValueError(
            f"Path argument is not a file: {path}. Only files can be uploaded to OSS."
        )
    try:
        return upload_file_to_oss(path, workspace_root)
    except Exception as e:
        raise RuntimeError(
            f"Cannot pass local file to calculation MCP: OSS upload required "
            f"but failed for {path}. Set OSS_ENDPOINT, OSS_BUCKET_NAME, "
            "OSS_ACCESS_KEY_ID, OSS_ACCESS_KEY_SECRET in .env."
        ) from e


# ---------------------------------------------------------------------------
# Main adaptor class
# ---------------------------------------------------------------------------

class CalculationPathAdaptor:
    """Bohrium storage + per-server executor/sync_tools.

    Sync tools → executor None; else Bohrium executor with env auth.
    """

    def __init__(self, calculation_executors: Optional[Dict[str, Any]] = None):
        self.calculation_executors = calculation_executors or {}

    def _resolve_executor(
        self,
        server_name: str,
        remote_tool_name: str,
        access_key: str | None = None,
        project_id: int | str | None = None,
        user_id: int | str | None = None,
    ) -> Optional[Dict[str, Any]]:
        """Return executor for this (server, tool)."""
        server_cfg = self.calculation_executors.get(server_name)
        if not server_cfg:
            return None
        sync_tools = server_cfg.get('sync_tools') or []
        if remote_tool_name in sync_tools:
            return None
        executor_map = server_cfg.get("executor_map")
        if executor_map and isinstance(executor_map, dict):
            tool_executor = executor_map.get(remote_tool_name)
            # Fallback: strip SDK-generated "submit_" prefix — the async
            # wrapper shares the same executor as the base tool.
            if not tool_executor and remote_tool_name.startswith("submit_"):
                tool_executor = executor_map.get(remote_tool_name[len("submit_"):])
            if tool_executor and isinstance(tool_executor, dict):
                return inject_bohrium_executor(
                    tool_executor,
                    access_key=access_key,
                    project_id=project_id,
                    user_id=user_id,
                )
        executor_template = server_cfg.get('executor')
        if not executor_template or not isinstance(executor_template, dict):
            return None
        return inject_bohrium_executor(
            executor_template,
            access_key=access_key,
            project_id=project_id,
            user_id=user_id,
        )

    def _is_async_remote_tool(self, server_name: str, remote_tool_name: str) -> bool:
        """Return True when tool should run as async remote execution."""
        server_cfg = self.calculation_executors.get(server_name)
        if not isinstance(server_cfg, dict):
            return False
        sync_tools = set(server_cfg.get("sync_tools") or [])
        if remote_tool_name in sync_tools:
            return False

        executor_map = server_cfg.get("executor_map")
        if isinstance(executor_map, dict):
            tool_executor = executor_map.get(remote_tool_name)
            if tool_executor is None and remote_tool_name.startswith("submit_"):
                tool_executor = executor_map.get(remote_tool_name[len("submit_"):])
            if _has_remote_profile(tool_executor):
                return True
        return _has_remote_profile(server_cfg.get("executor"))

    @staticmethod
    def _validate_executor_profile(
        executor: Optional[Dict[str, Any]],
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
                f"Executor missing 'machine.remote_profile' for '{server_name}_{remote_tool_name}'."
            )
        machine_type = remote_profile.get("machine_type")
        image_address = remote_profile.get("image_address")
        if not isinstance(machine_type, str) or not machine_type.strip():
            raise ValueError(
                f"Executor missing remote_profile.machine_type for '{server_name}_{remote_tool_name}'."
            )
        if not isinstance(image_address, str) or not image_address.strip():
            raise ValueError(
                f"Executor missing remote_profile.image_address for '{server_name}_{remote_tool_name}'."
            )

    def resolve_args(
        self,
        workspace_path: str,
        args: Dict[str, Any],
        tool_name: str,
        server_name: str,
        input_schema: Optional[Dict[str, Any]] = None,
        tool_description: Optional[str] = None,
        access_key: str | None = None,
        project_id: int | str | None = None,
        user_id: int | str | None = None,
    ) -> Dict[str, Any]:
        """Inject executor, storage and resolve Path-typed args → OSS URL.

        Path detection:
        1. Schema ``"format": "path"`` (primary).
        2. Description ``param_name (Path):`` (fallback when SDK strips format).
        """
        out = dict(args)
        remote_name = tool_name
        if server_name and tool_name.startswith(server_name + '_'):
            remote_name = tool_name[len(server_name) + 1:]

        is_async_tool = self._is_async_remote_tool(server_name, remote_name)
        if is_async_tool and not remote_name.startswith("submit_"):
            raise ValueError(
                f"Async tool '{tool_name}' is blocked for LLM runtime. "
                f"Use submit interface: '{server_name}_submit_*'."
            )

        # --- executor & storage injection ---
        if "executor" in out:
            logger.info("Ignoring user-provided executor for %s; using config executor.", tool_name)
        out['executor'] = self._resolve_executor(
            server_name,
            remote_name,
            access_key=access_key,
            project_id=project_id,
            user_id=user_id,
        )
        if is_async_tool:
            self._validate_executor_profile(
                out["executor"],
                server_name=server_name,
                remote_tool_name=remote_name,
            )
        out['storage'] = get_bohrium_storage_config(
            access_key=access_key,
            project_id=project_id,
            user_id=user_id,
        )

        # --- Detect path-typed params ---
        # Layer 1: schema format (works when MCP SDK preserves Pydantic format)
        path_arg_names = _path_keys_from_schema(input_schema)
        source = "schema"

        # Layer 2: description fallback (handles bohr-agent-sdk Path→str conversion)
        if not path_arg_names:
            path_arg_names = _path_keys_from_description(tool_description)
            if path_arg_names:
                source = "description"

        if path_arg_names:
            logger.debug(
                "Tool %s: Path params detected via %s: %s",
                remote_name,
                source,
                sorted(path_arg_names),
            )

        # --- Model alias resolution (DPA2.4-7M → OSS URL) ---
        if path_arg_names and tool_description:
            out = _resolve_model_aliases(out, tool_description, path_arg_names)

        # --- Upload local files → OSS ---
        if not path_arg_names or not workspace_path:
            return out

        workspace_root = Path(workspace_path).resolve()
        for key in sorted(path_arg_names):
            if key not in out:
                continue
            val = out[key]
            if isinstance(val, list):
                out[key] = [_resolve_one(str(v), workspace_root) for v in val]
            else:
                out[key] = _resolve_one(str(val), workspace_root)
        return out


def get_calculation_path_adaptor(
    mcp_config: Optional[Dict[str, Any]] = None,
) -> CalculationPathAdaptor:
    """Factory: return a CalculationPathAdaptor from mcp config."""
    executors = (
        (mcp_config or {}).get('calculation_executors')
        if mcp_config is not None
        else None
    )
    return CalculationPathAdaptor(calculation_executors=executors)
