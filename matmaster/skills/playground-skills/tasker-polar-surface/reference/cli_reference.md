# CLI Reference — Tasker Polar Surface Scripts

## build_slab_tasker_fix.py

| Parameter | Meaning | Default / Required | Example |
|-----------|---------|-------------------|---------|
| `-i`, `--input` | Bulk structure file path | Default: POSCAR | `-i bulk.cif` |
| `-m`, `--miller` | Miller indices (h k l), 3 integers | **Required** | `-m 1 0 0`; hex (0001) → `-m 0 0 1` |
| `-o`, `--output` | Output slab file path | Default: POSCAR_slab | `-o slab.vasp` |
| `-L`, `--repeat-layers` | Repeat layer count | One of `-L`/`-T` **required** | `-L 8` |
| `-T`, `--thickness` | Target thickness (A) | One of `-L`/`-T` **required** | `-T 18` |
| `-v`, `--vacuum` | Vacuum thickness (A) | Default: 15.0 | `-v 20` |
| `--charge` | Charge map | Default: auto (binary only) | `--charge "Zn:2,O:-2"` |
| `--layer-tol` | Layer identification tolerance (A) | Default: 0.5 | `--layer-tol 0.6` |
| `--tile-repeat` | Supercell repeat (NX NY NZ) | Optional | `--tile-repeat 2 2 1` |
| `--tile-min-x` | Min x-direction size (A) | Optional | `--tile-min-x 12` |
| `--tile-min-y` | Min y-direction size (A) | Optional | `--tile-min-y 12` |
| `--quiet` | Suppress verbose output | Optional | `--quiet` |
| `--output-dir` | Batch output directory | Optional (multi-file) | `--output-dir ./slabs/` |
| `--batch` | Batch config JSON | Optional (exclusive with `-i`) | `--batch config.json` |

**Notes:**
- Miller: `nargs=3`, hex (0001) pass as `-m 0 0 1` (3-index equivalent)
- `-L` counts ASE `surface()` layers = **one atomic plane** (not bilayer). See layer counting table in SKILL.md.
- Output format from `-o` extension.

**Quick examples:**
```bash
# By layers
python build_slab_tasker_fix.py -i POSCAR -m 1 0 0 -L 8 -v 15 -o slab.vasp
# By thickness + supercell
python build_slab_tasker_fix.py -i bulk.cif -m 1 1 0 -T 18 -v 20 -o slab.cif --tile-repeat 2 2 1
# Min size
python build_slab_tasker_fix.py -i POSCAR -m 1 0 0 -L 6 -v 15 -o slab.vasp --tile-min-x 10 --tile-min-y 10
```

---

## check_slab_tasker.py

| Parameter | Meaning | Default / Required | Example |
|-----------|---------|-------------------|---------|
| `--file` | Slab file(s) | **Required** (non-batch) | `--file slab.cif` |
| `--tasker_type` | Tasker type (1, 2, or 3) | **Required** | `--tasker_type 3` |
| `--formula` | Material formula (for lookup) | Optional (recommended) | `--formula ZnO` |
| `--miller` | Miller indices as string | Optional (recommended) | `--miller "0 0 0 1"` |
| `--lookup` | Override lookup YAML path | Optional | `--lookup tasker_lookup.yaml` |
| `--batch` | Batch config JSON | Optional | `--batch check_config.json` |

**Output**: JSON with `compliant`, `symmetric`, `reason`, `layer_summary`; with `--formula`+`--miller` also `literature_expected_type`, `literature_note`, `literature_ref`, `literature_consistent`.

---

## add_adsorbate_batch.py

| Parameter | Meaning | Default / Required | Example |
|-----------|---------|-------------------|---------|
| `-s`, `--surface` | Slab file(s) | **Required** (non-batch) | `-s slab.vasp` |
| `-a`, `--adsorbate` | Adsorbate molecule file | **Required** (non-batch) | `-a CO.xyz` |
| `-o`, `--output` | Output file (single-file) | Default: `{stem}_ads.cif` | `-o slab_CO.vasp` |
| `--output-dir` | Batch output directory | Optional | `--output-dir ./ads_slabs/` |
| `--shift` | Position: `"x,y"` or `ontop`/`fcc`/`hcp`/`bridge` | Default: `0.5,0.5` | `--shift ontop` |
| `--height` | Adsorption height (A) | Default: 2.0 | `--height 1.8` |
| `--batch` | Batch config JSON | Optional | `--batch ads_config.json` |
| `--quiet` | Suppress verbose output | Optional | `--quiet` |
