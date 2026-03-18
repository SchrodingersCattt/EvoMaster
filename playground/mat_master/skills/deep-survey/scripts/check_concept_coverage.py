#!/usr/bin/env python3
"""
Thin CLI wrapper for survey concept-coverage check.

Production finish gate uses survey_contract.check_concept_coverage_workspace()
via ToolGuard; this script is for manual/offline runs and debugging.
Reads key_concepts from collected_*.json (schema_version 2); files without
key_concepts are skipped (pass).
"""


import json
import sys
from pathlib import Path

# Ensure script dir is on path when run as CLI from any CWD
_script_dir = Path(__file__).resolve().parent
if str(_script_dir) not in sys.path:
    sys.path.insert(0, str(_script_dir))
from survey_contract import check_concept_coverage_workspace


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(
        description='Check that collected evidence covers key concepts (from JSON key_concepts).'
    )
    ap.add_argument(
        '--workspace',
        default='.',
        help='Workspace root; will look for _tmp/surveys/collected_*.json',
    )
    ap.add_argument(
        '--out',
        default=None,
        help='Write result JSON here (default: _tmp/surveys/concept_coverage.json under workspace)',
    )
    ap.add_argument(
        '--min_per_concept',
        type=int,
        default=1,
        help='Minimum cards per key concept (default 1)',
    )
    args = ap.parse_args()
    workspace = Path(args.workspace)
    surveys_dir = workspace / '_tmp' / 'surveys'
    if not surveys_dir.exists():
        print(
            json.dumps(
                {'ok': True, 'reason': 'No surveys dir; skip.'}, ensure_ascii=False
            )
        )
        return
    passed, reason = check_concept_coverage_workspace(
        workspace, min_per_concept=args.min_per_concept
    )
    out_path = Path(args.out) if args.out else surveys_dir / 'concept_coverage.json'
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps({'ok': passed, 'reason': reason}, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    print(json.dumps({'ok': passed, 'reason': reason}, ensure_ascii=False))
    if not passed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
