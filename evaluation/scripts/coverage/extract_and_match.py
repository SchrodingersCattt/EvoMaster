#!/usr/bin/env python3
"""Extract rules from Skills/Tools/System-Prompt, cross-reference with question bank.

Usage:
    python evaluation/scripts/coverage/extract_and_match.py
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required: pip install pyyaml")

# ── Paths ────────────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parents[3]  # matmaster-evo
SKILLS_DIR = ROOT / "matmaster" / "skills"
TOOLS_DIR = ROOT / "matmaster" / "tools" / "builtin"
BASE_TOML = ROOT / "matmaster" / "exps" / "_base.toml"
QUESTION_BANK = ROOT / "evaluation" / "question_bank"
OUTPUT_FILE = ROOT / "evaluation" / "coverage_report.json"

# ── Skill-to-tag mapping ─────────────────────────────────────────────────────

SKILL_TAG_MAP: dict[str, list[str]] = {
    "vasp": ["eng_vasp"],
    "abacus": ["eng_abacus"],
    "gpumd": ["eng_gpumd"],
    "lammps": ["eng_lammps"],
    "cp2k": ["eng_cp2k"],
    "gromacs": ["eng_gromacs"],
    "mlips": ["code_mlip"],
    "tasker-polar-surface": ["struct_surface"],
    "build-atomic-structure": ["struct_build"],
    "transform-atomic-structure": ["struct_transform"],
    "operate-molecular-crystal": ["struct_molcrys"],
    "mcp-mat-struct-db": ["meta_database"],
    "structure-manager": ["meta_database"],
    "xrd-analysis": ["char_diffraction"],
    "checkcif-validator": ["char_diffraction"],
    "mcp-mat-xrd": ["char_diffraction"],
    "pxrd-refinement": ["char_diffraction"],
}

# Section-to-rule_type classification
SECTION_TYPE_MAP: dict[str, str] = {
    "hard guards": "hard_guard",
    "common pitfalls": "pitfall",
    "physical checks": "physical_check",
    "submission workflow": "workflow_step",
    "workflow": "workflow_step",
    "bohrium submission config": "config_default",
    "decision tree": "decision_tree",
    "when to use": "decision_tree",
    "local api": "api_recipe",
    "task scripts": "api_recipe",
    "acceptance checklist": "acceptance",
}

STOP_WORDS = {
    "this",
    "that",
    "with",
    "from",
    "will",
    "have",
    "been",
    "when",
    "which",
    "their",
    "each",
    "only",
    "should",
    "would",
    "could",
    "about",
    "into",
    "than",
    "them",
    "then",
    "what",
    "your",
    "does",
    "also",
    "must",
    "before",
    "after",
    "under",
    "above",
    "below",
    "other",
    "these",
    "those",
    "being",
    "where",
    "there",
    "here",
    "more",
    "some",
    "same",
    "very",
    "just",
    "both",
    "such",
    "like",
    "over",
    "most",
    "through",
    "between",
    "value",
    "default",
    "file",
    "input",
    "output",
    "using",
    "used",
}


# ── Utility ──────────────────────────────────────────────────────────────────


def classify_section(heading: str) -> str:
    """Map a markdown heading to a rule_type."""
    key = heading.strip().lower()
    for pattern, rtype in SECTION_TYPE_MAP.items():
        if pattern in key:
            return rtype
    return "general"


def extract_keywords(text: str) -> set[str]:
    """Extract significant keywords from rule text."""
    words = re.findall(r"[A-Z_]{2,}|[a-zA-Z][a-zA-Z0-9_]{3,}", text)
    return {w.lower() for w in words if w.lower() not in STOP_WORDS and len(w) > 4}


def keyword_overlap(rule_kw: set[str], question_kw: set[str]) -> float:
    """Jaccard-like overlap: intersection / min(len_rule, len_question)."""
    if not rule_kw:
        return 0.0
    inter = rule_kw & question_kw
    denom = min(len(rule_kw), max(len(question_kw), 1))
    return len(inter) / denom if denom > 0 else 0.0


# ── Phase 1: Extract rules from SKILL.md files ──────────────────────────────


def parse_skill_md(filepath: Path, skill_name: str) -> list[dict]:
    """Parse a SKILL.md into discrete rules."""
    text = filepath.read_text(encoding="utf-8")
    # Strip YAML frontmatter
    if text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            text = text[end + 3 :]

    rules: list[dict] = []
    current_section = "General"
    lines = text.split("\n")
    i = 0
    in_code_block = False
    code_block_lines: list[str] = []

    while i < len(lines):
        line = lines[i]

        # Code block detection
        if line.strip().startswith("```"):
            if in_code_block:
                # End code block => emit rule
                code_text = "\n".join(code_block_lines).strip()
                if code_text:
                    rules.append(
                        {
                            "source_name": skill_name,
                            "section": current_section,
                            "rule_type": classify_section(current_section),
                            "text": code_text,
                        }
                    )
                code_block_lines = []
                in_code_block = False
            else:
                in_code_block = True
                code_block_lines = []
            i += 1
            continue

        if in_code_block:
            code_block_lines.append(line)
            i += 1
            continue

        # Heading detection
        heading_match = re.match(r"^(#{1,4})\s+(.+)$", line)
        if heading_match:
            current_section = heading_match.group(2).strip()
            i += 1
            continue

        # Table row (skip header separator)
        if "|" in line and not re.match(r"^\s*\|[-:\s|]+\|\s*$", line):
            cells = [c.strip() for c in line.split("|")[1:-1]]
            # Skip if it looks like a header row (next line is separator)
            if i + 1 < len(lines) and re.match(r"^\s*\|[-:\s|]+\|\s*$", lines[i + 1]):
                i += 1  # skip this header row
                i += 1  # skip separator
                continue
            if cells and any(c for c in cells):
                rules.append(
                    {
                        "source_name": skill_name,
                        "section": current_section,
                        "rule_type": classify_section(current_section),
                        "text": " | ".join(cells),
                    }
                )
            i += 1
            continue

        # Bullet point
        bullet_match = re.match(r"^\s*[-*]\s+(.+)$", line)
        if bullet_match:
            rule_text = bullet_match.group(1).strip()
            # Accumulate continuation lines
            while (
                i + 1 < len(lines)
                and lines[i + 1].startswith("  ")
                and not re.match(r"^\s*[-*]\s", lines[i + 1])
            ):
                i += 1
                rule_text += " " + lines[i].strip()
            rules.append(
                {
                    "source_name": skill_name,
                    "section": current_section,
                    "rule_type": classify_section(current_section),
                    "text": rule_text,
                }
            )
            i += 1
            continue

        # Numbered list
        num_match = re.match(r"^\s*\d+\.\s+(.+)$", line)
        if num_match:
            rule_text = num_match.group(1).strip()
            rules.append(
                {
                    "source_name": skill_name,
                    "section": current_section,
                    "rule_type": classify_section(current_section),
                    "text": rule_text,
                }
            )
            i += 1
            continue

        i += 1

    return rules


def extract_skill_rules() -> list[dict]:
    """Extract rules from all SKILL.md files (skipping planner)."""
    all_rules: list[dict] = []
    for skill_path in sorted(SKILLS_DIR.rglob("SKILL.md")):
        # Skip planner skills
        rel = skill_path.relative_to(SKILLS_DIR)
        parts = rel.parts
        if "planner" in parts:
            continue
        # Derive skill name from parent directory
        skill_name = skill_path.parent.name
        rules = parse_skill_md(skill_path, skill_name)
        for r in rules:
            r["source_type"] = "skill"
        all_rules.extend(rules)
    return all_rules


# ── Phase 2: Extract rules from builtin tools ────────────────────────────────


def extract_tool_rules() -> list[dict]:
    """Extract rules from builtin tool files."""
    all_rules: list[dict] = []

    for entry in sorted(TOOLS_DIR.iterdir()):
        # Check for tool.py or *_tool.py
        tool_file = None
        if entry.is_dir():
            candidate = entry / "tool.py"
            if candidate.exists():
                tool_file = candidate
        elif entry.is_file() and entry.name.endswith("_tool.py"):
            tool_file = entry

        if not tool_file:
            continue

        tool_name = entry.stem if entry.is_file() else entry.name
        try:
            source = tool_file.read_text(encoding="utf-8")
        except Exception:
            continue

        # Extract json_schema parameters
        schema_match = re.search(
            r"json_schema\s*[:=].*?(\{.*?\n\s*\})", source, re.DOTALL
        )
        if schema_match:
            # Try to find properties in the schema text
            props = re.findall(r'"(\w+)":\s*\{[^}]*"description":\s*"([^"]*)"', source)
            for param_name, desc in props:
                if param_name in (
                    "type",
                    "properties",
                    "required",
                    "additionalProperties",
                ):
                    continue
                all_rules.append(
                    {
                        "source_type": "tool",
                        "source_name": tool_name,
                        "section": "json_schema",
                        "rule_type": "general",
                        "text": f"Parameter '{param_name}': {desc}",
                    }
                )

        # Extract bullet points from prompt() method body
        prompt_match = re.search(
            r"def prompt\(.*?\).*?:\s*\n(.*?)(?=\n    def |\nclass |\Z)",
            source,
            re.DOTALL,
        )
        if prompt_match:
            body = prompt_match.group(1)
            # Find string literals with bullet points
            bullets = re.findall(r"[-*]\s+(.+)", body)
            for b in bullets:
                clean = b.strip().strip("'\"").strip()
                if len(clean) > 10:
                    all_rules.append(
                        {
                            "source_type": "tool",
                            "source_name": tool_name,
                            "section": "prompt",
                            "rule_type": "general",
                            "text": clean,
                        }
                    )

    return all_rules


# ── Phase 3: Extract rules from system prompt ────────────────────────────────


def extract_system_prompt_rules() -> list[dict]:
    """Parse system_prompt from _base.toml and extract bullet rules."""
    if not BASE_TOML.exists():
        return []

    content = BASE_TOML.read_text(encoding="utf-8")

    # Extract the multi-line string between triple quotes
    match = re.search(r"system_prompt\s*=\s*'''(.*?)'''", content, re.DOTALL)
    if not match:
        match = re.search(r'system_prompt\s*=\s*"""(.*?)"""', content, re.DOTALL)
    if not match:
        return []

    prompt_text = match.group(1)
    rules: list[dict] = []
    current_section = "General"

    for line in prompt_text.split("\n"):
        heading_match = re.match(r"^(#{1,4})\s+(.+)$", line)
        if heading_match:
            current_section = heading_match.group(2).strip()
            continue

        bullet_match = re.match(r"^\s*[-*]\s+(.+)$", line)
        if bullet_match:
            rules.append(
                {
                    "source_type": "system_prompt",
                    "source_name": "system_prompt",
                    "section": current_section,
                    "rule_type": "general",
                    "text": bullet_match.group(1).strip(),
                }
            )

    return rules


# ── Phase 3b: Classify rule scope ─────────────────────────────────────────────

_PROCESS_PATTERNS = re.compile(
    r"use\s+(the\s+)?(dedicated\s+)?(Read|Write|Edit|Glob|Grep|Bash|Skill|Agent)\s+tool"
    r"|route\s+(to|through)\s+`"
    r"|call\s+(the\s+)?`?(submit_|query_|terminate_)"
    r"|must\s+use\s+`"
    r"|prefer\s+(the\s+)?dedicated"
    r"|do\s+not\s+use\s+(this\s+)?skill"
    r"|use\s+MCP"
    r"|use\s+AskQuestion"
    r"|tool_include_only"
    r"|avoid.*bash",
    re.IGNORECASE,
)


def classify_scope(rules: list[dict]) -> list[dict]:
    """Tag each rule as 'universal' or 'matmaster_specific'."""
    for r in rules:
        if _PROCESS_PATTERNS.search(r.get("text", "")):
            r["scope"] = "matmaster_specific"
        else:
            r["scope"] = "universal"
    return rules


# ── Phase 4: Load questions and match ────────────────────────────────────────


def load_questions() -> list[dict]:
    """Load all questions from the question bank YAML files."""
    questions: list[dict] = []
    for yaml_file in sorted(QUESTION_BANK.rglob("*.yaml")):
        if yaml_file.name in ("manifest.yaml", "eval_slices.yaml"):
            continue
        # Skip data/ subdirectories
        if "/data/" in str(yaml_file):
            continue
        try:
            with open(yaml_file, encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except Exception:
            continue
        if not isinstance(data, dict) or "questions" not in data:
            continue
        for q in data["questions"]:
            tags = q.get("tags") or []
            if isinstance(tags, str):
                tags = [tags]
            # Build question keywords from checklist + prompt + intent
            text_parts: list[str] = []
            text_parts.append(q.get("intent", ""))
            text_parts.append(q.get("human_prompt_seed", ""))
            for item in q.get("scoring_checklist", []):
                text_parts.append(item.get("criterion", ""))
            full_text = " ".join(text_parts)
            questions.append(
                {
                    "id": q.get("id", "unknown"),
                    "tags": tags,
                    "keywords": extract_keywords(full_text),
                    "text": full_text,
                }
            )
    return questions


def match_rules_to_questions(rules: list[dict], questions: list[dict]) -> list[dict]:
    """For each rule, determine coverage by questions."""
    for idx, rule in enumerate(rules):
        rule["id"] = (
            f"{rule['source_name']}/{rule['section'].lower().replace(' ', '-')}/{idx}"
        )
        rule["is_covered"] = False
        rule["covered_by"] = []
        rule["match_method"] = None

        rule_kw = extract_keywords(rule["text"])
        skill_tags = SKILL_TAG_MAP.get(rule["source_name"], [])

        for q in questions:
            tag_match = bool(skill_tags and any(t in q["tags"] for t in skill_tags))
            kw_score = keyword_overlap(rule_kw, q["keywords"])

            if tag_match and kw_score >= 0.3:
                rule["is_covered"] = True
                rule["covered_by"].append(q["id"])
                rule["match_method"] = "tag+keyword"
            elif tag_match and kw_score >= 0.15:
                # Weaker match - tag present with some keyword overlap
                if not rule["is_covered"]:
                    rule["is_covered"] = True
                    rule["match_method"] = "tag+weak_keyword"
                rule["covered_by"].append(q["id"])
            elif not tag_match and kw_score >= 0.5:
                # Strong keyword match without tag
                if not rule["is_covered"]:
                    rule["is_covered"] = True
                    rule["match_method"] = "keyword_only"
                rule["covered_by"].append(q["id"])

        # Deduplicate covered_by
        rule["covered_by"] = list(dict.fromkeys(rule["covered_by"]))[:10]

    return rules


# ── Phase 5: Generate output ─────────────────────────────────────────────────


def build_report(rules: list[dict]) -> dict:
    """Build the coverage report JSON."""
    total = len(rules)
    covered = sum(1 for r in rules if r["is_covered"])
    uncovered = total - covered

    # By source_type
    by_source: dict[str, dict] = {}
    for r in rules:
        st = r["source_type"]
        if st not in by_source:
            by_source[st] = {"total": 0, "covered": 0}
        by_source[st]["total"] += 1
        if r["is_covered"]:
            by_source[st]["covered"] += 1
    for v in by_source.values():
        v["pct"] = round(100.0 * v["covered"] / v["total"], 1) if v["total"] else 0.0

    # By rule_type
    by_type: dict[str, dict] = {}
    for r in rules:
        rt = r["rule_type"]
        if rt not in by_type:
            by_type[rt] = {"total": 0, "covered": 0}
        by_type[rt]["total"] += 1
        if r["is_covered"]:
            by_type[rt]["covered"] += 1
    for v in by_type.values():
        v["pct"] = round(100.0 * v["covered"] / v["total"], 1) if v["total"] else 0.0

    # By skill (source_name for skills only)
    by_skill: dict[str, dict] = {}
    for r in rules:
        if r["source_type"] != "skill":
            continue
        sn = r["source_name"]
        if sn not in by_skill:
            by_skill[sn] = {"total": 0, "covered": 0}
        by_skill[sn]["total"] += 1
        if r["is_covered"]:
            by_skill[sn]["covered"] += 1
    for v in by_skill.values():
        v["pct"] = round(100.0 * v["covered"] / v["total"], 1) if v["total"] else 0.0

    # By scope
    by_scope: dict[str, dict] = {}
    for r in rules:
        scope = r.get("scope", "universal")
        if scope not in by_scope:
            by_scope[scope] = {"total": 0, "covered": 0}
        by_scope[scope]["total"] += 1
        if r["is_covered"]:
            by_scope[scope]["covered"] += 1
    for v in by_scope.values():
        v["pct"] = round(100.0 * v["covered"] / v["total"], 1) if v["total"] else 0.0

    # Critical uncovered rules
    critical_types = {"hard_guard", "pitfall", "physical_check"}
    uncovered_critical = [
        {
            "id": r["id"],
            "source_name": r["source_name"],
            "rule_type": r["rule_type"],
            "text": r["text"],
        }
        for r in rules
        if not r["is_covered"] and r["rule_type"] in critical_types
    ]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total_rules": total,
            "covered_rules": covered,
            "uncovered_rules": uncovered,
            "coverage_pct": round(100.0 * covered / total, 1) if total else 0.0,
            "by_source_type": by_source,
            "by_rule_type": by_type,
            "by_skill": by_skill,
            "by_scope": by_scope,
        },
        "rules": rules,
        "uncovered_critical": uncovered_critical,
    }


def print_summary(report: dict) -> None:
    """Print a human-readable summary table."""
    s = report["summary"]
    print(f"\n{'='*60}")
    print(f"  Coverage Report — {report['generated_at'][:10]}")
    print(f"{'='*60}")
    print(f"  Total rules:     {s['total_rules']}")
    print(f"  Covered:         {s['covered_rules']}")
    print(f"  Uncovered:       {s['uncovered_rules']}")
    print(f"  Coverage:        {s['coverage_pct']}%")
    print(f"\n{'─'*60}")
    print(f"  {'Source Type':<20} {'Total':>6} {'Covered':>8} {'Pct':>6}")
    print(f"  {'─'*20} {'─'*6} {'─'*8} {'─'*6}")
    for src, v in sorted(s["by_source_type"].items()):
        print(f"  {src:<20} {v['total']:>6} {v['covered']:>8} {v['pct']:>5.1f}%")
    print(f"\n{'─'*60}")
    print(f"  {'Rule Type':<20} {'Total':>6} {'Covered':>8} {'Pct':>6}")
    print(f"  {'─'*20} {'─'*6} {'─'*8} {'─'*6}")
    for rt, v in sorted(s["by_rule_type"].items()):
        print(f"  {rt:<20} {v['total']:>6} {v['covered']:>8} {v['pct']:>5.1f}%")
    print(f"\n{'─'*60}")
    print(f"  {'Skill':<30} {'Total':>6} {'Covered':>8} {'Pct':>6}")
    print(f"  {'─'*30} {'─'*6} {'─'*8} {'─'*6}")
    for sk, v in sorted(s["by_skill"].items(), key=lambda x: -x[1]["total"]):
        print(f"  {sk:<30} {v['total']:>6} {v['covered']:>8} {v['pct']:>5.1f}%")

    crit = report["uncovered_critical"]
    if crit:
        print(f"\n{'─'*60}")
        print(f"  UNCOVERED CRITICAL RULES ({len(crit)}):")
        print(f"  {'─'*56}")
        for item in crit[:20]:
            text_short = item["text"][:70] + ("..." if len(item["text"]) > 70 else "")
            print(f"  [{item['rule_type']}] {item['source_name']}: {text_short}")
        if len(crit) > 20:
            print(f"  ... and {len(crit)-20} more")

    print(f"\n{'='*60}\n")


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    print("Phase 1: Extracting rules from SKILL.md files...")
    skill_rules = extract_skill_rules()
    print(f"  Found {len(skill_rules)} rules from skills")

    print("Phase 2: Extracting rules from builtin tools...")
    tool_rules = extract_tool_rules()
    print(f"  Found {len(tool_rules)} rules from tools")

    print("Phase 3: Extracting rules from system prompt...")
    sp_rules = extract_system_prompt_rules()
    print(f"  Found {len(sp_rules)} rules from system prompt")

    all_rules = skill_rules + tool_rules + sp_rules
    all_rules = classify_scope(all_rules)

    print("Phase 4: Loading questions and matching...")
    questions = load_questions()
    print(f"  Loaded {len(questions)} questions")
    all_rules = match_rules_to_questions(all_rules, questions)

    print("Phase 5: Generating report...")
    report = build_report(all_rules)

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"  Report written to: {OUTPUT_FILE}")

    print_summary(report)


if __name__ == "__main__":
    main()
