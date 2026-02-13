"""Tool callback pipeline and MatMaster callback implementations.

All before/after tool hooks live here so that ``MatMasterAgent._step()``
stays free of inline hook logic.
"""

from __future__ import annotations

import json
import queue
import re
import shlex
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable
from urllib.parse import urlparse

if TYPE_CHECKING:
    from .agent import MatMasterAgent

BeforeToolCallback = Callable[[Any], None]
AfterToolCallback = Callable[[Any, str, dict[str, Any]], tuple[str, dict[str, Any]]]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DPA_MODEL_ALIAS_MAP: dict[str, str] = {
    "DPA2.4-7M": "https://bohrium.oss-cn-zhangjiakou.aliyuncs.com/13756/27666/store/upload/cd12300a-d3e6-4de9-9783-dd9899376cae/dpa-2.4-7M.pt",
    "DPA3.1-3M": "https://bohrium.oss-cn-zhangjiakou.aliyuncs.com/13756/27666/store/upload/18b8f35e-69f5-47de-92ef-af8ef2c13f54/DPA-3.1-3M.pt",
}

_OSS_URL_RE = re.compile(r"https?://[^\s,'\"<>)}\]]+")
_DEFAULT_DOWNLOAD_SUBDIR = "_tmp/mat_oss_downloads"
_AUTO_DOWNLOAD_MAX_BYTES = 100 * 1024 * 1024
_SKIP_DOWNLOAD_TOKENS = (
    "trajectory",
    "trace",
    "traj",
    "lammpstrj",
    "dump",
    "stdout",
    "stderr",
)


