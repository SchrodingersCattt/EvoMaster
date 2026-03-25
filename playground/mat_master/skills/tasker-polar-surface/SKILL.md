---
name: tasker-polar-surface
description: "Guides slab cutting for ionic and polar crystals using Tasker types (1/2/3). Use built-in script build_slab_tasker_fix.py as the default builder for all surface types, then validate with check_slab_tasker.py and assess_structure.py. If auto-fix fails, explicitly report to user and ask for manual adjustment or temporary acceptance."
---

<!-- multi-server: mat_sg, mat_sn, mat_doc -->

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

### Step 2 — 运行 build_slab_tasker_fix.py 生成 slab（优先）

- **根据用户或文献需求传参**：用户明确要求的层数、厚度、真空、扩胞、电荷等，必须通过 script_args 传给脚本，不要只用默认值。
- Call `use_skill` with `action=run_script`, `script_name=build_slab_tasker_fix.py`, and script args (choose one mode):
  - by layers: `-i <bulk_path> -m <h> <k> <l> -L <repeat_layers> -v <vacuum> -o <slab_path>`
  - by thickness: `-i <bulk_path> -m <h> <k> <l> -T <thickness_A> -v <vacuum> -o <slab_path>`
- Optional tiling (用户或文献要求超胞/最小尺寸时必传):
  - fixed repeat: `--tile-repeat NX NY NZ`
  - minimum in-plane size: `--tile-min-x <A> --tile-min-y <A>`
- Optional charge override: `--charge "Zn:2,O:-2"` or JSON string (非二元或文献给出电荷时使用).
- If the script exits non-zero (e.g. "No nonpolar solution found"), **do not hide it**. Report failure to user, include reason, and ask whether to:
  1) manually adjust termination/layers and retry, or
  2) temporarily accept a polar slab and continue.
- Only when this script clearly cannot satisfy the case, fall back to ad-hoc `execute_bash` custom Python.

### Step 3 — 校验与分型收敛（必做）

- Run **check_slab_tasker.py** on the generated file:
  `use_skill` … `script_name=check_slab_tasker.py`, `script_args="--file <slab_path> --tasker_type <provisional_type>"` (and `--formula`, `--miller` if known).
  Require `compliant: true`; if not, adjust n_layers or termination (from literature) and rebuild.
- Run **structure-manager** `assess_structure.py` on the same file for dimensionality and sanity.
Only after both checks pass (and optionally literature/lookup consistency) proceed to finish.

### 批量处理流程（当用户提供多个结构时）

**何时使用批量模式：** 当用户一次给出多个体相结构文件、或明确要求"批量处理"时，使用批量模式而非逐个手动调用。

**判断使用哪种批量模式：**
- 所有结构**共享相同参数**（同一 miller、层数、真空等）→ 使用**多文件 + 共享参数模式**（`-i file1 file2 ... + --output-dir`）
- 每个结构需要**不同参数**（不同 miller、不同层数等）→ 使用 **`--batch` JSON 配置文件模式**

#### 模式 A：多文件 + 共享参数

```
script_args="-i bulk1.cif bulk2.cif bulk3.vasp -m 1 0 0 -L 8 -v 15 --output-dir ./slabs/"
```

#### 模式 B：`--batch` JSON 配置文件

先用 `execute_bash` 或 `write_file` 生成 config.json：
```json
[
  {"input": "bulk1.cif", "miller": [1,0,0], "repeat_layers": 8, "vacuum": 15, "output": "slab1.vasp"},
  {"input": "bulk2.cif", "miller": [1,1,0], "thickness": 18, "vacuum": 20, "output": "slab2.cif", "charge": "Zn:2,O:-2"}
]
```
然后：`script_args="--batch config.json"`

#### 批量 build + 批量 check 联动

批量 build 完成后，**必须批量 check**。推荐流程：

1. 运行 `build_slab_tasker_fix.py` 批量生成 → 解析输出的 `{"results": [...]}` 获取所有 `success: true` 的输出文件列表
2. 用这些文件列表运行 `check_slab_tasker.py`：
   - 共享参数时：`--file slab1.vasp slab2.cif ... --tasker_type <type>`
   - 独立参数时：写 check_config.json 用 `--batch check_config.json`
