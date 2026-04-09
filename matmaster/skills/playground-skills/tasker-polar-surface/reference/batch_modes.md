# Batch Processing Modes

All three scripts (`build_slab_tasker_fix.py`, `check_slab_tasker.py`, `add_adsorbate_batch.py`) support three usage modes.

## Choosing a mode

| Condition | Mode |
|-----------|------|
| All structures share same params (miller, layers, vacuum, …) | Multi-file + shared params |
| Each structure needs different params | `--batch` JSON config |
| Single structure | Single-file (default) |

---

## build_slab_tasker_fix.py

### Single-file
```bash
python build_slab_tasker_fix.py -i bulk.cif -m 1 0 0 -L 8 -v 15 -o slab.vasp
```

### Multi-file + shared params
```bash
python build_slab_tasker_fix.py -i bulk1.cif bulk2.cif bulk3.vasp \
    -m 1 0 0 -L 8 -v 15 --output-dir ./slabs/
```
All files share the same miller, layers, vacuum. Output auto-named `{stem}_slab{ext}`.

### Batch JSON (`--batch`)
```bash
python build_slab_tasker_fix.py --batch config.json
```
config.json format:
```json
[
  {"input": "bulk1.cif", "miller": [1,0,0], "repeat_layers": 8, "vacuum": 15, "output": "slab1.vasp"},
  {"input": "bulk2.cif", "miller": [1,1,0], "thickness": 18, "vacuum": 20, "output": "slab2.cif", "charge": "Zn:2,O:-2"}
]
```

---

## check_slab_tasker.py

### Multi-file + shared params
```bash
python check_slab_tasker.py --file slab1.vasp slab2.cif slab3.vasp --tasker_type 3
```

### Batch JSON
```bash
python check_slab_tasker.py --batch check_config.json
```
```json
[
  {"file": "slab1.vasp", "tasker_type": 3, "formula": "ZnO", "miller": "0 0 0 1"},
  {"file": "slab2.cif", "tasker_type": 1}
]
```

---

## add_adsorbate_batch.py

### Single-file
```bash
python add_adsorbate_batch.py -s slab.vasp -a CO.xyz --shift "0.5,0.5" --height 2.0 -o slab_CO.cif
```

### Multi-slab + shared params
```bash
python add_adsorbate_batch.py -s slab1.vasp slab2.cif slab3.vasp \
    -a CO.xyz --shift ontop --height 1.8 --output-dir ./ads_slabs/
```

### Batch JSON
```bash
python add_adsorbate_batch.py --batch ads_config.json
```
```json
[
  {"surface": "slab1.vasp", "adsorbate": "CO.xyz", "shift": [0.5, 0.5], "height": 2.0, "output": "slab1_CO.cif"},
  {"surface": "slab2.vasp", "adsorbate": "OH.xyz", "shift": "ontop", "height": 1.5, "output": "slab2_OH.cif"}
]
```

---

## Common rules

- **Output**: JSON summary `{"results": [...]}` with per-entry `success` flag.
- **Exit code**: 0 = all success/compliant, 1 = any failure. One failure does not block others.
- **Linked pipeline** (build → check → adsorbate): see `reference/ht_screening_pipeline.md`.
