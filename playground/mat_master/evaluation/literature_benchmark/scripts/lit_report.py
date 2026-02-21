"""Generate the literature reproduction dashboard from progress state.

Produces ``lit_dashboard.md`` with:

- Summary table (PASS / PARTIAL / FAIL counts)
- Per-category breakdown
- Detail table (paper vs computed values)
- Failures & Issues section (placeholder for manual notes)
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

_BENCHMARK_DIR = Path(__file__).resolve().parents[1]
_TASKS_PATH = _BENCHMARK_DIR / "tasks.yaml"
_PROGRESS_PATH = _BENCHMARK_DIR / "progress.yaml"
_DASHBOARD_PATH = _BENCHMARK_DIR / "lit_dashboard.md"

_CATEGORY_NAMES: dict[str, str] = {
    "A": "EOS / Lattice / B0",
    "B": "Elastic Constants",
    "C": "Surface / Adsorption",
    "D": "Defect Formation",
    "E": "Electronic / Band / DOS",
    "F": "Phonon / Thermal",
    "G": "Molecular Dynamics",
    "H": "Catalysis / Reaction",
    "I": "Battery / Ion Diffusion",
    "J": "Magnetic / Spin",
    "K": "2D Materials",
    "L": "Phase Stability / Formation",
}


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def _load_tasks() -> list[dict[str, Any]]:
    """Load task specs from tasks.yaml."""
    if not _TASKS_PATH.exists():
        return []
    raw = yaml.safe_load(_TASKS_PATH.read_text(encoding="utf-8")) or {}
    return raw.get("tasks", [])


def _load_progress() -> dict[str, Any]:
    if _PROGRESS_PATH.exists():
        return yaml.safe_load(_PROGRESS_PATH.read_text(encoding="utf-8")) or {}
    return {}


# ---------------------------------------------------------------------------
# Dashboard generation
# ---------------------------------------------------------------------------


def generate_dashboard(
    *,
    progress: dict[str, Any] | None = None,
    run_dir: Path | None = None,
) -> Path:
    """Write ``lit_dashboard.md``."""
    if progress is None:
        progress = _load_progress()
    tasks = _load_tasks()

    lines: list[str] = []
    lines.append("# Literature Reproduction Dashboard")
    lines.append("")
    lines.append(f"> Auto-generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("")

    # Summary
    n_pass = sum(1 for v in progress.values() if isinstance(v, dict) and v.get("verdict") == "PASS")
    n_partial = sum(1 for v in progress.values() if isinstance(v, dict) and v.get("verdict") == "PARTIAL")
    n_fail = sum(1 for v in progress.values() if isinstance(v, dict) and v.get("verdict") == "FAIL")
    n_pending = len(tasks) - n_pass - n_partial - n_fail

    lines.append("## Summary")
    lines.append("")
    lines.append("| Status | Count |")
    lines.append("|--------|-------|")
    lines.append(f"| PASS | {n_pass} |")
    lines.append(f"| PARTIAL | {n_partial} |")
    lines.append(f"| FAIL | {n_fail} |")
    lines.append(f"| Pending | {n_pending} |")
    lines.append(f"| **Total** | **{len(tasks)}** |")
    lines.append("")

    # Per-category breakdown
    cat_stats: dict[str, dict[str, int]] = {}
    for task in tasks:
        pid = task.get("paper_id", "")
        cat = pid[0].upper() if pid else "?"
        stats = cat_stats.setdefault(cat, {"total": 0, "pass": 0, "partial": 0, "fail": 0})
        stats["total"] += 1
        qid = f"LIT_{task.get('id', '')}"
        entry = progress.get(qid, {})
        verdict = entry.get("verdict", "") if isinstance(entry, dict) else ""
        if verdict == "PASS":
            stats["pass"] += 1
        elif verdict == "PARTIAL":
            stats["partial"] += 1
        elif verdict == "FAIL":
            stats["fail"] += 1

    lines.append("## Per-Category")
    lines.append("")
    lines.append("| Cat | Name | Total | PASS | PARTIAL | FAIL |")
    lines.append("|-----|------|-------|------|---------|------|")
    for cat in sorted(cat_stats):
        s = cat_stats[cat]
        name = _CATEGORY_NAMES.get(cat, "Other")
        lines.append(f"| {cat} | {name} | {s['total']} | {s['pass']} | {s['partial']} | {s['fail']} |")
    lines.append("")

    # Detailed results
    lines.append("## Detailed Results")
    lines.append("")
    lines.append("| Task ID | Formula | Type | Verdict | Score | Walltime |")
    lines.append("|---------|---------|------|---------|-------|----------|")
    for task in tasks:
        tid = task.get("id", "?")
        formula = task.get("formula", "?")
        ctype = task.get("calc_type", "?")
        qid = f"LIT_{tid}"
        entry = progress.get(qid, {})
        if isinstance(entry, dict):
            verdict = entry.get("verdict", "-")
            score = entry.get("score", "-")
            walltime = entry.get("walltime", "-")
        else:
            verdict = score = walltime = "-"
        lines.append(f"| {tid} | {formula} | {ctype} | {verdict} | {score} | {walltime} |")
    lines.append("")

    # Placeholder sections
    lines.append("## Failures & Issues")
    lines.append("")
    lines.append("*(Add notes here manually)*")
    lines.append("")
    lines.append("## Deferred")
    lines.append("")
    lines.append("*(Add notes here manually)*")
    lines.append("")

    content = "\n".join(lines)
    _DASHBOARD_PATH.write_text(content, encoding="utf-8")
    return _DASHBOARD_PATH
