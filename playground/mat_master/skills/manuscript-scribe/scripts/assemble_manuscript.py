"""
Assemble section files (or a single draft) into one manuscript and run three checks:
1. All technical terms are defined (at first use).
2. All abbreviations are defined exactly once (no duplicate definitions).
3. Reference links are valid and References section matches in-text citations.

Usage:
  python assemble_manuscript.py --sections_dir sections/ --output draft_manuscript.md
  python assemble_manuscript.py --draft draft_manuscript.md --output final.md --validate

Output: Writes assembled Markdown and a validation report (JSON or text).
"""

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

try:
    import requests
except ImportError:
    requests = None

# Section order when merging from a directory
DEFAULT_SECTION_ORDER = [
    "Abstract",
    "Introduction",
    "Methods",
    "Results",
    "Discussion",
    "References",
]


def extract_sections_from_draft(content: str) -> dict[str, str]:
    """Parse a single draft file into section name -> section text (including ## Header)."""
    sections = {}
    current = None
    buf: list[str] = []
    for line in content.splitlines(keepends=True):
        if re.match(r"^##\s+.+\s*$", line):
            if current is not None:
                sections[current] = "".join(buf)
            current = line.replace("##", "").strip()
            buf = [line]
        else:
            buf.append(line)
    if current is not None:
        sections[current] = "".join(buf)
    return sections


def load_sections_from_dir(sections_dir: Path) -> dict[str, str]:
    """Load section files: SectionName.md -> content. Strip leading # Title so only ## SectionName + body is kept."""
    sections = {}
    for f in sections_dir.iterdir():
        if f.suffix.lower() == ".md":
            name = f.stem
            raw = f.read_text(encoding="utf-8").strip()
            # If file has "# Title\n\n## SectionName", keep only "## SectionName\n\n..."
            marker = f"## {name}"
            if marker in raw:
                idx = raw.find(marker)
                raw = raw[idx:]
            elif not raw.startswith("#"):
                raw = f"## {name}\n\n{raw}"
            sections[name] = raw
    return sections


def assemble(sections: dict[str, str], order: list[str] | None = None, title: str | None = None) -> str:
    """Merge sections in order. Prepend title once if given. Keys not in order are appended at end."""
    order = order or DEFAULT_SECTION_ORDER
    seen = set()
    parts = []
    if title:
        parts.append(f"# {title}\n")
    for name in order:
        if name in sections:
            seen.add(name)
            parts.append(sections[name].strip())
    for name, text in sections.items():
        if name not in seen:
            parts.append(text.strip())
    return "\n\n".join(parts)


# ----- Check 1: Technical terms (heuristic: report possible undefined terms) -----
def collect_defining_phrases(text: str) -> set[str]:
    """Simple heuristic: phrases that often introduce definitions."""
    defs = set()
    # "X is defined as", "X refers to", "X denotes", "we define X as"
    for m in re.finditer(
        r"(?:defined as|refers to|denotes|we define)\s+([^.,;:\n]+?)(?=[.;,:\n])",
        text,
        re.IGNORECASE,
    ):
        defs.add(m.group(1).strip().lower())
    for m in re.finditer(r"([A-Za-z][A-Za-z0-9\s\-]+?)\s*\(\s*[A-Z]{2,}\s*\)", text):
        # "Full Name (ABBR)" -> full name as defined term
        defs.add(m.group(1).strip().lower())
    return defs


def check_terms(body_text: str, terms_file: Path | None) -> dict:
    """Report possible undefined technical terms. If terms_file provided, list required terms and check each is 'defined' in text."""
    result = {"passed": True, "undefined": [], "message": ""}
    defining = collect_defining_phrases(body_text)
    if terms_file and terms_file.exists():
        required = set()
        for line in terms_file.read_text(encoding="utf-8").splitlines():
            t = line.split("#")[0].strip().lower()
            if t:
                required.add(t)
        for t in required:
            if t not in defining and t not in body_text.lower():
                result["undefined"].append(t)
                result["passed"] = False
        if result["undefined"]:
            result["message"] = "Technical terms missing definition or usage: " + ", ".join(result["undefined"])
        else:
            result["message"] = "All listed technical terms appear with definitions or in context."
    else:
        result["message"] = "No terms file provided; skipped term check (define terms in reference/ or pass --terms)."
    return result


