---
name: tasker-polar-surface
description: "Guides surface slab construction for ionic and polar crystals using Tasker's polar surface classification (Type 1/2/3). Type 1: may use mat_sg_build_surface_slab. Type 2/3: do NOT use mat_sg_build_surface_slab; use literature for parameters then 现场搓 (execute_bash + pymatgen/ASE) to build the slab, then validate with check_slab_tasker.py and assess_structure.py. Use when cutting surfaces from bulk (切面、切 slab、从体相切出表面), for oxides/ionic crystals (e.g. MgO, TiO2, ZnO)."
---

# Tasker Polar Surface Skill

When cutting a surface slab from a bulk crystal (especially ionic or oxide), apply **Tasker's polar surface theory** so the slab is non-polar or correctly handled. Otherwise the cut may be polar and physically unreasonable (e.g. diverging surface energy for Type 3).

## Tasker's three surface types

| Type | Perpendicular dipole in repeat unit | Action |
|------|-------------------------------------|--------|
| **Type 1** | None — each layer is charge-neutral along the normal | Safe to cut; no special handling. |
| **Type 2** | Zero in the repeat unit, but stacking can create a dipole across the slab | Use **symmetric termination** or choose slab thickness so the two surfaces cancel the dipole. |
| **Type 3** | Non-zero — each repeat has a net dipole | Polar; naive cut is unstable. Use **symmetric slab** (same termination on both sides), passivation, or note that reconstruction/charge transfer may stabilize. |

- **Type 1**: e.g. rocksalt (100): alternating cation/anion in-plane → neutral layers.
- **Type 2**: Repeat has no net dipole, but the way layers stack can make a finite slab polar unless symmetric.
- **Type 3**: Alternating layers of opposite charge (e.g. O- and Zn-terminated ZnO basal); classical ionic model gives diverging surface energy. Stabilization in practice: reconstruction, adsorbates, or charge transfer.

## Workflow: Type 1 vs Type 2/3 (different build path)

1. **Classify** the requested (hkl) for the material: Type 1, 2, or 3. Prefer **reference/tasker_lookup.yaml** and **reference.md**; if (formula, miller) is **not** in the lookup table, **search literature** first (see §2.1) with mat_sn_* tools, then infer the type from search results.

2. **Type 1 (non-polar)**
   - You **may** use the MCP slab builder **mat_sg_build_surface_slab** (or equivalent).
   - Then run the checker and validation as below.

3. **Type 2 or Type 3 (polar / need symmetric termination)**
   - **Do NOT use mat_sg_build_surface_slab.**
   - Get parameters from literature/web, then **现场搓**：用 **execute_bash** 跑一段 Python（pymatgen 或 ASE）按文献的层数、真空、终止面生成 slab 并写出文件，然后**必须**做校验。见下 § Type 2/3。

## Type 2/3: 现场搓 (no mat_sg_build_surface_slab)

**Type 2/3** 不调用 `mat_sg_build_surface_slab`。先查文献/网页拿参数，再**现场写代码、跑脚本**生成 slab，最后校验。

### Step 1 — 从文献/网页拿参数

- 用 **mat_sn_*** 或 **mat_sn_web-search** 搜该 (formula, 晶面)，如 "ZnO 0001 symmetric slab layers", "<formula> <hkl> slab construction vacuum"。
- 从论文/教程里提取：**层数**（或 repeat units）、**真空厚度**、以及是否推荐某种**终止面**（如上下都是 O 终止）。

### Step 2 — 现场搓 slab

- 用 **execute_bash** 写并执行一段 Python：用 pymatgen（`SlabGenerator`、`get_slabs()` 等）或 ASE，按上面拿到的 bulk 路径、miller、层数、真空生成 slab；**Type 2/3 要选上下层成分一致的对称终止**（从 `get_slabs()` 里挑或按文献指定）；把结构写到 POSCAR/CIF。
- 不依赖本 skill 的预置脚本；缺啥就现场写、现场跑。

### Step 3 — 校验（必做）

- Run **check_slab_tasker.py** on the generated file:
  `use_skill` … `script_name=check_slab_tasker.py`, `script_args="--file <slab_path> --tasker_type 2|3"` (and `--formula`, `--miller` if known).
  Require `compliant: true`; if not, adjust n_layers or termination (from literature) and rebuild.
- Run **structure-manager** `assess_structure.py` on the same file for dimensionality and sanity.
Only after both checks pass (and optionally literature/lookup consistency) proceed to finish.

## Checklist

- [ ] Classified the surface as Type 1, 2, or 3.
- [ ] **Type 1**: Used mat_sg_build_surface_slab (or script) → then validate.
- [ ] **Type 2/3**: Did **not** use mat_sg_build_surface_slab; got parameters from literature → 现场搓 (execute_bash + pymatgen/ASE) → ran check_slab_tasker.py and assess_structure.py; both pass.
- [ ] **After build**: Ran `check_slab_tasker.py` on the slab file; `compliant` is true. If not, re-build or warn user before finish.

## Review after building (mandatory) — script + literature/lookup

**Judging by script alone can be unreliable** (e.g. layer threshold, choice of normal). Combine **structure checker script** with **literature / lookup** when possible.

### 1. Run the checker script (required)

Call `use_skill` with `skill_name=tasker-polar-surface`, `action=run_script`, `script_name=check_slab_tasker.py`, and script args:

