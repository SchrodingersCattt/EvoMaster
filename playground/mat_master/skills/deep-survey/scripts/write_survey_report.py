"""
Compile collected findings (e.g. from run_survey or manual collection) into a final
structured Markdown report: Executive Summary, Key Methodologies, State of the Art,
Gap Analysis, References. Follow _common/reference/citation_and_output_format.md for citation format.

Usage:
  python write_survey_report.py --input "collected.json" --output "_tmp/surveys/survey_xyz.md" --topic "Perovskite stability"
  python write_survey_report.py --sections "intro.md,methods.md,results.md" --output "survey.md" --topic "DPA-2"
"""

import argparse
import json
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Compile survey sections into a single Markdown report."
    )
    ap.add_argument("--input", help="JSON file with collected findings (keys: summary, methodologies, state_of_art, gaps, references)")
    ap.add_argument("--sections", help="Comma-separated paths to section Markdown files to concatenate")
    ap.add_argument("--output", required=True, help="Output Markdown path (e.g. _tmp/surveys/survey_xyz.md)")
    ap.add_argument("--topic", required=True, help="Report topic (used in title)")
    args = ap.parse_args()

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if args.input:
        path = Path(args.input)
        if not path.exists():
            print(f"Error: input file not found: {path}", flush=True)
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"Error reading JSON: {e}", flush=True)
            return
        body = f"""# Survey: {args.topic}

## Executive Summary
{data.get('summary', '(TBD)')}

## Key Methodologies
{data.get('methodologies', '(TBD)')}

## State of the Art
{data.get('state_of_art', '(TBD)')}

## Gap Analysis
{data.get('gaps', '(TBD)')}

## References
{data.get('references', '(TBD)')}
"""
    elif args.sections:
        parts = [f"# Survey: {args.topic}\n"]
        for p in args.sections.split(","):
            p = Path(p.strip())
            if p.exists():
                parts.append(p.read_text(encoding="utf-8"))
            else:
                parts.append(f"<!-- missing: {p} -->\n")
        body = "\n".join(parts)
    else:
        body = f"""# Survey: {args.topic}

*Use --input <json> or --sections <file1,file2,...> to supply content.*

## Executive Summary
(TBD)

## Key Methodologies
(TBD)

## State of the Art
(TBD)

## Gap Analysis
(TBD)

## References
(TBD)
"""

    out_path.write_text(body, encoding="utf-8")
    print(f"Report written to {out_path}.", flush=True)


if __name__ == "__main__":
    main()
