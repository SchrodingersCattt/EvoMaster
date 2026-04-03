"""Client for matmaster-tools-server evaluation ingest API.

See ``docs/apifox-evaluation-openapi.json`` in matmaster-tools-server for the contract:
``EvalIngestRequest`` with ``run_id``, ``run_kind`` (baseline | iteration), ``items`` (≥1).
When ``run_kind`` is ``baseline``, matmaster-tools-server **requires** ``baseline_channel`` on the
request body: ``claude_code`` or ``cursor`` or ``codex`` (see ``EvalIngestRequest`` in
``matmaster-tools-server/src/models/evaluation.py``). For ``iteration``, omit it (null).
Each ``EvalItemIn`` requires ``question_id``; ``model`` / ``num_turns`` / ``score``
(optional) on the item top level. 题干（question_text）由独立的题库同步接口维护，
ingest 不再写入。When task outputs exist, this client adds top-level ``artifact``
(``bundle_object_key`` / ``manifest_object_key`` / ``files_prefix``) so tools-server
can serve file tree / preview / bundle download from the new artifact APIs. For
**immediate** ingest, :func:`build_ingest_item` sets ``score`` from the devshell
summary when present, else a 100/0 pass-fail proxy. Human ``score`` /
``score_reason`` / ``suggestion`` for **pending** ingest are passed by CLI and
validated by :func:`normalize_pending_item_for_submission`
（``score_reason`` / ``suggestion`` 最长 16384）. For deferred ingest,
``run_devshell_eval.py --eval-ingest-pending-only`` writes ``pending_ingest/*.json``
with ``item`` **without** ``score`` (and without human ``score_reason`` /
``suggestion``). After judging, Claude Code passes ``--score`` / ``--score-reason`` /
``--suggestion`` to ``evaluation/scripts/eval_ingest_submit_pending.py --pending <path>``
before POST. Artifact upload only includes the current task under that run:
``workspaces/<task_id>`` and ``logs/<task_id>`` (see
:func:`upload_eval_task_artifacts_to_oss`). The parent ``devshell_eval_*`` folder is
shared by all tasks in the batch; it is not uploaded whole. ``extra`` is stored as
opaque JSON. When devshell ``summary.usage`` is present, ``extra`` includes a JSON-safe copy as
``usage`` (run-level **accumulated scalars**). ``summary.usage_vendor_by_turn`` lists
one vendor-native usage dict per LLM round (possibly ``{}``); when present it is
copied into ``extra["usage_vendor_by_turn"]``. Top-level ``item["tokens"]`` and
``extra["tokens_last_turn"]`` use the **last LLM round** raw ``total_tokens`` when
``usage_vendor_by_turn`` is present; otherwise ``summary.usage.total_tokens`` (whole-run
accumulated scalar, **not** cache-adjusted). Neither path subtracts cache reads.
Optional ``eval_tooling`` (from
:func:`evaluation.eval_tooling_snapshot.snapshot_eval_tooling`) records builtin /
skill / MCP server config for batch analysis. Optional ``events_timeline`` (from
:func:`load_devshell_events_timeline`) is a short list of step labels in order, e.g.
``["response", "read_file", "execute_bash", "run_result"]``, derived from
``logs/<task_id>/events_*.jsonl``.

Ingest POST URL is ``MATMASTER_TOOLS_SERVER`` + ``EVAL_INGEST_API_PATH``（在 **首次 import**
本模块时按 ``utils.env`` 解析；与配额等共用同一 host）。见 ``EVAL_INGEST_URL``。

评测相关 POST 须带服务密钥：环境变量 ``MATMASTER_TOOLS_EVALUATION_BEARER`` 会设置
``Authorization: Bearer …``（与 tools-server Nacos ``evaluation.service_api_keys`` 一致）。
未配置时对接已加权限的 tools-server 将返回 401。
"""

from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import os
import subprocess
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Any, Literal

import httpx

import utils.env
from evaluation.core.evidence import TokenUsage

logger = logging.getLogger(__name__)

# Align with matmaster-tools-server ``EvalItemIn`` field limits.
EVAL_ITEM_TEXT_FIELD_MAX_LEN = 16384
EVAL_ITEM_QUESTION_TEXT_MAX_LEN = 4_194_304
# Align with matmaster-tools-server ``EvalIngestRequest.baseline_channel`` Literal.
EvalBaselineChannel = Literal["claude_code", "cursor", "codex"]
_EVAL_BASELINE_CHANNELS: frozenset[str] = frozenset({"claude_code", "cursor", "codex"})

