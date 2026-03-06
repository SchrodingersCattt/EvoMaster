"""Stateful tool-call guard: loop prevention, manuscript gate,
structure-retrieval gate, prepare gate, auth-failure stop, dangerous script gate.

Six independent concerns, each with its own state and public method:

1. **Loop detection** (``evaluate`` / ``record_tool_call``): Blocks repeated
   calls with identical arguments within a sliding window.
2. **Manuscript gate** (``can_finish_manuscript``): Blocks ``finish`` when
   manuscript sections were written but never validated, or when the last
   validation failed.
3. **Structure-retrieval gate** (``can_finish_structure_retrieval`` /
   ``update_structure_retrieval``): Classifies mat_struct_db_* candidates by
   confidence (fallback_level), enforces ``task_completed=partial`` when no
   CIF is delivered, and blocks low-value repeated retrieval when the stop
   condition is already met.
4. **Prepare gate** (``evaluate``): Blocks ``mat_binary_calc_prepare_*`` when
   ``input_file`` was created by ``str_replace_editor`` instead of sourced
   via ``input-manual-helper get_reference``.
5. **Auth-failure stop gate** (``evaluate`` / ``update_after_tool``): After
   AUTH_FAILURE_THRESHOLD consecutive authentication errors from mat_* MCP
   tools, blocks further execute_bash and str_replace_editor calls to prevent
   autonomous credential hunting.
6. **Dangerous script gate** (``evaluate``): Scans Python file content on
   str_replace_editor create and on execute_bash python <script> for dangerous
   patterns (os.environ, credential hunting, etc.).
"""

from __future__ import annotations

import importlib.util
import json
import logging
import re
import sys
from collections import deque
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any

from .constants import MANUSCRIPT_FAIL_MARKERS

try:
    from evomaster.agent.tools.builtin.bash_safety import (
        is_dangerous_bash_command,
        is_dangerous_python_content,
    )
except ImportError:
    is_dangerous_bash_command = None  # evomaster not available in some test envs
    is_dangerous_python_content = None

# Only STRONG markers trigger the auth-failure gate (system auth, not third-party 403).
# 403 is excluded: often from anti-scraping, IP/subscription limits, not our credentials.
AUTH_FAILURE_MARKERS_STRONG: tuple[str, ...] = (
    "authentication failed",
    "invalid api key",
    "invalid accesskey",
    "accesskey invalid",
    "akid",
    "invalid credentials",
    "unauthorized",
)
# 401 can indicate system auth; we do not increment on 401 alone to avoid third-party false positives.
AUTH_FAILURE_MARKERS_WEAK: tuple[str, ...] = ("401",)
# If observation contains these URL fragments, do not count as auth failure (third-party response).
AUTH_FAILURE_THIRD_PARTY_DOMAINS: tuple[str, ...] = (
    "pubs.acs.org",
    "pmc.ncbi.nlm.nih.gov",
    "springer.com",
    "nature.com",
    "sciencedirect.com",
    "doi.org",
)
AUTH_FAILURE_THRESHOLD = 3

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


# ── Structure-retrieval gate parameters ───────────────────────
# fallback_level > 0 → element-fallback result, treated as candidate (not accepted)
STRUCT_FALLBACK_ACCEPTED_LEVEL = 0
# After this many consecutive retrieval calls with no new accepted candidate, block further db calls.
STRUCT_RETRIEVAL_STALL_LIMIT = 3


@dataclass
class StructCandidateRecord:
    """Record of a single mat_struct_db_* retrieval result."""
    formula: str = ""
    material_id: str = ""
    fallback_level: int = 0
    accepted: bool = False
    rejection_reason: str = ""


