"""Look up VASP INCAR tag details from structured index.

Usage:
  python get_tag_info.py --tags "ALGO,ISMEAR,SIGMA"
"""

import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="VASP INCAR tag lookup")
    parser.add_argument("--tags", "-t", required=True, help="Comma-separated tag names")
    args = parser.parse_args()

    index_path = Path(__file__).resolve().parent.parent / "data" / "vasp_wiki" / "knowledge" / "tags_index.json"
    with open(index_path) as f:
        db = json.load(f)

    tag_names = [t.strip().upper() for t in args.tags.split(",") if t.strip()]
    not_found = []

    for tag in tag_names:
        info = db.get(tag)
        if info:
            print(f"## {tag}")
            for key in ["default", "values", "type", "unit", "category", "brief", "notes"]:
                if key in info:
                    val = info[key]
                    if isinstance(val, list):
                        val = ", ".join(str(v) for v in val)
                    print(f"  {key}: {val}")
            print()
        else:
            not_found.append(tag)

    if not_found:
        print(f"Not found: {', '.join(not_found)}")
        print("Try: python scripts/search_wiki.py --query <tag_name>")


if __name__ == "__main__":
    main()
