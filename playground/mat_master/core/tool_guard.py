from __future__ import annotations

import json
import re
from collections import deque
from dataclasses import dataclass
from typing import Any

# How many recent tool calls to track for loop detection.
LOOP_WINDOW = 30
# Block on the 2nd identical call for deterministic tools.
LOOP_THRESHOLD = 1
# Maximum number of peek_manual search/section queries per run.
PEEK_MANUAL_MAX_CALLS = 12
# For the same "no-result" manual query intent, block after this many misses.
PEEK_MANUAL_LOW_GAIN_MAX_REPEATS = 2

# Tools exempt from loop detection and fingerprint recording.
LOOP_EXEMPT_SUFFIXES = (
    "query_job_status",
    "get_job_status",
)


@dataclass
class GuardDecision:
    blocked: bool
    message: str = ""
    info: dict[str, Any] | None = None


class ToolGuard:
    """Stateful tool-call guard (loop protection + validation gate)."""

    def __init__(self, logger: Any):
        self.logger = logger
        self._recent_tool_fps: deque[str] = deque(maxlen=LOOP_WINDOW)
        self._recent_sem_fps: deque[str] = deque(maxlen=LOOP_WINDOW)
        self._peek_manual_call_count: int = 0
        self._peek_manual_nohit_counts: dict[str, int] = {}
        # Keyed by (normalized_input_file, normalized_software).
        self._validate_status_by_key: dict[tuple[str, str], bool] = {}

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
            sw = ""
            sw_m = re.search(r'--SOFTWARE\s+(\S+)', sa)
            if sw_m:
                sw = sw_m.group(1)
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
            return f"peek_manual|{sw}|search={search_kw}|section={section}|sections={sections}|tree={tree}"

        try:
            canonical = json.dumps(args, sort_keys=True, ensure_ascii=False)
        except TypeError:
            canonical = args_str
        return f"{name}|{canonical}"

    @staticmethod
    def _is_loop_exempt(tool_call) -> bool:
        name = tool_call.function.name or ""
        return any(name.endswith(suffix) for suffix in LOOP_EXEMPT_SUFFIXES)

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
    def _infer_binary_submit_software(tool_name: str) -> str:
        m = re.match(r"^mat_binary_calc_submit_run_(.+)$", tool_name or "")
        if not m:
            return ""
        token = m.group(1).lower()
        mapping = {
            "cp2k": "cp2k",
            "abinit": "abinit",
            "lammps": "lammps",
            "orca": "orca",
            "pyatb": "pyatb",
            "quantum_espresso": "quantum_espresso",
        }
        return mapping.get(token, token)

    @staticmethod
    def _display_software(software: str) -> str:
        mapping = {
            "cp2k": "CP2K",
            "abinit": "ABINIT",
            "lammps": "LAMMPS",
            "orca": "ORCA",
            "pyatb": "PyATB",
            "quantum_espresso": "Quantum Espresso",
        }
        return mapping.get(software, software.upper())

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

    def _is_loop(self, tool_call) -> tuple[bool, dict[str, Any]]:
        if self._is_loop_exempt(tool_call):
            return False, {"reason": "loop_exempt"}

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

    def evaluate(self, tool_call) -> GuardDecision:
        """Evaluate whether a tool call should be blocked."""
        submit_reason, software = self._submit_block_reason(tool_call)
        if submit_reason:
            args = self._parse_tool_args(tool_call)
            input_file = args.get("input_file", "<unknown>")
            self.logger.warning(
                "VALIDATION GATE BLOCKED: tool='%s' input_file='%s' reason=%s software=%s",
                tool_call.function.name,
                input_file,
                submit_reason,
                software,
            )
            sw_disp = self._display_software(software)
            if submit_reason == "not_validated":
                return GuardDecision(
                    blocked=True,
                    message=(
                        "⚠️ VALIDATION GATE BLOCKED: Submission is blocked because this input file has not passed "
                        "`validate_input.py` yet.\n\n"
                        "ACTION REQUIRED:\n"
                        "1. Run `use_skill` with `validate_input.py --input_file \""
                        f"{input_file}"
                        f"\" --software {sw_disp}`.\n"
                        "2. Fix all validation errors.\n"
                        "3. Re-run validation until exit_code=0, then submit."
                    ),
                    info={"reason": "not_validated", "software": software},
                )
            if submit_reason == "validate_failed":
                return GuardDecision(
                    blocked=True,
                    message=(
                        "⚠️ VALIDATION GATE BLOCKED: Submission is blocked because the latest "
                        "`validate_input.py` result for this input file is FAIL.\n\n"
                        "ACTION REQUIRED:\n"
                        "1. Read validation errors.\n"
                        "2. Fix the input file.\n"
                        "3. Re-run validation until exit_code=0, then submit."
                    ),
                    info={"reason": "validate_failed", "software": software},
                )
            return GuardDecision(
                blocked=True,
                message=(
                    "⚠️ VALIDATION GATE BLOCKED: Submission is blocked because input_file is missing or invalid.\n"
                    "Provide a valid input_file and pass validation first."
                ),
                info={"reason": "missing_input_file", "software": software},
            )

        is_loop, loop_info = self._is_loop(tool_call)
        if not is_loop:
            return GuardDecision(blocked=False, info={"reason": "ok"})

        reason = loop_info.get("reason", "loop_detected")
        if reason == "manual_budget":
            self.logger.warning(
                "BUDGET EXHAUSTED: peek_manual called %d times (max %d), skipping.",
                self._peek_manual_call_count,
                PEEK_MANUAL_MAX_CALLS,
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

        self.logger.warning(
            "LOOP DETECTED: tool '%s' with same args called %d+ times, skipping.",
            tool_call.function.name,
            LOOP_THRESHOLD,
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

    def record_tool_call(self, tool_call) -> None:
        """Record a tool call fingerprint in the sliding window."""
        if self._is_loop_exempt(tool_call):
            return
        self._recent_tool_fps.append(self._tool_fingerprint(tool_call))
        self._recent_sem_fps.append(self._semantic_fingerprint(tool_call))
        if self._is_peek_manual_call(tool_call):
            self._peek_manual_call_count += 1

    def update_after_tool(self, tool_call, observation: str, info: dict[str, Any]) -> None:
        """Update low-gain and validation gate states from a completed tool call."""
        args = self._parse_tool_args(tool_call)

        if self._is_peek_manual_call(tool_call):
            sem_fp = self._semantic_fingerprint(tool_call)
            if self._is_low_gain_manual_observation(observation):
                self._peek_manual_nohit_counts[sem_fp] = self._peek_manual_nohit_counts.get(sem_fp, 0) + 1
            else:
                self._peek_manual_nohit_counts.pop(sem_fp, None)

        if tool_call.function.name == "use_skill" and args.get("script_name") == "validate_input.py":
            script_args = args.get("script_args", "") or ""
            input_file = self._extract_flag_value(script_args, "input_file")
            software = self._extract_flag_value(script_args, "software")
            normalized_file = self._normalize_input_path(input_file)
            normalized_sw = self._normalize_software_name(software)
            if normalized_file and normalized_sw:
                self._validate_status_by_key[(normalized_file, normalized_sw)] = (info.get("exit_code") == 0)

