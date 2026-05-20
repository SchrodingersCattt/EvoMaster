#!/usr/bin/env python3
"""Semantic coverage analysis using LLM to judge rule-question coverage.

For each rule extracted by extract_and_match.py, asks an LLM:
"If an agent violated this rule, would any existing question's scoring
checklist catch it?"

Usage:
    python evaluation/scripts/coverage/semantic_coverage.py [--max-rules N] [--model MODEL]

Reads evaluation/coverage_report.json (from extract_and_match.py).
Outputs evaluation/semantic_coverage_report.json.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

try:
    import openai
    import yaml
except ImportError:
    sys.exit("Requires: pip install openai pyyaml")

ROOT = Path(__file__).resolve().parents[3]
COVERAGE_INPUT = ROOT / "evaluation" / "coverage_report.json"
OUTPUT_FILE = ROOT / "evaluation" / "semantic_coverage_report.json"
QUESTION_BANK = ROOT / "evaluation" / "question_bank"
CACHE_FILE = ROOT / "evaluation" / "scripts" / "coverage" / ".semantic_cache.json"

# ── LLM Config ───────────────────────────────────────────────────────────────

DEFAULT_MODEL = "deepseek-v3.2"
API_KEY = os.getenv("LITELLM_PROXY_API_KEY", "")
API_BASE = os.getenv("LITELLM_PROXY_API_BASE", "")

# ── Load questions ────────────────────────────────────────────────────────────


def load_questions() -> dict[str, dict]:
    """Load all questions keyed by id."""
    questions = {}
    for yf in QUESTION_BANK.rglob("*.yaml"):
        if yf.name in ("manifest.yaml", "eval_slices.yaml"):
            continue
        try:
            data = yaml.safe_load(yf.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        for q in data.get("questions", []):
            qid = q.get("id", "")
            if qid:
                questions[qid] = q
    return questions


def format_question_for_judge(q: dict) -> str:
    """Format question info for the LLM judge."""
    parts = [
        f"ID: {q.get('id', '')}",
        f"Intent: {q.get('intent', '')}",
        f"Prompt: {q.get('human_prompt_seed', '')[:300]}",
        f"Tags: {q.get('tags', [])}",
        "Scoring checklist:",
    ]
    for item in q.get("scoring_checklist", []):
        parts.append(f"  - [{item.get('verify', '')}] {item.get('criterion', '')}")
    return "\n".join(parts)


# ── LLM Judge ─────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are an evaluation coverage analyst for a computational chemistry AI agent.

Your task: determine whether a specific RULE from a skill/tool document is TESTED by any of the given evaluation questions.

A rule is COVERED if violating it would cause at least one question's scoring_checklist to FAIL.
A rule is NOT COVERED if an agent could violate the rule and still pass all questions.

Respond with a JSON object:
{"covered": true/false, "confidence": "high"/"medium"/"low", "covered_by": ["question_id1", ...], "reason": "one sentence explanation"}

Be strict: a rule is only covered if the scoring checklist has a DETERMINISTIC or LLM-judge criterion that would specifically catch the violation. Mere topic overlap is not coverage."""


def build_user_prompt(rule: dict, relevant_questions: list[dict]) -> str:
    parts = [
        f"RULE (from {rule['source_type']}:{rule['source_name']}, section: {rule['section']}):",
        f"  \"{rule['text']}\"",
        "",
        f"POTENTIALLY RELEVANT QUESTIONS ({len(relevant_questions)}):",
    ]
    for q in relevant_questions:
        parts.append("")
        parts.append(format_question_for_judge(q))
    if not relevant_questions:
        parts.append("  (no questions with matching tags found)")
    return "\n".join(parts)


def judge_rule(
    client: openai.OpenAI, model: str, rule: dict, relevant_questions: list[dict]
) -> dict:
    """Ask LLM whether this rule is covered."""
    user = build_user_prompt(rule, relevant_questions)
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ],
            temperature=0.0,
            max_tokens=200,
        )
        text = response.choices[0].message.content or ""
        # Parse JSON from response
        # Find JSON in the response
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            result = json.loads(text[start:end])
            return result
        return {
            "covered": False,
            "confidence": "low",
            "covered_by": [],
            "reason": "parse error",
        }
    except Exception as e:
        return {
            "covered": False,
            "confidence": "low",
            "covered_by": [],
            "reason": f"error: {e}",
        }