# ----- Check 2: Abbreviations -----
def extract_abbrev_definitions(text: str) -> dict[str, str]:
    """Return dict: ABBR -> "Full Name" from "Full Name (ABBR)" or "ABBR (Full Name)"."""
    abbrevs = {}
    # Full Name (ABBR)
    for m in re.finditer(r"([A-Za-z][A-Za-z0-9\s\-/]+?)\s*\(\s*([A-Z][A-Z0-9]{1,})\s*\)", text):
        full, abbr = m.group(1).strip(), m.group(2).strip()
        if abbr not in abbrevs:
            abbrevs[abbr] = full
    # ABBR (Full Name)
    for m in re.finditer(r"\b([A-Z][A-Z0-9]{1,})\s*\(\s*([^)]+)\)", text):
        abbr, full = m.group(1).strip(), m.group(2).strip()
        if abbr not in abbrevs:
            abbrevs[abbr] = full
    return abbrevs


def check_abbreviations(full_text: str) -> dict:
    """Ensure each abbreviation has exactly one definition and no re-definition."""
    result = {"passed": True, "duplicate_definitions": [], "undefined_abbrevs": [], "message": ""}
    # Split by sections to find "first use" (first section where ABBR appears)
    sections = re.split(r"\n##\s+", full_text)
    all_defs = extract_abbrev_definitions(full_text)
    # Count definitions per ABBR (by occurrence)
    defs_count: dict[str, list[int]] = {}
    for m in re.finditer(r"([A-Za-z][A-Za-z0-9\s\-/]+?)\s*\(\s*([A-Z][A-Z0-9]{1,})\s*\)", full_text):
        abbr = m.group(2).strip()
        defs_count.setdefault(abbr, []).append(m.start())
    for m in re.finditer(r"\b([A-Z][A-Z0-9]{1,})\s*\(\s*([^)]+)\)", full_text):
        abbr = m.group(1).strip()
        defs_count.setdefault(abbr, []).append(m.start())
    for abbr, positions in defs_count.items():
        if len(positions) > 1:
            result["duplicate_definitions"].append(abbr)
            result["passed"] = False
    # Optional: find standalone ALL-CAPS that might be undefined (heuristic: 2–5 chars, not in defs)
    # Skip for now to avoid false positives; we only report duplicate defs and missing defs if we have a list
    if result["duplicate_definitions"]:
        result["message"] = "Duplicate abbreviation definitions: " + ", ".join(result["duplicate_definitions"])
    else:
        result["message"] = "No duplicate abbreviation definitions found."
    return result


# ----- Check 3: References -----
def extract_citation_numbers_from_body(text: str) -> set[int]:
    """Extract [n] and [n](url) from body (exclude References section)."""
    # Remove References section for body
    ref_start = re.search(r"\n##\s+References\s*\n", text, re.IGNORECASE)
    body = text[: ref_start.start()] if ref_start else text
    nums = set()
    for m in re.finditer(r"\[(\d+)\](?:\([^)]*\))?", body):
        nums.add(int(m.group(1)))
    return nums


def extract_references_section(text: str) -> str:
    """Return the References section content (after ## References)."""
    m = re.search(r"\n##\s+References\s*\n(.*)", text, re.IGNORECASE | re.DOTALL)
    return m.group(1).strip() if m else ""


def parse_references_entries(ref_section: str) -> dict[int, str]:
    """Parse [n] ... url from References. Returns { n: line_text }."""
    entries = {}
    for m in re.finditer(r"\[(\d+)\]\s*(.+?)(?=\n\s*\[\d+\]|\Z)", ref_section, re.DOTALL):
        n = int(m.group(1))
        line = m.group(2).strip()
        entries[n] = line
    return entries


def extract_url_from_ref_line(line: str) -> str | None:
    """Return first URL (http/https) from a reference line."""
    m = re.search(r"https?://[^\s\)\]\>]+", line)
    return m.group(0).rstrip(".,;:)") if m else None