# Direct tools-server path (not the gateway ``/bohrapi/v1/matmaster-tools-server/...`` prefix).
EVAL_INGEST_API_PATH = "/api/v1/evaluation/ingest"
QUESTION_CATALOG_SYNC_API_PATH = "/api/v1/evaluation/question-catalog/sync"

_base = (utils.env.MATMASTER_TOOLS_SERVER or "").strip().rstrip("/")
EVAL_INGEST_URL: str | None = f"{_base}{EVAL_INGEST_API_PATH}" if _base else None
QUESTION_CATALOG_SYNC_URL: str | None = (
    f"{_base}{QUESTION_CATALOG_SYNC_API_PATH}" if _base else None
)


def normalize_baseline_channel(
    value: Any, *, default: EvalBaselineChannel = "claude_code"
) -> EvalBaselineChannel:
    """Coerce to a known baseline channel; unknown values log a warning and use *default*."""
    if default not in _EVAL_BASELINE_CHANNELS:
        default = "claude_code"
    if value is None:
        return default
    s = str(value).strip()
    if not s:
        return default
    if s in _EVAL_BASELINE_CHANNELS:
        return s  # type: ignore[return-value]
    logger.warning(
        "invalid baseline_channel %r (allowed: %s); using %r",
        s,
        ", ".join(sorted(_EVAL_BASELINE_CHANNELS)),
        default,
    )
    return default


