"""Stateful tool-call guard: loop prevention, validation gate, manuscript gate.

Three independent concerns, each with its own state and public method:

1. **Loop detection** (``evaluate`` / ``record_tool_call``): Blocks repeated
   calls with identical arguments within a sliding window.
2. **Input-validation gate** (``_submit_block_reason``): Blocks ``submit_*``
   calls until ``validate_input.py`` passes for that (file, software) pair.
3. **Manuscript gate** (``can_finish_manuscript``): Blocks ``finish`` when
   manuscript sections were written but never validated, or when the last
   validation failed.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from collections import deque
from dataclasses import dataclass
from typing import Any

from .constants import MANUSCRIPT_FAIL_MARKERS

# ── Loop-detection parameters ──────────────────────────────────
LOOP_WINDOW = 5          # sliding-window size for recent tool fingerprints
LOOP_THRESHOLD = 2       # block after N identical calls inside the window

# ── Peek-manual budget ─────────────────────────────────────────
PEEK_MANUAL_MAX_CALLS = 12
PEEK_MANUAL_LOW_GAIN_MAX_REPEATS = 2

# ── Loop-detection exemptions ──────────────────────────────────
EXEMPT_TOOL_SUFFIXES = frozenset({
    "query_job_status",
    "get_job_status",
})

# Scripts that legitimately produce different output each call (content varies)
# and therefore should bypass duplicate-call loop detection.
# validate_content.py and similar scripts are NOT listed here so loop detection
# can catch runaway identical invocations.
EXEMPT_MANUSCRIPT_SCRIPTS = frozenset({
    "write_section.py",
    "append_chunk.py",
    "polish_text.py",
    "init_manuscript.py",
    "export_docx.py",
    "export_latex.py",
})

logger = logging.getLogger(__name__)


@dataclass
class GuardDecision:
    blocked: bool
    message: str = ""
    info: dict[str, Any] | None = None


class ToolGuard:
    """Stateful tool-call guard (loop protection + validation gate + manuscript gate)."""

    # ── construction / reset ───────────────────────────────────

    def __init__(self, logger: Any, config_dict: dict[str, Any] | None = None):
        self.logger = logger

        # Loop detection
        self._recent_tool_fps: deque[str] = deque(maxlen=LOOP_WINDOW)
        self._recent_sem_fps: deque[str] = deque(maxlen=LOOP_WINDOW)
        self._peek_manual_call_count: int = 0
        self._peek_manual_nohit_counts: dict[str, int] = {}

        # Input-validation gate: (normalised_file, normalised_software) → passed?
        self._validate_status_by_key: dict[tuple[str, str], bool] = {}

        # Manuscript gate (three-state):
        #   _manuscript_writes > 0  →  "some section was written; validation needed"
        #   _manuscript_validated is None  →  "never validated"
        #   _manuscript_validated is True  →  "last validation passed"
        #   _manuscript_validated is False →  "last validation failed"
        self._manuscript_writes: int = 0
        self._manuscript_validated: bool | None = None
        self._manuscript_fail_reason: str = ""
        planner_cfg = ((config_dict or {}).get("mat_master") or {}).get("planner") or {}
        quality_cfg = planner_cfg.get("quality_gates") or {}
        self._survey_min_retrieval_calls: int = int(quality_cfg.get("survey_min_retrieval_calls", 6))
        self._survey_min_retrieval_calls = max(1, self._survey_min_retrieval_calls)
        self._survey_retrieval_count: int = 0
        self._survey_writes: int = 0

    def reset_loop_history(self) -> None:
        """Clear loop-detection state so a new planner step starts fresh.

        Preserves ``_validate_status_by_key`` (input-file validations remain
        valid) and ``_manuscript_*`` state (manuscript gate spans the whole run).
        """
        self._recent_tool_fps.clear()
        self._recent_sem_fps.clear()
        self._peek_manual_call_count = 0
        self._peek_manual_nohit_counts.clear()
        self.logger.debug("ToolGuard loop history reset.")

    # ── static helpers: parsing / normalisation ────────────────

    @staticmethod
    def _parse_tool_args(tool_call) -> dict[str, Any]:
        args_str = tool_call.function.arguments or ""
        try:
            return json.loads(args_str) if args_str else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    @staticmethod
    def _normalize_input_path(path: str | None) -> str:
        if not path:
            return ""
        return str(path).replace("\\", "/").strip().lower()

    @staticmethod
    def _normalize_software_name(name: str | None) -> str:
        if not name:
            return ""
        return str(name).strip().lower().replace(" ", "_").replace("-", "_")

    @staticmethod
    def _extract_flag_value(script_args: str, flag_name: str) -> str | None:
        if not script_args:
            return None
        m = re.search(
            rf"--{re.escape(flag_name)}\s+[\"']?([^\"']+?)[\"']?(?:\s+--|$)",
            script_args,
            flags=re.IGNORECASE,
        )
        return m.group(1).strip() if m else None

    @staticmethod
    def _display_software(software: str) -> str:
        mapping = {
            "cp2k": "CP2K", "abinit": "ABINIT", "lammps": "LAMMPS",
            "orca": "ORCA", "pyatb": "PyATB", "quantum_espresso": "Quantum Espresso",
        }
        return mapping.get(software, software.upper())

    @staticmethod
    def _infer_binary_submit_software(tool_name: str) -> str:
        m = re.match(r"^mat_binary_calc_submit_run_(.+)$", tool_name or "")
        if not m:
            return ""
        token = m.group(1).lower()
        mapping = {
            "cp2k": "cp2k", "abinit": "abinit", "lammps": "lammps",
            "orca": "orca", "pyatb": "pyatb", "quantum_espresso": "quantum_espresso",
        }
        return mapping.get(token, token)

    # ── fingerprinting ─────────────────────────────────────────

    @staticmethod
    def _tool_fingerprint(tool_call) -> str:
        name = tool_call.function.name
        args_str = tool_call.function.arguments or ""
        try:
            args_obj = json.loads(args_str) if args_str else {}
            canonical = json.dumps(args_obj, sort_keys=True, ensure_ascii=False)
        except (json.JSONDecodeError, TypeError):
            canonical = args_str
        return f"{name}|{canonical}"

    @staticmethod
    def _semantic_fingerprint(tool_call) -> str:
        name = tool_call.function.name
        args_str = tool_call.function.arguments or ""
        try:
            args = json.loads(args_str) if args_str else {}
        except (json.JSONDecodeError, TypeError):
            args = {}

        script_args = args.get("script_args", "")
        script_name = args.get("script_name", "")
        if name == "use_skill" and "peek_manual" in script_name and script_args:
            sa = script_args.upper()
            sw_m = re.search(r'--SOFTWARE\s+(\S+)', sa)
            sw = sw_m.group(1) if sw_m else ""
            search_kw = ""
            m = re.search(r'--SEARCH\s+["\']?([^"\']+?)["\']?(?:\s+--|$)', sa)
            if m:
                search_kw = m.group(1).strip()
            section = ""
            m = re.search(r'--SECTION\s+["\']?([^"\']+?)["\']?(?:\s+--|$)', sa)
            if m:
                section = m.group(1).strip()
            sections = ""
            m = re.search(r'--SECTIONS\s+["\']?([^"\']+?)["\']?(?:\s+--|$)', sa)
            if m:
                sections = m.group(1).strip()
            tree = "--TREE" in sa
            return (
                f"peek_manual|{sw}|search={search_kw}"
                f"|section={section}|sections={sections}|tree={tree}"
            )

        try:
            canonical = json.dumps(args, sort_keys=True, ensure_ascii=False)
        except TypeError:
            canonical = args_str
        return f"{name}|{canonical}"

    # ── exemption / classification predicates ──────────────────

    @staticmethod
    def _is_exempt(tool_call) -> bool:
        name = tool_call.function.name or ""
        if any(name.endswith(s) for s in EXEMPT_TOOL_SUFFIXES):
            return True
        if name == "use_skill":
            args = ToolGuard._parse_tool_args(tool_call)
            skill = str(args.get("skill_name", "")).strip().lower()
            script = str(args.get("script_name", "")).strip().lower()
            # Exempt write/export scripts whose content legitimately differs each call.
            # validate_content.py and other review scripts are NOT exempt so that
            # identical repeated calls are caught by loop detection.
            if skill in {"manuscript-scribe", "deep-survey"} and script in EXEMPT_MANUSCRIPT_SCRIPTS:
                return True
            return False
        return False

    @staticmethod
    def _is_peek_manual_call(tool_call) -> bool:
        if tool_call.function.name != "use_skill":
            return False
        args = ToolGuard._parse_tool_args(tool_call)
        return "peek_manual" in str(args.get("script_name", ""))

    @staticmethod
    def _is_low_gain_manual_observation(observation: str) -> bool:
        text = (observation or "").lower()
        markers = (
            "no params matching",
            "no params found for section",
            "did you mean one of:",
            "do not retry this exact section path",
            "do not repeat this same search",
        )
        return any(m in text for m in markers)

    @staticmethod
    def _extract_observation_text(observation: str) -> str:
        """Unwrap formatted tool observation payload when possible."""
        if not observation:
            return ""
        text = observation
        try:
            parsed = json.loads(observation)
            if isinstance(parsed, dict) and "observation" in parsed:
                inner = parsed.get("observation")
                if isinstance(inner, str):
                    text = inner
                elif isinstance(inner, dict):
                    text = json.dumps(inner, ensure_ascii=False)
        except Exception:
            pass
        return text

    @classmethod
    def _extract_longtask_status(cls, observation: str) -> tuple[str, str] | None:
        """Parse LONGTASK_RESULT_JSON status from tool output text."""
        from ..skills._common.longtask_runtime import parse_prefixed_result_line

        text = cls._extract_observation_text(observation)
        obj = parse_prefixed_result_line(text)
        if obj is None:
            return None
        status = str(obj.get("status", "")).strip().lower()
        message = str(obj.get("message", "")).strip()
        return (status, message) if status else None

    # ── manuscript gate ────────────────────────────────────────

    @classmethod
    def _check_manuscript_script_ok(
        cls,
        script_name: str,
        observation: str,
        info: dict[str, Any],
    ) -> tuple[bool, str]:
        """Check whether a manuscript-scribe validation / assembly script passed.

        Returns ``(ok, fail_reason)``.  Only inspects scripts that produce
        validation verdicts (``validate_content.py``, ``assemble_manuscript.py``).
        """
        script = (script_name or "").strip().lower()
        if script not in {"validate_content.py", "assemble_manuscript.py"}:
            return True, ""

        longtask = cls._extract_longtask_status(observation)
        if longtask is not None:
            status, msg = longtask
            if status == "completed":
                return True, ""
            if status in {"retryable_error", "fatal_error", "needs_input", "running"}:
                reason = msg or f"{script} returned status={status}"
                return False, reason

        exit_code = int(info.get("exit_code", 0) or 0)
        if exit_code != 0:
            return False, f"{script} exited with code {exit_code}."

        text = cls._extract_observation_text(observation).lower()
        for marker in MANUSCRIPT_FAIL_MARKERS:
            if marker in text:
                return False, f"Validation failed: '{marker}' detected in output."
        return True, ""

    def can_finish_manuscript(self) -> tuple[bool, str]:
        """Gate for ``finish``: block if manuscript was written but not validated,
        or if the latest validation / assembly failed.

        Returns ``(can_finish, reason_if_blocked)``.
        """
        if self._manuscript_writes == 0:
            return True, ""  # No manuscript work this run — gate open.
        if self._manuscript_validated is None:
            return False, (
                "Manuscript sections were written but never validated. "
                "Run validate_content.py or assemble_manuscript.py before finishing."
            )
        if self._manuscript_validated is False:
            return False, self._manuscript_fail_reason or "Manuscript validation failed."
        return True, ""

    def can_finish_survey(self) -> tuple[bool, str]:
        """Gate for ``finish`` on survey/literature runs."""
        if self._survey_writes == 0:
            return True, ""
        if self._survey_retrieval_count < self._survey_min_retrieval_calls:
            return False, (
                "Survey/literature artifacts were written but retrieval depth is insufficient. "
                f"Observed retrieval calls: {self._survey_retrieval_count}, "
                f"required minimum: {self._survey_min_retrieval_calls}."
            )
        return True, ""

    # ── input-validation gate (submit blocking) ────────────────

    def _submit_block_reason(self, tool_call) -> tuple[str | None, str]:
        name = tool_call.function.name
        software = self._infer_binary_submit_software(name)
        if not software:
            return None, ""

        args = self._parse_tool_args(tool_call)
        input_file = self._normalize_input_path(args.get("input_file"))
        if not input_file:
            return "missing_input_file", software

        key = (input_file, software)
        validated = self._validate_status_by_key.get(key)
        if validated is None:
            return "not_validated", software
        if validated is False:
            return "validate_failed", software
        return None, software

    # ── loop detection core ────────────────────────────────────

    def _is_loop(self, tool_call) -> tuple[bool, dict[str, Any]]:
        if self._is_exempt(tool_call):
            return False, {"reason": "exempt"}

        sem_fp = self._semantic_fingerprint(tool_call)
        if self._is_peek_manual_call(tool_call):
            if self._peek_manual_call_count >= PEEK_MANUAL_MAX_CALLS:
                return True, {"reason": "manual_budget"}
            if self._peek_manual_nohit_counts.get(sem_fp, 0) >= PEEK_MANUAL_LOW_GAIN_MAX_REPEATS:
                return True, {"reason": "manual_low_gain_repeat"}

        fp = self._tool_fingerprint(tool_call)
        if self._recent_tool_fps.count(fp) >= LOOP_THRESHOLD:
            return True, {"reason": "exact_duplicate"}
        if self._recent_sem_fps.count(sem_fp) >= LOOP_THRESHOLD:
            return True, {"reason": "semantic_duplicate"}
        return False, {"reason": "ok"}

    # ── public API ─────────────────────────────────────────────

    def evaluate(self, tool_call) -> GuardDecision:
        """Evaluate whether a tool call should be blocked."""
        # Windows heredoc gate
        if tool_call.function.name == "execute_bash" and sys.platform == "win32":
            args = self._parse_tool_args(tool_call)
            command = str(args.get("command", "") or "")
            if "<<" in command:
                return GuardDecision(
                    blocked=True,
                    message=(
                        "⚠️ WINDOWS SHELL GATE: heredoc syntax (`<<`) is blocked on Windows shell. "
                        "Use `str_replace_editor` to write files, then run the script with `execute_bash`."
                    ),
                    info={"reason": "windows_heredoc_blocked"},
                )

        # Input-validation gate
        submit_reason, software = self._submit_block_reason(tool_call)
        if submit_reason:
            return self._build_submit_block(tool_call, submit_reason, software)

        # Loop detection
        is_loop, loop_info = self._is_loop(tool_call)
        if not is_loop:
            return GuardDecision(blocked=False, info={"reason": "ok"})
        return self._build_loop_block(tool_call, loop_info)

    def record_tool_call(self, tool_call) -> None:
        """Record a tool call fingerprint in the sliding window."""
        if self._is_exempt(tool_call):
            return
        self._recent_tool_fps.append(self._tool_fingerprint(tool_call))
        self._recent_sem_fps.append(self._semantic_fingerprint(tool_call))
        if self._is_peek_manual_call(tool_call):
            self._peek_manual_call_count += 1

    def update_after_tool(
        self, tool_call, observation: str, info: dict[str, Any],
    ) -> None:
        """Update post-execution state from a completed tool call."""
        args = self._parse_tool_args(tool_call)
        tool_name = str(tool_call.function.name or "").strip()
        if info.get("loop_blocked"):
            return

        # ── peek_manual low-gain tracking ────────────────────
        if self._is_peek_manual_call(tool_call):
            sem_fp = self._semantic_fingerprint(tool_call)
            if self._is_low_gain_manual_observation(observation):
                self._peek_manual_nohit_counts[sem_fp] = (
                    self._peek_manual_nohit_counts.get(sem_fp, 0) + 1
                )
            else:
                self._peek_manual_nohit_counts.pop(sem_fp, None)

        # ── validate_input gate tracking ─────────────────────
        if (
            tool_call.function.name == "use_skill"
            and args.get("script_name") == "validate_input.py"
        ):
            script_args = args.get("script_args", "") or ""
            input_file = self._extract_flag_value(script_args, "input_file")
            software = self._extract_flag_value(script_args, "software")
            nf = self._normalize_input_path(input_file)
            ns = self._normalize_software_name(software)
            if nf and ns:
                self._validate_status_by_key[(nf, ns)] = (info.get("exit_code") == 0)

        # ── manuscript gate tracking ─────────────────────────
        if tool_call.function.name == "use_skill":
            skill = str(args.get("skill_name", "")).strip().lower()
            script = str(args.get("script_name", "")).strip().lower()
            if skill == "manuscript-scribe":
                if "write_section" in script:
                    self._manuscript_writes += 1
                if script in {"validate_content.py", "assemble_manuscript.py"}:
                    ok, reason = self._check_manuscript_script_ok(
                        script, observation, info or {},
                    )
                    self._manuscript_validated = ok
                    self._manuscript_fail_reason = "" if ok else reason
            if skill == "deep-survey":
                if script in {"run_survey.py", "write_section.py"} and int(info.get("exit_code", 0) or 0) == 0:
                    self._survey_writes += 1

        _SURVEY_RETRIEVAL_TOOLS = {
            "mat_sn_search-papers-enhanced",
            "mat_sn_web-search",
            "extract_info_from_webpage",
            "mat_struct_db_fetch_structures_from_db",
        }
        if tool_name in _SURVEY_RETRIEVAL_TOOLS:
            if self._observation_has_content(observation):
                self._survey_retrieval_count += 1

    @staticmethod
    def _observation_has_content(observation: str) -> bool:
        """Return True when the tool observation looks like a successful retrieval.

        Recognises:
        - ``{"status": "success", ...}`` (mat_sn_* tools)
        - ``{"code": 0, ...}`` (mat_struct_db_*)
        - ``{"webpage_detailed_contents ...": {...}, ...}`` (extract_info_from_webpage)
        - Any non-empty dict/string that does NOT contain an error marker.
        """
        text = (observation or "").strip()
        if not text:
            return False
        _ERROR_MARKERS = {"error", "timed out", "failed", "exception"}
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                status = str(parsed.get("status", "")).strip().lower()
                if status:
                    return status == "success"
                inner = parsed.get("observation")
                if isinstance(inner, dict):
                    inner_status = str(inner.get("status", "")).strip().lower()
                    if inner_status:
                        return inner_status == "success"
                if parsed.get("code") == 0:
                    return True
                if any(k.startswith("webpage_detailed_contents") for k in parsed):
                    return not any(
                        m in text.lower()[:500] for m in _ERROR_MARKERS
                    )
                return bool(parsed)
        except Exception:
            pass
        return len(text) > 20 and not any(m in text.lower()[:500] for m in _ERROR_MARKERS)

    # ── private: decision builders ─────────────────────────────

    def _build_submit_block(
        self, tool_call, reason: str, software: str,
    ) -> GuardDecision:
        args = self._parse_tool_args(tool_call)
        input_file = args.get("input_file", "<unknown>")
        sw_disp = self._display_software(software)
        self.logger.warning(
            "VALIDATION GATE BLOCKED: tool='%s' input_file='%s' reason=%s software=%s",
            tool_call.function.name, input_file, reason, software,
        )
        messages = {
            "not_validated": (
                "⚠️ VALIDATION GATE BLOCKED: Submission is blocked because this input file has not passed "
                "`validate_input.py` yet.\n\n"
                "ACTION REQUIRED:\n"
                f"1. Run `use_skill` with `validate_input.py --input_file \"{input_file}\" --software {sw_disp}`.\n"
                "2. Fix all validation errors.\n"
                "3. Re-run validation until exit_code=0, then submit."
            ),
            "validate_failed": (
                "⚠️ VALIDATION GATE BLOCKED: Submission is blocked because the latest "
                "`validate_input.py` result for this input file is FAIL.\n\n"
                "ACTION REQUIRED:\n"
                "1. Read validation errors.\n"
                "2. Fix the input file.\n"
                "3. Re-run validation until exit_code=0, then submit."
            ),
        }
        msg = messages.get(reason, (
            "⚠️ VALIDATION GATE BLOCKED: Submission is blocked because input_file is missing or invalid.\n"
            "Provide a valid input_file and pass validation first."
        ))
        return GuardDecision(
            blocked=True, message=msg,
            info={"reason": reason, "software": software},
        )

    def _build_loop_block(
        self, tool_call, loop_info: dict[str, Any],
    ) -> GuardDecision:
        reason = loop_info.get("reason", "loop_detected")

        if reason == "manual_budget":
            self.logger.warning(
                "BUDGET EXHAUSTED: peek_manual called %d times (max %d), skipping.",
                self._peek_manual_call_count, PEEK_MANUAL_MAX_CALLS,
            )
            return GuardDecision(
                blocked=True,
                message=(
                    f"⚠️ MANUAL QUERY BUDGET EXHAUSTED: You have already called peek_manual.py "
                    f"{self._peek_manual_call_count} times (limit: {PEEK_MANUAL_MAX_CALLS}). "
                    "ALL further manual queries are BLOCKED.\n\n"
                    "ACTION REQUIRED: STOP searching the manual. You have enough information. "
                    "Use your domain knowledge to write the input file directly and move to validate/fix loop."
                ),
                info={"reason": reason},
            )

        if reason == "manual_low_gain_repeat":
            self.logger.warning(
                "LOW-GAIN REPEAT BLOCKED: same no-result manual intent repeated >= %d times.",
                PEEK_MANUAL_LOW_GAIN_MAX_REPEATS,
            )
            return GuardDecision(
                blocked=True,
                message=(
                    "⚠️ LOW-GAIN MANUAL QUERY BLOCKED: This same manual-query intent has already returned "
                    f"no useful parameters {PEEK_MANUAL_LOW_GAIN_MAX_REPEATS}+ times.\n\n"
                    "ACTION REQUIRED: switch strategy now.\n"
                    "1. Stop repeating this section/keyword.\n"
                    "2. Use a different section path OR proceed with domain knowledge and validate.\n"
                    "3. If writing an input file, move to validate/fix loop."
                ),
                info={"reason": reason},
            )

        # exact_duplicate / semantic_duplicate
        self.logger.warning(
            "LOOP DETECTED: tool '%s' with same args called %d+ times, skipping.",
            tool_call.function.name, LOOP_THRESHOLD,
        )
        return GuardDecision(
            blocked=True,
            message=(
                f"⚠️ LOOP DETECTED: You have called '{tool_call.function.name}' with the exact same arguments "
                f"{LOOP_THRESHOLD}+ times already and received the same result each time. "
                "This call was SKIPPED to prevent an infinite loop.\n\n"
                "ACTION REQUIRED: Do NOT call this tool again with the same arguments. "
                "Try a different approach."
            ),
            info={"reason": reason},
        )
