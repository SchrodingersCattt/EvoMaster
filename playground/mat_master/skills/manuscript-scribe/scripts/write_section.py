"""
Write or update one section of a manuscript from raw notes or data. Supports chunked writing:
use once to create the section, then --append to add more paragraphs (avoids generating whole section in one go).
Citations: [n](URL) or [n](#ref-n); References section must list same [n]. See reference/citation_and_references.md.

With --profile, prints a word-count warning if the section is below the profile's minimum.
With --min_words, uses an explicit floor instead.

Usage:
  python write_section.py --section "Methods" --content_file "methods_notes.txt" --draft "draft_manuscript.md"
  python write_section.py --section "Introduction" --content "First paragraph..." --output "sections/Introduction.md"
  python write_section.py --section "Introduction" --append --content "Second paragraph..." --draft "draft_manuscript.md"
  python write_section.py --section "Methods" --content_file m.txt --draft d.md --profile computational_report

Output: Updates the draft file or writes to --output; with --append, appends to existing section body.
"""

import argparse
import re
import sys
from pathlib import Path

# Allow importing sibling modules when script is run from any cwd
sys.path.insert(0, str(Path(__file__).resolve().parent))
from section_utils import find_section as _find_section

try:
    from format_profiles import get_profile as _get_profile
except ImportError:
    _get_profile = None  # type: ignore[assignment]


def _word_count(text: str) -> int:
    """Count words (handles mixed CJK and Latin text)."""
    cjk = len(re.findall(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]", text))
    latin = len(re.findall(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)*", text))
    return cjk + latin


def _check_word_count(section_name: str, body: str, profile_name: str | None, min_words_override: int | None) -> None:
    """Print word count and warn if below minimum."""
    wc = _word_count(body)
    min_w = min_words_override or 0

    if not min_w and profile_name and _get_profile is not None:
        try:
            profile = _get_profile(profile_name)
            meta = profile.get("section_meta", {}).get(section_name, {})
            min_w = meta.get("min_words", 0)
        except KeyError:
            pass

    if min_w and wc < min_w:
        print(
            f"WARNING: {section_name} has {wc} words (minimum {min_w}). "
            f"Consider adding more content with --append or --content_file.",
            flush=True,
        )
    else:
        print(f"Section {section_name}: {wc} words.", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="Write or append to one section of a manuscript.")
    ap.add_argument("--section", required=True, help="Section name (e.g. Methods, Introduction)")
    ap.add_argument("--content_file", default=None, help="Path to file with raw notes or JSON")
    ap.add_argument("--content", default=None, help="Inline content (paragraph or chunk)")
    ap.add_argument("--append", action="store_true", help="Append to existing section body instead of replacing")
    ap.add_argument("--tone", default="formal", choices=["formal", "neutral"], help="Writing tone")
    ap.add_argument("--draft", default=None, help="Path to draft file (to update one section)")
    ap.add_argument("--output", default=None, help="Write section to this file (e.g. sections/Introduction.md)")
    ap.add_argument(
        "--profile",
        default=None,
        help="Format profile name (e.g. generic, computational_report). "
             "Used to check word-count minimums after writing.",
    )
    ap.add_argument(
        "--min_words",
        type=int,
        default=None,
        help="Explicit minimum word count (overrides profile setting).",
    )
    args = ap.parse_args()

    if not args.draft and not args.output:
        args.draft = "draft_manuscript.md"
    if args.draft and args.output:
        print("Use either --draft or --output, not both.", file=sys.stderr)
        sys.exit(1)

    draft_path = Path(args.draft or args.output)
    raw = ""
    if args.content_file:
        p = Path(args.content_file)
        if p.exists():
            raw = p.read_text(encoding="utf-8")
    if args.content:
        raw = (raw + "\n" + args.content).strip()

    new_body = raw.strip() or "(Section content not provided.)"
    new_section = f"## {args.section}\n\n{new_body}\n\n"

    # --- Determine the final body text for word-count reporting ---
    final_body = new_body

    if args.output:
        draft_path.parent.mkdir(parents=True, exist_ok=True)
        draft_path.write_text(new_section.strip(), encoding="utf-8")
        print(f"Section {args.section} written to {draft_path}.")
        _check_word_count(args.section, final_body, args.profile, args.min_words)
        return

    if not draft_path.exists():
        draft_path.write_text(f"# Draft\n\n{new_section}", encoding="utf-8")
        print(f"Section {args.section} written to {draft_path}.")
        _check_word_count(args.section, final_body, args.profile, args.min_words)
        return

    content = draft_path.read_text(encoding="utf-8")
    span = _find_section(content, args.section, include_text=True)
    if span is None:
        if "## References" in content:
            content = content.replace("## References", new_section + "## References")
        else:
            content = content.rstrip() + "\n\n" + new_section
        draft_path.write_text(content, encoding="utf-8")
        print(f"Section {args.section} written to {draft_path}.")
        _check_word_count(args.section, final_body, args.profile, args.min_words)
        return

    start, end, existing = span
    if args.append:
        # Append new_body to existing section (after header, before next ##)
        lines = existing.splitlines()
        header = lines[0] if lines else f"## {args.section}"
        body = "\n".join(lines[1:]).strip() if len(lines) > 1 else ""
        combined = f"{body}\n\n{new_body}".strip() if body else new_body
        replacement = f"{header}\n\n{combined}\n\n"
        final_body = combined
    else:
        replacement = new_section
    content = content[:start] + replacement + content[end:].lstrip()
    draft_path.write_text(content, encoding="utf-8")
    print(f"Section {args.section} written to {draft_path}.")
    _check_word_count(args.section, final_body, args.profile, args.min_words)


if __name__ == "__main__":
    main()
