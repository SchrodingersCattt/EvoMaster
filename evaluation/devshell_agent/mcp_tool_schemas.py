"""JSON schemas for MatmasterEvalMcpToolkit (split from ``sdk_tools.py`` for line limits)."""

from __future__ import annotations

from typing import Any

from evaluation.scripts.devshell.eval_model_routes import (
    DEFAULT_DEVSHELL_FALLBACK_MODEL_ROUTE,
    DEFAULT_DEVSHELL_MODEL_ROUTE,
)

RUN_DEVSHELL_EVAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "iteration_tag": {
            "type": "string",
            "description": (
                "Directory name under session eval_runs/, e.g. iter_01. "
                "Each call should use a fresh tag."
            ),
        },
        "jobs": {
            "type": "integer",
            "description": (
                "Parallel mm-devshell eval tasks and automatic score --score-jobs; "
                "orchestrator also passes --parallel-checklist-workers = jobs×2 for "
                "per-question scoring_checklist (default: CLI)."
            ),
        },
        "limit": {
            "type": "integer",
            "description": "Max plan items (default: CLI defaults).",
        },
        "questions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Question IDs (default: CLI defaults).",
        },
        "slices": {
            "type": "string",
            "description": (
                "OR-of-slices for run_devshell_eval --slices: whitespace separates "
                'slices; no spaces inside "[...]". E.g. '
                '"workflow_orchestration[polymer] input_generation" (default: CLI).'
            ),
        },
        "model": {
            "type": "string",
            "description": (
                "LLM route for inner mm-devshell --model "
                f"(default: {DEFAULT_DEVSHELL_MODEL_ROUTE})."
            ),
        },
        "fallback_model": {
            "type": "string",
            "description": (
                "run_devshell_eval --fallback-model (default: "
                f"{DEFAULT_DEVSHELL_FALLBACK_MODEL_ROUTE}): retry a "
                "failed task once when devshell logs indicate Bedrock transport errors; "
                "omit or match model to skip fallback."
            ),
        },
        "exp": {
            "type": "string",
            "description": "Optional mm-devshell --exp (default: CLI defaults).",
        },
        "eval_ingest_pending_only": {
            "type": "boolean",
            "description": (
                "Whether to write pending_ingest without POST (default: CLI defaults)."
            ),
        },
        "no_export_review": {
            "type": "boolean",
            "description": "Skip claude_review.md export (default: CLI defaults).",
        },
        "task_timeout_sec": {
            "type": "number",
            "description": (
                "Per-task wall timeout for mm-devshell (default: CLI defaults)."
            ),
        },
        "k": {
            "type": "integer",
            "description": (
                "Repeat each question k times (repeat_idx 0..k-1); overrides eval "
                "config (default: CLI)."
            ),
        },
        "extra_args": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Extra argv tokens appended to run_devshell_eval.py.",
        },
    },
    "required": ["iteration_tag"],
}

REPORT_ITERATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "iteration_index": {
            "type": "integer",
            "description": "1-based iteration index matching the user message.",
        },
        "macro_mean_0_100": {
            "type": "integer",
            "description": (
                "Per-question 0/100: 100 only when every repeat run for that question "
                "scored 100 (all required checklist items each time; token_budget_total "
                "and turn_budget are optional for ingest). Mean over questions = "
                "(questions fully passed ÷ question count) × 100; matches target_pass_rate."
            ),
        },
        "target_met": {
            "type": "boolean",
            "description": (
                "True if macro_mean_0_100 >= configured target (same threshold as before, "
                "now applied to pass-rate scale)."
            ),
        },
        "rationale": {
            "type": "string",
            "description": (
                "Short Markdown: auto-score summary, low-score evidence paths, stop/continue."
            ),
        },
        "files_touched": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Repo-relative paths edited this iteration (if any).",
        },
    },
    "required": [
        "iteration_index",
        "macro_mean_0_100",
        "target_met",
        "rationale",
    ],
}

ESCALATE_CHECKLIST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "iteration_index": {
            "type": "integer",
            "description": "Same 1-based iteration as the current user message.",
        },
        "question_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Question id(s) whose scoring_checklist / rubric seem unfair.",
        },
        "rationale": {
            "type": "string",
            "description": (
                "Why the checklist or reference_answers need human-aligned fixes; "
                "cite evidence paths (logs, workspace, YAML)."
            ),
        },
        "evidence_paths": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Optional repo-relative or session paths supporting the case.",
        },
    },
    "required": ["iteration_index", "rationale"],
}

REPORT_CHECKLIST_REVISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "iteration_index": {
            "type": "integer",
            "description": "Must match the checklist follow-up round.",
        },
        "no_changes": {
            "type": "boolean",
            "description": (
                "True if after review no proposal was needed (no substantive checklist/core fix)."
            ),
        },
        "rationale": {
            "type": "string",
            "description": "What was reviewed and what was proposed or skipped.",
        },
        "files_touched": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Repo-relative paths: typically proposed_question_bank_changes.md under the "
                "session dir, and/or evaluation/question_bank/ or evaluation/core/ targets "
                "named inside the proposal."
            ),
        },
    },
    "required": ["iteration_index", "no_changes", "rationale"],
}

