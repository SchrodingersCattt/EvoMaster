---
name: xrd-phase-identification
description: Use for powder-XRD raw-file parsing, peak features, reference-database phase identification, chemistry-constrained phase matching, or XRD comparison-chart export.
---

# XRD Phase Identification

Use this skill for powder-XRD **phase identification**, not Pawley/Rietveld
refinement. Its CLI uploads the input to the deployed XRD REST service, which
holds the reference database and phase-matching implementation. No MCP server is
used.

## Run

Parse a supported raw pattern (`.xrdml`, `.xy`, `.asc`, `.txt`, `.mdi`, or
`.raw`) into the required processed CSV:

```bash
python3 "${SKILL_DIR}/scripts/xrd_phase_identification.py" parse \
  --input /absolute/path/to/pattern.xrdml \
  --output-dir /absolute/path/to/xrd-results
```

Identify phases from either that generated CSV or another CSV containing exactly
the `2Theta` and `Intensity` columns:

```bash
python3 "${SKILL_DIR}/scripts/xrd_phase_identification.py" identify \
  --input /absolute/path/to/xrd-results/pattern_raw_data.csv \
  --output-dir /absolute/path/to/xrd-results \
  --chem-include-any Fe,Ni \
  --chem-include-all O \
  --chem-exclude C \
  --top-n 5 \
  --show-top-n 1
```

Do not pass an unprocessed raw pattern directly to `identify`. For Pawley,
Rietveld, or auto-indexing workflows, use `pxrd-refinement` instead.

## Output

Both subcommands emit one JSON object to stdout. `parse` returns:

- the processed raw-data CSV (`2Theta`, `Intensity`, `Baseline`);
- peak-feature CSV and optional ECharts configuration.

`identify` returns ranked `top_phases`, plus top/all-phase CSV files and an
ECharts comparison-chart JSON. It exits nonzero on invalid input or analysis
errors. All artifacts are written under the explicit `--output-dir`.

## Service configuration

The default XRD service is `http://221.194.152.152:8010`. Override it only for
an approved deployment with `XRD_SERVICE_URL`; do not accept a service URL from
an end user. The local Skill package does not contain the reference HDF5 database.
