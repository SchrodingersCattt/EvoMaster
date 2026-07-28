---
name: nmr-analysis
description: Use for NMR database structure search, 1H/13C chemical-shift prediction from SMILES or molecular files, or reverse structure prediction constrained by NMR spectra and formula.
---

# NMR Analysis

Use this skill for the three deployed NMR capabilities: database search,
structure-to-spectrum prediction, and spectrum-to-structure prediction. The CLI
calls the existing NMR inference service directly rather than using MCP.

## Run

Pass shift lists as JSON arrays. At least one of `--h-shifts` and `--c-shifts`
is required for search and reverse prediction.

```bash
# Search the NMR database for candidate structures.
python3 "${SKILL_DIR}/scripts/nmr_client.py" search \
  --h-shifts '[2.1, 2.1, 7.64]' \
  --c-shifts '[30.0, 205.0]' \
  --allowed-elements C,H,O,N \
  --topk 10 \
  --output-dir /absolute/path/to/nmr-results

# Predict NMR shifts from SMILES and optionally compare to experimental shifts.
python3 "${SKILL_DIR}/scripts/nmr_client.py" predict \
  --smiles 'CCO' \
  --h-shifts '[1.2, 3.6]' \
  --output-dir /absolute/path/to/nmr-results

# Molecular files can be .xyz, .pdb, .sdf, .mol, or .mol2.
python3 "${SKILL_DIR}/scripts/nmr_client.py" predict \
  --molecule-file /absolute/path/to/molecule.sdf \
  --output-dir /absolute/path/to/nmr-results

# Reverse-predict candidate structures from an NMR spectrum.
python3 "${SKILL_DIR}/scripts/nmr_client.py" reverse-predict \
  --h-shifts '[2.1, 2.1, 7.64]' \
  --c-shifts '[30.0, 205.0]' \
  --formula C6H12O \
  --allowed-elements C,H,O \
  --topk 10 \
  --output-dir /absolute/path/to/nmr-results
```

The CLI accepts local molecular files. It also preserves the existing support
for HTTP/HTTPS structure-file URLs; only use URLs supplied by the user or a
trusted workflow. Set `NMR_SERVICE_URL` only when deployment configuration
requires a non-default inference endpoint.

## Output

One JSON object is written to stdout. Successful results contain candidate
SMILES, scores when reference shifts were supplied, and paths for the best SVG
and XYZ structure. For multiple candidates, the output directory contains all
generated SVG and XYZ files.

The command exits nonzero with a JSON error object when input parsing, the
inference service, or artifact generation fails. Do not present an error result
as a predicted spectrum or identified structure.

## Scope boundary

This skill does not analyze IR, mass spectrometry, or generic molecular images.
