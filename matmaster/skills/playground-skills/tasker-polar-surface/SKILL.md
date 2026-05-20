---
name: tasker-polar-surface
description: "Use ONLY for ionic, heterovalent, or known-polar surfaces needing Tasker classification, dipole compensation, or symmetric termination (LiCoO2, ZnO 0001, MgO 111, ABO3 001, fluorite 111). Do NOT use for ordinary metallic/covalent slabs such as Cu, Pt, Au, Si, Ge, graphene."
depends_on: mcp-mat-doc
---

<!-- multi-server: mat_sg, mat_sn, mat_doc -->

# Tasker Polar Surface Skill

When cutting a surface slab from a bulk crystal (especially ionic or heterovalent), apply **Tasker's polar surface theory** so the slab is non-polar or correctly handled. Otherwise the cut may be polar and physically unreasonable (e.g. diverging surface energy for Type 3).

## Tasker's three surface types

| Type | Perpendicular dipole in repeat unit | Action |
|------|-------------------------------------|--------|
| **Type 1** | None — each layer is charge-neutral along the normal | Safe to cut; no special handling. |
| **Type 2** | Zero in the repeat unit, but stacking can create a dipole across the slab | Use **symmetric termination** or choose slab thickness so the two surfaces cancel the dipole. |
| **Type 3** | Non-zero — each repeat has a net dipole | Polar; naive cut is unstable. Use **symmetric slab** (same termination on both sides), passivation, or note that reconstruction/charge transfer may stabilize. |

- **Type 1**: e.g. rocksalt (100): alternating cation/anion in-plane → neutral layers.
- **Type 2**: Repeat has no net dipole, but the way layers stack can make a finite slab polar unless symmetric.
- **Type 3**: Alternating layers of opposite charge (e.g. O- and Zn-terminated ZnO basal); classical ionic model gives diverging surface energy. Stabilization in practice: reconstruction, adsorbates, or charge transfer.

## Workflow: unified build path for all types

1. **Pre-classify (provisional)** the requested (hkl): Type 1, 2, or 3.
   - Prefer **reference/tasker_lookup.yaml** and **reference.md**.
   - If (formula, miller) is not in lookup, do literature search first (see §2.1) and set a **provisional** type.
   - This pre-classification is for choosing build path. Final judgment still depends on post-build checks.

2. **Build slab (default for Type 1/2/3)**
   - Use `build_slab_tasker_fix.py` as the default builder for all types.
   - If type is uncertain, still build first with this script, then decide with checker + literature consistency.
   - `mat_sg_build_surface_slab` is optional fallback only when explicitly needed.

3. **Post-build validate and iterate (mandatory)**
   - Run `check_slab_tasker.py` on the built slab with the provisional type.
   - If non-compliant or inconsistent with literature, adjust parameters (layers/termination/thickness) and rebuild.
   - If repeated attempts still fail, report limitation and ask user to choose manual adjustment vs temporary ignore.

## Build slab with script (Type 1/2/3)

对 **Type 1/2/3** 都优先运行本 skill 的 `build_slab_tasker_fix.py` 生成 slab（可直接扩胞），最后校验。

### Step 1 — 从文献/网页拿参数

- 用 **mat_sn_*** 或 **web-search** 搜该 (formula, 晶面)，如 "ZnO 0001 symmetric slab layers", "<formula> <hkl> slab construction vacuum"。
- 从论文/教程里提取：**层数**（或 repeat units）、**真空厚度**、以及是否推荐某种**终止面**（如上下都是 O 终止）。

#### ⚠ Layer counting — `-L` maps to ASE `surface()` layers, not bilayers

The `-L` (repeat-layers) parameter equals ASE's `layers` arg — one "layer" = **one atomic plane** (a single-species sheet). For compound crystals this is NOT one formula-unit bilayer:

| Structure type | Example | Atomic planes per bilayer | `-L` for 3 bilayers |
|----------------|---------|--------------------------|---------------------|
| Zinc blende (001) | ZnS | 2 (Zn + S) | `-L 6` |
| Rocksalt (001) | MgO | 2 (Mg + O) | `-L 6` |
| Wurtzite (0001) | ZnO | 2 (Zn + O) | `-L 6` |
| Fluorite (111) | CeO₂ | 3 (O + Ce + O) | `-L 9` |
| Perovskite (001) | SrTiO₃ | 2 (SrO + TiO₂) | `-L 6` |

**Rule**: When a task says "N-layer slab", first determine how many **atomic planes per repeat bilayer** for the given crystal and Miller index, then set `-L = N × (planes per bilayer)`. After building, **count the resulting atomic planes** in the checker output (`layer_summary`) to verify. If the count exceeds the task requirement, reduce `-L` and rebuild. For a Type 3 symmetric slab fix, the script may add ≤1 extra plane — budget for this.

### Step 2 — 运行 build_slab_tasker_fix.py 生成 slab（优先）

