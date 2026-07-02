#!/usr/bin/env python3
"""Convert an alloy Excel spreadsheet (one row = one alloy) to the composition-JSON
format consumed by composition_json_to_mixed_npy.py.

Supported Excel layout
----------------------
The sheet must have:
  - one column per element (e.g. Fe, Ni, Co, Cr, V, Cu, Si, Al) whose values
    are molar fractions or atomic percentages (auto-detected: values > 1.5 are
    treated as percentages and divided by 100)
  - a scalar-property column (default: TEC)
  - a phase column (default: Stable Phase) with values like 'FCC_A1', 'BCC_A2',
    'HCP_A3' (surrounding quotes are stripped automatically)

Row IDs are taken from --id-col if given; otherwise auto-generated as row0000, ...

Usage
-----
  python excel_to_composition_json.py \
      --input Data_base_DFT_Thermal.xlsx \
      --sheet Sheet1 \
      --elements Fe Ni Co Cr V Cu \
      --property-col TEC \
      --phase-col "Stable Phase" \
      --out-json base_compositions.json

  # Merge with an iteration JSON:
  python excel_to_composition_json.py \
      --input Data_base_DFT_Thermal.xlsx \
      --elements Fe Ni Co Cr V Cu Si Al \
      --extra-json iter03_experimental_batch.json \
      --out-json all_compositions.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

PHASE_ALIASES = {
    "fcc_a1": "fcc", "fcc": "fcc",
    "bcc_a2": "bcc", "bcc": "bcc",
    "hcp_a3": "hcp", "hcp": "hcp",
}


def normalize_phase(raw: str) -> str:
    cleaned = str(raw).strip("' \"").lower()
    for key, value in PHASE_ALIASES.items():
        if key in cleaned:
            return value
    return "fcc"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--sheet", default="Sheet1")
    ap.add_argument("--elements", nargs="+", default=["Fe", "Ni", "Co", "Cr", "V", "Cu"])
    ap.add_argument("--property-col", default="TEC")
    ap.add_argument("--phase-col", default="Stable Phase")
    ap.add_argument("--id-col", default=None)
    ap.add_argument("--extra-json", type=Path, default=None)
    ap.add_argument("--out-json", type=Path, required=True)
    ap.add_argument("--prefix", default="row")
    args = ap.parse_args()

    df = pd.read_excel(args.input, sheet_name=args.sheet)
    df = df.dropna(subset=[args.property_col])

    present_elements = [e for e in args.elements if e in df.columns]
    if not present_elements:
        raise SystemExit(f"None of {args.elements} found. Available: {df.columns.tolist()}")

    max_val = float(df[present_elements].abs().max().max())
    is_percent = max_val > 1.5

    rows = []
    for global_idx, (_, row) in enumerate(df.iterrows()):
        composition = {}
        for elem in present_elements:
            val = float(row.get(elem, 0.0) or 0.0)
            if is_percent:
                val /= 100.0
            composition[elem] = val

        if sum(composition.values()) <= 0:
            continue

        prop = float(row[args.property_col])
        phase_raw = row.get(args.phase_col, "fcc")
        import pandas as _pd
        phase = normalize_phase(str(phase_raw) if _pd.notna(phase_raw) else "fcc")

        if args.id_col and args.id_col in df.columns:
            cell = row.get(args.id_col)
            import pandas as _pd2
            sample_id = str(cell).strip() if _pd2.notna(cell) else f"{args.prefix}{global_idx:04d}"
        else:
            sample_id = f"{args.prefix}{global_idx:04d}"

        rows.append({
            "id": sample_id,
            "composition": composition,
            args.property_col: prop,
            "phase": phase,
        })

    if args.extra_json is not None:
        extra = json.loads(args.extra_json.read_text(encoding="utf-8"))
        rows.extend(extra)
        print(f"Appended {len(extra)} rows from {args.extra_json}")

    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"Wrote {len(rows)} rows -> {args.out_json}")


if __name__ == "__main__":
    main()