# ── Skill-tag mapping for filtering relevant questions ────────────────────────

SKILL_TAG_MAP: dict[str, list[str]] = {
    "vasp": ["eng_vasp"],
    "abacus": ["eng_abacus"],
    "gpumd": ["eng_gpumd"],
    "lammps": ["eng_lammps"],
    "cp2k": ["eng_cp2k"],
    "gromacs": ["eng_gromacs"],
    "mlips": ["code_mlip"],
    "atomic-structure": [
        "struct_build",
        "struct_transform",
        "struct_surface",
        "struct_molcrys",
        "meta_database",
    ],
    "inspect-atomic-structure": [
        "struct_build",
        "struct_transform",
        "struct_surface",
        "struct_molcrys",
    ],
    "build-atomic-structure": ["struct_build"],
    "transform-atomic-structure": ["struct_transform"],
    "assemble-atomic-structure": ["struct_surface"],
    "operate-molecular-crystal": ["struct_molcrys"],
    "sample-atomic-structures": ["struct_build"],
    "mcp-mat-struct-db": ["meta_database"],
    "xrd-analysis": ["char_diffraction"],
    "checkcif-validator": ["char_diffraction"],
    "mcp-mat-xrd": ["char_diffraction"],
    "pxrd-refinement": ["char_diffraction"],
}

# Also match by capability for workflow/general questions
CAPABILITY_SKILLS: dict[str, list[str]] = {
    "workflow_orchestration": [
        "cp2k",
        "mlips",
        "atomic-structure",
        "inspect-atomic-structure",
    ],
    "execution_contract": [
        "vasp",
        "abacus",
        "cp2k",
        "gpumd",
        "lammps",
        "gromacs",
    ],
    "input_generation": [
        "vasp",
        "abacus",
        "cp2k",
        "quantum_espresso",
        "abinit",
        "orca",
        "lammps",
        "gromacs",
    ],
}


def get_relevant_questions(rule: dict, all_questions: dict[str, dict]) -> list[dict]:
    """Get questions relevant to this rule based on tags and capability."""
    skill = rule["source_name"]
    tags = SKILL_TAG_MAP.get(skill, [])

    tag_matched = []
    cap_matched = []

    for q in all_questions.values():
        q_tags = q.get("tags") or []
        q_cap = q.get("capability") or ""

        # Tag match (highest priority)
        if tags and any(t in q_tags for t in tags):
            tag_matched.append(q)
            continue

        # Capability match (fallback)
        cap_skills = CAPABILITY_SKILLS.get(q_cap, [])
        if skill in cap_skills:
            cap_matched.append(q)
            continue

        # For system_prompt / tool rules, include workflow_orchestration questions
        if rule["source_type"] in ("tool", "system_prompt"):
            if q_cap in ("workflow_orchestration", "execution_contract"):
                cap_matched.append(q)

    # Prefer tag-matched; only use capability fallback if no tag matches
    if tag_matched:
        return tag_matched
    return cap_matched


# ── Main ──────────────────────────────────────────────────────────────────────


def _compute_cache_key(rule: dict, relevant_questions: list[dict]) -> str:
    """Compute a cache key from rule text + relevant questions' checklists."""
    import hashlib

    parts = [rule.get("text", "")]
    for q in sorted(relevant_questions, key=lambda x: x.get("id", "")):
        parts.append(q.get("id", ""))
        for item in q.get("scoring_checklist", []):
            parts.append(
                f"{item.get('id', '')}:{item.get('verify', '')}:{item.get('criterion', '')}"
            )
    return hashlib.md5("|".join(parts).encode()).hexdigest()


def load_cache() -> dict[str, dict]:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text())
        except Exception:
            pass
    return {}