- **根据用户或文献需求传参**：用户明确要求的层数、厚度、真空、扩胞、电荷等，必须作为命令行参数传给脚本，不要只用默认值。
- Run `python ${SKILL_DIR}/scripts/build_slab_tasker_fix.py` with one of these argument sets:
  - by layers: `-i <bulk_path> -m <h> <k> <l> -L <repeat_layers> -v <vacuum> -o <slab_path>`
  - by thickness: `-i <bulk_path> -m <h> <k> <l> -T <thickness_A> -v <vacuum> -o <slab_path>`
- Optional tiling (用户或文献要求超胞/最小尺寸时必传):
  - fixed repeat: `--tile-repeat NX NY NZ`
  - minimum in-plane size: `--tile-min-x <A> --tile-min-y <A>`
- Optional charge override: `--charge "Zn:2,O:-2"` or JSON string (非二元或文献给出电荷时使用).
- If the script exits non-zero (e.g. "No nonpolar solution found"), **do not hide it**. Report failure to user, include reason, and ask whether to:
  1) manually adjust termination/layers and retry, or
  2) temporarily accept a polar slab and continue.
- Only when this script clearly cannot satisfy the case, fall back to ad-hoc `Bash` custom Python.

### Step 3 — 校验与分型收敛（必做）

- Run **check_slab_tasker.py** on the generated file:
  `python ${SKILL_DIR}/scripts/check_slab_tasker.py --file <slab_path> --tasker_type <provisional_type>` (and `--formula`, `--miller` if known).
  Require `compliant: true`; if not, adjust n_layers or termination (from literature) and rebuild.
- Run **retrieve-structure** `assess_structure.py` on the same file for dimensionality and sanity.
Only after both checks pass (and optionally literature/lookup consistency) proceed to finish.

### Batch processing

All three scripts support batch modes. See **`reference/batch_modes.md`** for full details.
- **Shared params**: `-i bulk1.cif bulk2.cif -m 1 0 0 -L 8 --output-dir ./slabs/`
- **Independent params**: `--batch config.json` (each entry can override everything)
- After batch build, **must** batch check. Pipeline: `build --batch` → parse success list → `check --batch` → `add_adsorbate --batch`.

## Checklist

- [ ] Did provisional classification first (lookup/literature), then finalized decision after structure check.
- [ ] Used `build_slab_tasker_fix.py` first for Type 1/2/3 (or gave clear reason for fallback).
- [ ] **After build**: Ran `check_slab_tasker.py` on the slab file; `compliant` is true. If not, re-build or warn user before finish.
- [ ] If auto-build/auto-fix fails: explicitly reported failure reason to user and asked for manual adjustment vs temporary ignore.

## Review after building (mandatory) — script + literature/lookup

**Judging by script alone can be unreliable** (e.g. layer threshold, choice of normal). Combine **structure checker script** with **literature / lookup** when possible.

### 1. Run the checker script (required)

Run `python ${SKILL_DIR}/scripts/check_slab_tasker.py` with:

- **Minimum**: `--file <path_to_slab_file> --tasker_type <1|2|3>` (same Tasker type used when building).
- **Recommended when material and surface are known**: add `--formula <formula>` and `--miller "<h k l>"` (e.g. `--formula ZnO --miller "0 0 0 1"`). The script will load `reference/tasker_lookup.yaml` and add `literature_expected_type`, `literature_note`, `literature_ref`, and `literature_consistent` to the JSON. Use this to cross-check that the chosen Tasker type matches known classifications.

The script reads the slab with pymatgen, infers layers along the surface normal, and checks: Type 1 = all layers stoichiometric; Type 2/3 = symmetric termination (top and bottom layers mirror composition).

### 2. Use reference and lookup

- **reference.md**: Tasker types and common materials (MgO, TiO2, ZnO, etc.); consult when classifying (hkl) and when interpreting script output.
- **reference/tasker_lookup.yaml**: Machine-readable (material, surface) → Tasker type + short note + ref. When you pass `--formula` and `--miller`, the checker compares with this table; if `literature_consistent` is false, re-check your classification or warn the user.
- **Existing structures**: If the user or workflow has access to structures from literature or databases (e.g. same material and surface from ICSD/COD or papers), compare layer count and termination with the built slab as an extra sanity check. Prefer script + lookup first; use DB/literature structures when available.

### 2.1 When the lookup table has no entry: search literature on the fly

If **(formula, miller)** is **not** in `tasker_lookup.yaml`, do **not** rely on script-only classification. Use MCP retrieval tools to search the literature, infer the Tasker type, then run the checker and optionally add the result to the table.

1. **Search**: Call available **mat_sn_*** search tools with queries targeting polar surface / Tasker classification for that material and surface. If a tool returns errors or is unavailable, switch to a different available search tool or method — do not retry the same failing tool. Suggested query templates:
   - `Tasker polar surface <formula> <hkl>`
   - `<formula> <miller> surface polar slab`
   - `<formula> <hkl> non-polar symmetric slab`
   - `ionic crystal surface <formula> <miller> dipole`
