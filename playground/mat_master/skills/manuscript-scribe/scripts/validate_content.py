"""
Validate manuscript content against a format profile: word counts, completeness,
required elements, information flow between sections, and section ordering.

Run this **before** assemble_manuscript.py to catch short or incomplete sections early,
or pass --profile to assemble_manuscript.py to run checks at assembly time.

Usage:
  python validate_content.py --draft draft.md --profile generic
  python validate_content.py --sections_dir sections/ --profile computational_report
  python validate_content.py --draft draft.md --profile generic --planner_mode --report report.json

Output: Prints validation summary; optionally writes JSON report to --report.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from format_profiles import get_profile
from section_utils import find_section


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _word_count(text: str) -> int:
    """Count words (handles mixed CJK and Latin text)."""
    # Count CJK characters individually
    cjk = len(re.findall(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]", text))
    # Count Latin/ASCII words
    latin = len(re.findall(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)*", text))
    return cjk + latin


def _strip_html_comments(text: str) -> str:
    """Remove HTML comments (<!-- ... -->) from text."""
    return re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL).strip()


def _extract_sections_from_draft(content: str) -> dict[str, str]:
    """Parse a single draft into {section_name: body_text} (body only, no header).

    HTML comments (writing hints from init_manuscript) are stripped so they do not
    inflate word counts or interfere with completeness checks.
    """
    sections: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    for line in content.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            if current is not None:
                sections[current] = _strip_html_comments("\n".join(buf).strip())
            current = m.group(1).strip()
            buf = []
        else:
            buf.append(line)
    if current is not None:
        sections[current] = _strip_html_comments("\n".join(buf).strip())
    return sections


def _load_sections(args: argparse.Namespace) -> dict[str, str]:
    """Load sections from either --draft or --sections_dir."""
    if args.draft:
        content = Path(args.draft).read_text(encoding="utf-8")
        return _extract_sections_from_draft(content)
    elif args.sections_dir:
        sdir = Path(args.sections_dir)
        sections: dict[str, str] = {}
        for f in sdir.iterdir():
            if f.suffix.lower() == ".md" and not f.name.startswith("_"):
                raw = f.read_text(encoding="utf-8").strip()
                name = f.stem
                # Strip "# Title" and "## SectionName" headers to get body only
                lines = raw.splitlines()
                body_lines = []
                for line in lines:
                    if re.match(r"^#{1,2}\s+", line):
                        continue
                    if line.strip().startswith("<!--") and line.strip().endswith("-->"):
                        continue
                    body_lines.append(line)
                sections[name] = "\n".join(body_lines).strip()
        return sections
    return {}


def _extract_key_terms(text: str) -> set[str]:
    """Extract potential key terms: multi-word capitalized phrases and technical terms."""
    terms: set[str] = set()
    # Abbreviations defined as "Full Name (ABBR)"
    for m in re.finditer(r"([A-Za-z][A-Za-z0-9\s\-]+?)\s*\(\s*([A-Z]{2,})\s*\)", text):
        terms.add(m.group(2).strip())
    # Standalone abbreviations (2+ uppercase chars)
    for m in re.finditer(r"\b([A-Z][A-Z0-9]{1,})\b", text):
        t = m.group(1)
        # Filter common words
        if t not in {"TBD", "URL", "HTTP", "HTTPS", "PDF", "JSON", "HTML", "API", "URL", "AND", "THE", "FOR", "NOT"}:
            terms.add(t)
    return terms


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def check_word_counts(
    sections: dict[str, str],
    profile: dict[str, Any],
    planner_mode: bool = False,
) -> dict[str, Any]:
    """Check per-section and overall word counts against profile minimums."""
    result: dict[str, Any] = {
        "passed": True,
        "total_words": 0,
        "overall_min": profile["overall_min_words"],
        "sections": {},
        "warnings": [],
        "errors": [],
    }
    multiplier = 1.5 if planner_mode else 1.0

    for sec_name in profile["sections"]:
        meta = profile["section_meta"].get(sec_name, {})
        min_w = int(meta.get("min_words", 0) * multiplier)
        max_w = meta.get("max_words")
        body = sections.get(sec_name, "")
        wc = _word_count(body)
        result["total_words"] += wc

        sec_info: dict[str, Any] = {"word_count": wc, "min_words": min_w}
        if max_w:
            sec_info["max_words"] = max_w

        if wc < min_w:
            sec_info["status"] = "UNDER"
            msg = f"{sec_name}: {wc} words (minimum {min_w})"
            if min_w > 0:
                result["errors"].append(msg)
                result["passed"] = False
        elif max_w and wc > max_w:
            sec_info["status"] = "OVER"
            result["warnings"].append(f"{sec_name}: {wc} words (max {max_w})")
        else:
            sec_info["status"] = "OK"

        result["sections"][sec_name] = sec_info

    overall_min = int(profile["overall_min_words"] * multiplier)
    if result["total_words"] < overall_min:
        result["errors"].append(
            f"Overall: {result['total_words']} words (minimum {overall_min})"
        )
        result["passed"] = False

    return result


def check_completeness(
    sections: dict[str, str],
    profile: dict[str, Any],
) -> dict[str, Any]:
    """Check for TBD placeholders and missing sections."""
    result: dict[str, Any] = {"passed": True, "missing": [], "tbd": [], "empty": []}

    for sec_name in profile["sections"]:
        if sec_name not in sections:
            result["missing"].append(sec_name)
            result["passed"] = False
            continue
        body = sections[sec_name].strip()
        if not body:
            result["empty"].append(sec_name)
            result["passed"] = False
        elif body == "(TBD)" or body.startswith("(TBD)"):
            result["tbd"].append(sec_name)
            result["passed"] = False

    return result


def check_required_elements(
    sections: dict[str, str],
    profile: dict[str, Any],
) -> dict[str, Any]:
    """Heuristic check for required elements in each section."""
    result: dict[str, Any] = {"passed": True, "missing_elements": {}}

    for sec_name in profile["sections"]:
        meta = profile["section_meta"].get(sec_name, {})
        required = meta.get("required_elements", [])
        if not required:
            continue
        body = sections.get(sec_name, "").lower()
        if not body or body.startswith("(tbd)"):
            continue  # Already caught by completeness check

        missing = []
        for elem in required:
            # Flexible matching: split underscore-joined element names and check
            # if any of the component words appear
            keywords = elem.replace("_", " ").split()
            if not any(kw in body for kw in keywords):
                missing.append(elem)
        if missing:
            result["missing_elements"][sec_name] = missing
            result["passed"] = False

    return result


def check_information_flow(
    sections: dict[str, str],
    profile: dict[str, Any],
) -> dict[str, Any]:
    """Check cross-section coherence: terms defined in earlier sections should
    appear in later sections where appropriate."""
    result: dict[str, Any] = {"passed": True, "warnings": []}

    sec_names = profile["sections"]
    sec_terms: dict[str, set[str]] = {}
    for name in sec_names:
        body = sections.get(name, "")
        sec_terms[name] = _extract_key_terms(body)

    # Define expected flow relationships based on common patterns
    flow_pairs = []
    lower_names = [n.lower() for n in sec_names]

    # Methods -> Results (terms in methods should appear in results)
    methods_idx = next((i for i, n in enumerate(lower_names) if "method" in n), None)
    results_idx = next((i for i, n in enumerate(lower_names) if "result" in n), None)
    if methods_idx is not None and results_idx is not None:
        flow_pairs.append((sec_names[methods_idx], sec_names[results_idx]))

    # Results -> Discussion (terms in results should appear in discussion)
    discussion_idx = next((i for i, n in enumerate(lower_names) if "discussion" in n or "analysis" in n), None)
    if results_idx is not None and discussion_idx is not None:
        flow_pairs.append((sec_names[results_idx], sec_names[discussion_idx]))

    for src, dst in flow_pairs:
        src_terms = sec_terms.get(src, set())
        dst_terms = sec_terms.get(dst, set())
        if not src_terms or not dst_terms:
            continue
        # Check how many source terms appear in destination
        overlap = src_terms & dst_terms
        if src_terms and len(overlap) < len(src_terms) * 0.3:
            missing = src_terms - dst_terms
            if missing:
                sample = sorted(missing)[:5]
                result["warnings"].append(
                    f"Low term overlap from {src} -> {dst}: "
                    f"terms {sample} from {src} not found in {dst}"
                )

    if result["warnings"]:
        result["passed"] = False

    return result


def check_section_ordering(
    sections: dict[str, str],
    profile: dict[str, Any],
) -> dict[str, Any]:
    """Verify that sections appear in the expected profile order."""
    result: dict[str, Any] = {"passed": True, "expected": profile["sections"], "actual": [], "warnings": []}

    actual_order = list(sections.keys())
    result["actual"] = actual_order

    expected = profile["sections"]
    # Filter actual to only those that match expected names
    actual_in_expected = [s for s in actual_order if s in expected]
    expected_in_actual = [s for s in expected if s in actual_order]

    if actual_in_expected != expected_in_actual:
        result["warnings"].append(
            f"Section order mismatch. Expected: {expected_in_actual}, "
            f"Actual: {actual_in_expected}"
        )
        result["passed"] = False

    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_validation(
    sections: dict[str, str],
    profile: dict[str, Any],
    planner_mode: bool = False,
) -> dict[str, Any]:
    """Run all validation checks and return a combined report."""
    report: dict[str, Any] = {}
    report["word_counts"] = check_word_counts(sections, profile, planner_mode)
    report["completeness"] = check_completeness(sections, profile)
    report["required_elements"] = check_required_elements(sections, profile)
    report["information_flow"] = check_information_flow(sections, profile)
    report["section_ordering"] = check_section_ordering(sections, profile)

    report["overall_passed"] = all(
        report[k]["passed"]
        for k in ["word_counts", "completeness", "required_elements",
                   "information_flow", "section_ordering"]
    )
    return report


def print_report(report: dict[str, Any]) -> None:
    """Print a human-readable validation summary."""
    wc = report["word_counts"]
    print(f"=== Word Counts (total: {wc['total_words']} words, min: {wc['overall_min']}) ===")
    for sec, info in wc["sections"].items():
        status = info["status"]
        marker = "OK" if status == "OK" else ("WARN" if status == "OVER" else "FAIL")
        min_str = f" (min {info['min_words']})" if info["min_words"] else ""
        print(f"  [{marker}] {sec}: {info['word_count']} words{min_str}")
    for e in wc.get("errors", []):
        print(f"  ERROR: {e}")
    for w in wc.get("warnings", []):
        print(f"  WARN:  {w}")

    comp = report["completeness"]
    if comp["missing"]:
        print(f"\n=== Missing Sections: {comp['missing']} ===")
    if comp["tbd"]:
        print(f"=== Still TBD: {comp['tbd']} ===")
    if comp["empty"]:
        print(f"=== Empty Sections: {comp['empty']} ===")

    elems = report["required_elements"]
    if elems["missing_elements"]:
        print("\n=== Missing Required Elements ===")
        for sec, missing in elems["missing_elements"].items():
            print(f"  {sec}: missing {missing}")

    flow = report["information_flow"]
    if flow["warnings"]:
        print("\n=== Information Flow Warnings ===")
        for w in flow["warnings"]:
            print(f"  {w}")

    order = report["section_ordering"]
    if order["warnings"]:
        print("\n=== Section Ordering ===")
        for w in order["warnings"]:
            print(f"  {w}")

    overall = "PASSED" if report["overall_passed"] else "FAILED"
    print(f"\nOverall: {overall}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Validate manuscript content against a format profile.")
    ap.add_argument("--draft", default=None, help="Path to single draft file")
    ap.add_argument("--sections_dir", default=None, help="Directory of section .md files")
    ap.add_argument("--profile", required=True, help="Format profile name (e.g. generic, computational_report)")
    ap.add_argument("--planner_mode", action="store_true", help="Enforce stricter minimums (1.5x base)")
    ap.add_argument("--report", default=None, help="Write JSON report to this path")
    args = ap.parse_args()

    if not args.draft and not args.sections_dir:
        print("Provide --draft or --sections_dir.", file=sys.stderr)
        sys.exit(1)
    if args.draft and args.sections_dir:
        print("Use either --draft or --sections_dir, not both.", file=sys.stderr)
        sys.exit(1)

    profile = get_profile(args.profile)
    sections = _load_sections(args)

    report = run_validation(sections, profile, planner_mode=args.planner_mode)
    print_report(report)

    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(
            json.dumps(report, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        print(f"\nReport written to {args.report}.")


if __name__ == "__main__":
    main()
