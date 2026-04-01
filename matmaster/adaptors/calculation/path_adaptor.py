"""Path adaptor: Bohrium HTTPS storage and executor/sync logic for MCP tools.

For any MCP tool routed through this adaptor, file/path arguments must be URL-based
(OSS or HTTP). If the caller passes a local path, the adaptor uploads the file to OSS
and passes the resulting URL.

Path detection uses layers (in order); later layers **merge** into the result:
1. **Schema-driven**: ``"format": "path"`` in the JSON Schema (works when MCP SDK
   preserves Pydantic's format annotation).
2. **Description fallback**: parse the tool docstring for ``param_name (Path):``
   patterns (handles SDKs that convert ``Path -> str`` and strip the format).
3. **Param-name heuristic**: parameter names ending with ``_path`` (catches servers
   where both schema and docstring detection miss, e.g. ``processed_csv_path``).
4. **Config** (optional): ``mcp.calculation_executors.<server>.path_params_by_tool`` maps
   remote tool names to extra parameter names that must be treated as paths when present
   in ``inputSchema.properties`` (e.g. ``submit_run_gromacs`` -> ``input_files`` when
   MCP exposes ``List[Path]`` as a plain array of strings).

Model alias resolution: short model names like ``"DPA2.4-7M"`` are automatically
resolved to their full OSS URLs by matching against URLs found in the parameter's
description text.

Remote session support: when a ``session`` is provided to ``resolve_args()`` and the
session is non-local (SSH/Docker), file existence and download are performed via the
session's ``is_file`` / ``download`` methods rather than the local filesystem.
"""

from __future__ import annotations

import json
import logging
import re
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

if TYPE_CHECKING:
    pass

from .oss_io import upload_file_to_oss

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# JSON Schema ``format`` values that indicate a filesystem path.
# Pydantic v2 emits:
#   pathlib.Path          -> "format": "path"
#   pydantic.FilePath     -> "format": "file-path"
#   pydantic.DirectoryPath-> "format": "directory-path"
# ---------------------------------------------------------------------------
_PATH_FORMATS: frozenset[str] = frozenset(
    {
        'path',
        'file-path',
        'directory-path',
    }
)


# ---------------------------------------------------------------------------
# Executor helpers
# ---------------------------------------------------------------------------
def _has_remote_profile(executor_cfg: Any) -> bool:
    """Return True if executor config contains machine.remote_profile."""
    if not isinstance(executor_cfg, dict):
        return False
    machine = executor_cfg.get('machine')
    if not isinstance(machine, dict):
        return False
    remote_profile = machine.get('remote_profile')
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
                if (
                    isinstance(branch, dict)
                    and branch.get('format', '') in _PATH_FORMATS
                ):
                    return True
    return False


def _path_keys_from_schema(input_schema: dict[str, Any] | None) -> set[str]:
    """Derive path-typed parameter names from a JSON Schema ``"format"`` field."""
    if not input_schema or not isinstance(input_schema, dict):
        return set()

    props = input_schema.get('properties') or {}
    out: set[str] = set()

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
    r'(\w+)\s*\('  # param_name (
    r'\s*(?:Optional\[|List\[|Dict\[[\w,\s]*)?'  # optional wrapper
    r'Path'  # the keyword Path
    r'(?:\])*'  # closing brackets
    r'\s*\)',  # )
)


def _path_keys_from_description(description: str | None) -> set[str]:
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
# Layer 3: parameter-name heuristic
# ---------------------------------------------------------------------------


def _path_keys_from_param_names(input_schema: dict[str, Any] | None) -> set[str]:
    """Heuristic: parameter names ending with ``_path`` likely accept file paths.

    Catches common conventions like ``processed_csv_path``, ``img_path``,
    ``file_path`` even when both schema format and docstring detection miss.
    """
    if not input_schema or not isinstance(input_schema, dict):
        return set()
    props = input_schema.get('properties') or {}
    return {k for k in props if k.endswith('_path')}