def save_cache(cache: dict[str, dict]) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--max-rules", type=int, default=0, help="Limit rules to process (0=all)"
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument(
        "--skip-covered",
        action="store_true",
        help="Only judge rules marked uncovered by keyword matching",
    )
    args = parser.parse_args()

    if not COVERAGE_INPUT.exists():
        sys.exit(f"Run extract_and_match.py first: {COVERAGE_INPUT} not found")

    report = json.loads(COVERAGE_INPUT.read_text())
    rules = report["rules"]
    print(f"Loaded {len(rules)} rules from coverage_report.json")

    all_questions = load_questions()
    print(f"Loaded {len(all_questions)} questions")

    # Filter rules if requested
    if args.skip_covered:
        rules = [r for r in rules if not r["is_covered"]]
        print(f"Filtered to {len(rules)} uncovered rules")

    if args.max_rules > 0:
        rules = rules[: args.max_rules]
        print(f"Limited to {len(rules)} rules")

    # Load cache (keyed by content hash, not rule ID)
    cache = load_cache()
    print(f"Cache: {len(cache)} entries")

    client = openai.OpenAI(api_key=API_KEY, base_url=API_BASE, timeout=30.0)

    # For each rule, compute relevant questions and cache key
    results = []
    to_judge = []

    for rule in rules:
        relevant = get_relevant_questions(rule, all_questions)
        cache_key = _compute_cache_key(rule, relevant)
        rule["_cache_key"] = cache_key
        rule["_relevant"] = relevant
        if cache_key in cache:
            results.append({**rule, **cache[cache_key]})
        else:
            to_judge.append(rule)

    print(f"From cache: {len(results)}, to judge: {len(to_judge)}")

    if to_judge:
        judged = 0
        errors = 0

        def process_rule(rule):
            relevant = rule["_relevant"]
            return rule, judge_rule(client, args.model, rule, relevant)

        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {executor.submit(process_rule, r): r for r in to_judge}
            for future in as_completed(futures):
                try:
                    rule, judgment = future.result()
                    cache_key = rule["_cache_key"]
                    cache[cache_key] = judgment
                    results.append({**rule, **judgment})
                    judged += 1
                    if judged % 50 == 0:
                        print(f"  Progress: {judged}/{len(to_judge)}")
                        save_cache(cache)
                except Exception as e:
                    errors += 1
                    rule = futures[future]
                    results.append(
                        {
                            **rule,
                            "covered": False,
                            "confidence": "low",
                            "covered_by": [],
                            "reason": f"error: {e}",
                        }
                    )

        save_cache(cache)
        print(f"Judged {judged} rules ({errors} errors)")

    # Clean internal fields before output
    for r in results:
        r.pop("_cache_key", None)
        r.pop("_relevant", None)

    # Compute summary
    total = len(results)
    covered = sum(1 for r in results if r.get("covered"))
    uncovered = total - covered
    actionable_results = [r for r in results if r.get("is_actionable", True)]
    actionable_total = len(actionable_results)
    actionable_covered = sum(1 for r in actionable_results if r.get("covered"))
    actionable_uncovered = actionable_total - actionable_covered

    by_type = {}
    for r in results:
        rt = r.get("rule_type", "general")
        entry = by_type.setdefault(rt, {"total": 0, "covered": 0})
        entry["total"] += 1
        if r.get("covered"):
            entry["covered"] += 1

    by_skill = {}
    for r in results:
        sn = r.get("source_name", "unknown")
        entry = by_skill.setdefault(sn, {"total": 0, "covered": 0})
        entry["total"] += 1
        if r.get("covered"):
            entry["covered"] += 1

    by_actionability = {}
    for r in results:
        actionability = r.get("actionability", "testable")
        entry = by_actionability.setdefault(
            actionability,
            {
                "total": 0,
                "covered": 0,
                "actionable": bool(r.get("is_actionable", True)),
            },
        )
        entry["total"] += 1
        if r.get("covered"):
            entry["covered"] += 1

    # Sort uncovered by severity
    severity_order = {
        "hard_guard": 0,
        "pitfall": 1,
        "physical_check": 2,
        "decision_tree": 3,
    }
    uncovered_rules = [r for r in results if not r.get("covered")]
    uncovered_rules.sort(key=lambda r: severity_order.get(r.get("rule_type", ""), 99))

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": args.model,
        "summary": {
            "total_rules": total,
            "covered_rules": covered,
            "uncovered_rules": uncovered,
            "coverage_pct": round(100 * covered / total, 1) if total else 0,
            "actionable_total": actionable_total,
            "actionable_covered": actionable_covered,
            "actionable_uncovered": actionable_uncovered,
            "actionable_pct": (
                round(100 * actionable_covered / actionable_total, 1)
                if actionable_total
                else 0
            ),
            "by_rule_type": {
                k: {
                    **v,
                    "pct": (
                        round(100 * v["covered"] / v["total"], 1) if v["total"] else 0
                    ),
                }
                for k, v in sorted(by_type.items())
            },
            "by_skill": {
                k: {
                    **v,
                    "pct": (
                        round(100 * v["covered"] / v["total"], 1) if v["total"] else 0
                    ),
                }
                for k, v in sorted(by_skill.items(), key=lambda x: -x[1]["total"])
            },
            "by_actionability": {
                k: {
                    **v,
                    "pct": (
                        round(100 * v["covered"] / v["total"], 1) if v["total"] else 0
                    ),
                }
                for k, v in sorted(by_actionability.items())
            },
        },
        "uncovered_critical": [
            {
                "id": r["id"],
                "source_name": r.get("source_name"),
                "rule_type": r.get("rule_type"),
                "actionability": r.get("actionability", "testable"),
                "actionability_reason": r.get("actionability_reason", ""),
                "text": r.get("text", "")[:200],
                "reason": r.get("reason", ""),
            }
            for r in uncovered_rules
            if (
                r.get("rule_type") in ("hard_guard", "pitfall", "physical_check")
                and r.get("is_actionable", True)
            )
        ],
        "excluded_from_actionable": [
            {
                "id": r["id"],
                "source_name": r.get("source_name"),
                "source_type": r.get("source_type"),
                "rule_type": r.get("rule_type"),
                "actionability": r.get("actionability", "testable"),
                "actionability_reason": r.get("actionability_reason", ""),
                "text": r.get("text", "")[:200],
            }
            for r in results
            if not r.get("is_actionable", True)
        ],
        "rules": [
            {
                "id": r["id"],
                "source_name": r.get("source_name"),
                "section": r.get("section"),
                "rule_type": r.get("rule_type"),
                "text": r.get("text", "")[:200],
                "actionability": r.get("actionability", "testable"),
                "is_actionable": r.get("is_actionable", True),
                "actionability_reason": r.get("actionability_reason", ""),
                "covered": r.get("covered", False),
                "confidence": r.get("confidence", "low"),
                "covered_by": r.get("covered_by", []),
                "reason": r.get("reason", ""),
            }
            for r in results
        ],
    }

    OUTPUT_FILE.write_text(json.dumps(output, ensure_ascii=False, indent=2))
    print(f"\nOutput: {OUTPUT_FILE}")

    # Print summary
    print(f"\n{'='*60}")
    print(f"  SEMANTIC COVERAGE REPORT ({args.model})")
    print(f"{'='*60}")
    print(f"  Total rules:    {total}")
    print(f"  Covered:        {covered} ({output['summary']['coverage_pct']}%)")
    print(f"  Uncovered:      {uncovered}")
    print(
        f"  Actionable:     {actionable_covered}/{actionable_total} "
        f"({output['summary']['actionable_pct']}%)"
    )
    print(f"  Critical gaps:  {len(output['uncovered_critical'])}")
    print(f"{'='*60}")
    print("\n  By rule type:")
    for rt, info in output["summary"]["by_rule_type"].items():
        print(
            f"    {rt:20s}: {info['covered']:3d}/{info['total']:3d} ({info['pct']:.0f}%)"
        )
    print("\n  Top skills by gap count:")
    gaps_by_skill = {}
    for r in uncovered_rules:
        sn = r.get("source_name", "?")
        gaps_by_skill[sn] = gaps_by_skill.get(sn, 0) + 1
    for sn, count in sorted(gaps_by_skill.items(), key=lambda x: -x[1])[:15]:
        total_s = by_skill[sn]["total"]
        cov_s = by_skill[sn]["covered"]
        print(
            f"    {sn:35s}: {count:3d} uncovered / {total_s} total ({round(100*cov_s/total_s)}% covered)"
        )


if __name__ == "__main__":
    main()