def _normalize_alias(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


_DPA_MODEL_ALIAS_NORM_MAP = {
    _normalize_alias(k): v for k, v in _DPA_MODEL_ALIAS_MAP.items()
}

# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


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
                self.logger.warning("before_tool callback failed: %s", e)

    def run_after(
        self, tool_call: Any, observation: str, info: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        obs = observation
        meta = dict(info or {})
        for cb in self._after:
            try:
                obs, meta = cb(tool_call, obs, meta)
            except Exception as e:
                self.logger.warning("after_tool callback failed: %s", e)
        return obs, meta


# ---------------------------------------------------------------------------
# Concrete callbacks
# ---------------------------------------------------------------------------


class MatToolCallbacks:
    """Concrete MAT callback rules.

    All before/after tool hooks are registered here so that ``_step()``
    stays free of inline hook logic.
    """

    def __init__(
        self,
        agent: MatMasterAgent,
        *,
        download_subdir: str = _DEFAULT_DOWNLOAD_SUBDIR,
    ) -> None:
        self.agent = agent
        self.logger = agent.logger
        self._download_subdir = download_subdir

    def register(self, pipeline: ToolCallbackPipeline) -> None:
        """Register all MAT callbacks in execution order."""
        # Normalisation callbacks run first so downstream hooks see clean args.
        pipeline.register_before(self.before_normalize_skill_script_args)
        pipeline.register_before(self.before_resolve_skill_reference_name)
        pipeline.register_before(self.before_resolve_dpa_model_alias)
        pipeline.register_before(self.before_patch_job_manager_bohr_id)
        # MCP business-error detection runs FIRST among after-callbacks so that
        # downstream hooks (track_async_submit, autodownload, etc.) can
        # short-circuit on ``info.get("error")``.
        pipeline.register_after(self.after_detect_mcp_business_error)
        pipeline.register_after(self.after_ask_human_interaction)
        pipeline.register_after(self.after_track_async_submit)
        pipeline.register_after(self.after_autodownload_oss_results)
        pipeline.register_after(self.after_survey_reminder)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_oss_url(url: str) -> bool:
        try:
            host = urlparse(url).netloc.lower()
        except Exception:
            return False
        return ("aliyuncs.com" in host) or (".oss-" in host) or host.startswith("oss-")

    @staticmethod
    def _should_skip_download(url: str) -> bool:
        lower = url.lower()
        return any(tok in lower for tok in _SKIP_DOWNLOAD_TOKENS)

    @staticmethod
    def _pick_download_path(download_dir: Path, url: str) -> Path:
        parsed = urlparse(url)
        name = Path(parsed.path).name or "artifact.bin"
        base = download_dir / name
        if not base.exists():
            return base
        stem = base.stem
        suffix = base.suffix
        i = 1
        while True:
            candidate = download_dir / f"{stem}_{i}{suffix}"
            if not candidate.exists():
                return candidate
            i += 1

    def _resolve_download_dir(self) -> Path | None:
        """Derive download directory from the agent's workspace config."""
        workspace = (
            getattr(getattr(self.agent.session, "config", None), "workspace_path", None)
            or ""
        )
        if not workspace:
            return None
        return (Path(workspace).resolve() / self._download_subdir).resolve()

    def _download_single(self, url: str, download_dir: Path) -> Path | None:
        """Download a single OSS URL to *download_dir*. Returns local path or None."""
        dest = self._pick_download_path(download_dir, url)
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=60) as resp:  # nosec B310
            content_len = resp.headers.get("Content-Length")
            if content_len:
                try:
                    if int(content_len) > _AUTO_DOWNLOAD_MAX_BYTES:
                        self.logger.info("Skip large OSS file: %s (%s bytes)", url, content_len)
                        return None
                except ValueError:
                    pass
            data = resp.read(_AUTO_DOWNLOAD_MAX_BYTES + 1)
            if len(data) > _AUTO_DOWNLOAD_MAX_BYTES:
                self.logger.info("Skip oversized OSS payload during read: %s", url)
                return None
            dest.write_bytes(data)
        return dest

    def _collect_submit_job_map(self) -> dict[str, str]:
        """Collect ``job_id -> bohr_job_id`` from previous submit tool outputs.

        Reads prior ToolMessage payloads from dialog history and extracts:
        ``{"job_id": "...", "extra_info": {"bohr_job_id": "..."}}``
        """
        from evomaster.utils.types import ToolMessage

        mapping: dict[str, str] = {}
        dialog = self.agent.current_dialog
        if dialog is None:
            return mapping

        for msg in dialog.messages:
            if not isinstance(msg, ToolMessage):
                continue
            name = getattr(msg, "name", "") or ""
            if "_submit_" not in name:
                continue
            content = getattr(msg, "content", "") or ""
            try:
                payload = json.loads(content)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(payload, dict):
                continue
            job_id = payload.get("job_id")
            extra_info = payload.get("extra_info") or {}
            bohr_job_id = extra_info.get("bohr_job_id") if isinstance(extra_info, dict) else None
            if isinstance(job_id, str) and isinstance(bohr_job_id, str) and job_id and bohr_job_id:
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
            # Fallback for non-JSON strings that still contain JSON-like fragments.
            job_match = re.search(r'"job_id"\s*:\s*"([^"]+)"', observation)
            bohr_match = re.search(r'"bohr_job_id"\s*:\s*"([^"]+)"', observation)
            if not job_match:
                return None
            out: dict[str, Any] = {"job_id": job_match.group(1)}
            if bohr_match:
                out["extra_info"] = {"bohr_job_id": bohr_match.group(1)}
            return out
        if not isinstance(payload, dict):
            return None
        # Common wrapper: {"status":"success", "observation": {...}}
        obs = payload.get("observation")
        if isinstance(obs, dict):
            payload = obs
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _derive_software_from_tool_name(tool_name: str) -> str:
        # e.g. mat_dpa_submit_optimize_structure -> dpa
        if not isinstance(tool_name, str):
            return "unknown"
        parts = tool_name.split("_")
        if len(parts) >= 2 and parts[0] == "mat":
            return parts[1]
        return "unknown"

    # ------------------------------------------------------------------
    # Before callbacks
    # ------------------------------------------------------------------

    # ------ use_skill normalisation helpers ------

    @staticmethod
    def _unwrap_quoted_args(raw: str) -> str:
        """Strip one redundant outer quote pair from a script_args value.

        LLMs frequently produce ``script_args = '"--file foo.cif"'`` which
        makes the whole string a single shell token.  This helper peels
        exactly one outer pair so that ``shlex.split`` can work correctly.
        """
        s = raw.strip()
        if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
            s = s[1:-1].strip()
        return s

    def before_normalize_skill_script_args(self, tool_call: Any) -> None:
        """Unwrap redundant outer quotes around ``script_args``.

        LLMs often wrap the entire argument string in an extra pair of
        quotes, e.g. ``"--file foo.cif"`` instead of ``--file foo.cif``.
        When that happens, shlex / argparse inside the skill script sees
        one single token ``--file foo.cif`` and fails.  This callback
        strips one outer quote pair so downstream parsing succeeds.
        """
        if (tool_call.function.name or "") != "use_skill":
            return
        args_str = tool_call.function.arguments or ""
        try:
            args = json.loads(args_str) if args_str else {}
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(args, dict):
            return
        if args.get("action") != "run_script":
            return

        script_args = args.get("script_args")
        if not isinstance(script_args, str) or not script_args.strip():
            return

        cleaned = self._unwrap_quoted_args(script_args)
        if cleaned != script_args:
            args["script_args"] = cleaned
            tool_call.function.arguments = json.dumps(args, ensure_ascii=False)
            self.logger.info(
                "before_tool: unwrapped outer quotes in script_args: %r -> %r",
                script_args,
                cleaned,
            )

    def before_resolve_skill_reference_name(self, tool_call: Any) -> None:
        """Auto-resolve bare reference filenames to their full sub-path.

        When the LLM passes ``reference_name="task_band.inp"`` but the
        actual template lives at ``references/cp2k/task_band.inp``, this
        callback searches the skill's ``references/`` tree for a unique
        match and rewrites the argument so the downstream
        ``get_reference()`` succeeds on the first try.
        """
        if (tool_call.function.name or "") != "use_skill":
            return
        args_str = tool_call.function.arguments or ""
        try:
            args = json.loads(args_str) if args_str else {}
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(args, dict):
            return
        if args.get("action") != "get_reference":
            return

        ref_name = args.get("reference_name")
        if not isinstance(ref_name, str) or not ref_name.strip():
            return
        ref_name = ref_name.strip()
        # Only try to resolve bare filenames (no directory separator).
        if "/" in ref_name or "\\" in ref_name:
            return

        skill_name = args.get("skill_name")
        if not skill_name:
            return

        # Access the skill registry through the agent.
        registry = getattr(self.agent, "skill_registry", None)
        if registry is None:
            return
        skill = registry.get_skill(skill_name)
        if skill is None:
            return

        from pathlib import Path

        candidates: list[Path] = []
        for root_name in ("references", "reference"):
            root = skill.skill_path / root_name
            if root.exists():
                try:
                    candidates.extend(p for p in root.rglob(ref_name) if p.is_file())
                except Exception:
                    pass

        if len(candidates) == 1:
            # Compute the relative path from the references root.
            matched = candidates[0]
            for root_name in ("references", "reference"):
                root = skill.skill_path / root_name
                if root.exists():
                    try:
                        rel = matched.relative_to(root).as_posix()
                        args["reference_name"] = rel
                        tool_call.function.arguments = json.dumps(
                            args, ensure_ascii=False
                        )
                        self.logger.info(
                            "before_tool: resolved reference name %r -> %r",
                            ref_name,
                            rel,
                        )
                        return
                    except ValueError:
                        continue

    def before_resolve_dpa_model_alias(self, tool_call: Any) -> None:
        """Resolve DPA short model key to hard-coded OSS URL."""
        tool_name = tool_call.function.name or ""
        if not tool_name.startswith("mat_dpa_"):
            return
        args_str = tool_call.function.arguments or ""
        try:
            args = json.loads(args_str) if args_str else {}
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(args, dict):
            return
        model_path = args.get("model_path")
        if not isinstance(model_path, str):
            return
        norm = _normalize_alias(model_path)
        resolved = _DPA_MODEL_ALIAS_NORM_MAP.get(norm)
        if not resolved:
            return
        args["model_path"] = resolved
        tool_call.function.arguments = json.dumps(args, ensure_ascii=False)
        self.logger.info(
            "before_tool: resolved DPA model alias %s -> %s",
            model_path,
            resolved,
        )

    def before_patch_job_manager_bohr_id(self, tool_call: Any) -> None:
        """Auto-fill missing ``--bohr_job_id`` for job-manager run_script calls.

        Avoids fragile failures when the LLM remembers job_id but forgets
        bohr_job_id, which is required/safer for some async backends.
        """
        if (tool_call.function.name or "") != "use_skill":
            return
        args_str = tool_call.function.arguments or ""
        try:
            args = json.loads(args_str) if args_str else {}
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(args, dict):
            return
        if args.get("skill_name") != "job-manager":
            return
        if args.get("action") != "run_script":
            return
        if args.get("script_name") != "run_resilient_job.py":
            return

        script_args = args.get("script_args")
        if not isinstance(script_args, str) or not script_args.strip():
            return
        if "--bohr_job_id" in script_args:
            return

        try:
            tokens = shlex.split(script_args)
        except ValueError:
            return

        job_id = None
        for i, tok in enumerate(tokens):
            if tok == "--job_id" and i + 1 < len(tokens):
                job_id = tokens[i + 1]
                break
        if not job_id:
            return

        bohr_map = self._collect_submit_job_map()
        bohr_job_id = bohr_map.get(job_id)
        if not bohr_job_id:
            return

        args["script_args"] = f'{script_args} --bohr_job_id "{bohr_job_id}"'
        tool_call.function.arguments = json.dumps(args, ensure_ascii=False)
        self.logger.info(
            "before_tool: patched job-manager args with bohr_job_id for job_id=%s",
            job_id,
        )

    # ------------------------------------------------------------------
    # After callbacks
    # ------------------------------------------------------------------

    # --- MCP business-error detection helpers ---

    @staticmethod
    def _try_parse_observation_json(observation: str) -> dict | None:
        """Try to parse observation text as a JSON object.

        Returns the parsed dict if successful, None otherwise.
        Only attempts parsing when the text looks like a JSON object
        (starts with ``{``) to avoid unnecessary work.
        """
        if not isinstance(observation, str):
            return None
        text = observation.strip()
        if not text or not text.startswith("{"):
            return None
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else None
        except (json.JSONDecodeError, TypeError):
            return None

    @staticmethod
    def _detect_business_error(payload: dict) -> str | None:
        """Detect business-level error from a parsed MCP tool result.

        Returns an error message string if error is detected, None otherwise.

        Detection rules (in priority order):

        1. ``code`` field: non-zero integer → error
           Common pattern: ``{"code": 1, "message": "Lattice mismatch ..."}``
        2. ``success`` field: explicitly ``False`` → error
           Common pattern: ``{"success": false, "message": "..."}``
        3. ``error`` field: non-empty string → error
           Common pattern: ``{"error": "something went wrong"}``
        """
        # Rule 1: code field — non-zero integer indicates failure
        code = payload.get("code")
        if isinstance(code, (int, float)) and int(code) != 0:
            msg = payload.get("message") or payload.get("msg") or payload.get("error")
            return str(msg) if msg else f"Tool returned error code {int(code)}"

        # Rule 2: success field — explicit False
        success = payload.get("success")
        if success is False:  # must be explicit False, not None/missing
            msg = payload.get("message") or payload.get("msg") or payload.get("error")
            return str(msg) if msg else "Tool returned success=false"

        # Rule 3: error field — non-empty string
        error = payload.get("error")
        if isinstance(error, str) and error.strip():
            return error.strip()

        return None

    def after_detect_mcp_business_error(
        self,
        tool_call: Any,
        observation: str,
        info: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        """Detect business-level errors in MCP tool observation content.

        MCP transport success (no Python exception) does **not** mean the tool
        operation succeeded.  Many ``mat_*`` MCP tools return structured JSON
        with error indicators (``code != 0``, ``success == false``, etc.)
        while the MCP call itself completes normally.

        This callback inspects the observation text, and when a business
        error is detected it sets ``info["error"]`` so that downstream
        ``_format_tool_observation()`` correctly reports ``status = "error"``
        and the LLM can adjust parameters or retry.
        """
        # Only applies to MCP tools (identified by mcp_tool in info)
        if not info.get("mcp_tool"):
            return observation, info
        # Already marked as error by upstream logic
        if "error" in info:
            return observation, info

        parsed = self._try_parse_observation_json(observation)
        if parsed is None:
            return observation, info

        error_msg = self._detect_business_error(parsed)
        if error_msg is None:
            return observation, info

        # Mark as business-level error so _format_tool_observation sets
        # status="error" and the LLM sees the failure clearly.
        new_info = dict(info)
        new_info["error"] = error_msg
        new_info["success"] = False
        self.logger.warning(
            "MCP business error detected for tool '%s': %s",
            info.get("mcp_tool", "?"),
            error_msg[:200],
        )
        return observation, new_info

    def after_ask_human_interaction(
        self,
        tool_call: Any,
        observation: str,
        info: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        """Intercept ask-human skill results: emit event and block for user reply.

        When ``ask.py`` finishes it outputs a JSON ``{"question": "...", "context": "..."}``.
        This callback:
        1. Detects the ask-human skill by checking the tool call arguments.
        2. Emits an ``ask_human`` event via the agent's ``event_callback``
           (StreamingMatMasterAgent) so the frontend can display the question.
        3. Blocks on ``agent._ask_human_queue`` until the user replies or a
           timeout (5 min) is reached.
        4. Returns the user's reply as the new observation.

        If the agent has no ``_ask_human_queue`` (e.g. non-web mode), it returns
        a message indicating that interactive input is unavailable.
        """
        # Only handle use_skill calls for ask-human
        if (tool_call.function.name or "") != "use_skill":
            return observation, info
        try:
            args = json.loads(tool_call.function.arguments or "{}")
        except (json.JSONDecodeError, TypeError):
            return observation, info
        if not isinstance(args, dict) or args.get("skill_name") != "ask-human":
            return observation, info

        # Extract the question from the script output
        question = ""
        context = ""
        # The script outputs a JSON line; try to parse it from stdout.
        script_stdout = observation
        # Strip the "Script output:\n" prefix if present
        if script_stdout.startswith("Script output:\n"):
            script_stdout = script_stdout[len("Script output:\n"):]
        for line in script_stdout.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
                if isinstance(payload, dict):
                    question = payload.get("question", question)
                    context = payload.get("context", context)
                    break
            except (json.JSONDecodeError, TypeError):
                # Fallback: treat the whole output as the question
                if not question:
                    question = line

        if not question:
            question = "The agent is asking for your input."

        # Emit ask_human event to the frontend
        emit_fn = getattr(self.agent, "event_callback", None)
        if callable(emit_fn):
            ask_payload = {"question": question}
            if context:
                ask_payload["context"] = context
            emit_fn("MatMaster", "ask_human", ask_payload)
        else:
            # For non-streaming agents, try the _emit helper
            _emit = getattr(self.agent, "_emit", None)
            if callable(_emit):
                ask_payload = {"question": question}
                if context:
                    ask_payload["context"] = context
                _emit("MatMaster", "ask_human", ask_payload)

        # Block waiting for the user's reply
        reply_queue: queue.Queue | None = getattr(self.agent, "_ask_human_queue", None)
        if reply_queue is None:
            self.logger.warning(
                "ask-human invoked but no _ask_human_queue is set on the agent. "
                "Returning a placeholder. Set agent._ask_human_queue for interactive mode."
            )
            return (
                "⚠️ Interactive input is not available in the current execution mode. "
                "The agent asked: " + question,
                info,
            )

        self.logger.info("ask-human: waiting for user reply (timeout=300s)...")
        try:
            reply = reply_queue.get(timeout=300)
        except queue.Empty:
            self.logger.warning("ask-human: user reply timed out after 300s.")
            return "⚠️ User did not reply within 5 minutes. Proceeding without input.", info

        self.logger.info("ask-human: received user reply (%d chars).", len(reply))
        return f"User replied: {reply}", info

    def after_track_async_submit(
        self,
        tool_call: Any,
        observation: str,
        info: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        """Track submit_* jobs in runtime registry for finish-attempt gating."""
        tool_name = tool_call.function.name or ""
        if "_submit_" not in tool_name:
            return observation, info
        if info.get("error") is not None:
            return observation, info

        payload = self._extract_submit_payload(observation)
        if not payload:
            return observation, info

        job_id = payload.get("job_id")
        if not isinstance(job_id, str) or not job_id:
            return observation, info

        extra_info = payload.get("extra_info")
        bohr_job_id = None
        if isinstance(extra_info, dict):
            b = extra_info.get("bohr_job_id")
            if isinstance(b, str) and b:
                bohr_job_id = b

        software = self._derive_software_from_tool_name(tool_name)
        registry = getattr(self.agent, "_job_registry", None)
        if registry is None:
            return observation, info

        registry.record_submit(
            job_id=job_id,
            software=software,
            source_tool=tool_name,
            bohr_job_id=bohr_job_id,
        )
        self.logger.info(
            "after_tool: tracked async submit job_id=%s software=%s bohr_job_id=%s",
            job_id,
            software,
            bohr_job_id,
        )
        return observation, info

    def after_autodownload_oss_results(
        self,
        tool_call: Any,
        observation: str,
        info: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        """Auto-download OSS artifacts for any mat_* tool."""
        tool_name = tool_call.function.name or ""
        if not tool_name.startswith("mat_"):
            return observation, info
        urls = [u for u in _OSS_URL_RE.findall(observation or "") if self._is_oss_url(u)]
        if not urls:
            return observation, info

        download_dir = self._resolve_download_dir()
        if download_dir is None:
            return observation, info
        download_dir.mkdir(parents=True, exist_ok=True)

        # De-duplicate and filter
        targets: list[str] = []
        seen: set[str] = set()
        for url in urls:
            if url in seen or self._should_skip_download(url):
                continue
            seen.add(url)
            targets.append(url)

        if not targets:
            return observation, info

        # Parallel download (up to 4 concurrent)
        downloaded: list[dict[str, str]] = []

        def _do(u: str) -> tuple[str, Path | None]:
            return u, self._download_single(u, download_dir)

        with ThreadPoolExecutor(max_workers=min(4, len(targets))) as pool:
            futures = {pool.submit(_do, u): u for u in targets}
            for fut in as_completed(futures):
                url_key = futures[fut]
                try:
                    _, local_path = fut.result()
                except Exception as e:
                    self.logger.warning("after_tool: OSS download failed (%s): %s", url_key, e)
                    continue
                if local_path is not None:
                    downloaded.append({"url": url_key, "local_path": str(local_path)})

        if not downloaded:
            return observation, info

        note_lines = [
            "",
            "[Auto-download callback] Downloaded OSS artifacts to local workspace:",
        ]
        for item in downloaded:
            note_lines.append(f"- {item['url']}")
            note_lines.append(f"  local_path: {item['local_path']}")
        new_obs = (observation or "") + "\n" + "\n".join(note_lines)
        new_info = dict(info or {})
        new_info["auto_downloaded_files"] = downloaded
        return new_obs, new_info

    def after_survey_reminder(
        self,
        tool_call: Any,
        observation: str,
        info: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        """Append survey-retrieval reminder after mat_sn_search-papers-enhanced."""
        if tool_call.function.name != "mat_sn_search-papers-enhanced":
            return observation, info
        if info.get("error") is not None:
            return observation, info

        n_papers = ""
        try:
            obj = json.loads(observation)
            if isinstance(obj, dict) and "data" in obj and isinstance(obj["data"], list):
                n_papers = str(len(obj["data"]))
        except (json.JSONDecodeError, TypeError):
            pass

        call_count = info.get("call_count", "?")
        reminder = (
            f"\n\n[Survey reminder: {n_papers or '?'} papers returned (retrieval #{call_count}). "
            "A thorough survey requires at least 6-15 retrievals; if results are sparse or "
            "retrieval count is low, vary your question/words and call "
            "mat_sn_search-papers-enhanced or mat_sn_web-search again.]"
        )
        return observation + reminder, info