@dataclass
class StructRetrievalState:
    """Tracks structure retrieval progress for a single task run."""
    # Set from concept-alignment artifact when available
    target_terms: list[str] = field(default_factory=list)
    requested_count: int = 0          # 0 = no explicit count requested
    # Counters
    accepted_count: int = 0
    candidate_count: int = 0          # fallback-level results
    rejected_count: int = 0
    # Stall tracking (consecutive db calls that produced no new accepted item)
    db_calls_since_last_accept: int = 0
    # Whether any structure file (CIF/POSCAR) was delivered to the user
    structure_file_delivered: bool = False
    # Running list for diagnostics
    records: list[StructCandidateRecord] = field(default_factory=list)

    def stop_condition_met(self) -> bool:
        """True when enough validated structures have been collected."""
        if self.requested_count > 0 and self.accepted_count >= self.requested_count:
            return True
        return False

    def stalled(self) -> bool:
        """True when no new accepted item for STRUCT_RETRIEVAL_STALL_LIMIT calls."""
        return (
            self.accepted_count == 0
            and self.db_calls_since_last_accept >= STRUCT_RETRIEVAL_STALL_LIMIT
        )


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
        # Structure-retrieval gate
        self._struct_retrieval: StructRetrievalState = StructRetrievalState()
        # Input-file origin tracking (prepare_* gate)
        self._str_replace_created_paths: set[str] = set()
        # Auth-failure stop gate
        self._auth_failure_count: int = 0
        self._auth_failure_locked: bool = False

    def reset_loop_history(self) -> None:
        """Clear loop-detection state so a new planner step starts fresh.

        Preserves ``_manuscript_*`` state (manuscript gate spans the whole run).
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
    def _extract_python_script_path(command: str) -> str | None:
        """Extract script path from 'python <script.py>' or 'python3 <script.py>'."""
        m = re.match(r"^\s*python3?\s+([^\s;|&]+\.py)\b", command.strip())
        return m.group(1) if m else None

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

    @staticmethod
    def _infer_prepare_software(tool_name: str) -> str:
        m = re.match(r"^mat_binary_calc_prepare_(.+?)_job$", tool_name or "")
        return m.group(1).lower() if m else ""

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
        # peek_manual.py was removed; input-manual-helper uses official docs only.
        return False

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

    def can_finish_survey(self, workspace: str = "") -> tuple[bool, str]:
        """Gate for ``finish`` on survey/literature runs.

        When workspace is set and retrieval count passes, also runs concept-coverage
        check via survey_contract library (reads key_concepts from collected_*.json).
        """
        if self._survey_writes == 0:
            return True, ""
        if self._survey_retrieval_count < self._survey_min_retrieval_calls:
            return False, (
                "Survey/literature artifacts were written but retrieval depth is insufficient. "
                f"Observed retrieval calls: {self._survey_retrieval_count}, "
                f"required minimum: {self._survey_min_retrieval_calls}."
            )
        if workspace and workspace.strip():
            _survey_contract_path = (
                Path(__file__).resolve().parents[1]
                / "skills"
                / "deep-survey"
                / "scripts"
                / "survey_contract.py"
            )
            if _survey_contract_path.exists():
                try:
                    spec = importlib.util.spec_from_file_location(
                        "survey_contract", _survey_contract_path
                    )
                    if spec and spec.loader:
                        mod = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(mod)
                        passed, reason = mod.check_concept_coverage_workspace(
                            Path(workspace)
                        )
                        if not passed:
                            return False, reason
                except Exception as e:
                    self.logger.warning(
                        "Survey concept coverage check failed: %s", e
                    )
        return True, ""

    # ── structure-retrieval gate ────────────────────────────────

    def init_structure_retrieval(
        self,
        target_terms: list[str] | None = None,
        requested_count: int = 0,
    ) -> None:
        """Initialise (or re-initialise) the structure-retrieval tracking state.

        Call this at task start when a concept-alignment artifact is available, so
        the guard knows the target concept and when to stop retrieval.

        Args:
            target_terms: Normalised terms that a retrieved structure must match
                (material name, formula aliases, CCDC/ICSD prefix, etc.).  If
                empty, target-consistency checks are skipped.
            requested_count: How many validated structures the task requested.
                0 means no explicit count; stop-condition based on count is
                disabled.
        """
        self._struct_retrieval = StructRetrievalState(
            target_terms=[t.lower().strip() for t in (target_terms or []) if t],
            requested_count=max(0, int(requested_count)),
        )
        self.logger.debug(
            "StructRetrievalState initialised: target_terms=%s requested_count=%d",
            self._struct_retrieval.target_terms,
            self._struct_retrieval.requested_count,
        )

    def update_structure_retrieval(
        self,
        observation: str,
        info: dict[str, Any],
    ) -> None:
        """Parse a mat_struct_db_* observation and classify the result.

        ``fallback_level > 0`` → element-based fallback → candidate only (not
        accepted).  A level-0 result is accepted only if it passes a loose
        target-consistency check against ``_struct_retrieval.target_terms``.

        Also tracks whether any structure file was delivered.
        """
        sr = self._struct_retrieval

        # Track structure-file delivery (CIF/POSCAR downloaded to local_path)
        if not sr.structure_file_delivered:
            text = (observation or "").lower()
            if any(ext in text for ext in (".cif", ".vasp", "poscar", ".xyz", ".res")):
                sr.structure_file_delivered = True

        # Parse fallback_level and formula from observation
        fallback_level: int = 0
        formula: str = ""
        material_id: str = ""
        try:
            parsed = json.loads(observation) if observation else {}
            if isinstance(parsed, dict):
                inner = parsed.get("observation", parsed)
                if isinstance(inner, str):
                    try:
                        inner = json.loads(inner)
                    except Exception:
                        inner = {}
                if isinstance(inner, dict):
                    fallback_level = int(inner.get("fallback_level", 0) or 0)
                    # structures may be a list; pull first entry
                    structs = inner.get("structures") or inner.get("results") or []
                    if isinstance(structs, list) and structs:
                        first = structs[0] if isinstance(structs[0], dict) else {}
                        formula = str(first.get("formula", "") or first.get("reduced_formula", ""))
                        material_id = str(first.get("material_id", "") or first.get("id", ""))
                    elif isinstance(inner.get("formula"), str):
                        formula = inner["formula"]
        except Exception:
            pass

        sr.candidate_count += 1
        sr.db_calls_since_last_accept += 1

        # Acceptance decision
        rejection_reason = ""
        accepted = False

        if fallback_level > STRUCT_FALLBACK_ACCEPTED_LEVEL:
            rejection_reason = (
                f"fallback_level={fallback_level} (element-based match, "
                "not a direct hit for the requested formula/compound)"
            )
        elif sr.target_terms:
            # Loose target-consistency: at least one target term must appear in
            # formula or material_id string (case-insensitive).
            haystack = (formula + " " + material_id).lower()
            if not any(term in haystack for term in sr.target_terms):
                rejection_reason = (
                    f"target-consistency check failed: formula='{formula}' "
                    f"does not match target_terms={sr.target_terms}"
                )
        if not rejection_reason:
            accepted = True

        record = StructCandidateRecord(
            formula=formula,
            material_id=material_id,
            fallback_level=fallback_level,
            accepted=accepted,
            rejection_reason=rejection_reason,
        )
        sr.records.append(record)

        if accepted:
            sr.accepted_count += 1
            sr.db_calls_since_last_accept = 0
            self.logger.info(
                "[struct-retrieval] Accepted candidate: formula='%s' id='%s' fallback=%d",
                formula, material_id, fallback_level,
            )
        else:
            sr.rejected_count += 1
            self.logger.info(
                "[struct-retrieval] Rejected candidate: formula='%s' fallback=%d reason='%s'",
                formula, fallback_level, rejection_reason,
            )

    def can_finish_structure_retrieval(
        self, requested_task_completed: str
    ) -> tuple[bool, str]:
        """Gate for ``finish`` on structure-retrieval runs.

        Rules:
        - If ``task_completed=true`` but no structure file was delivered →
          downgrade to ``partial`` (blocked with message).
        - If ``task_completed=true`` and ``requested_count > 0`` but
          ``accepted_count < requested_count`` → require partial.

        Returns ``(can_finish_as_requested, reason_if_blocked)``.
        """
        sr = self._struct_retrieval

        # Only gate when the retrieval state has been actively used.
        if sr.candidate_count == 0 and not sr.structure_file_delivered:
            return True, ""

        if requested_task_completed == "true":
            if not sr.structure_file_delivered:
                reason = (
                    "Structure-retrieval gate: no CIF/POSCAR file was delivered. "
                    "Set task_completed='partial' and include any found identifiers "
                    "(CCDC REFCODE, ICSD code, DOI, space group, lattice constants) "
                    "in the finish message."
                )
                return False, reason

        return True, ""

    def _is_struct_db_retrieval_blocked(self, tool_call) -> bool:
        """Return True if another mat_struct_db_* call should be blocked because
        the stop condition is already met or retrieval has stalled."""
        name = str(tool_call.function.name or "")
        if not name.startswith("mat_struct_db_"):
            return False
        sr = self._struct_retrieval
        if sr.stop_condition_met():
            self.logger.info(
                "[struct-retrieval] Blocking mat_struct_db_* call: "
                "stop condition met (accepted=%d >= requested=%d).",
                sr.accepted_count, sr.requested_count,
            )
            return True
        return False

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
        # Gate 1: Auth-failure lock (runs first)
        if self._auth_failure_locked and tool_call.function.name in {
            "execute_bash", "str_replace_editor"
        }:
            return GuardDecision(
                blocked=True,
                message=(
                    "AUTH FAILURE STOP GATE: Authentication errors were detected "
                    f"{self._auth_failure_count} time(s). Writing or executing scripts "
                    "to locate alternative credentials is not allowed.\n\n"
                    "ACTION REQUIRED: call finish with task_completed=false and report "
                    "the authentication error so the user can fix the configuration."
                ),
                info={"reason": "auth_failure_locked"},
            )

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

        # Gate 2: str_replace_editor file content scan
        if (
            tool_call.function.name == "str_replace_editor"
            and is_dangerous_python_content is not None
        ):
            args = self._parse_tool_args(tool_call)
            cmd = str(args.get("command", "")).strip().lower()
            if cmd in {"create", "write"}:
                file_text = str(args.get("file_text", "") or "")
                if file_text:
                    is_dangerous, reason = is_dangerous_python_content(file_text)
                    if is_dangerous:
                        return GuardDecision(
                            blocked=True,
                            message=(
                                f"DANGEROUS SCRIPT CONTENT BLOCKED: {reason}\n\n"
                                "Writing scripts that read environment variables or scan "
                                "for credentials is not permitted."
                            ),
                            info={"reason": "dangerous_python_content"},
                        )

        # Dangerous command gate: block env, rm -rf /, etc.
        if tool_call.function.name == "execute_bash" and is_dangerous_bash_command is not None:
            args = self._parse_tool_args(tool_call)
            command = str(args.get("command", "") or "")
            is_dangerous, reason = is_dangerous_bash_command(command)
            if is_dangerous:
                return GuardDecision(
                    blocked=True,
                    message=f"⚠️ BLOCKED: {reason}",
                    info={"reason": "dangerous_bash_command"},
                )
            # Gate 3: execute_bash python script pre-execution scan
            if is_dangerous_python_content is not None:
                script_path = self._extract_python_script_path(command)
                if script_path:
                    script_norm = self._normalize_input_path(script_path)
                    if script_norm in self._str_replace_created_paths:
                        path_to_read = script_path
                    else:
                        path_to_read = next(
                            (p for p in self._str_replace_created_paths if Path(p).name == Path(script_path).name),
                            None,
                        )
                    if path_to_read:
                        try:
                            content = Path(path_to_read).read_text(encoding="utf-8", errors="ignore")
                            is_dangerous, reason = is_dangerous_python_content(content)
                            if is_dangerous:
                                return GuardDecision(
                                    blocked=True,
                                    message=(
                                        f"DANGEROUS SCRIPT EXECUTION BLOCKED: {reason}\n\n"
                                        f"The script '{script_path}' contains dangerous patterns "
                                        "and cannot be executed."
                                    ),
                                    info={"reason": "dangerous_python_script", "path": script_path},
                                )
                        except OSError:
                            pass  # file unreadable — allow normal flow

        # Structure-retrieval stop gate: block mat_struct_db_* when done
        if self._is_struct_db_retrieval_blocked(tool_call):
            sr = self._struct_retrieval
            return GuardDecision(
                blocked=True,
                message=(
                    f"⚠️ STRUCTURE RETRIEVAL STOP GATE: {sr.accepted_count} validated structures "
                    f"have been collected (requested: {sr.requested_count}). "
                    "Further mat_struct_db_* calls are BLOCKED — the stop condition is satisfied.\n\n"
                    "ACTION REQUIRED: call finish with task_completed='partial' (or 'true' if a "
                    "CIF/POSCAR was delivered) and report all accepted structures."
                ),
                info={"reason": "struct_retrieval_stop", "accepted_count": sr.accepted_count},
            )

        # Literature-phase-complete gate: when survey has started, block downstream
        # (structure retrieval, calculation submit) until minimum retrieval calls are met.
        tool_name = tool_call.function.name or ""
        if (
            self._survey_writes > 0
            and self._survey_retrieval_count < self._survey_min_retrieval_calls
            and (
                tool_name.startswith("mat_struct_db_")
                or re.match(r"^mat_binary_calc_submit_", tool_name)
            )
        ):
            return GuardDecision(
                blocked=True,
                message=(
                    "LITERATURE PHASE GATE: Survey evidence has been started but retrieval "
                    f"depth is insufficient (observed: {self._survey_retrieval_count}, "
                    f"required: {self._survey_min_retrieval_calls}). Complete more "
                    "mat_sn_* retrieval calls and run collect_evidence (and assign_facet if needed) "
                    "before structure retrieval or calculation submit."
                ),
                info={
                    "reason": "literature_phase_incomplete",
                    "survey_retrieval_count": self._survey_retrieval_count,
                    "survey_min_retrieval_calls": self._survey_min_retrieval_calls,
                },
            )

        # prepare_* gate: block hand-written input files
        if re.match(r"^mat_binary_calc_prepare_", tool_name):
            args = self._parse_tool_args(tool_call)
            inp = self._normalize_input_path(args.get("input_file"))
            if inp and inp in self._str_replace_created_paths:
                sw = self._infer_prepare_software(tool_call.function.name)
                return GuardDecision(
                    blocked=True,
                    message=(
                        f"⚠️ PREPARE GATE: `input_file` was created by `str_replace_editor` "
                        f"and cannot be passed to `{tool_call.function.name}` directly.\n\n"
                        f"Use `use_skill input-manual-helper get_reference` to obtain a "
                        f"validated template, then call the prepare tool with that reference path."
                    ),
                    info={"reason": "prepare_hand_written_input", "software": sw, "path": inp},
                )

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

        # ── prepare_* gate: track str_replace_editor-created paths ──
        if tool_name == "str_replace_editor":
            cmd = str(args.get("command", "")).strip().lower()
            if cmd in {"create", "write"}:
                p = self._normalize_input_path(args.get("path"))
                if p:
                    self._str_replace_created_paths.add(p)

        _SURVEY_RETRIEVAL_TOOLS = {
            "mat_sn_search-papers-enhanced",
            "mat_sn_web-search",
            "extract_info_from_webpage",
            "mat_struct_db_fetch_structures_from_db",
        }
        if tool_name in _SURVEY_RETRIEVAL_TOOLS:
            if self._observation_has_content(observation):
                self._survey_retrieval_count += 1

        # ── structure-retrieval gate tracking ─────────────────
        if tool_name.startswith("mat_struct_db_"):
            self.update_structure_retrieval(observation, info or {})

        # Track structure-file delivery from structure-manager scripts
        if tool_name == "use_skill":
            skill = str(args.get("skill_name", "")).strip().lower()
            script = str(args.get("script_name", "")).strip().lower()
            if skill == "structure-manager" and script in {
                "fetch_web_structure.py", "assess_structure.py",
            }:
                text_lower = (observation or "").lower()
                if any(ext in text_lower for ext in (".cif", ".vasp", "poscar", ".xyz", ".res")):
                    self._struct_retrieval.structure_file_delivered = True

        # ── Auth-failure stop gate tracking ─────────────────────
        # Only track MCP tool failures (mat_* tools), not internal tools.
        # Only STRONG markers count; ignore observations from third-party domains (e.g. 403 from publisher sites).
        if tool_name.startswith("mat_") and not self._auth_failure_locked:
            obs_lower = (observation or "").lower()
            from_third_party = any(
                domain in obs_lower for domain in AUTH_FAILURE_THIRD_PARTY_DOMAINS
            )
            if not from_third_party and any(
                marker in obs_lower for marker in AUTH_FAILURE_MARKERS_STRONG
            ):
                self._auth_failure_count += 1
                self.logger.warning(
                    "[auth-failure-gate] Auth failure #%d detected in %s output.",
                    self._auth_failure_count, tool_name,
                )
                if self._auth_failure_count >= AUTH_FAILURE_THRESHOLD:
                    self._auth_failure_locked = True
                    self.logger.warning(
                        "[auth-failure-gate] LOCKED after %d auth failures.",
                        self._auth_failure_count,
                    )

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
        # Do not count as success when observation is not parseable JSON (avoids
        # inflating survey_retrieval_count from non-JSON or malformed responses).
        return False

    # ── private: decision builders ─────────────────────────────

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