- **Minimum**: `--file <path_to_slab_file> --tasker_type <1|2|3>` (same Tasker type used when building).
- **Recommended when material and surface are known**: add `--formula <formula>` and `--miller "<h k l>"` (e.g. `--formula ZnO --miller "0 0 0 1"`). The script will load `reference/tasker_lookup.yaml` and add `literature_expected_type`, `literature_note`, `literature_ref`, and `literature_consistent` to the JSON. Use this to cross-check that the chosen Tasker type matches known classifications.

The script reads the slab with pymatgen, infers layers along the surface normal, and checks: Type 1 = all layers stoichiometric; Type 2/3 = symmetric termination (top and bottom layers mirror composition).

### 2. Use reference and lookup

- **reference.md**: Tasker types and common materials (MgO, TiO2, ZnO, etc.); consult when classifying (hkl) and when interpreting script output.
- **reference/tasker_lookup.yaml**: Machine-readable (material, surface) → Tasker type + short note + ref. When you pass `--formula` and `--miller`, the checker compares with this table; if `literature_consistent` is false, re-check your classification or warn the user.
- **Existing structures**: If the user or workflow has access to structures from literature or databases (e.g. same material and surface from ICSD/COD or papers), compare layer count and termination with the built slab as an extra sanity check. Prefer script + lookup first; use DB/literature structures when available.

### 2.1 When the lookup table has no entry: search literature on the fly

If **(formula, miller)** is **not** in `tasker_lookup.yaml`, do **not** rely on script-only classification. Use MCP retrieval tools to search the literature, infer the Tasker type, then run the checker and optionally add the result to the table.

1. **Search**: Call **mat_sn_search-papers-normal**, **mat_sn_scholar-search**, or **mat_sn_web-search** (prefer English queries) with queries targeting polar surface / Tasker classification for that material and surface. Suggested query templates:
   - `Tasker polar surface <formula> <hkl>`
   - `<formula> <miller> surface polar slab`
   - `<formula> <hkl> non-polar symmetric slab`
   - `ionic crystal surface <formula> <miller> dipole`
2. **Interpret**: From titles/snippets (or full page via **`extract_info_from_webpage`** if one URL is clearly relevant), infer whether the surface is Type 1 (non-polar), Type 2 (symmetric needed), or Type 3 (polar; symmetric slab or stabilization). Prefer peer-reviewed sources when available.
3. **Use and verify**: Use the inferred type for building (if still before build) and for `check_slab_tasker.py` (`--tasker_type <inferred>`). Run the checker as usual; script output does **not** include `literature_*` when the table has no entry, but the type you pass is now literature-based.
4. **Optional — extend the table**: After a compliant result, you may add the new (formula, miller, tasker_type, note, ref) to `reference/tasker_lookup.yaml` (or report to the user: "Consider adding this material/surface to the skill's lookup table for future runs" and paste a suggested YAML block).

### 3. Interpret output

The script prints JSON with `compliant`, `symmetric`, `reason`, `layer_summary`, and optionally `literature_*`. Exit code 0 = compliant, non-zero = non-compliant.

- If **non-compliant**: Do **not** call finish. Re-cut with adjusted parameters (symmetric termination, layer count) and run the checker again, or warn the user and offer to re-cut.
- If **compliant** but **literature_consistent** is false (when lookup was used): Prefer correcting the Tasker type and re-checking, or explicitly note the discrepancy and that the chosen type is still used.
- If **compliant** (and literature-consistent when applicable): Proceed to structure-manager (e.g. `assess_structure.py`) if needed, then finish.

## Scripts

- **check_slab_tasker.py**: Reads the built slab file (POSCAR/CIF), infers layers along the surface normal, and checks Tasker compliance. **Required** after every slab build before finish.
  - Usage: `python check_slab_tasker.py --file <slab_path> --tasker_type 1|2|3`
  - Optional (recommended when material/surface known): `--formula <formula> --miller "<h k l>"`; use `--lookup <path>` to override default `reference/tasker_lookup.yaml`.
  - Output: JSON with `compliant`, `symmetric`, `reason`, `layer_summary`; with lookup also `literature_expected_type`, `literature_note`, `literature_ref`, `literature_consistent`. Exit code 0 = compliant.
  - Requires: pymatgen, numpy; PyYAML for lookup.

## Integration

- **Type 1**: Apply this skill before calling **mat_sg_build_surface_slab** (or equivalent). After building, run check_slab_tasker.py and structure-manager as below.
- **Type 2/3**: Do **not** use mat_sg_build_surface_slab. Get parameters from literature, then **现场搓** (execute_bash + pymatgen/ASE) to build the slab; then **mandatorily** run **check_slab_tasker.py** and **assess_structure.py** on the generated file.
- After any slab build, you **must** run `check_slab_tasker.py` on the **actual slab file**. Do not finish without this check or with a non-compliant result unless the user has been explicitly warned.
- After a compliant slab is confirmed, use structure-manager (e.g. `assess_structure.py`) for sanity/dimensionality if needed.

## Additional resources

- **reference.md**: Tasker type examples and common materials (MgO, TiO2, ZnO, etc.).
- **reference/tasker_lookup.yaml**: Machine-readable (formula, miller) → Tasker type, note, ref; used by the checker with `--formula` and `--miller` for literature cross-check. Extend this file for more materials/surfaces. When the table has no entry, use **mat_sn_*** retrieval to search the literature on the fly (§2.1).
