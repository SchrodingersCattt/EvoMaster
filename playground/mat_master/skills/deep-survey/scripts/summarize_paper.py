"""
Extract and summarize specific sections from a single paper (PDF or text).
Focus on methodology, results, or other sections rather than a generic summary.

Usage:
  python summarize_paper.py --pdf "paper.pdf" --focus "methodology"
  python summarize_paper.py --text "abstract.txt" --focus "results"

Output: JSON or plain text to stdout with extracted/summarized content.
"""

import argparse
import json
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Summarize specific sections of a paper (methodology, results, etc.)."
    )
    ap.add_argument("--pdf", default=None, help="Path to PDF file")
    ap.add_argument("--text", default=None, help="Path to plain text file (e.g. abstract)")
    ap.add_argument(
        "--focus",
        default="methodology",
        choices=["methodology", "results", "abstract", "conclusion", "full"],
        help="Section focus for extraction",
    )
    ap.add_argument("--output", choices=["json", "text"], default="json", help="Output format")
    args = ap.parse_args()

    if not args.pdf and not args.text:
        print(json.dumps({"error": "Provide --pdf or --text"}), flush=True)
        return

    path = Path(args.pdf or args.text)
    if not path.exists():
        out = {"error": f"File not found: {path}", "focus": args.focus}
        print(json.dumps(out, ensure_ascii=False), flush=True)
        return

    # Placeholder: real implementation would use PDF extraction (e.g. pypdf, docling)
    # and/or RAG to extract the requested section.
    result = {
        "source": str(path),
        "focus": args.focus,
        "summary": "(Section extraction not implemented; integrate PDF/RAG tools here.)",
    }
    if args.output == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    else:
        print(result["summary"], flush=True)


if __name__ == "__main__":
    main()