3. 解析 check 输出的 JSON 数组，对 `compliant: false` 的逐个报告或重试
4. 对 build 阶段 `success: false` 的，也向用户汇报失败原因

**退出码语义：** 0=全部成功/compliant，1=任一失败/non-compliant。批量模式下单个失败不中断其他结构的处理。

## Checklist

- [ ] Did provisional classification first (lookup/literature), then finalized decision after structure check.
- [ ] Used `build_slab_tasker_fix.py` first for Type 1/2/3 (or gave clear reason for fallback).
- [ ] **After build**: Ran `check_slab_tasker.py` on the slab file; `compliant` is true. If not, re-build or warn user before finish.
- [ ] If auto-build/auto-fix fails: explicitly reported failure reason to user and asked for manual adjustment vs temporary ignore.

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
- If **compliant** (and literature-consistent when applicable): Proceed to structure-manager (e.g. `assess_structure.py`) if needed, then finish.

## Scripts

- **build_slab_tasker_fix.py**: Builds slab using ASE `surface`, applies heuristic Tasker-style nonpolar fix (layer-charge + dipole check), and supports tiling. **Use the CLI parameters below to match user/literature requirements; do not rely on defaults when the user or literature specifies values.**

  **CLI 参数一览（熟练使用）：**

  | 参数 | 含义 | 默认/必填 | 示例 |
  |------|------|-----------|------|
  | `-i`, `--input` | 体相结构文件路径 | 默认 POSCAR | `-i bulk.cif` |
  | `-m`, `--miller` | Miller 指数 (h k l)，**3 个整数** | **必填** | `-m 1 0 0`；六方 (0001) 用 `-m 0 0 1` |
  | `-o`, `--output` | 输出 slab 文件路径 | 默认 POSCAR_slab | `-o slab.vasp` |
  | `-L`, `--repeat-layers` | 重复层数 | 与 `-T` 二选一**必填** | `-L 8` |
  | `-T`, `--thickness` | 目标厚度（Å） | 与 `-L` 二选一**必填** | `-T 18` |
  | `-v`, `--vacuum` | 真空层厚度（Å） | 默认 15.0 | `-v 20` |
  | `--charge` | 电荷映射 | 默认自动（仅二元） | `--charge "Zn:2,O:-2"` 或 JSON |
  | `--layer-tol` | 层识别容差（Å） | 默认 0.5 | `--layer-tol 0.6` |
  | `--tile-repeat` | 扩胞重复 (NX NY NZ) | 可选 | `--tile-repeat 2 2 1` |
  | `--tile-min-x` | x 方向最小尺寸（Å） | 可选 | `--tile-min-x 12` |
  | `--tile-min-y` | y 方向最小尺寸（Å） | 可选 | `--tile-min-y 12` |
  | `--quiet` | 静默，少打日志 | 可选 | `--quiet` |
  | `--output-dir` | 批量模式输出目录（自动命名 `{stem}_slab{ext}`） | 可选（多文件时推荐） | `--output-dir ./slabs/` |
  | `--batch` | 批量配置 JSON 文件（每条独立参数） | 可选（与 `-i` 互斥） | `--batch config.json` |

  - Miller：脚本为 `nargs=3`，即 3 个整数；六方 (0001) 传 `-m 0 0 1`（3-index 等价）。
  - 示例（按层数）：`-i POSCAR -m 1 0 0 -L 8 -v 15 -o slab.vasp`
  - 示例（按厚度+扩胞）：`-i bulk.cif -m 1 1 0 -T 18 -v 20 -o slab.cif --tile-repeat 2 2 1`
  - 示例（最小尺寸）：`-i POSCAR -m 1 0 0 -L 6 -v 15 -o slab.vasp --tile-min-x 10 --tile-min-y 10`
  - Output: slab 文件（格式由 `-o` 扩展名决定）+ 终端日志。**Not universal**：部分极性面仍可能失败，需向用户汇报并请其手动调整或暂时接受。

  **批量处理（Batch）：**

  支持三种使用模式：

  1. **单文件模式（向后兼容）：**
     ```bash
     python build_slab_tasker_fix.py -i bulk.cif -m 1 0 0 -L 8 -v 15 -o slab.vasp
     ```

  2. **多文件 + 共享参数：**
     ```bash
     python build_slab_tasker_fix.py -i bulk1.cif bulk2.cif bulk3.vasp \
         -m 1 0 0 -L 8 -v 15 --output-dir ./slabs/
     ```
     所有文件共享相同的 miller、layers、vacuum 等参数。输出自动命名为 `{stem}_slab{ext}`，存入 `--output-dir`。

  3. **配置文件 + 独立参数（`--batch`）：**
     ```bash
     python build_slab_tasker_fix.py --batch config.json
     ```
     config.json 格式：
     ```json
     [
       {"input": "bulk1.cif", "miller": [1,0,0], "repeat_layers": 8, "vacuum": 15, "output": "slab1.vasp"},
       {"input": "bulk2.cif", "miller": [1,1,0], "thickness": 18, "vacuum": 20, "output": "slab2.cif", "charge": "Zn:2,O:-2"}
     ]
     ```
     每条可有独立的 miller、layers/thickness、vacuum、charge、tile 等参数。未指定的参数回退到 CLI 默认值。

  批量模式输出汇总 JSON：`{"results": [{"input": ..., "output": ..., "success": bool, "error": ...}, ...]}`。退出码：全部成功=0，任一失败=1。每个结构独立处理，一个失败不影响其他。

