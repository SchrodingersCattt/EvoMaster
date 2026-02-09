"""
Polish one section of a draft for academic English: reduce redundancy ("In this paper", "We show that"),
improve formality, and overwrite the section in place. Does not change structure.

Usage:
  python polish_text.py --file "draft.md" --target_section "Introduction"
  python polish_text.py --file "draft.md" --target_section "Results" --in_place

Output: Overwrites the section in the file and prints "Polished section <name> in <path>."
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from section_utils import find_section


def simple_polish(text: str) -> str:
    """Placeholder polish: strip common colloquialisms. Real impl would use LLM."""
    # Remove leading filler
    text = re.sub(r"^(In this paper,?\s*)+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^(We show that\s*)+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^\s*So,?\s+", "", text, flags=re.IGNORECASE)
    return text.strip()


def main() -> None:
    ap = argparse.ArgumentParser(description="Polish one section for academic English.")
    ap.add_argument("--file", required=True, help="Path to draft file")
    ap.add_argument("--target_section", required=True, help="Section to polish (e.g. Introduction)")
    ap.add_argument("--in_place", action="store_true", help="Overwrite file (default: true)")
    args = ap.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"Error: file not found: {path}", flush=True)
        return

    content = path.read_text(encoding="utf-8")
    found = find_section(content, args.target_section, include_text=True)
    if not found:
        print(f"Section '{args.target_section}' not found in {path}.", flush=True)
        return

    start, end, section_text = found
    # Preserve header line
    lines = section_text.splitlines()
    header = lines[0] if lines else f"## {args.target_section}"
    body = "\n".join(lines[1:]) if len(lines) > 1 else ""
    polished_body = simple_polish(body)
    new_section = f"{header}\n\n{polished_body}\n\n"

    new_content = content[:start] + new_section + content[end:].lstrip()
    path.write_text(new_content, encoding="utf-8")
    print(f"Polished section {args.target_section} in {path}.", flush=True)


if __name__ == "__main__":
    main()