def _brief_json_schema_prop(spec: Any, depth: int = 0) -> Any:
    """Compact view of one JSON Schema property for logs (type/format/items/union)."""
    if depth > 4:
        return '...'
    if not isinstance(spec, dict):
        return type(spec).__name__
    out: dict[str, Any] = {}
    for k in ('title', 'description'):
        if k in spec and isinstance(spec[k], str) and len(spec[k]) > 120:
            out[k] = spec[k][:117] + '...'
        elif k in spec:
            out[k] = spec[k]
    if 'type' in spec:
        out['type'] = spec['type']
    if 'format' in spec:
        out['format'] = spec['format']
    items = spec.get('items')
    if isinstance(items, dict):
        out['items'] = _brief_json_schema_prop(items, depth + 1)
    for branch_key in ('anyOf', 'oneOf', 'allOf'):
        branches = spec.get(branch_key)
        if isinstance(branches, list):
            out[branch_key] = [
                _brief_json_schema_prop(b, depth + 1) if isinstance(b, dict) else b
                for b in branches[:6]
            ]
            if len(branches) > 6:
                out[f'{branch_key}_len'] = len(branches)
    if not out and spec:
        out['_keys'] = sorted(spec.keys())
    return out


def _input_schema_params_brief(input_schema: dict[str, Any] | None) -> dict[str, Any]:
    """Per-parameter type summary from MCP ``inputSchema`` (``properties``)."""
    if not input_schema or not isinstance(input_schema, dict):
        return {}
    props = input_schema.get('properties') or {}
    return {k: _brief_json_schema_prop(v) for k, v in sorted(props.items())}


# ---------------------------------------------------------------------------
# Model alias resolution
# ---------------------------------------------------------------------------

_URL_RE = re.compile(r'https?://[^\s,\'"<>)}\]]+')


def _normalize(text: str) -> str:
    """Lowercase, strip non-alphanumeric for fuzzy matching."""
    return re.sub(r'[^a-z0-9]', '', text.lower())


def _build_alias_map(description: str | None, param_name: str) -> dict[str, str]:
    """Build a map of normalised-short-name -> full OSS URL for a parameter.

    Parses the parameter's description block for HTTP(S) URLs and associates
    each URL's filename stem with the URL.  Also picks up dict-style aliases
    like ``'DPA2.4-7M': "https://..."`` commonly found in DPA docstrings.
    """
    if not description or not param_name:
        return {}

    # Find the block for this specific parameter in the Args section.
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

    alias_map: dict[str, str] = {}
    for url in urls:
        # Stem of the URL filename:  .../dpa-2.4-7M.pt -> dpa-2.4-7M
        fname = url.rstrip('/').rsplit('/', 1)[-1]
        stem = fname.rsplit('.', 1)[0] if '.' in fname else fname
        alias_map[_normalize(stem)] = url

    # Also pick up explicit dict keys like  'DPA2.4-7M': "url"
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
        # Already a URL -> skip
        parsed = urlparse(val)
        if parsed.scheme in ('http', 'https'):
            continue
        # Looks like a real local file path (has slash or common extension)
        if '/' in val or '\\' in val:
            continue

        alias_map = _build_alias_map(tool_description, key)
        if not alias_map:
            continue

        norm = _normalize(val)
        # Exact normalised match
        if norm in alias_map:
            logger.info(
                'Model alias resolved: %s -> %s (param=%s)', val, alias_map[norm], key
            )
            out[key] = alias_map[norm]
            continue
        # Substring match (e.g. "DPA2.4-7M" matches "dpa247m" in the stem)
        for alias_norm, url in alias_map.items():
            if norm in alias_norm or alias_norm in norm:
                logger.info(
                    'Model alias fuzzy-resolved: %s -> %s (param=%s)', val, url, key
                )
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
    """Map workspace-prefixed, workspace_root-prefixed, or relative path to local Path."""
    value = value.strip().replace('\\', '/')
    ws_str = str(workspace_root).replace('\\', '/').rstrip('/')
    # Try stripping known prefixes (most specific first).
    for prefix in (ws_str, '/workspace'):
        if value.startswith(prefix + '/'):
            rel = value[len(prefix) + 1 :].lstrip('/')
            return (workspace_root / rel).resolve()
        if value == prefix:
            return workspace_root.resolve()
    path = Path(value)
    if not path.is_absolute():
        return (workspace_root / path).resolve()
    return path


