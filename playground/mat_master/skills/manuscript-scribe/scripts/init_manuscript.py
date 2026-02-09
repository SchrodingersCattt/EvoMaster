"""
Create a new manuscript draft with a standard outline (Abstract, Introduction, Methods, Results, Discussion).
Optional template: Nature, generic, grant. With --sections_dir, creates one file per section for later assembly.

Usage:
  python init_manuscript.py --title "My Paper" --template "Nature"
  python init_manuscript.py --title "My Paper" --template "generic" --sections_dir sections/
  python init_manuscript.py --title "Grant Proposal" --template "grant" --output "grant_draft.md"

Output: Creates draft_manuscript.md (or --output path), or section files under sections_dir.
"""

import argparse
from pathlib import Path

SECTION_ORDER = ["Abstract", "Introduction", "Methods", "Results", "Discussion", "References"]

OUTLINES = {
    "generic": """# {title}

## Abstract
(TBD)

## Introduction
(TBD)

## Methods
(TBD)

## Results
(TBD)

## Discussion
(TBD)

## References
(TBD)
""",
    "Nature": """# {title}

## Abstract
(TBD)

## Introduction
(TBD)

## Results
(TBD)

## Discussion
(TBD)

## Methods
(TBD)

## References
(TBD)
""",
    "grant": """# {title}

## Summary / Abstract
(TBD)

## Significance
(TBD)

## Approach
(TBD)

## Preliminary Results
(TBD)

## Timeline
(TBD)

## References
(TBD)
""",
}


def main() -> None:
    ap = argparse.ArgumentParser(description="Initialize a manuscript draft with outline.")
    ap.add_argument("--title", required=True, help="Paper or grant title")
    ap.add_argument("--template", default="generic", choices=list(OUTLINES), help="Outline template")
    ap.add_argument("--output", default=None, help="Output path (default: draft_manuscript.md)")
    ap.add_argument(
        "--sections_dir",
        default=None,
        help="Create one .md file per section in this directory for later assembly",
    )
    args = ap.parse_args()

    if args.sections_dir:
        sections_dir = Path(args.sections_dir)
        sections_dir.mkdir(parents=True, exist_ok=True)
        for name in SECTION_ORDER:
            path = sections_dir / f"{name}.md"
            if name == "References":
                path.write_text(
                    f"# {args.title}\n\n## References\n\n"
                    "[1] Author. Title. *Journal* Year. https://doi.org/...\n",
                    encoding="utf-8",
                )
            else:
                path.write_text(
                    f"# {args.title}\n\n## {name}\n\n(TBD)\n",
                    encoding="utf-8",
                )
        print(f"Section files created in {sections_dir}. Run write_section for each, then assemble_manuscript.")
        return

    out_path = Path(args.output or "draft_manuscript.md")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    body = OUTLINES[args.template].format(title=args.title)
    out_path.write_text(body, encoding="utf-8")
    print(f"Manuscript initialized: {out_path}")


if __name__ == "__main__":
    main()