DELEGATE_OPTIMIZATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "iteration_index": {
            "type": "integer",
            "description": "Same 1-based iteration as the current user message.",
        },
        "problem_summary": {
            "type": "string",
            "description": (
                "Sanitized product-side problem summary. Prefer capability-level patterns; "
                "avoid naming specific question ids unless unavoidable."
            ),
        },
        "symptom": {
            "type": "string",
            "description": "Observed external symptom without rubric wording.",
        },
        "suggested_focus": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Product-side directories or modules to inspect.",
        },
        "candidate_layers": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": ["skill", "tool", "system_prompt", "runtime"],
            },
            "description": (
                "Optional likely ownership layers for the fix. Use one or more of "
                "skill / tool / system_prompt / runtime to make the delegation more precise."
            ),
        },
        "failure_buckets": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Optional coarse failure categories (e.g. batch_timeout, structure_relax). "
                "Prefer this over per-task paths."
            ),
        },
        "capabilities_affected": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Optional capability names from the eval run (aggregated).",
        },
        "allowed_evidence_paths": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Optional non-evaluation paths. Prefer session-level artifacts such as "
                "``eval_runs/iter_XX/raw_runs.jsonl`` or orchestrator logs; avoid listing "
                "per-task workspace paths unless strictly necessary."
            ),
        },
        "notes": {
            "type": "string",
            "description": "Sanitized notes without raw score_reason or rubric text.",
        },
    },
    "required": [
        "iteration_index",
        "problem_summary",
        "symptom",
        "suggested_focus",
        "notes",
    ],
}

REPORT_OPTIMIZATION_RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "iteration_index": {
            "type": "integer",
            "description": "Must match the optimization sub-round iteration.",
        },
        "optimization_round": {
            "type": "integer",
            "description": "1-based optimization round within the iteration.",
        },
        "summary": {
            "type": "string",
            "description": "Short Markdown summary of the product-side changes.",
        },
        "files_touched": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Repo-relative product-side files touched in this sub-round.",
        },
        "commit_shas": {
            "type": "array",
            "items": {"type": "string"},
            "description": ("Always empty in proposal-only mode. " "Leave as []."),
        },
        "needs_more_work": {
            "type": "boolean",
            "description": "Whether the main agent should consider another optimization round.",
        },
        "followup_suggestion": {
            "type": "string",
            "description": "Suggested next step for the main agent.",
        },
    },
    "required": [
        "iteration_index",
        "optimization_round",
        "summary",
        "files_touched",
        "commit_shas",
        "needs_more_work",
        "followup_suggestion",
    ],
}

READ_TEXT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "Repo-relative or absolute file path.",
        }
    },
    "required": ["path"],
}

WRITE_TEXT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "Repo-relative or absolute file path.",
        },
        "content": {
            "type": "string",
            "description": "Full file content to write.",
        },
    },
    "required": ["path", "content"],
}

REPLACE_TEXT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "Repo-relative or absolute file path.",
        },
        "old_text": {
            "type": "string",
            "description": "Exact text to replace.",
        },
        "new_text": {
            "type": "string",
            "description": "Replacement text.",
        },
        "replace_all": {
            "type": "boolean",
            "description": "Replace all occurrences instead of the first match.",
        },
    },
    "required": ["path", "old_text", "new_text"],
}

GLOB_PATHS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "base_dir": {
            "type": "string",
            "description": "Repo-relative or absolute directory to search under.",
        },
        "pattern": {
            "type": "string",
            "description": "Glob pattern such as `*.py`.",
        },
        "limit": {
            "type": "integer",
            "description": "Max paths to return (default 500, max 500). Use a specific pattern to narrow results if truncated.",
        },
    },
    "required": ["base_dir", "pattern"],
}

GREP_TEXT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "base_dir": {
            "type": "string",
            "description": "Repo-relative or absolute directory to search under.",
        },
        "pattern": {
            "type": "string",
            "description": "Plain-text substring to search for.",
        },
        "file_pattern": {
            "type": "string",
            "description": "Optional glob used to limit files, default `*`.",
        },
        "limit": {
            "type": "integer",
            "description": "Maximum number of matches to return.",
        },
    },
    "required": ["base_dir", "pattern"],
}

GIT_REVERT_COMMITS_AFTER_BASE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "base_sha": {
            "type": "string",
            "description": (
                "Full 40-char commit SHA: revert every commit **after** this ancestor up to "
                "HEAD (newest first). Must exactly match the SHA the orchestrator pinned for "
                "this P0 revert round."
            ),
        },
    },
    "required": ["base_sha"],
}
