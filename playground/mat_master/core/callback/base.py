"""MatToolCallbacks base: __init__, register(), and shared helpers."""

import json
import re
import tempfile
import threading
import urllib.request
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from .constants import (
    _AUTO_DOWNLOAD_MAX_BYTES,
    _DEFAULT_DOWNLOAD_SUBDIR,
)
from .pipeline import ToolCallbackPipeline

if TYPE_CHECKING:
    from ..agent import MatMasterAgent


class MatToolCallbacksBase:
    """Base for MAT callbacks: init, register, and helpers used by before/after."""

    def __init__(
        self,
        agent: 'MatMasterAgent',
        *,
        download_subdir: str = _DEFAULT_DOWNLOAD_SUBDIR,
    ) -> None:
        self.agent = agent
        self.logger = agent.logger
        self._download_subdir = download_subdir
        self._ensured_download_dirs: set[str] = set()
        self._ensure_dir_lock = threading.Lock()

    def register(self, pipeline: ToolCallbackPipeline) -> None:
        """Register all MAT callbacks in execution order."""
        pipeline.register_before(self.before_normalize_skill_script_args)
        pipeline.register_before(self.before_resolve_skill_reference_name)
        pipeline.register_before(self.before_resolve_dpa_model_alias)
        pipeline.register_before(self.before_patch_monitor_job_bohr_id)
        pipeline.register_before(self.before_normalize_sn_search_words_parameter)
        pipeline.register_before(self.before_upload_nmr_predict_files)
        pipeline.register_after(self.after_detect_mcp_business_error)
        pipeline.register_after(self.after_ask_human_interaction)
        pipeline.register_after(self.after_track_async_submit)
        pipeline.register_after(self.after_autodownload_oss_results)
        pipeline.register_after(self.after_download_characterization_results)
        pipeline.register_after(self.after_normalize_struct_db_metadata)
        pipeline.register_after(self.after_clean_sn_response)
        pipeline.register_after(self.after_survey_reminder)

    @property
    def _session(self):
        """Shortcut for ``self.agent.session``."""
        return self.agent.session

    @property
    def _is_remote(self) -> bool:
        """True when the session is remote (SSH / Docker), not local."""
        cls_name = type(self._session).__name__
        return 'Local' not in cls_name

    @staticmethod
    def _is_oss_url(url: str) -> bool:
        try:
            host = urlparse(url).netloc.lower()
        except Exception:
            return False
        return ('aliyuncs.com' in host) or ('.oss-' in host) or host.startswith('oss-')

    def _pick_download_path(self, download_dir: str, url: str) -> str:
        """Return a unique destination path (string) under *download_dir*."""
        parsed = urlparse(url)
        name = PurePosixPath(parsed.path).name or 'artifact.bin'

        if self._is_remote:
            base = f"{download_dir.rstrip('/')}/{name}"
            if not self._session.path_exists(base):
                return base
            stem = PurePosixPath(name).stem
            suffix = PurePosixPath(name).suffix
            i = 1
            while True:
                candidate = f"{download_dir.rstrip('/')}/{stem}_{i}{suffix}"
                if not self._session.path_exists(candidate):
                    return candidate
                i += 1
        else:
            base = Path(download_dir) / name
            if not base.exists():
                return str(base)
            stem = base.stem
            suffix = base.suffix
            i = 1
            while True:
                candidate = Path(download_dir) / f"{stem}_{i}{suffix}"
                if not candidate.exists():
                    return str(candidate)
                i += 1

    def _to_workspace_rel_path(self, abs_path: str) -> str:
        """Convert an absolute download path to a workspace-relative path."""
        workspace = (
            getattr(getattr(self.agent.session, 'config', None), 'workspace_path', None)
            or ''
        )
        if not workspace:
            return abs_path
        try:
            if self._is_remote:
                ws = workspace.replace('\\', '/').rstrip('/')
                norm = abs_path.replace('\\', '/')
                if norm.startswith(ws + '/'):
                    return norm[len(ws) + 1 :]
                if norm == ws:
                    return '.'
            else:
                ws_path = Path(workspace).resolve()
                file_path = Path(abs_path).resolve()
                rel = file_path.relative_to(ws_path)
                return rel.as_posix()
        except (ValueError, TypeError):
            pass
        return abs_path

    def _resolve_download_dir(self) -> str | None:
        """Derive download directory from the agent's workspace config."""
        workspace = (
            getattr(getattr(self.agent.session, 'config', None), 'workspace_path', None)
            or ''
        )
        if not workspace:
            return None
        if self._is_remote:
            workspace = workspace.replace('\\', '/')
            subdir = self._download_subdir
            if subdir:
                return f"{workspace.rstrip('/')}/{subdir}"
            return workspace.rstrip('/')
        if self._download_subdir:
            return str((Path(workspace).resolve() / self._download_subdir).resolve())
        return str(Path(workspace).resolve())

    def _ensure_download_dir(self, download_dir: str) -> None:
        """Create *download_dir* if it does not exist."""
        with self._ensure_dir_lock:
            if download_dir in self._ensured_download_dirs:
                return
            if self._is_remote:
                try:
                    self._session.exec_bash(
                        f"mkdir -p '{download_dir}'",
                        timeout=30,
                    )
                except Exception as e:
                    self.logger.warning(
                        '[autodownload] _ensure_download_dir remote mkdir failed: %s',
                        e,
                    )
                    return
            else:
                Path(download_dir).mkdir(parents=True, exist_ok=True)
            self._ensured_download_dirs.add(download_dir)

    def _download_single(self, url: str, download_dir: str) -> str | None:
        """Download a single URL to *download_dir*. Returns dest path string or None."""
        self.logger.info(
            '[autodownload] _download_single start url=%s dir=%s is_remote=%s',
            url[:80],
            download_dir,
            self._is_remote,
        )
        dest = self._pick_download_path(download_dir, url)
        req = urllib.request.Request(url, method='GET')
        with urllib.request.urlopen(req, timeout=60) as resp:  # nosec B310
            content_len = resp.headers.get('Content-Length')
            if content_len:
                try:
                    if int(content_len) > _AUTO_DOWNLOAD_MAX_BYTES:
                        self.logger.info(
                            'Skip large OSS file: %s (%s bytes)', url, content_len
                        )
                        return None
                except ValueError:
                    pass
            data = resp.read(_AUTO_DOWNLOAD_MAX_BYTES + 1)
            if len(data) > _AUTO_DOWNLOAD_MAX_BYTES:
                self.logger.info('Skip oversized OSS payload during read: %s', url)
                return None

        if self._is_remote:
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                tmp.write(data)
                tmp_path = tmp.name
            try:
                self._session.upload(tmp_path, dest)
            finally:
                Path(tmp_path).unlink(missing_ok=True)
        else:
            Path(dest).parent.mkdir(parents=True, exist_ok=True)
            Path(dest).write_bytes(data)
        return dest

    def _collect_submit_job_map(self) -> dict[str, str]:
        """Collect job_id -> bohr_job_id from previous submit tool outputs."""
        from evomaster.utils.types import ToolMessage

        mapping: dict[str, str] = {}
        dialog = self.agent.current_dialog
        if dialog is None:
            return mapping

        for msg in dialog.messages:
            if not isinstance(msg, ToolMessage):
                continue
            name = getattr(msg, 'name', '') or ''
            if '_submit_' not in name:
                continue
            content = getattr(msg, 'content', '') or ''
            try:
                payload = json.loads(content)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(payload, dict):
                continue
            inner = payload.get('observation')
            if isinstance(inner, dict):
                payload = inner
            job_id = payload.get('job_id')
            extra_info = payload.get('extra_info') or {}
            bohr_job_id = (
                extra_info.get('bohr_job_id') if isinstance(extra_info, dict) else None
            )
            if (
                isinstance(job_id, str)
                and isinstance(bohr_job_id, str)
                and job_id
                and bohr_job_id
            ):
                mapping[job_id] = bohr_job_id
        return mapping

    @staticmethod
    def _extract_submit_payload(observation: str) -> dict[str, Any] | None:
        """Best-effort extraction of submit payload from tool observation text."""
        if not isinstance(observation, str) or not observation.strip():
            return None
        try:
            payload = json.loads(observation)
        except (json.JSONDecodeError, TypeError):
            payload = None
        if payload is None:
            job_match = re.search(r'"job_id"\s*:\s*"([^"]+)"', observation)
            bohr_match = re.search(r'"bohr_job_id"\s*:\s*"([^"]+)"', observation)
            if not job_match:
                return None
            out: dict[str, Any] = {'job_id': job_match.group(1)}
            if bohr_match:
                out['extra_info'] = {'bohr_job_id': bohr_match.group(1)}
            return out
        if not isinstance(payload, dict):
            return None
        obs = payload.get('observation')
        if isinstance(obs, dict):
            payload = obs
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _derive_software_from_tool_name(tool_name: str) -> str:
        if not isinstance(tool_name, str):
            return 'unknown'
        parts = tool_name.split('_')
        if len(parts) >= 2 and parts[0] == 'mat':
            return parts[1]
        return 'unknown'

    @staticmethod
    def _unwrap_quoted_args(raw: str) -> str:
        """Strip one redundant outer quote pair from a script_args value."""
        s = raw.strip()
        if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
            s = s[1:-1].strip()
        return s

    @staticmethod
    def _try_parse_observation_json(observation: str) -> dict | None:
        """Try to parse observation text as a JSON object."""
        if not isinstance(observation, str):
            return None
        text = observation.strip()
        if not text or not text.startswith('{'):
            return None
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else None
        except (json.JSONDecodeError, TypeError):
            return None

    @staticmethod
    def _detect_business_error(payload: dict) -> str | None:
        """Detect business-level error from a parsed MCP tool result."""
        code = payload.get('code')
        if isinstance(code, (int, float)) and int(code) != 0:
            msg = payload.get('message') or payload.get('msg') or payload.get('error')
            return str(msg) if msg else f"Tool returned error code {int(code)}"
        success = payload.get('success')
        if success is False:
            msg = payload.get('message') or payload.get('msg') or payload.get('error')
            return str(msg) if msg else 'Tool returned success=false'
        error = payload.get('error')
        if isinstance(error, str) and error.strip():
            return error.strip()
        return None