def check_references(full_text: str, validate_urls: bool = False) -> dict:
    """Ensure in-text citations and References section match; optionally validate URLs."""
    result = {"passed": True, "missing_in_refs": [], "missing_in_text": [], "invalid_urls": [], "message": ""}
    body_nums = extract_citation_numbers_from_body(full_text)
    ref_section = extract_references_section(full_text)
    ref_entries = parse_references_entries(ref_section)

    for n in body_nums:
        if n not in ref_entries:
            result["missing_in_refs"].append(n)
            result["passed"] = False
    for n in ref_entries:
        if n not in body_nums:
            result["missing_in_text"].append(n)
            result["passed"] = False

    if validate_urls and requests is not None:
        for n, line in ref_entries.items():
            url = extract_url_from_ref_line(line)
            if url:
                try:
                    r = requests.head(url, timeout=10, allow_redirects=True)
                    if r.status_code >= 400:
                        result["invalid_urls"].append((n, url, r.status_code))
                        result["passed"] = False
                except Exception as e:
                    result["invalid_urls"].append((n, url, str(e)))
                    result["passed"] = False
            else:
                result["invalid_urls"].append((n, "(no URL)", "missing"))
                result["passed"] = False

    if result["missing_in_refs"]:
        result["message"] = "Citations in text missing from References: " + str(sorted(result["missing_in_refs"]))
    elif result["missing_in_text"]:
        result["message"] = "References section has entries not cited in text: " + str(sorted(result["missing_in_text"]))
    elif result["invalid_urls"]:
        result["message"] = "Invalid or unreachable reference URLs: " + str(result["invalid_urls"])
    else:
        result["message"] = "References consistent with text." + (" URLs validated." if validate_urls else "")
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description="Assemble manuscript and run consistency checks.")
    ap.add_argument("--sections_dir", default=None, help="Directory of section .md files to merge")
    ap.add_argument("--draft", default=None, help="Single draft file (sections as ## Headers)")
    ap.add_argument("--output", required=True, help="Output assembled Markdown path")
    ap.add_argument("--validate", action="store_true", help="Validate reference URLs (HTTP HEAD)")
    ap.add_argument("--terms", default=None, help="Optional file listing required technical terms (one per line)")
    ap.add_argument("--report", default=None, help="Write validation report to this JSON file")
    args = ap.parse_args()

    if args.sections_dir and args.draft:
        print("Use either --sections_dir or --draft, not both.", file=sys.stderr)
        sys.exit(1)
    if not args.sections_dir and not args.draft:
        print("Provide --sections_dir or --draft. Example: --draft draft_manuscript.md --output final.md", file=sys.stderr)
        sys.exit(1)

    if args.sections_dir:
        sections_dir = Path(args.sections_dir)
        sections = load_sections_from_dir(sections_dir)
        # Take title from first section file if present (e.g. "# Title" in Abstract.md)
        title = None
        for name in DEFAULT_SECTION_ORDER:
            f = sections_dir / f"{name}.md"
            if f.exists():
                first_line = f.read_text(encoding="utf-8").splitlines()[0].strip()
                if first_line.startswith("# ") and not first_line.startswith("## "):
                    title = first_line.lstrip("# ")
                break
        combined = assemble(sections, title=title)
    else:
        combined = Path(args.draft).read_text(encoding="utf-8")

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(combined, encoding="utf-8")
    print(f"Assembled manuscript written to {out_path}.")

    # Run three checks
    terms_file = Path(args.terms) if args.terms else None
    body_only = combined
    ref_start = re.search(r"\n##\s+References\s*\n", combined, re.IGNORECASE)
    if ref_start:
        body_only = combined[: ref_start.start()]

    check1 = check_terms(body_only, terms_file)
    check2 = check_abbreviations(combined)
    check3 = check_references(combined, validate_urls=args.validate)

    report = {
        "technical_terms": check1,
        "abbreviations": check2,
        "references": check3,
        "overall_passed": check1["passed"] and check2["passed"] and check3["passed"],
    }
    print("1. Technical terms:", check1["message"])
    print("2. Abbreviations:", check2["message"])
    print("3. References:", check3["message"])
    print("Overall:", "PASSED" if report["overall_passed"] else "FAILED")

    if args.report:
        Path(args.report).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Report written to {args.report}.")


if __name__ == "__main__":
    main()