def _is_remote_session(session: Any) -> bool:
    """Return True when *session* is a non-local session (SSH / Docker).

    Uses duck-typing to avoid importing session types from evomaster
    at module level.
    """
    if session is None:
        return False
    return 'Local' not in type(session).__name__


def _resolve_one(
    value: str,
    workspace_root: Path,
    session: Any = None,
) -> str:
    """If value is a local path, upload to OSS and return the OSS URL.

    When *session* is a non-local session (SSH/Docker), file existence and
    content are fetched via the session's ``is_file`` / ``download`` methods
    so that files on the remote data disk can be uploaded without first
    copying them to the agent machine.
    """
    if not _is_local_path(value):
        return value

    if _is_remote_session(session):
        # Build the remote absolute path the same way as the local case.
        remote_path = str(_workspace_path_to_local(value, workspace_root)).replace(
            '\\', '/'
        )
        try:
            if not session.is_file(remote_path):  # type: ignore[union-attr]
                raise FileNotFoundError(
                    f"Path argument file not found on remote: {remote_path}. "
                    'For calculation MCP tools, input files must exist in the '
                    'remote workspace so they can be uploaded to OSS.'
                )
            data = session.download(remote_path)  # type: ignore[union-attr]
        except FileNotFoundError:
            raise
        except Exception as e:
            raise RuntimeError(
                f"Failed to read remote file {remote_path} for OSS upload: {e}"
            ) from e

        suffix = Path(remote_path).suffix or ''
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(data)
            tmp_path = Path(tmp.name)
        try:
            return upload_file_to_oss(
                tmp_path,
                tmp_path.parent,
                object_basename=Path(remote_path).name,
            )
        except Exception as e:
            raise RuntimeError(
                f"Cannot pass remote file to calculation MCP: OSS upload required "
                f"but failed for {remote_path}. Set OSS_ENDPOINT, OSS_BUCKET_NAME, "
                'OSS_ACCESS_KEY_ID, OSS_ACCESS_KEY_SECRET in .env.'
            ) from e
        finally:
            tmp_path.unlink(missing_ok=True)

    path = _workspace_path_to_local(value, workspace_root)
    if not path.exists():
        raise FileNotFoundError(
            f"Path argument file not found: {path}. "
            'For calculation MCP tools, input files must exist in workspace '
            'so they can be uploaded to OSS and passed as URL.'
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
            'OSS_ACCESS_KEY_ID, OSS_ACCESS_KEY_SECRET in .env.'
        ) from e


# ---------------------------------------------------------------------------
# Main adaptor class
# ---------------------------------------------------------------------------


