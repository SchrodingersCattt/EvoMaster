"""
Create a survey outline and optional search plan. All report content is written by the LLM:
agent runs 6–15+ retrieval calls (mat_sn_*), then uses write_section / str_replace_editor to fill
Executive Summary, Key Methodologies, State of the Art, Gap Analysis, References.

Usage:
  python run_survey.py --topic "DPA-2 for Alloys" --depth deep --output survey_dpa.md
  python run_survey.py --title "My Survey" --output survey.md
"""

import argparse
import re
from pathlib import Path

DEFAULT_FACETS = [
    "Definition",
    "Mechanism",
    "Methods",
    "Reviews / state of the art",
    "Caveats",
]


def _project_tmp() -> Path:
    cwd = Path.cwd()
    for p in [cwd, cwd.parent, cwd.parent.parent]:
        t = p / "_tmp"
        t.mkdir(parents=True, exist_ok=True)
        return t
    (cwd / "_tmp").mkdir(parents=True, exist_ok=True)
    return cwd / "_tmp"


def sanitize_topic(topic: str) -> str:
    if topic is None:
        return "survey"
    s = str(topic).strip()
    s = re.sub(r"[^\w\s\-]", "", s, flags=re.UNICODE)
    s = s.strip().replace(" ", "_")[:80]
    return s or "survey"


def main() -> None:
    ap = argparse.ArgumentParser(description="Create survey outline and search plan; LLM fills content.")
    ap.add_argument("--topic", default=None, help="Survey topic")
    ap.add_argument("--title", dest="topic_alias", default=None, help="Alias for --topic")
    ap.add_argument("--depth", default="deep", choices=["quick", "deep"])
    ap.add_argument("--output", default=None)
    ap.add_argument("--write_plan", action="store_true")
    args = ap.parse_args()

    topic = args.topic or args.topic_alias
    if not topic:
        ap.error("required: --topic or --title")
    topic = str(topic).strip()

    base = _project_tmp() / "surveys"
    base.mkdir(parents=True, exist_ok=True)
    out_name = args.output or f"survey_{sanitize_topic(topic)}.md"
    out_path = base / out_name

    write_plan = args.write_plan or (args.depth == "deep")
    if write_plan:
        plan_path = base / f"{sanitize_topic(topic)}_plan.md"
        plan_path.write_text(
            f"# Search plan: {topic}\n\n"
            "Run 6–15+ retrieval calls (mat_sn_search-papers-enhanced, mat_sn_web-search). "
            "Then use manuscript-scribe write_section or str_replace_editor to write Executive Summary, "
            "Key Methodologies, State of the Art, Gap Analysis, and References from the retrieval results.\n\n"
            + "\n".join(f"## {f}" for f in DEFAULT_FACETS),
            encoding="utf-8",
        )
        print(f"Search plan: {plan_path}")

    outline = f"""# Survey: {topic}

## Executive Summary
(TBD — LLM writes from retrieval results)

## Key Methodologies
(TBD)

## State of the Art
(TBD)

## Gap Analysis
(TBD)

## References
(TBD)
"""
    out_path.write_text(outline, encoding="utf-8")
    print(f"Survey outline: {out_path}. Fill sections with write_section / str_replace_editor from retrieval results.")


if __name__ == "__main__":
    main()
