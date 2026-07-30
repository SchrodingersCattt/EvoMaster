---
name: xrd-phase-identification
description: Use for powder-XRD/PXRD parsing, multi-pattern peak features, chemistry-constrained reference phase candidate screening, CIF-derived ideal XRD simulation, or experimental/CIF PXRD comparison.
---

# PXRD Analysis and Reference Screening

Use this Skill for powder-XRD parsing, reference-database candidate screening,
CIF-to-ideal-PXRD simulation, and **experimental pattern versus CIF** comparison.
It calls a dedicated XRD REST service from the Worker runtime. No MCP server,
local reference database, or public API endpoint is used.

This Skill is **not** Pawley/Rietveld refinement, phase-fraction analysis,
structure solution, or auto-indexing. For those workflows, use `pxrd-refinement`.

## Choose a workflow

| User goal | Command | What happens |
|---|---|---|
| Raw pattern → candidate phases | `phase-id` | `parse` then `identify` |
| Already-processed CSV → candidates | `identify` | Direct screening |
| CIF → ideal PXRD stick pattern | `simulate` | Ideal Bragg sticks |
| Does a CIF explain experimental peaks? | `validate-cif` | `parse` then `compare` |
| Only parse/preprocess a raw pattern | `parse` | Standardized CSV + peaks |
| Continue with Pawley/Rietveld | handoff → `pxrd-refinement` | Use `compare` output |

## Run

### phase-id (recommended for raw → candidate screening)

```bash
python3 "${SKILL_DIR}/scripts/xrd_phase_identification.py" phase-id \
  --input /absolute/path/to/pattern.xy \
  --output-dir /absolute/path/to/xrd-results \
  --chem-include-any Fe,Ni \
  --chem-exclude C \
  --top-n 5
```

### validate-cif (experimental pattern vs reference CIF)

```bash
python3 "${SKILL_DIR}/scripts/xrd_phase_identification.py" validate-cif \
  --input /absolute/path/to/pattern.xy \
  --cif /absolute/path/to/model.cif \
  --output-dir /absolute/path/to/xrd-results \
  --radiation cu-ka1 \
  --tolerance 0.20
```

### parse (raw → standardized CSV, peak features, chart)

```bash
python3 "${SKILL_DIR}/scripts/xrd_phase_identification.py" parse \
  --input /absolute/path/to/pattern.xrdml \
  --output-dir /absolute/path/to/xrd-results \
  --profile standard
```

### identify (processed CSV → reference screening)

```bash
python3 "${SKILL_DIR}/scripts/xrd_phase_identification.py" identify \
  --input /absolute/path/to/xrd-results/pattern_trace_1_raw_data.csv \
  --output-dir /absolute/path/to/xrd-results \
  --chem-include-all O \
  --top-n 5 --show-top-n 1
```

### simulate (CIF → ideal stick PXRD)

```bash
python3 "${SKILL_DIR}/scripts/xrd_phase_identification.py" simulate \
  --cif /absolute/path/to/model.cif \
  --output-dir /absolute/path/to/xrd-results \
  --radiation cu-ka1
```

### compare (experimental + CIF → peak diagnostics)

```bash
python3 "${SKILL_DIR}/scripts/xrd_phase_identification.py" compare \
  --input /absolute/path/to/experiment.xy \
  --cif /absolute/path/to/model.cif \
  --output-dir /absolute/path/to/xrd-results \
  --tolerance 0.20
```

## Accepted input formats

- Text patterns: `.xy`, `.xye`, `.asc`, `.txt`, `.dat`, `.csv`.
- Structured formats: `.xrdml`, `.mdi`.
- `.raw` only when it is a verified two-column **text** export.

The parser detects common encodings, multi-column tables with a shared 2θ
column, comment/header rows, and common delimiters. Multi-trace datasets are
automatically split; use `--trace-ids id1,id2` to select specific traces.

Unrecognized binary `.raw` files are rejected. Export them as XY or CSV first.

## Output

Every command prints one JSON object to stdout and exits nonzero on failure.
All artifacts are written under the explicit `--output-dir`:

- `parse`: per-trace raw-data CSV, peak-feature CSV, ECharts JSON, and a
  parse manifest.
- `identify`: top/candidate-phase CSVs and comparison chart.
- `simulate`: ideal-stick CSV with 2θ, intensity, d-spacing, hkl.
- `compare`: peak-match CSV, overlay chart, and a refinement-handoff manifest.
- `phase-id`: all `parse` + `identify` artifacts.
- `validate-cif`: all `parse` + `compare` artifacts.

## Chemistry constraints (identify / phase-id)

- `--chem-include-any Fe,Ni` — candidate has at least one listed element.
- `--chem-include-all O` — candidate has every listed element.
- `--chem-exclude C` — candidate has none of the listed elements.

Constraints are independent and can be combined.

## Radiation and wavelength (simulate / compare / validate-cif)

Default is Cu Kα1, λ = 1.540598 Å. Override with:

- `--radiation cu-ka1 | cu-ka2 | cu-ka | cu-kb`
- `--wavelength <value in Å>` (takes precedence over named radiation)

## Scientific boundaries

- `heuristic_rank_score` is a **screening rank**, not confidence, probability,
  or phase fraction.
- `compare` reports peak-position diagnostics; it does **not** fit background,
  refine cells, or calculate phase fractions.
- Do not claim a candidate list solves a multiphase mixture.
- For Pawley, Rietveld, or quantitative phase analysis, use the handoff from
  `compare` and switch to `pxrd-refinement`.

## Limits

- Aggregate upload per request: 64 MiB (including multipart framing).
- Single request timeout: 180 seconds.
- Response artifact limit: 32 MiB inline content.

## Service configuration

The service URL and identity headers (`X-User-Id`, `X-Org-Id`) are injected by
the Worker runtime from the authenticated session. They must never be requested
from or exposed to the end user. There is no `--service-url`, `--user-id`, or
`--org-id` CLI option.

If either environment variable is missing, the CLI fails before sending any
request, with a clear error indicating the Worker runtime must be configured.
