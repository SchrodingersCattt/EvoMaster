# High-Throughput Adsorption Screening Pipeline

End-to-end pipeline for batch adsorption screening: multiple surfaces x multiple adsorbates x multiple sites.

## Step 1: Batch Slab Generation
```bash
python build_slab_tasker_fix.py --batch slab_config.json
```
`slab_config.json` lists each (material, miller, layers, vacuum) combination.

## Step 2: Batch Slab Validation
```bash
python check_slab_tasker.py --batch check_config.json
```
Filter for `compliant: true` slabs. Report non-compliant but do not block pipeline.

## Step 3: Adsorption Site Enumeration

**Option A** — MCP `mat_sg_build_surface_adsorbate` with `enumerate_all=true` (single slab, discovers all sites):
- Best for: detailed single-surface studies needing all unique sites

**Option B** — `add_adsorbate_batch.py --batch` with explicit site list (batch-friendly):
```json
[
  {"surface": "slab_Cu111.vasp", "adsorbate": "CO.xyz", "shift": "ontop", "height": 2.0, "output": "Cu111_CO_ontop.cif"},
  {"surface": "slab_Cu111.vasp", "adsorbate": "CO.xyz", "shift": "fcc", "height": 2.0, "output": "Cu111_CO_fcc.cif"},
  {"surface": "slab_Cu111.vasp", "adsorbate": "CO.xyz", "shift": "hcp", "height": 2.0, "output": "Cu111_CO_hcp.cif"},
  {"surface": "slab_Cu111.vasp", "adsorbate": "CO.xyz", "shift": "bridge", "height": 2.0, "output": "Cu111_CO_bridge.cif"},
  {"surface": "slab_Ag111.vasp", "adsorbate": "CO.xyz", "shift": "ontop", "height": 2.0, "output": "Ag111_CO_ontop.cif"}
]
```
Best for: HT screening across multiple surfaces with known standard sites.

**Common sites by surface type:**
| Surface | Sites |
|---------|-------|
| FCC(111) | ontop, fcc, hcp, bridge |
| FCC(100) | ontop, bridge, hollow |
| BCC(110) | ontop, short-bridge, long-bridge, hollow |

## Step 4: Calculation (if required)
- MLIP: use `calculate_adsorption.py` from **mlips** skill, or write ONE consolidated script computing E_ads = E(slab+ads) - E(slab) - E(gas).
- DFT: prepare ABACUS/DFT inputs for each config, submit as batch Bohrium jobs.

## Orchestration Rules
1. **Breadth-first**: generate ALL slabs → check ALL → add ALL adsorbates. Maximizes throughput.
2. **Fail-forward**: one failed slab does not block others. Track failures, report at end.
3. **Naming**: use `{material}_{miller}_{adsorbate}_{site}.cif` for traceability.
4. **Token economy**: use `--batch config.json` instead of sequential single calls.
