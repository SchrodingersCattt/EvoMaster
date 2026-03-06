"""
Create a survey outline and optional search plan.

depth=brief  → outputs collected.json (structured evidence skeleton); 3-5 retrieval calls expected.
depth=standard → outputs a concise Markdown outline (Executive Summary + References); 6-8 calls expected.
depth=deep   → full 5-section outline + search plan; 10-15+ calls expected (default, original behaviour).

All report content is always written by the LLM: the agent runs retrieval calls (mat_sn_*), then uses
write_section / str_replace_editor to fill in the sections.

Usage:
  python run_survey.py --topic "DPA-2 for Alloys" --depth deep --output survey_dpa.md
  python run_survey.py --topic "Perovskite stability" --depth brief --output collected.json
  python run_survey.py --title "My Survey" --output survey.md
"""

import argparse
import json
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


def _extract_key_concepts(topic: str) -> list[str]:
    """Extract key concepts from topic for coverage contract (e.g. 'A vs B' -> [A, B])."""
    if not topic or not isinstance(topic, str):
        return []
    t = topic.strip()
    for sep in (" vs ", " versus ", " and ", " / "):
        if sep in t.lower():
            parts = re.split(re.escape(sep), t, flags=re.I)
            concepts = [p.strip().split()[0] for p in parts if p.strip()]
            if len(concepts) >= 2:
                return concepts[:5]
    words = re.findall(r"[a-zA-Z0-9\u4e00-\u9fff]+", t)
    skip = {"the", "a", "an", "in", "on", "of", "for", "to", "and", "or"}
    concepts = [w for w in words if w.lower() not in skip and len(w) > 1]
    return concepts[:3] if concepts else []


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Create survey outline / evidence skeleton; LLM fills content via retrieval."
    )
    ap.add_argument("--topic", default=None, help="Survey topic")
    ap.add_argument("--title", dest="topic_alias", default=None, help="Alias for --topic")
    ap.add_argument(
        "--depth",
        default="deep",
        choices=["brief", "standard", "deep"],
        help=(
            "brief: output collected.json (evidence skeleton, 3-5 retrieval calls); "
            "standard: concise MD outline (Executive Summary + References, 6-8 calls); "
            "deep: full 5-section outline + search plan (10-15+ calls, default)."
        ),
    )
    ap.add_argument("--output", default=None)
    ap.add_argument("--write_plan", action="store_true")
    ap.add_argument(
        "--key_concepts",
        default=None,
        help="Comma-separated key concepts to require in evidence (default: auto from topic)",
    )
    # Accept but ignore --state (lit-data-organizer uses it; agent may pass it by mistake).
    ap.add_argument("--state", default=None, help=argparse.SUPPRESS)
    args = ap.parse_args()

    topic = args.topic or args.topic_alias
    if not topic:
        ap.error("required: --topic or --title")
    topic = str(topic).strip()

    key_concepts = (
        [s.strip() for s in (args.key_concepts or "").split(",") if s.strip()]
        if args.key_concepts
        else _extract_key_concepts(topic)
    )

    base = _project_tmp() / "surveys"
    base.mkdir(parents=True, exist_ok=True)

    def _resolve_output(output_arg: str | None, default_name: str) -> Path:
        """Resolve --output to a Path.

        If the caller already provided a path containing a directory separator
        (e.g. ``_tmp/surveys/foo.md``), treat it as relative to CWD so the
        path is not doubled by prepending ``_tmp/surveys/`` again.
        Only bare filenames (no separator) get the automatic ``_tmp/surveys/``
        prefix.
        """
        if output_arg is None:
            return base / default_name
        if "/" in output_arg or "\\" in output_arg:
            p = Path(output_arg)
            p.parent.mkdir(parents=True, exist_ok=True)
            return p
        return base / output_arg

    # ------------------------------------------------------------------ brief
    if args.depth == "brief":
        out_path = _resolve_output(args.output, f"collected_{sanitize_topic(topic)}.json")
        skeleton = {
            "schema_version": "2",
            "source_kind": "survey",
            "topic": topic,
            "key_concepts": key_concepts,
            "depth": "brief",
            "facets": DEFAULT_FACETS[:2],
            "evidence_cards": [],
            "_instructions": (
                "Fill evidence_cards via 3-5 mat_sn_* retrieval calls. "
                "Each card: {source_title, source_url, year, first_author, facet, claim, data_points}."
            ),
        }
        out_path.write_text(json.dumps(skeleton, ensure_ascii=False, indent=2), encoding="utf-8")
        print(
            f"Evidence skeleton: {out_path}. "
            "Run 3-5 mat_sn_* retrieval calls, then run: "
            f"collect_evidence.py --collected_json {out_path} "
            "to auto-populate evidence_cards."
        )
        return

    # --------------------------------------------------------------- standard
    if args.depth == "standard":
        out_path = _resolve_output(args.output, f"survey_{sanitize_topic(topic)}.md")
        outline = f"""# Survey: {topic}

## Executive Summary
(TBD — LLM writes from retrieval results; 1-2 paragraphs)

## References
(TBD)
"""
        out_path.write_text(outline, encoding="utf-8")

        collected_path = base / f"collected_{sanitize_topic(topic)}.json"
        skeleton = {
            "schema_version": "2",
            "source_kind": "survey",
            "topic": topic,
            "key_concepts": key_concepts,
            "depth": "standard",
            "facets": DEFAULT_FACETS[:3],
            "evidence_cards": [],
            "_instructions": (
                "Fill evidence_cards via 6-8 mat_sn_* retrieval calls. "
                "Each card: {source_title, source_url, year, first_author, facet, claim, data_points}."
            ),
        }
        collected_path.write_text(json.dumps(skeleton, ensure_ascii=False, indent=2), encoding="utf-8")

        print(
            f"Survey outline (standard): {out_path}. "
            f"Evidence skeleton: {collected_path}. "
            "Run 6-8 mat_sn_* retrieval calls. "
            "After retrieval, run: "
            f"collect_evidence.py --collected_json {collected_path} "
            "to auto-populate evidence_cards. "
            "Then fill Executive Summary and References with write_section / str_replace_editor."
        )
        return

    # ------------------------------------------------------------------- deep
    write_plan = args.write_plan or (args.depth == "deep")
    out_path = _resolve_output(args.output, f"survey_{sanitize_topic(topic)}.md")

    if write_plan:
        plan_path = base / f"{sanitize_topic(topic)}_plan.md"
        plan_path.write_text(
            f"# Search plan: {topic}\n\n"
            "Run 10-15+ retrieval calls (mat_sn_search-papers-enhanced, mat_sn_web-search). "
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

    collected_path = base / f"collected_{sanitize_topic(topic)}.json"
    skeleton = {
        "schema_version": "2",
        "source_kind": "survey",
        "topic": topic,
        "key_concepts": key_concepts,
        "depth": "deep",
        "facets": DEFAULT_FACETS,
        "evidence_cards": [],
        "_instructions": (
            "Fill evidence_cards via 10-15+ mat_sn_* retrieval calls. "
            "Each card: {source_title, source_url, year, first_author, facet, claim, data_points}."
        ),
    }
    collected_path.write_text(json.dumps(skeleton, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        f"Survey outline: {out_path}. "
        f"Evidence skeleton: {collected_path}. "
        "Run 10-15+ mat_sn_* retrieval calls. "
        "After ALL retrieval is complete, run: "
        f"collect_evidence.py --collected_json {collected_path} "
        "to auto-populate evidence_cards. "
        "Then fill sections with write_section / str_replace_editor from the collected evidence."
    )


if __name__ == "__main__":
    main()
