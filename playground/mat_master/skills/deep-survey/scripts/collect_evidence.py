#!/usr/bin/env python3
"""
Collect and persist evidence cards from raw mat_sn_* tool outputs.

Scans _tmp/tool_outputs/mat_sn_*/ for auto-saved JSON files and converts them
into structured evidence_cards, writing results back to the collected_<topic>.json
skeleton produced by run_survey.py.

This script performs purely mechanical data conversion — no LLM judgment needed.

Supported source formats:
  mat_sn_search-papers-enhanced  → {data: [{enName, paperUrl, doi, authors, coverDateStart, enAbstract, ...}]}
  mat_sn_web-search              → {results: [{title, link, snippet}]}

Usage:
  python collect_evidence.py --collected_json _tmp/surveys/collected_MyTopic.json
  python collect_evidence.py --collected_json _tmp/surveys/collected_MyTopic.json --tool_outputs_dir _tmp/tool_outputs
  python collect_evidence.py --topic "MyTopic" --tool_outputs_dir _tmp/tool_outputs
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


# ---------------------------------------------------------------------------
# Field extraction helpers
# ---------------------------------------------------------------------------

def _first_str(*values: object) -> str:
    """Return the first non-empty string from the given values."""
    for v in values:
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, list) and v:
            first = v[0]
            if isinstance(first, str) and first.strip():
                return first.strip()
    return ""


def _truncate(text: str, max_chars: int = 400) -> str:
    if not text:
        return ""
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(" ", 1)[0] + " [...]"


def _year_from_date(date_str: str) -> int | None:
    """Extract 4-digit year from a date string like '2024-03-01'."""
    if not date_str:
        return None
    m = re.search(r"\b(19|20)\d{2}\b", date_str)
    return int(m.group(0)) if m else None


def _build_url(paper: dict) -> str:
    """Prefer explicit paperUrl; fall back to doi URL."""
    url = _first_str(paper.get("paperUrl", ""))
    if url:
        return url
    doi = _first_str(paper.get("doi", ""))
    if doi:
        return f"https://doi.org/{doi}"
    return ""


def _source_url_dedup_key(url: str) -> str:
    """Normalise a URL for deduplication (strip trailing slash, lowercase)."""
    return url.strip().rstrip("/").lower()


# ---------------------------------------------------------------------------
# Per-source-type extraction
# ---------------------------------------------------------------------------

def _extract_papers_enhanced(payload: dict) -> list[dict]:
    """Extract evidence cards from mat_sn_search-papers-enhanced output."""
    data = payload.get("data", [])
    if not isinstance(data, list):
        return []
    cards = []
    for paper in data:
        if not isinstance(paper, dict):
            continue
        source_url = _build_url(paper)
        if not source_url:
            continue
        authors = paper.get("authors", [])
        first_author = _first_str(authors[0] if authors else "")
        # prefer English name / abstract
        title = _first_str(
            paper.get("enName", ""),
            paper.get("zhName", ""),
            paper.get("title", ""),
        )
        abstract = _first_str(
            paper.get("enAbstract", ""),
            paper.get("zhAbstract", ""),
            paper.get("pieces", ""),
        )
        year = _year_from_date(_first_str(paper.get("coverDateStart", "")))
        cards.append({
            "source_title": title,
            "source_url": source_url,
            "year": year,
            "first_author": first_author,
            "facet": "",   # caller can assign facet later
            "claim": _truncate(abstract, 400),
            "data_points": {},
        })
    return cards


def _extract_web_search(payload: dict) -> list[dict]:
    """Extract evidence cards from mat_sn_web-search output."""
    results = payload.get("results", [])
    if not isinstance(results, list):
        return []
    cards = []
    for result in results:
        if not isinstance(result, dict):
            continue
        url = _first_str(result.get("link", ""))
        if not url:
            continue
        cards.append({
            "source_title": _first_str(result.get("title", "")),
            "source_url": url,
            "year": None,
            "first_author": "",
            "facet": "",
            "claim": _truncate(_first_str(result.get("snippet", "")), 400),
            "data_points": {},
        })
    return cards


def _extract_cards_from_file(path: Path) -> list[dict]:
    """Dispatch extraction based on parent directory name (tool type)."""
    try:
        with path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        return []
    if not isinstance(payload, dict):
        return []
    tool_dir = path.parent.name   # e.g. mat_sn_search-papers-enhanced
    if "search-papers" in tool_dir or "scholar" in tool_dir:
        return _extract_papers_enhanced(payload)
    if "web-search" in tool_dir:
        return _extract_web_search(payload)
    # Generic fallback: try both formats
    cards = _extract_papers_enhanced(payload)
    if not cards:
        cards = _extract_web_search(payload)
    return cards


# ---------------------------------------------------------------------------
# Skeleton helpers
# ---------------------------------------------------------------------------

DEFAULT_FACETS = [
    "Definition",
    "Mechanism",
    "Methods",
    "Reviews / state of the art",
    "Caveats",
]


def _make_skeleton(topic: str) -> dict:
    return {
        "topic": topic,
        "depth": "auto",
        "facets": DEFAULT_FACETS,
        "evidence_cards": [],
        "_instructions": (
            "Populated automatically by collect_evidence.py from _tmp/tool_outputs/mat_sn_*."
        ),
    }


def _load_skeleton(path: Path) -> dict:
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------

def collect_evidence(
    collected_json: Path,
    tool_outputs_dir: Path,
    topic: str | None = None,
    facet: str | None = None,
) -> dict:
    """
    Scan tool_outputs_dir for mat_sn_* JSON files, extract evidence cards,
    merge into collected_json skeleton, and write it back.

    If facet is provided, it must be one of skeleton["facets"]; all new cards
    will get that facet. If not provided, new cards keep facet empty (can be
    filled later by deprecated assign_facet.py for legacy workflows).

    Returns a summary dict.
    """
    # Load or create skeleton
    skeleton = _load_skeleton(collected_json)
    if not skeleton:
        t = topic or collected_json.stem.replace("collected_", "").replace("_", " ")
        skeleton = _make_skeleton(t)

    if facet is not None and facet.strip():
        facets = skeleton.get("facets") or DEFAULT_FACETS
        if not isinstance(facets, list):
            return {"status": "error", "message": "skeleton facets is not a list"}
        if facet.strip() not in facets:
            return {
                "status": "error",
                "message": f"facet '{facet}' is not in skeleton facets: {facets}",
            }

    existing_cards: list[dict] = skeleton.get("evidence_cards", [])
    if not isinstance(existing_cards, list):
        existing_cards = []

    # Build dedup set from existing cards
    seen_urls: set[str] = {
        _source_url_dedup_key(c.get("source_url", ""))
        for c in existing_cards
        if c.get("source_url")
    }

    # Discover all mat_sn_* JSON files
    new_cards: list[dict] = []
    if tool_outputs_dir.exists():
        for subdir in sorted(tool_outputs_dir.iterdir()):
            if not subdir.is_dir():
                continue
            if not subdir.name.startswith("mat_sn_"):
                continue
            for json_file in sorted(subdir.glob("*.json")):
                for card in _extract_cards_from_file(json_file):
                    url_key = _source_url_dedup_key(card.get("source_url", ""))
                    if not url_key or url_key in seen_urls:
                        continue
                    seen_urls.add(url_key)
                    new_cards.append(card)

    if facet and facet.strip():
        for c in new_cards:
            c["facet"] = facet.strip()

    # Merge
    skeleton["evidence_cards"] = existing_cards + new_cards

    # Write back
    collected_json.parent.mkdir(parents=True, exist_ok=True)
    with collected_json.open("w", encoding="utf-8") as f:
        json.dump(skeleton, f, ensure_ascii=False, indent=2)

    summary = {
        "status": "ok",
        "cards_added": len(new_cards),
        "cards_total": len(skeleton["evidence_cards"]),
        "collected_json_path": str(collected_json),
    }
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(
        description=(
            "Auto-populate evidence_cards in collected_<topic>.json from "
            "raw mat_sn_* tool outputs saved in _tmp/tool_outputs/."
        )
    )
    ap.add_argument(
        "--collected_json",
        default=None,
        help=(
            "Path to collected_<topic>.json skeleton (produced by run_survey.py). "
            "Will be created if it does not exist (requires --topic)."
        ),
    )
    ap.add_argument(
        "--tool_outputs_dir",
        default=None,
        help=(
            "Directory containing mat_sn_* subdirectories of auto-saved outputs. "
            "Defaults to _tmp/tool_outputs/ relative to CWD or to the collected_json parent's _tmp/."
        ),
    )
    ap.add_argument(
        "--topic",
        default=None,
        help="Survey topic (used to create skeleton if --collected_json does not exist).",
    )
    ap.add_argument(
        "--facet",
        default=None,
        help=(
            "Assign this facet to all newly collected cards (must be one of skeleton facets). "
            "Recommended when this batch of retrieval targets a single facet."
        ),
    )
    args = ap.parse_args()

    # Resolve collected_json path
    if args.collected_json:
        collected_json = Path(args.collected_json)
    elif args.topic:
        # Auto-derive path from topic
        safe = re.sub(r"[^\w\s\-]", "", args.topic, flags=re.UNICODE)
        safe = safe.strip().replace(" ", "_")[:80] or "survey"
        collected_json = Path("_tmp") / "surveys" / f"collected_{safe}.json"
    else:
        ap.error("Provide --collected_json or --topic.")

    # Resolve tool_outputs_dir
    if args.tool_outputs_dir:
        tool_outputs_dir = Path(args.tool_outputs_dir)
    else:
        # Search: sibling _tmp/tool_outputs relative to collected_json, then CWD
        candidates = [
            collected_json.parent.parent / "tool_outputs",  # _tmp/tool_outputs
            Path("_tmp") / "tool_outputs",
        ]
        tool_outputs_dir = next(
            (p for p in candidates if p.exists()), candidates[0]
        )

    summary = collect_evidence(
        collected_json, tool_outputs_dir, topic=args.topic, facet=args.facet
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary.get("status") == "error":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
