#!/usr/bin/env python3
"""
Survey contract and concept-coverage check (importable library).

Reads key_concepts from collected_*.json (schema_version 2 / source_kind survey).
Used by ToolGuard.can_finish_survey() and by check_concept_coverage.py CLI wrapper.
No subprocess: pure Python, so finish gate does not depend on script paths.
"""

from __future__ import annotations

import json
from pathlib import Path


def load_survey_contract(collected_path: Path) -> dict | None:
    """Load a collected_*.json file and return the skeleton dict, or None if invalid."""
    if not collected_path.exists():
        return None
    try:
        data = json.loads(collected_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return data


def check_concept_coverage_from_contract(data: dict, min_per_concept: int = 1) -> tuple[bool, str]:
    """
    Check that evidence_cards cover all key_concepts from the contract.

    Uses key_concepts from the JSON (no re-parsing of topic). If key_concepts
    is missing or empty, returns (True, "no key concepts").
    """
    concepts = data.get("key_concepts")
    if not concepts or not isinstance(concepts, list):
        return True, "no key concepts"
    concepts = [c for c in concepts if isinstance(c, str) and c.strip()]
    if not concepts:
        return True, "no key concepts"

    cards = data.get("evidence_cards") or []
    if not isinstance(cards, list):
        return True, "no evidence cards"
    text_pool = " ".join(
        (c.get("claim") or "") + " " + (c.get("source_title") or "")
        for c in cards if isinstance(c, dict)
    ).lower()

    missing: list[str] = []
    for concept in concepts:
        c_lower = concept.strip().lower()
        if len(c_lower) < 2:
            continue
        first_word = c_lower.split()[0] if c_lower.split() else c_lower
        count = sum(
            1
            for c in cards
            if isinstance(c, dict)
            and (
                c_lower in (c.get("claim") or "").lower()
                or c_lower in (c.get("source_title") or "").lower()
                or first_word in (c.get("claim") or "").lower()
                or first_word in (c.get("source_title") or "").lower()
            )
        )
        if count < min_per_concept:
            missing.append(f"'{concept}' (found {count}, need {min_per_concept})")
    if missing:
        return False, "Topic key concepts not covered in evidence: " + "; ".join(missing)
    return True, "All key concepts covered."


def check_concept_coverage_workspace(
    workspace: Path | str,
    min_per_concept: int = 1,
) -> tuple[bool, str]:
    """
    Scan workspace/_tmp/surveys/collected_*.json and check concept coverage.

    Uses key_concepts from each file (schema_version 2 contract). If a file
    has no key_concepts, it is skipped (pass). Returns (False, reason) if
    any file fails coverage.
    """
    workspace = Path(workspace)
    surveys_dir = workspace / "_tmp" / "surveys"
    if not surveys_dir.exists():
        return True, "no surveys dir"
    for p in sorted(surveys_dir.glob("collected_*.json")):
        data = load_survey_contract(p)
        if not data:
            continue
        passed, reason = check_concept_coverage_from_contract(data, min_per_concept)
        if not passed:
            return False, reason
    return True, "ok"