class CalculationPathAdaptor:
    """Bohrium storage + per-server executor/sync_tools.

    For tools covered by this adaptor:
    - local file paths are rewritten to OSS URLs
    - ``sync_tools`` (MatMaster-aligned): always use ``type: local`` +
      ``inject_bohrium_executor`` (Bohrium AK in ``executor.env``), overriding
      server-level dispatcher / executor_map for those tool names. MCP-side
      ``submit_*`` filtering still uses ``sync_tools`` (see MCPToolManager).
    """

    def __init__(self, calculation_executors: dict[str, Any] | None = None):
        self.calculation_executors = calculation_executors or {}

    def _path_keys_from_tool_config(
        self,
        server_name: str,
        remote_tool_name: str,
        input_schema: dict[str, Any] | None,
    ) -> set[str]:
        """Extra path param names from ``path_params_by_tool`` (must exist in schema)."""
        server_cfg = self.calculation_executors.get(server_name)
        if not isinstance(server_cfg, dict):
            return set()
        mapping = server_cfg.get('path_params_by_tool')
        if not isinstance(mapping, dict):
            return set()
        raw = mapping.get(remote_tool_name)
        if raw is None and remote_tool_name.startswith('submit_'):
            raw = mapping.get(remote_tool_name[len('submit_') :])
        if raw is None:
            return set()
        if isinstance(raw, str):
            names = [raw]
        elif isinstance(raw, (list, tuple)):
            names = [x for x in raw if isinstance(x, str)]
        else:
            return set()
        props = set((input_schema or {}).get('properties') or {})
        return {n for n in names if n in props}

    def _resolve_executor(
        self,
        server_name: str,
        remote_tool_name: str,
        access_key: str | None = None,
        project_id: int | str | None = None,
        user_id: int | str | None = None,
        user_no: str | None = None,
    ) -> dict[str, Any] | None:
        """Return executor for this (server, tool).

        Bohrium executor injection is done via lazy import to avoid
        top-level evomaster dependency (per D-08).
        """
        from matmaster.integration.bohrium_env import inject_bohrium_executor

        server_cfg = self.calculation_executors.get(server_name)
        if not server_cfg:
            return None
        sync_tools = server_cfg.get('sync_tools') or []
        if remote_tool_name in sync_tools:
            return inject_bohrium_executor(
                {'type': 'local', 'env': {}},
                access_key=access_key,
                project_id=project_id,
                user_id=user_id,
                user_no=user_no,
            )
        executor_map = server_cfg.get('executor_map')
        if executor_map and isinstance(executor_map, dict):
            tool_executor = executor_map.get(remote_tool_name)
            if not tool_executor and remote_tool_name.startswith('submit_'):
                tool_executor = executor_map.get(remote_tool_name[len('submit_') :])
            if tool_executor and isinstance(tool_executor, dict):
                return inject_bohrium_executor(
                    tool_executor,
                    access_key=access_key,
                    project_id=project_id,
                    user_id=user_id,
                    user_no=user_no,
                )
        executor_template = server_cfg.get('executor')
        if not executor_template or not isinstance(executor_template, dict):
            return None
        return inject_bohrium_executor(
            executor_template,
            access_key=access_key,
            project_id=project_id,
            user_id=user_id,
            user_no=user_no,
        )

    def _is_async_remote_tool(self, server_name: str, remote_tool_name: str) -> bool:
        """Return True when tool should run as async remote execution."""
        server_cfg = self.calculation_executors.get(server_name)
        if not isinstance(server_cfg, dict):
            return False
        sync_tools = set(server_cfg.get('sync_tools') or [])
        if remote_tool_name in sync_tools:
            return False

        executor_map = server_cfg.get('executor_map')
        if isinstance(executor_map, dict):
            tool_executor = executor_map.get(remote_tool_name)
            if tool_executor is None and remote_tool_name.startswith('submit_'):
                tool_executor = executor_map.get(remote_tool_name[len('submit_') :])
            if _has_remote_profile(tool_executor):
                return True
        return _has_remote_profile(server_cfg.get('executor'))

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
                'Check calculation_executors config.'
            )
        machine = executor.get('machine')
        if not isinstance(machine, dict):
            raise ValueError(
                f"Executor missing 'machine' for '{server_name}_{remote_tool_name}'."
            )
        remote_profile = machine.get('remote_profile')
        if not isinstance(remote_profile, dict):
            raise ValueError(
                f"Executor missing 'machine.remote_profile' for "
                f"'{server_name}_{remote_tool_name}'."
            )
        machine_type = remote_profile.get('machine_type')
        image_address = remote_profile.get('image_address')
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

    def resolve_args(
        self,
        workspace_path: str,
        args: dict[str, Any],
        tool_name: str,
        server_name: str,
        input_schema: dict[str, Any] | None = None,
        tool_description: str | None = None,
        access_key: str | None = None,
        project_id: int | str | None = None,
        user_id: int | str | None = None,
        user_no: str | None = None,
        session: Any = None,
    ) -> dict[str, Any]:
        """Inject executor, storage and resolve Path-typed args -> OSS URL.

        Path detection:
        1. Schema ``"format": "path"`` (primary).
        2. Description ``param_name (Path):`` (fallback when SDK strips format).
        3. Param names ending with ``_path``; 4. ``path_params_by_tool`` in config.

        When *session* is a non-local session (SSH/Docker), remote files are
        fetched via SFTP and uploaded to OSS without touching the local filesystem.
        """
        # Lazy import: avoid top-level evomaster dependency (per D-08)
        from matmaster.integration.bohrium_env import get_bohrium_storage_config

        out = dict(args)
        remote_name = tool_name
        if server_name and tool_name.startswith(server_name + '_'):
            remote_name = tool_name[len(server_name) + 1 :]

        # Block submit_* when the base tool is in sync_tools (sync version only).
        server_cfg = self.calculation_executors.get(server_name)
        if isinstance(server_cfg, dict):
            sync_tools = set(server_cfg.get('sync_tools') or [])
            if remote_name.startswith('submit_'):
                base_name = remote_name[len('submit_') :]
                if base_name in sync_tools:
                    raise ValueError(
                        f"Tool '{tool_name}' is blocked: '{base_name}' is a sync "
                        f"tool. Use sync interface: '{server_name}_{base_name}' "
                        f"instead of submit_*."
                    )

        is_async_tool = self._is_async_remote_tool(server_name, remote_name)
        if is_async_tool and not remote_name.startswith('submit_'):
            raise ValueError(
                f"Async tool '{tool_name}' is blocked for LLM runtime. "
                f"Use submit interface: '{server_name}_submit_*'."
            )

        # --- executor & storage injection ---
        if 'executor' in out:
            logger.info(
                'Ignoring user-provided executor for %s; using config executor.',
                tool_name,
            )
        out['executor'] = self._resolve_executor(
            server_name,
            remote_name,
            access_key=access_key,
            project_id=project_id,
            user_id=user_id,
            user_no=user_no,
        )
        if is_async_tool:
            self._validate_executor_profile(
                out['executor'],
                server_name=server_name,
                remote_tool_name=remote_name,
            )
        out['storage'] = get_bohrium_storage_config(
            access_key=access_key,
            project_id=project_id,
            user_id=user_id,
        )

        # --- Detect path-typed params ---
        _schema_keys = _path_keys_from_schema(input_schema)
        _desc_keys = _path_keys_from_description(tool_description)
        _param_keys = _path_keys_from_param_names(input_schema)

        if _schema_keys:
            path_arg_names = _schema_keys
            source = 'schema'
        elif _desc_keys:
            path_arg_names = _desc_keys
            source = 'description'
        elif _param_keys:
            path_arg_names = _param_keys
            source = 'param_name'
        else:
            path_arg_names = set()
            source = 'schema'

        _before_config_keys = set(path_arg_names)
        _config_keys = self._path_keys_from_tool_config(
            server_name, remote_name, input_schema
        )
        if _config_keys:
            path_arg_names = path_arg_names | _config_keys
            if not _before_config_keys:
                source = 'config'

        _td = tool_description if isinstance(tool_description, str) else ''
        _desc_len = len(_td)
        _desc_has_args_header = bool(re.search(r'Args:\s*\n', _td))

        _schema_props = (
            sorted((input_schema or {}).get('properties') or {})
            if isinstance(input_schema, dict)
            else []
        )
        _has_input_files_prop = 'input_files' in (
            (input_schema or {}).get('properties') or {}
        )
        _params_brief = _input_schema_params_brief(
            input_schema if isinstance(input_schema, dict) else None
        )
        logger.info(
            'Path adaptor [%s] path_keys=%s source=%s L1_schema=%s L2_description=%s '
            'L3_param_name=%s L4_config=%s tool_description_len=%s '
            'tool_description_has_args_header=%s workspace_path_set=%s schema_props=%s '
            'input_files_in_schema=%s input_files_classified=%s '
            'schema_params_brief=%s',
            tool_name,
            sorted(path_arg_names),
            source,
            sorted(_schema_keys),
            sorted(_desc_keys),
            sorted(_param_keys),
            sorted(_config_keys),
            _desc_len,
            _desc_has_args_header,
            bool(workspace_path and str(workspace_path).strip()),
            _schema_props,
            bool(_has_input_files_prop),
            'input_files' in path_arg_names,
            json.dumps(_params_brief, ensure_ascii=False, default=str),
        )
        if _has_input_files_prop:
            _raw_if = (
                ((input_schema or {}).get('properties') or {}).get('input_files')
            )
            logger.info(
                'Path adaptor [%s] input_files raw schema (JSON): %s',
                tool_name,
                json.dumps(_raw_if, ensure_ascii=False, default=str),
            )
        if _has_input_files_prop and 'input_files' not in path_arg_names:
            logger.info(
                'Path adaptor [%s] tool_description_head (first 400 chars, repr): %s',
                tool_name,
                repr(_td[:400]) if _td else repr(_td),
            )
            logger.warning(
                'Path adaptor [%s]: schema has input_files but it was not classified '
                'as path params (no format/path in schema, docstring Path, *_path '
                'name, or path_params_by_tool); local paths may be sent to MCP '
                'unchanged.',
                tool_name,
            )

        if path_arg_names:
            logger.debug(
                'Tool %s: Path params detail via %s: %s',
                remote_name,
                source,
                sorted(path_arg_names),
            )

        # --- Model alias resolution (DPA2.4-7M -> OSS URL) ---
        if path_arg_names and tool_description:
            out = _resolve_model_aliases(out, tool_description, path_arg_names)

        # --- Upload local files -> OSS ---
        if not path_arg_names or not workspace_path:
            if not path_arg_names:
                logger.info(
                    'Path adaptor [%s]: skip OSS upload (no path-typed params '
                    'detected).',
                    tool_name,
                )
            elif not workspace_path:
                logger.info(
                    'Path adaptor [%s]: skip OSS upload (workspace_path empty).',
                    tool_name,
                )
            return out

        def _is_null_or_empty_path(v: Any) -> bool:
            if v is None:
                return True
            if isinstance(v, str) and v.strip().lower() in ('none', 'null', ''):
                return True
            return False

        workspace_root = Path(workspace_path).resolve()
        logger.info(
            'Path adaptor [%s]: resolving local paths -> OSS '
            '(keys=%s workspace_root=%s)',
            tool_name,
            sorted(path_arg_names),
            workspace_root,
        )
        for key in sorted(path_arg_names):
            if key not in out:
                continue
            val = out[key]
            if _is_null_or_empty_path(val):
                out[key] = None
                continue
            if isinstance(val, list):
                out[key] = [
                    (
                        None
                        if _is_null_or_empty_path(v)
                        else _resolve_one(str(v), workspace_root, session)
                    )
                    for v in val
                ]
            else:
                out[key] = _resolve_one(str(val), workspace_root, session)
        return out


def get_calculation_path_adaptor(
    mcp_config: dict[str, Any] | None = None,
) -> CalculationPathAdaptor:
    """Factory: return a CalculationPathAdaptor from mcp config."""
    executors = (
        (mcp_config or {}).get('calculation_executors')
        if mcp_config is not None
        else None
    )
    return CalculationPathAdaptor(calculation_executors=executors)
