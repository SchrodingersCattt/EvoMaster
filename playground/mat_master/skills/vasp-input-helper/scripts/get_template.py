"""Get VASP calculation template for a task type.

Usage:
  python get_template.py --task-type relax
  python get_template.py --task-type band_structure
"""

import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="VASP task template")
    parser.add_argument(
        "--task-type",
        "-t",
        required=True,
        help="scf, relax, band_structure, dos, md, hybrid, phonon, gw, optical, neb",
    )
    args = parser.parse_args()

    templates_dir = (
        Path(__file__).resolve().parent.parent
        / "data"
        / "vasp_wiki"
        / "knowledge"
        / "task_templates"
    )
    potcar_path = (
        Path(__file__).resolve().parent.parent
        / "data"
        / "vasp_wiki"
        / "knowledge"
        / "potcar_recommend.json"
    )

    task_type = args.task_type.strip().lower()
    template_path = templates_dir / f"{task_type}.json"

    if not template_path.exists():
        available = [p.stem for p in templates_dir.glob("*.json")]
        print(f"Unknown task type '{task_type}'.")
        print(f"Available: {', '.join(sorted(available))}")
        return

    with open(template_path) as f:
        template = json.load(f)

    print(json.dumps(template, indent=2, ensure_ascii=False))

    # Append POTCAR general rules
    if potcar_path.exists():
        with open(potcar_path) as f:
            potcar_db = json.load(f)
        rules = potcar_db.get("general_rules", [])
        if rules:
            print("\n## POTCAR general rules:")
            for r in rules:
                print(f"  - {r}")


if __name__ == "__main__":
    main()