- **check_slab_tasker.py**: Reads the built slab file (POSCAR/CIF), infers layers along the surface normal, and checks Tasker compliance. **Required** after every slab build before finish.
  - Usage: `python check_slab_tasker.py --file <slab_path> --tasker_type 1|2|3`
  - Optional (recommended when material/surface known): `--formula <formula> --miller "<h k l>"`; use `--lookup <path>` to override default `reference/tasker_lookup.yaml`.
  - Output: JSON with `compliant`, `symmetric`, `reason`, `layer_summary`; with lookup also `literature_expected_type`, `literature_note`, `literature_ref`, `literature_consistent`. Exit code 0 = compliant.
  - Requires: pymatgen, numpy; PyYAML for lookup.

  **批量处理（Batch）：**

  1. **多文件 + 共享参数：**
     ```bash
     python check_slab_tasker.py --file slab1.vasp slab2.cif slab3.vasp --tasker_type 3
     ```
     所有文件共享 `--tasker_type`、`--formula`、`--miller` 参数。输出 JSON 数组。

  2. **配置文件 + 独立参数（`--batch`）：**
     ```bash
     python check_slab_tasker.py --batch check_config.json
     ```
     check_config.json 格式：
     ```json
     [
       {"file": "slab1.vasp", "tasker_type": 3, "formula": "ZnO", "miller": "0 0 0 1"},
       {"file": "slab2.cif", "tasker_type": 1}
     ]
     ```
     每条可有独立的 tasker_type、formula、miller 参数。

  批量模式输出 JSON 数组 `[{...}, {...}]`，每个结果包含 `file` 字段。退出码：全部 compliant=0，任一非 compliant=1。

