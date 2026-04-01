"""Get POTCAR pseudopotential recommendations for elements.

Usage:
  python get_potcar.py --elements "Fe,O"
  python get_potcar.py --elements "Mo,S" --for-gw
"""

import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="VASP POTCAR recommendation")
    parser.add_argument("--elements", "-e", required=True, help="Comma-separated elements")
    parser.add_argument("--for-gw", action="store_true", help="Use GW pseudopotentials")
    args = parser.parse_args()

    potcar_path = Path(__file__).resolve().parent.parent / "data" / "vasp_wiki" / "knowledge" / "potcar_recommend.json"
    with open(potcar_path) as f:
        db = json.load(f)

    recs = db.get("recommendations", {})
    magnetic = set(db.get("magnetic_elements", []))
    heavy_soc = set(db.get("heavy_elements_soc", []))

    elements = [e.strip() for e in args.elements.split(",") if e.strip()]
    max_enmax = 0
    potcar_order = []

    for elem in elements:
        rec = recs.get(elem, {})
        if not rec:
            print(f"  {elem}: NOT FOUND — check element symbol")
            continue

        pot_name = rec.get("gw", rec.get("default", elem)) if args.for_gw else rec.get("default", elem)
        enmax = rec.get("enmax", 0)
        max_enmax = max(max_enmax, enmax)
        potcar_order.append(pot_name)

        flags = []
        if elem in magnetic:
            flags.append("MAGNETIC")
        if elem in heavy_soc:
            flags.append("SOC")
        flag_str = f"  [{', '.join(flags)}]" if flags else ""
        note = f"  ({rec['note']})" if "note" in rec else ""
        print(f"  {elem:3s} -> {pot_name:14s}  ENMAX={enmax:4d} eV{flag_str}{note}")

    print(f"\n  Max ENMAX = {max_enmax} eV")
    print(f"  Suggested ENCUT (1.3x) = {int(1.3 * max_enmax)} eV")
    print(f"\nPOTCAR command:")
    cat_parts = " \\\n    ".join(f"$VASP_PP_PATH/PBE/{p}/POTCAR" for p in potcar_order)
    print(f"  cat {cat_parts} > POTCAR")


if __name__ == "__main__":
    main()
