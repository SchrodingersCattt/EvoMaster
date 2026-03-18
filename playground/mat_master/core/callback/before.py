"""Before-tool callbacks: skill args, DPA alias, monitor_job, NMR upload, SN words."""

import json
import os
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from .constants import (
    _DPA_MODEL_ALIAS_NORM_MAP,
    _MOL_FILE_EXTS,
    _normalize_alias,
)


class MatToolCallbacksBefore:
    """Mixin: all before_* callbacks and helpers used only by them."""

    def _resolve_workspace_root(self) -> str | None:
        """Return workspace root as a string path."""
        workspace = (
            getattr(getattr(self.agent.session, 'config', None), 'workspace_path', None)
            or ''
        )
        if not workspace:
            return None
        if self._is_remote:
            return workspace
        return str(Path(workspace).resolve())

    def _resolve_file_path(self, value: str, workspace_root: str) -> str | None:
        """Resolve a user-provided file path against *workspace_root*."""
        mount_prefix = os.environ.get('WORKSPACE_MOUNT_PREFIX', '/workspace')
        value = value.strip().replace('\\', '/')
        prefix_slash = mount_prefix.rstrip('/') + '/'

        if self._is_remote:
            pp = PurePosixPath
            if value.startswith(prefix_slash):
                rel = value[len(prefix_slash) :].lstrip('/')
                return str(pp(workspace_root) / rel)
            if value.startswith(mount_prefix):
                rel = value[len(mount_prefix) :].lstrip('/')
                return str(pp(workspace_root) / (rel or '.'))
            if not PurePosixPath(value).is_absolute():
                return str(pp(workspace_root) / value)
            return value
        else:
            ws = Path(workspace_root)
            if value.startswith(prefix_slash):
                rel = value[len(prefix_slash) :].lstrip('/')
                return str((ws / rel).resolve())
            if value.startswith(mount_prefix):
                rel = value[len(mount_prefix) :].lstrip('/')
                return str((ws / (rel or '.')).resolve())
            path = Path(value)
            if not path.is_absolute():
                return str((ws / path).resolve())
            return str(path)

    def _file_exists_and_is_file(self, path: str) -> bool:
        """Check if *path* exists and is a regular file (local or remote)."""
        if self._is_remote:
            return self._session.is_file(path)
        return Path(path).is_file()

    def _download_remote_to_temp(self, remote_path: str) -> Path:
        """Download a remote file to a local temp file for OSS upload."""
        data = self._session.download(remote_path)
        suffix = PurePosixPath(remote_path).suffix or ''
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        tmp.write(data)
        tmp.close()
        return Path(tmp.name)

    def before_normalize_skill_script_args(self, tool_call: Any) -> None:
        """Unwrap redundant outer quotes around ``script_args``."""
        if (tool_call.function.name or '') != 'use_skill':
            return
        args_str = tool_call.function.arguments or ''
        try:
            args = json.loads(args_str) if args_str else {}
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(args, dict):
            return
        if args.get('action') != 'run_script':
            return

        script_args = args.get('script_args')
        if not isinstance(script_args, str) or not script_args.strip():
            return

        cleaned = self._unwrap_quoted_args(script_args)
        if cleaned != script_args:
            args['script_args'] = cleaned
            tool_call.function.arguments = json.dumps(args, ensure_ascii=False)
            self.logger.info(
                'before_tool: unwrapped outer quotes in script_args: %r -> %r',
                script_args,
                cleaned,
            )

    def before_resolve_skill_reference_name(self, tool_call: Any) -> None:
        """Auto-resolve bare reference filenames to their full sub-path."""
        if (tool_call.function.name or '') != 'use_skill':
            return
        args_str = tool_call.function.arguments or ''
        try:
            args = json.loads(args_str) if args_str else {}
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(args, dict):
            return
        if args.get('action') != 'get_reference':
            return

        ref_name = args.get('reference_name')
        if not isinstance(ref_name, str) or not ref_name.strip():
            return
        ref_name = ref_name.strip()
        if '/' in ref_name or '\\' in ref_name:
            return

        skill_name = args.get('skill_name')
        if not skill_name:
            return

        registry = getattr(self.agent, 'skill_registry', None)
        if registry is None:
            return
        skill = registry.get_skill(skill_name)
        if skill is None:
            return

        candidates: list[Path] = []
        for root_name in ('references', 'reference', 'prompts'):
            root = skill.skill_path / root_name
            if root.exists():
                try:
                    candidates.extend(p for p in root.rglob(ref_name) if p.is_file())
                except Exception:
                    pass

        if len(candidates) == 1:
            matched = candidates[0]
            for root_name in ('references', 'reference', 'prompts'):
                root = skill.skill_path / root_name
                if root.exists():
                    try:
                        rel = matched.relative_to(root).as_posix()
                        args['reference_name'] = rel
                        tool_call.function.arguments = json.dumps(
                            args, ensure_ascii=False
                        )
                        self.logger.info(
                            'before_tool: resolved reference name %r -> %r',
                            ref_name,
                            rel,
                        )
                        return
                    except ValueError:
                        continue

    def before_resolve_dpa_model_alias(self, tool_call: Any) -> None:
        """Resolve DPA short model key to hard-coded OSS URL."""
        tool_name = tool_call.function.name or ''
        if not (
            tool_name.startswith('mat_dpa_') or tool_name.startswith('mat_compdart_')
        ):
            return
        args_str = tool_call.function.arguments or ''
        try:
            args = json.loads(args_str) if args_str else {}
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(args, dict):
            return
        model_path = args.get('model_path')
        if not isinstance(model_path, str):
            return
        norm = _normalize_alias(model_path)
        resolved = _DPA_MODEL_ALIAS_NORM_MAP.get(norm)
        if not resolved:
            return
        args['model_path'] = resolved
        tool_call.function.arguments = json.dumps(args, ensure_ascii=False)
        self.logger.info(
            'before_tool: resolved DPA model alias %s -> %s',
            model_path,
            resolved,
        )

    def before_patch_monitor_job_bohr_id(self, tool_call: Any) -> None:
        """Auto-fill missing bohr_job_id for monitor_job calls."""
        tool_name = tool_call.function.name or ''
        if tool_name != 'monitor_job':
            return

        args_str = tool_call.function.arguments or ''
        try:
            args = json.loads(args_str) if args_str else {}
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(args, dict):
            return

        if args.get('bohr_job_id'):
            return
        job_id = args.get('job_id')
        if not job_id:
            return
        bohr_map = self._collect_submit_job_map()
        bohr_job_id = bohr_map.get(job_id)
        if not bohr_job_id:
            return
        args['bohr_job_id'] = bohr_job_id
        tool_call.function.arguments = json.dumps(args, ensure_ascii=False)
        self.logger.info(
            'before_tool: patched monitor_job bohr_job_id for job_id=%s',
            job_id,
        )

    def before_upload_nmr_predict_files(self, tool_call: Any) -> None:
        """Upload molecular file paths in NMR_predict_tool's ``smiles_list`` to OSS."""
        tool_name = tool_call.function.name or ''
        if tool_name != 'mat_nmr_NMR_predict_tool':
            return
        args_str = tool_call.function.arguments or ''
        try:
            args = json.loads(args_str) if args_str else {}
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(args, dict):
            return
        smiles_list = args.get('smiles_list')
        if not isinstance(smiles_list, list):
            return

        workspace_root = self._resolve_workspace_root()
        if workspace_root is None:
            return

        changed = False
        new_list: list[Any] = []
        for item in smiles_list:
            if not isinstance(item, str) or not item.strip():
                new_list.append(item)
                continue
            val = item.strip()
            if val.startswith('http://') or val.startswith('https://'):
                new_list.append(item)
                continue
            is_file = '/' in val or '\\' in val
            if not is_file:
                suffix = PurePosixPath(val).suffix.lower()
                is_file = suffix in _MOL_FILE_EXTS
            if not is_file:
                new_list.append(item)
                continue

            resolved = self._resolve_file_path(val, workspace_root)
            if resolved is None or not self._file_exists_and_is_file(resolved):
                self.logger.warning(
                    'NMR_predict_tool: file in smiles_list not found: %s', val
                )
                new_list.append(item)
                continue

            local_for_upload = Path(resolved) if not self._is_remote else None
            try:
                if self._is_remote:
                    local_for_upload = self._download_remote_to_temp(resolved)

                from evomaster.adaptors.calculation.oss_io import upload_file_to_oss

                oss_url = upload_file_to_oss(local_for_upload, Path(workspace_root))
                new_list.append(oss_url)
                changed = True
                self.logger.info(
                    'before_tool: uploaded NMR predict file %s -> %s', val, oss_url
                )
            except Exception as e:
                self.logger.warning(
                    'before_tool: NMR predict file upload failed %s: %s', val, e
                )
                new_list.append(item)
            finally:
                if self._is_remote and local_for_upload is not None:
                    local_for_upload.unlink(missing_ok=True)

        if changed:
            args['smiles_list'] = new_list
            tool_call.function.arguments = json.dumps(args, ensure_ascii=False)

    def before_normalize_sn_search_words_parameter(self, tool_call: Any) -> None:
        """Convert 'words' parameter from string to native list for mat_sn_search-papers-enhanced."""
        tool_name = tool_call.function.name or ''
        if tool_name != 'mat_sn_search-papers-enhanced':
            return

        args_str = tool_call.function.arguments or ''
        try:
            args = json.loads(args_str) if args_str else {}
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(args, dict):
            return

        words = args.get('words')
        if words is None:
            return

        if isinstance(words, list):
            return

        if isinstance(words, str):
            cleaned = words.strip()
            if cleaned.startswith('[') and cleaned.endswith(']'):
                try:
                    parsed = json.loads(cleaned)
                    if isinstance(parsed, list):
                        args['words'] = parsed
                        tool_call.function.arguments = json.dumps(
                            args, ensure_ascii=False
                        )
                        self.logger.info(
                            'before_tool: converted words from string to list for mat_sn_search-papers-enhanced: %r -> %r',
                            words,
                            parsed,
                        )
                        return
                except (json.JSONDecodeError, TypeError):
                    pass