def prompt_sha256(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def load_devshell_events_timeline(log_dir: Path) -> list[str] | None:
    """Build a compact ordered timeline from ``events_*.jsonl`` under *log_dir*.

    Uses the lexicographically last ``events_*.jsonl`` if several exist (filename
    timestamp). For each line in file order:

    - ``tool_call`` → append the tool name (``tool`` field).
    - ``tool_result`` → skip (avoids duplicating the name next to ``tool_call``).
    - ``response`` → append ``\"response\"``.
    - ``run_result`` → append ``\"run_result\"``.
    - ``thought`` and other types → skip.

    Returns ``None`` if no matching file or no recognized steps.
    """
    root = Path(log_dir)
    if not root.is_dir():
        return None
    matches = sorted(root.glob("events_*.jsonl"))
    if not matches:
        return None
    path = matches[-1]
    out: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue
        typ = rec.get("type")
        if typ == "tool_call":
            name = rec.get("tool")
            if isinstance(name, str) and name.strip():
                out.append(name.strip())
            else:
                out.append("?")
        elif typ == "tool_result":
            continue
        elif typ == "response":
            out.append("response")
        elif typ == "run_result":
            out.append("run_result")
        else:
            continue
    return out if out else None


def _json_safe_usage_tree(obj: Any) -> Any:
    """Recursively coerce usage payloads to JSON-serializable structures for ingest ``extra``."""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _json_safe_usage_tree(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe_usage_tree(x) for x in obj]
    try:
        return int(obj)
    except (TypeError, ValueError):
        pass
    try:
        return float(obj)
    except (TypeError, ValueError):
        pass
    return str(obj)


def extract_ingest_tokens(summary: Any) -> int | None:
    """Token count for ingest ``item["tokens"]``: **last round** raw ``total_tokens``, no cache deduction.

    1. If ``summary["usage_vendor_by_turn"]`` is a non-empty list, use
       ``int(last_entry["total_tokens"])`` when set.
    2. Else use ``summary["usage"]["total_tokens"]`` (whole-run accumulated from kernel).
    3. Else derive from ``usage`` via :class:`evaluation.core.evidence.TokenUsage` (still
       **no** cache subtraction — uses reported ``total_tokens`` or ``prompt+completion``).
    """
    if not summary or not isinstance(summary, dict):
        return None
    turns = summary.get("usage_vendor_by_turn")
    if isinstance(turns, list) and turns:
        last = turns[-1]
        if isinstance(last, dict):
            tt = last.get("total_tokens")
            if tt is not None:
                try:
                    v = int(tt)
                    if v >= 0:
                        return v
                except (TypeError, ValueError):
                    pass
    usage = summary.get("usage")
    if isinstance(usage, dict) and usage:
        raw_tt = usage.get("total_tokens")
        if raw_tt is not None:
            try:
                v = int(raw_tt)
                if v >= 0:
                    return v
            except (TypeError, ValueError):
                pass
        tu = TokenUsage.from_usage_dict(usage)
        if tu.total_tokens > 0:
            return tu.total_tokens
        if tu.prompt_tokens or tu.completion_tokens:
            return max(0, tu.prompt_tokens + tu.completion_tokens)
    return None


def score_for_eval_ingest(
    summary: dict[str, Any] | None,
    devshell_exit_code: int,
) -> float:
    """Numeric score for tools-server ``EvalItemIn.score``.

    Prefer an explicit value from the devshell summary (``score``, ``eval_score``,
    ``weighted_score``). Otherwise use a simple proxy: **100** when the run exited 0
    and the summary is not a parse error; **0** otherwise. (DevShell does not run
    MATTER BinaryEvaluator, so there is no automatic checklist score unless the
    kernel adds one of the keys above.)
    """
    if isinstance(summary, dict):
        for key in ("score", "eval_score", "weighted_score"):
            raw = summary.get(key)
            if raw is None:
                continue
            try:
                return float(raw)
            except (TypeError, ValueError):
                continue
        if summary.get("parse_error"):
            return 0.0
    if devshell_exit_code == 0:
        return 100.0
    return 0.0


def eval_run_zip_should_skip_arcname(arcname: str) -> bool:
    """True if a path under the run directory should not be packed (noise / bytecode)."""
    parts = arcname.replace("\\", "/").split("/")
    if "__pycache__" in parts:
        return True
    lower = arcname.lower()
    if lower.endswith(".pyc") or lower.endswith(".pyo"):
        return True
    if arcname.endswith(".DS_Store"):
        return True
    return False


def _sanitize_oss_segment(value: str, *, fallback: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return fallback
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in raw)
    safe = safe.strip("._")
    return safe[:120] or fallback


def _guess_artifact_mime_type(path: str) -> str | None:
    mime_type, _ = mimetypes.guess_type(path, strict=False)
    return mime_type


def _guess_artifact_preview_type(path: str, mime_type: str | None) -> str:
    lower = path.lower()
    if mime_type == "application/pdf" or lower.endswith(".pdf"):
        return "pdf"
    if mime_type and mime_type.startswith("image/"):
        return "image"
    if lower.endswith((".json", ".jsonl")):
        return "json"
    if mime_type and mime_type.startswith("text/"):
        return "text"
    if lower.endswith(
        (
            ".txt",
            ".log",
            ".md",
            ".csv",
            ".tsv",
            ".yaml",
            ".yml",
            ".xml",
            ".html",
            ".cif",
            ".vasp",
            ".poscar",
            ".out",
            ".err",
            ".py",
            ".sh",
        )
    ):
        return "text"
    return "binary"


def _collect_eval_task_files(root: Path, task_id: str) -> list[tuple[Path, str]]:
    files: list[tuple[Path, str]] = []
    for base in (root / "workspaces" / task_id, root / "logs" / task_id):
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            try:
                rel = path.relative_to(root).as_posix()
            except ValueError:
                continue
            if eval_run_zip_should_skip_arcname(rel):
                continue
            files.append((path, rel))
    return files


def upload_eval_task_artifacts_to_oss(
    run_dir: Path,
    task_id: str,
    *,
    oss_prefix: str = "matmaster/evaluation",
) -> dict[str, Any] | None:
    """Upload one task's bundle + file tree + manifest for tools-server artifacts.

    A run root like ``results/devshell_eval_*`` holds **all** questions; each task has
    ``workspaces/<task_id>/`` and ``logs/<task_id>/``. This packs just those two
    subtrees and uploads:

    - ``bundle.zip`` for full download
    - ``manifest.json`` for tree listing metadata
    - ``files/...`` for file preview/download by relative path

    Returns the top-level ``artifact`` payload expected by tools-server, or
    ``None`` if nothing to pack, OSS env is missing, or upload fails.
    """
    root = Path(run_dir).resolve()
    if not root.is_dir():
        return None

    files = _collect_eval_task_files(root, task_id)
    if not files:
        return None

    safe_run = _sanitize_oss_segment(root.name, fallback="eval_run")
    safe_tid = _sanitize_oss_segment(task_id, fallback="task")
    artifact_root = "/".join(
        [
            oss_prefix.strip().strip("/"),
            safe_run,
            safe_tid,
            uuid.uuid4().hex,
        ]
    )
    bundle_object_key = f"{artifact_root}/bundle.zip"
    manifest_object_key = f"{artifact_root}/manifest.json"
    files_prefix = f"{artifact_root}/files"
    fd, tmp = tempfile.mkstemp(suffix=".zip", prefix="eval_ingest_")
    os.close(fd)
    tmp_path = Path(tmp)
    try:
        from utils.oss_io import upload_bytes_to_oss, upload_file_to_oss_with_key

        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for fs_path, arcname in sorted(files, key=lambda item: item[1]):
                zf.write(fs_path, arcname=arcname)

        upload_file_to_oss_with_key(tmp_path, bundle_object_key)

        manifest_entries: list[dict[str, Any]] = []
        for fs_path, rel_path in sorted(files, key=lambda item: item[1]):
            upload_file_to_oss_with_key(fs_path, f"{files_prefix}/{rel_path}")
            mime_type = _guess_artifact_mime_type(rel_path)
            manifest_entries.append(
                {
                    "path": rel_path,
                    "size": fs_path.stat().st_size,
                    "mime_type": mime_type,
                    "preview_type": _guess_artifact_preview_type(rel_path, mime_type),
                }
            )

        manifest_payload = {
            "schema": "matmaster_eval_artifact_manifest_v1",
            "task_id": task_id,
            "entries": manifest_entries,
        }
        upload_bytes_to_oss(
            (json.dumps(manifest_payload, ensure_ascii=False, indent=2) + "\n").encode(
                "utf-8"
            ),
            manifest_object_key,
        )
        return {
            "bundle_object_key": bundle_object_key,
            "manifest_object_key": manifest_object_key,
            "files_prefix": files_prefix,
        }
    except (OSError, RuntimeError, ValueError, ImportError) as e:
        logger.warning("eval ingest OSS task artifacts upload failed: %s", e)
        return None
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


def clip_ingest_text_field(
    value: str | None, *, max_len: int = EVAL_ITEM_TEXT_FIELD_MAX_LEN
) -> str | None:
    """Return stripped non-empty string, truncated to ``max_len``, or ``None``."""
    if value is None:
        return None
    t = str(value).strip()
    if not t:
        return None
    return t[:max_len] if len(t) > max_len else t


def normalize_pending_item_for_submission(
    item: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    """Validate and normalize ``item`` from a pending-ingest JSON before POST.

    Requires ``score`` (coerced to ``float``). Optional ``score_reason`` / ``suggestion``
    must be strings if present; empty after strip are dropped.
    Returns ``(item, None)`` or ``(None, error_message)``.
    """
    out = dict(item)
    out.pop("question_text", None)
    raw_score = out.get("score")
    if raw_score is None:
        return None, 'missing item["score"] — pass --score (e.g. 0–100)'
    try:
        out["score"] = float(raw_score)
    except (TypeError, ValueError):
        return None, f'invalid item["score"]: {raw_score!r}'

    for key in ("score_reason", "suggestion"):
        if key not in out:
            continue
        val = out[key]
        if val is None:
            out.pop(key, None)
            continue
        if not isinstance(val, str):
            return None, f'item["{key}"] must be a string if present'
        clipped = clip_ingest_text_field(val)
        if clipped is None:
            out.pop(key, None)
        else:
            out[key] = clipped

    return out, None


def git_head_commit(repo_root: Path, *, max_len: int = 64) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if proc.returncode != 0:
            return None
        h = proc.stdout.strip()
        if len(h) < 7:
            return None
        return h[:max_len]
    except (OSError, subprocess.TimeoutExpired):
        return None


def build_ingest_item(
    *,
    question_id: str,
    task_id: str,
    mode: str,
    repeat_idx: int,
    devshell_exit_code: int,
    summary: dict[str, Any] | None,
    duration_ms: int | None,
    artifact: dict[str, Any] | None = None,
    eval_tooling: dict[str, Any] | None = None,
    events_timeline: list[str] | None = None,
) -> dict[str, Any]:
    raw_summary = summary if isinstance(summary, dict) else None
    s: dict[str, Any] = raw_summary if raw_summary is not None else {}
    usage = s.get("usage")
    tokens = extract_ingest_tokens(s)
    preview: str | None = None
    fc = s.get("final_content")
    if isinstance(fc, str) and fc:
        preview = fc[:2000]

    extra: dict[str, Any] = {
        "task_id": task_id,
        "devshell_exit_code": devshell_exit_code,
        "mode": mode,
        "repeat_idx": repeat_idx,
    }
    extra.update(
        {
            "status": s.get("status"),
            "reason": s.get("reason"),
            "profile_key": s.get("profile_key"),
        }
    )
    if s.get("parse_error"):
        extra["parse_error"] = True
        for k in ("error", "missing_file", "empty_file"):
            if k in s:
                extra[k] = s[k]
    if preview is not None:
        extra["final_content_preview"] = preview

    if eval_tooling is not None:
        extra["eval_tooling"] = eval_tooling
    if events_timeline:
        extra["events_timeline"] = list(events_timeline)

    if isinstance(usage, dict) and usage:
        extra["usage"] = _json_safe_usage_tree(dict(usage))
    uv_turns = s.get("usage_vendor_by_turn")
    if isinstance(uv_turns, list) and uv_turns:
        extra["usage_vendor_by_turn"] = [
            (
                _json_safe_usage_tree(dict(x))
                if isinstance(x, dict)
                else _json_safe_usage_tree(x)
            )
            for x in uv_turns
        ]
    if tokens is not None:
        extra["tokens_last_turn"] = int(tokens)

    item: dict[str, Any] = {
        "question_id": question_id,
        "extra": extra,
    }
    nt = s.get("num_turns")
    if nt is not None:
        try:
            nti = int(nt)
            if nti >= 0:
                item["num_turns"] = nti
        except (TypeError, ValueError):
            pass
    mod = s.get("model")
    if isinstance(mod, str) and mod.strip():
        item["model"] = mod.strip()[:256]

    if duration_ms is not None and duration_ms >= 0:
        item["duration_ms"] = int(duration_ms)
    if tokens is not None:
        item["tokens"] = tokens

    item["score"] = score_for_eval_ingest(raw_summary, devshell_exit_code)
    if isinstance(artifact, dict) and artifact:
        item["artifact"] = dict(artifact)
    return item


def matmaster_evaluation_request_headers() -> dict[str, str]:
    """Build HTTP headers for matmaster-tools-server evaluation routes."""
    headers: dict[str, str] = {"Content-Type": "application/json"}
    bearer = utils.env.MATMASTER_TOOLS_EVALUATION_BEARER
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    return headers


def _post_matmaster_tools_json(
    url: str,
    body: dict[str, Any],
    *,
    timeout: float,
) -> tuple[bool, str, dict[str, Any] | None]:
    """POST JSON to tools-server; parse ``{code, msg, data?}`` envelope.

    Returns ``(ok, message, full_json)`` where *full_json* is the decoded body on
    success or on JSON parse failure path is still returned when decode succeeded
    but ``code != 0``; ``None`` on transport / non-JSON / empty decode errors.
    """
    headers = matmaster_evaluation_request_headers()
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json=body, headers=headers)
    except httpx.HTTPError as exc:
        return False, str(exc), None

    if resp.status_code != 200:
        return False, f"HTTP {resp.status_code}: {resp.text[:500]}", None

    try:
        data = resp.json()
    except Exception:
        return False, f"non-JSON response: {resp.text[:200]}", None

    if not isinstance(data, dict):
        return False, f"unexpected JSON type: {type(data).__name__}", None

    if data.get("code") != 0:
        return False, str(data.get("msg", data)), data

    return True, str(data.get("msg", "success")), data


def post_eval_ingest(
    url: str,
    body: dict[str, Any],
    *,
    timeout: float = 30.0,
) -> tuple[bool, str]:
    ok, msg, _ = _post_matmaster_tools_json(url, body, timeout=timeout)
    return ok, msg


def post_question_catalog_sync(
    url: str,
    items: list[dict[str, str]],
    *,
    timeout: float = 120.0,
) -> tuple[bool, str]:
    """POST catalog sync payload to matmaster-tools-server.

    Each element must include ``question_id`` and ``question_text`` (trimmed non-empty
    after clip), matching tools-server ``EvalQuestionCatalogItemIn``. Server marks all
    catalog rows inactive, then upserts these rows as active. ``question_id`` length
    1–512; ``question_text`` clipped to ``EVAL_ITEM_QUESTION_TEXT_MAX_LEN``.
    """
    if not items:
        return False, "no items to sync (server requires at least one item)"

    body_items: list[dict[str, str]] = []
    for raw in items:
        qid = str(raw.get("question_id", "")).strip()
        if not qid:
            return False, "missing or empty question_id in item"
        if len(qid) > 512:
            return False, f"question_id too long (>512): {qid[:80]!r}..."
        if "question_text" not in raw:
            return False, f'missing question_text for question_id={qid!r}'
        qt_raw = raw["question_text"]
        if not isinstance(qt_raw, str):
            return False, f'question_text must be a string for question_id={qid!r}'
        qtext = clip_ingest_text_field(qt_raw, max_len=EVAL_ITEM_QUESTION_TEXT_MAX_LEN)
        if not qtext:
            return False, f"empty question_text after trim for question_id={qid!r}"
        body_items.append({"question_id": qid, "question_text": qtext})

    body = {"items": body_items}
    ok, err_msg, data = _post_matmaster_tools_json(url, body, timeout=timeout)
    if not ok:
        return False, err_msg

    inner = (data or {}).get("data") or {}
    ac = inner.get("active_count")
    ic = inner.get("inactive_count")
    if ac is not None and ic is not None:
        return True, f"success active_count={ac} inactive_count={ic}"
    return True, str(data.get("msg", "success"))