- **add_adsorbate_batch.py**: 在 slab 表面添加吸附分子，本地版（替代 MCP `mat_sg_build_surface_adsorbate`），支持批量。底层用 ASE `add_adsorbate`。

  **CLI 参数一览：**

  | 参数 | 含义 | 默认/必填 | 示例 |
  |------|------|-----------|------|
  | `-s`, `--surface` | slab 结构文件（支持多个） | **必填**（非 batch） | `-s slab.vasp` 或 `-s slab1.vasp slab2.cif` |
  | `-a`, `--adsorbate` | 吸附分子结构文件 | **必填**（非 batch） | `-a CO.xyz` |
  | `-o`, `--output` | 输出文件名（单文件模式） | 默认 structure_adsorbate.cif | `-o slab_CO.vasp` |
  | `--output-dir` | 批量输出目录（自动命名 `{stem}_ads.cif`） | 可选 | `--output-dir ./ads_slabs/` |
  | `--shift` | 吸附位置：分数坐标 `"0.5,0.5"` 或 ASE 关键词 `ontop`/`fcc`/`hcp`/`bridge` | 默认 [0.5,0.5] | `--shift "0.25,0.75"` 或 `--shift ontop` |
  | `--height` | 吸附高度（angstrom） | 默认 2.0 | `--height 1.8` |
  | `--batch` | 批量配置 JSON 文件 | 可选 | `--batch ads_config.json` |
  | `--quiet` | 静默模式 | 可选 | `--quiet` |

  **使用模式：**

  1. **单文件模式（等价于 MCP `mat_sg_build_surface_adsorbate` 单次调用）：**
     ```bash
     python add_adsorbate_batch.py -s slab.vasp -a CO.xyz --shift "0.5,0.5" --height 2.0 -o slab_CO.cif
     ```

  2. **多 slab + 共享吸附参数：**
     ```bash
     python add_adsorbate_batch.py -s slab1.vasp slab2.cif slab3.vasp \
         -a CO.xyz --shift ontop --height 1.8 --output-dir ./ads_slabs/
     ```
     所有 slab 共享同一个吸附分子和位置参数。输出自动命名 `{stem}_ads.cif`。

  3. **`--batch` JSON 配置（每条独立参数）：**
     ```bash
     python add_adsorbate_batch.py --batch ads_config.json
     ```
     ads_config.json 格式：
     ```json
     [
       {"surface": "slab1.vasp", "adsorbate": "CO.xyz", "shift": [0.5, 0.5], "height": 2.0, "output": "slab1_CO.cif"},
       {"surface": "slab2.vasp", "adsorbate": "OH.xyz", "shift": "ontop", "height": 1.5, "output": "slab2_OH.cif"},
       {"surface": "slab3.vasp", "adsorbate": "CO.xyz", "shift": [0.25, 0.75], "height": 2.5}
     ]
     ```
     每条可有不同的吸附分子、位置、高度。

  批量模式输出汇总 JSON：`{"results": [{"surface": ..., "adsorbate": ..., "output": ..., "success": bool, "error": ...}, ...]}`。退出码：全部成功=0，任一失败=1。

  **与 build/check 联动的典型批量流程：**
  1. `build_slab_tasker_fix.py --batch` 批量生成 slab
  2. `check_slab_tasker.py --batch` 批量校验
  3. `add_adsorbate_batch.py --batch` 批量加吸附分子

## Integration

- **Type 1/2/3**: Use `build_slab_tasker_fix.py` first, then **mandatorily** run **check_slab_tasker.py** and **assess_structure.py** on the generated file.
- **Unknown/uncertain type**: still build with `build_slab_tasker_fix.py` first, then use checker + literature consistency to converge on final handling path.
- If `build_slab_tasker_fix.py` fails or checker stays non-compliant after retries, explicitly report to user and request decision: manual adjustment now vs temporarily continue with polar slab.
- After any slab build, you **must** run `check_slab_tasker.py` on the **actual slab file**. Do not finish without this check or with a non-compliant result unless the user has been explicitly warned.
- After a compliant slab is confirmed, use structure-manager (e.g. `assess_structure.py`) for sanity/dimensionality if needed.
- **吸附分子**：需要在 slab 上加吸附分子时，优先使用 `add_adsorbate_batch.py`（本地脚本，支持批量），而非 MCP `mat_sg_build_surface_adsorbate`（仅支持单次调用）。尤其是批量场景下必须使用本地脚本。单个结构时两者均可。

## Additional resources

- **reference.md**: Tasker type examples and common materials (MgO, TiO2, ZnO, etc.).
- **reference/tasker_lookup.yaml**: Machine-readable (formula, miller) → Tasker type, note, ref; used by the checker with `--formula` and `--miller` for literature cross-check. Extend this file for more materials/surfaces. When the table has no entry, use **mat_sn_*** retrieval to search the literature on the fly (§2.1).
