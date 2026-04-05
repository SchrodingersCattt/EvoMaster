#!/usr/bin/env python3
"""
DEPRECATED: Backfill facet by keyword rules for legacy collected_*.json.

Preferred path: use collect_evidence.py --facet <facet> when ingesting so
cards get the correct facet at ingest time. This script remains for old
workspaces or one-off repair only. Not part of the recommended production flow.
"""

import argparse
import json
from pathlib import Path

# Keywords (lowercase) that suggest a facet. First match wins.
FACET_KEYWORDS: list[tuple[str, str]] = [
    ('Reviews / state of the art', 'review'),
    ('Reviews / state of the art', 'state of the art'),
    ('Mechanism', 'mechanism'),
    ('Mechanism', 'polarization'),
    ('Mechanism', 'polar'),
    ('Mechanism', 'dipole'),
    ('Methods', 'method'),
    ('Methods', 'calculation'),
    ('Methods', 'dft'),
    ('Methods', 'simulation'),
    ('Definition', 'definition'),
    ('Definition', 'define'),
    ('Caveats', 'limitation'),
    ('Caveats', 'caveat'),
]


def _assign_facet_for_text(text: str, facets: list[str]) -> str:
    """Return best-matching facet for text, or facets[0] if no match."""
    if not text or not facets:
        return facets[0] if facets else ''
    lower = text.lower()
    for facet, keyword in FACET_KEYWORDS:
        if facet in facets and keyword in lower:
            return facet
    return facets[0] if facets else ''


def assign_facets(collected_path: Path) -> dict:
    """
    Load collected JSON, assign facet to each evidence_card, write back.
    Returns summary dict with counts.
    """
    if not collected_path.exists():
        return {'status': 'error', 'message': f"File not found: {collected_path}"}
    try:
        data = json.loads(collected_path.read_text(encoding='utf-8'))
    except Exception as e:
        return {'status': 'error', 'message': str(e)}
    if not isinstance(data, dict):
        return {'status': 'error', 'message': 'Invalid JSON structure'}
    facets = data.get('facets') or []
    cards = data.get('evidence_cards') or []
    if not isinstance(cards, list):
        return {'status': 'error', 'message': 'evidence_cards is not a list'}
    assigned = 0
    for card in cards:
        if not isinstance(card, dict):
            continue
        current = (card.get('facet') or '').strip()
        if current and current in facets:
            continue
        text = f"{card.get('source_title') or ''} {card.get('claim') or ''}"
        facet = _assign_facet_for_text(text, facets)
        card['facet'] = facet
        assigned += 1
    collected_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    return {
        'status': 'ok',
        'assigned': assigned,
        'cards_total': len(cards),
        'collected_json_path': str(collected_path),
    }


def main() -> None:
    import warnings

    warnings.warn(
        'assign_facet.py is deprecated; use collect_evidence.py --facet when ingesting.',
        DeprecationWarning,
        stacklevel=1,
    )
    ap = argparse.ArgumentParser(
        description='(Deprecated) Assign facet to evidence_cards by keyword rules. Prefer collect_evidence.py --facet.'
    )
    ap.add_argument(
        '--collected_json',
        required=True,
        help='Path to collected_<topic>.json',
    )
    args = ap.parse_args()
    result = assign_facets(Path(args.collected_json))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get('status') != 'ok':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