2. **Interpret**: From titles/snippets (or full page via **mat_doc_extract_info_from_webpage** if one URL is clearly relevant), infer whether the surface is Type 1 (non-polar), Type 2 (symmetric needed), or Type 3 (polar; symmetric slab or stabilization). Prefer peer-reviewed sources when available.
3. **Use and verify**: Use the inferred type for building (if still before build) and for `check_slab_tasker.py` (`--tasker_type <inferred>`). Run the checker as usual; script output does **not** include `literature_*` when the table has no entry, but the type you pass is now literature-based.
4. **Optional — extend the table**: After a compliant result, you may add the new (formula, miller, tasker_type, note, ref) to `reference/tasker_lookup.yaml` (or report to the user: "Consider adding this material/surface to the skill's lookup table for future runs" and paste a suggested YAML block).

### 3. Interpret output

The script prints JSON with `compliant`, `symmetric`, `reason`, `layer_summary`, and optionally `literature_*`. Exit code 0 = compliant, non-zero = non-compliant.

- If **non-compliant**: Do **not** call finish. Re-cut with adjusted parameters (symmetric termination, layer count) and run the checker again, or warn the user and offer to re-cut.
- If **compliant** but **literature_consistent** is false (when lookup was used): Prefer correcting the Tasker type and re-checking, or explicitly note the discrepancy and that the chosen type is still used.
- If **compliant** (and literature-consistent when applicable): Proceed to retrieve-structure (e.g. `assess_structure.py`) if needed, then finish.

## Scripts

- **build_slab_tasker_fix.py**: Builds slab using ASE `surface`, applies heuristic Tasker-style nonpolar fix (layer-charge + dipole check), and supports tiling. **Use the CLI parameters below to match user/literature requirements; do not rely on defaults when the user or literature specifies values.**

  **Key parameters**: `-i <bulk>`, `-m <h k l>`, `-L <layers>` or `-T <thickness>`, `-v <vacuum>`, `-o <output>`, `--charge`, `--tile-repeat`/`--tile-min-x`/`--tile-min-y`. Full CLI reference: `reference/cli_reference.md`.
  - Quick example: `-i POSCAR -m 1 0 0 -L 8 -v 15 -o slab.vasp`
  - Supports single-file, multi-file (`-i f1 f2 --output-dir`), and `--batch config.json` modes. See `reference/batch_modes.md` for details.

- **check_slab_tasker.py**: Reads slab (POSCAR/CIF), infers layers, checks Tasker compliance. **Required** after every build.
  - Usage: `--file <slab> --tasker_type 1|2|3` (add `--formula`/`--miller` for literature cross-check)
  - Output: JSON with `compliant`, `symmetric`, `reason`, `layer_summary` (+ `literature_*` with lookup). Exit 0 = compliant.
  - Supports batch modes: see `reference/batch_modes.md` and `reference/cli_reference.md`.

- **add_adsorbate_batch.py**: Place adsorbate molecules on slab surfaces (ASE-based), supports batch. Local alternative to MCP `mat_sg_build_surface_adsorbate`.
  - Usage: `-s slab.vasp -a CO.xyz --shift "0.5,0.5" --height 2.0 -o slab_CO.cif`
  - Shift: fractional `"x,y"` or keyword (`ontop`/`fcc`/`hcp`/`bridge`). Default height: 2.0 A.
  - Supports single, multi-file (`-s f1 f2 --output-dir`), and `--batch config.json` modes. See `reference/batch_modes.md`.
  - **Prefer over MCP for batch scenarios**; for single structures either works.

## Integration

- **Type 1/2/3**: Use `build_slab_tasker_fix.py` first, then **mandatorily** run **check_slab_tasker.py** and **assess_structure.py** on the generated file.
- **Unknown/uncertain type**: still build with `build_slab_tasker_fix.py` first, then use checker + literature consistency to converge on final handling path.
- If `build_slab_tasker_fix.py` fails or checker stays non-compliant after retries, explicitly report to user and request decision: manual adjustment now vs temporarily continue with polar slab.
- After any slab build, you **must** run `check_slab_tasker.py` on the **actual slab file**. Do not finish without this check or with a non-compliant result unless the user has been explicitly warned.
- After a compliant slab is confirmed, use retrieve-structure (e.g. `assess_structure.py`) for sanity/dimensionality if needed.
- **吸附分子**：需要在 slab 上加吸附分子时，优先使用 `add_adsorbate_batch.py`（本地脚本，支持批量），而非 MCP `mat_sg_build_surface_adsorbate`（仅支持单次调用）。尤其是批量场景下必须使用本地脚本。单个结构时两者均可。

## High-Throughput Adsorption Screening

For batch screening tasks (multiple surfaces x adsorbates x sites), follow the 4-step pipeline in **`reference/ht_screening_pipeline.md`**: batch slab generation → batch validation → batch adsorbate placement → calculation. Key rules: breadth-first execution, fail-forward, use `--batch` modes.

## Additional resources

- **reference.md**: Tasker type examples and common materials.
- **reference/tasker_lookup.yaml**: (formula, miller) → Tasker type + note + ref; used by checker with `--formula`/`--miller`. When no entry exists, search literature on the fly (§2.1).
- **reference/ht_screening_pipeline.md**: Full HT adsorption screening pipeline.
- **reference/batch_modes.md**: Batch processing details for all 3 scripts.
- **reference/cli_reference.md**: Complete CLI parameter tables.
