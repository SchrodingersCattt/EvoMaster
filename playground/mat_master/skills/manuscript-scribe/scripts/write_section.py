"""
Write or update one section of a manuscript from raw notes or data. Appends/updates the section
in the draft file (or writes to a single section file) to avoid generating the whole paper in one turn.
Citations must be in-text with hyperlinks: [n](URL) or [n](#ref-n), and References section must list
the same [n] with original source URLs. See reference/citation_and_references.md.

Usage:
  python write_section.py --section "Methods" --content_file "methods_notes.txt" --tone "formal" --draft "draft_manuscript.md"
  python write_section.py --section "Introduction" --content_file "intro_bullets.txt" --output "sections/Introduction.md"

Output: Updates the draft file or writes to --output and prints "Section <name> written to <path>."
"""

import argparse
import re
import sys
from pathlib import Path

# Allow importing section_utils when script is run from any cwd
sys.path.insert(0, str(Path(__file__).resolve().parent))
from section_utils import find_section as _find_section


def main() -> None:
    ap = argparse.ArgumentParser(description="Write one section of a manuscript from notes/data.")
    ap.add_argument("--section", required=True, help="Section name (e.g. Methods, Introduction)")
    ap.add_argument("--content_file", default=None, help="Path to file with raw notes or JSON")
    ap.add_argument("--content", default=None, help="Inline content (short notes)")
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

    # Placeholder: real implementation would call LLM to turn raw into polished prose.
    # Here we insert a placeholder block so the section exists and the workflow is clear.
    new_body = raw.strip() or "(Section content not provided; integrate LLM here to generate from raw notes.)"
    new_section = f"## {args.section}\n\n{new_body}\n\n"

    if args.output:
        # Write standalone section file (for later assembly)
        draft_path.parent.mkdir(parents=True, exist_ok=True)
        draft_path.write_text(new_section.strip(), encoding="utf-8")
        print(f"Section {args.section} written to {draft_path}.")
        return

    if not draft_path.exists():
        draft_path.write_text(f"# Draft\n\n{new_section}", encoding="utf-8")
        print(f"Section {args.section} written to {draft_path}.")
        return

    content = draft_path.read_text(encoding="utf-8")
    span = _find_section(content, args.section, include_text=False)
    if span is None:
        # Append new section before References or at end
        if "## References" in content:
            content = content.replace("## References", new_section + "## References")
        else:
            content = content.rstrip() + "\n\n" + new_section
    else:
        start, end = span
        content = content[:start] + new_section + content[end:].lstrip()
    draft_path.write_text(content, encoding="utf-8")
    print(f"Section {args.section} written to {draft_path}.")


if __name__ == "__main__":
    main()
