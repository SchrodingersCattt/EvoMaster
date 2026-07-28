---
name: electron-microscopy-analysis
description: Use for SEM/TEM image particle recognition, scale-bar-based size measurement, morphology statistics, particle CSV export, or annotated overlay generation.
---

# Electron Microscopy Analysis

Use this skill only for particle recognition and measurement from SEM/TEM images. It
calls the deployed EM recognition service directly; it does not perform image
analysis locally.

## Run

```bash
python3 "${SKILL_DIR}/scripts/analyze_em.py" \
  --image /absolute/path/to/image.tif \
  --output-dir /absolute/path/to/em-results
```

The input must be a readable local image (`.jpg`, `.png`, `.tif`, or another
format accepted by the recognition service). Use an explicit, empty or dedicated
`--output-dir`; the command writes `particles.csv` and `overlay.png` there.

Set `EM_SERVICE_URL` only when the deployment uses a non-default inference
endpoint. Do not pass service URLs through user-controlled CLI arguments.

## Output

The command writes one JSON object to stdout. On success it includes:

- scale-bar conversion (`nm_per_pixel`), particle counts, and morphology stats;
- a compact sample of up to five particles;
- absolute paths to `particles.csv` and the annotated `overlay.png`.

It exits nonzero and emits `{"status": "error", "error": "..."}` on input,
service, or artifact-generation failure. Do not treat a failed command as an
analysis result.

## Scope boundary

This is not an XRD or general image-plotting skill. For powder-XRD phase
identification, use `xrd-phase-identification`.
