# Complex Structure Recipes — Script-First Patterns

> **Rule**: If a dedicated build script exists for a structure type, use it FIRST.
> Do NOT write custom Python/ASE/pymatgen build scripts from scratch — they waste turns, introduce bugs, and cause timeouts.
> Each recipe below is designed to complete in 1–2 agent turns.

---

## γ-Al₂O₃ (Defect Spinel)

γ-alumina has a disordered defect-spinel structure with specific Al vacancy patterns. `build_gamma_al2o3.py` handles this automatically.

### Recipe (2 turns)

**Turn 1 — Build (local, instant):**
```bash
python build_gamma_al2o3.py -o gamma_al2o3.cif
```
Verify immediately with `assess_structure.py --file gamma_al2o3.cif` (expect 3D-Bulk, Al₂O₃ composition).

**Turn 2 — Relax with MLIP (Bohrium):**
Copy `optimize_structure.py` + `_calculator.py` from **mlips** skill to working dir, then submit:
```bash
python optimize_structure.py --structure gamma_al2o3.cif --model DPA3.1-3M --relax-cell --fmax 0.05
```
Save `gamma_al2o3_optimized.cif` immediately after job completes. Target: max force < 0.1 eV/Å.

### Anti-patterns
- ❌ Writing custom spinel/vacancy code → always wrong or slow
- ❌ Attempting `build_bulk_structure_by_wyckoff` for γ-Al₂O₃ → vacancy pattern not expressible via Wyckoff alone
- ❌ Skipping relaxation → γ-Al₂O₃ tasks typically require relaxed structure

---

## Organic Molecular Crystal Hydrogenation

Adding H atoms to complete valence on C/N in organic molecular crystals (NOT semiconductor surfaces).

### Recipe (1 turn)

**Option A — OpenBabel (preferred, 1 command):**
```bash
obabel input.cif -O output.cif -h
```
If OpenBabel is not installed: `pip install openbabel-wheel` or `conda install -c conda-forge openbabel`.

**Option B — Inline Python (fallback):**
Use pymatgen or ASE to place H at standard bond lengths:
- C-H ≈ 1.09 Å (sp3: tetrahedral 109.5°; sp2: trigonal planar 120°)
- N-H ≈ 1.01 Å

After either method, verify: `assess_structure.py --file output.cif` — check that formula matches expected hydrogenated composition.

### Anti-patterns
- ❌ Using `passivate_surface.py` → designed for semiconductor surface dangling bonds, NOT molecular crystals
- ❌ Hydrogenating carbonyl/ester O atoms → only C and N with incomplete valence need H
- ❌ Writing >20 lines of custom hydrogenation code → use OpenBabel

---

## General Principles

1. **Check scripts/ first**: Before writing ANY structure manipulation code, scan the loaded skill's `scripts/` directory. If a dedicated script exists, use it.
2. **Save immediately**: After building or receiving any structure file, save it to the workspace. Partial credit > no credit.
3. **Verify once**: Run `assess_structure.py` once per structure. Don't add extra verification passes.
4. **Time budget**: Complex structures should take ≤2 turns (build + relax/verify). If you're on turn 3+ for the same structure, you're likely writing custom code unnecessarily — stop and check if a script exists.
