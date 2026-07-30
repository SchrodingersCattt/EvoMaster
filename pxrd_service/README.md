# XRD Service

Standalone FastAPI service for PXRD parsing, reference-database candidate
screening, CIF-derived ideal-pattern simulation, and experimental/CIF peak
comparison. MatMaster Workers call it through the `xrd-phase-identification`
Skill; the API process does not run these calculations.

## Build and run

Build with `pxrd_service/` as the Docker context:

```bash
docker build -t pxrd-service:local pxrd_service/
docker run --rm -p 8010:8010 pxrd-service:local
```

The container listens on port `8010`. Deploy it behind an approved internal
platform service endpoint, then configure Workers with `PXRD_SERVICE_URL`. Do
not expose a hard-coded public IP in the Skill.

## Readiness

`GET /health` confirms that the bundled HDF5 reference database exists, opens
successfully, and returns the service version plus its SHA-256. It deliberately
does not expose a container filesystem path.

## Endpoints

| Endpoint | Capability |
|---|---|
| `POST /v1/xrd/parse` | Parse one or more PXRD traces and return standardized CSV, peak features, ECharts options, and a manifest. |
| `POST /v1/xrd/identify` | Screen processed CSV traces against the HDF5 reference database with element constraints. |
| `POST /v1/xrd/simulate` | Create ideal PXRD Bragg sticks from a CIF. |
| `POST /v1/xrd/compare` | Compare experimental trace(s) with an ideal CIF-derived pattern and emit a refinement handoff manifest. |

Artifacts are request-scoped and returned as text payloads. The Worker-side CLI
writes them to the run workspace. Candidate scores are heuristic screening
ranks, never probability/confidence or phase-fraction results.

## Limits

- Upload limit: 64 MiB per file.
- Binary vendor `.raw` formats are intentionally rejected; only validated
  two-column text `.raw` files are accepted.
- Pawley/Rietveld/auto-indexing and quantitative phase analysis are out of
  scope. Use `pxrd-refinement` for those workflows.
