"""
Write or update one section of a manuscript from raw notes or data. Supports chunked writing:
use once to create the section, then --append to add more paragraphs (avoids generating whole section in one go).
Citations: [n](URL) or [n](#ref-n); References section must list same [n]. See reference/citation_and_references.md.

Usage:
  python write_section.py --section "Methods" --content_file "methods_notes.txt" --draft "draft_manuscript.md"
  python write_section.py --section "Introduction" --content "First paragraph..." --output "sections/Introduction.md"
  python write_section.py --section "Introduction" --append --content "Second paragraph..." --draft "draft_manuscript.md"

Output: Updates the draft file or writes to --output; with --append, appends to existing section body.
"""

import argparse
import re
import sys
from pathlib import Path

# Allow importing section_utils when script is run from any cwd
sys.path.insert(0, str(Path(__file__).resolve().parent))
from section_utils import find_section as _find_section


def main() -> None:
    ap = argparse.ArgumentParser(description="Write or append to one section of a manuscript.")
    ap.add_argument("--section", required=True, help="Section name (e.g. Methods, Introduction)")
    ap.add_argument("--content_file", default=None, help="Path to file with raw notes or JSON")
    ap.add_argument("--content", default=None, help="Inline content (paragraph or chunk)")
    ap.add_argument("--append", action="store_true", help="Append to existing section body instead of replacing")
    ap.add_argument("--tone", default="formal", choices=["formal", "neutral"], help="Writing tone")
    ap.add_argument("--draft", default=None, help="Path to draft file (to update one section)")
    ap.add_argument("--output", default=None, help="Write section to this file (e.g. sections/Introduction.md)")
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

    if args.output:
        draft_path.parent.mkdir(parents=True, exist_ok=True)
        draft_path.write_text(new_section.strip(), encoding="utf-8")
        print(f"Section {args.section} written to {draft_path}.")
        return

    if not draft_path.exists():
        draft_path.write_text(f"# Draft\n\n{new_section}", encoding="utf-8")
        print(f"Section {args.section} written to {draft_path}.")
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
        return

    start, end, existing = span
    if args.append:
        # Append new_body to existing section (after header, before next ##)
        lines = existing.splitlines()
        header = lines[0] if lines else f"## {args.section}"
        body = "\n".join(lines[1:]).strip() if len(lines) > 1 else ""
        combined = f"{body}\n\n{new_body}".strip() if body else new_body
        replacement = f"{header}\n\n{combined}\n\n"
    else:
        replacement = new_section
    content = content[:start] + replacement + content[end:].lstrip()
    draft_path.write_text(content, encoding="utf-8")
    print(f"Section {args.section} written to {draft_path}.")


if __name__ == "__main__":
    main()
