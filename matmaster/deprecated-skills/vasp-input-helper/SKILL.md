---
name: vasp-input-helper
description: "VASP input file generation assistant with offline wiki (1320 pages). Workflow: get_potcar→get_template→get_tag_info→search_wiki→write INCAR→validate_incar. Call via run_script ONLY. task-types: scf,relax,band_structure,dos,md,hybrid,phonon,gw,optical,neb."
skill_type: operator
---

# VASP Input Helper

Generate VASP input files through a **template-driven, wiki-backed** workflow. The skill ships an offline VASP wiki knowledge base (1320 pages) and a structured tag index, so the LLM never needs to guess parameter values.

## Core logic

```
identify task & elements
       │
       ▼
┌─────────────────┐
│  get_potcar.py  │  确定赝势 & ENMAX → 算出 ENCUT
└───────┬─────────┘
        ▼
┌─────────────────┐
│ get_template.py │  按任务类型拿 INCAR 模板（含 required/recommended tags 列表）
└───────┬─────────┘
        ▼
┌─────────────────┐
│ get_tag_info.py │  逐个查模板中关键 tag 的类型、默认值、取值约束
└───────┬─────────┘
        ▼
┌─────────────────┐
│ search_wiki.py  │  针对具体场景（材料/方法/任务）查 wiki，获取最佳实践
└───────┬─────────┘
        ▼
┌─────────────────┐
│   Write INCAR   │  模板 + POTCAR 信息 + tag 规范 + wiki 知识 → 生成完整 INCAR
└───────┬─────────┘
        ▼
┌──────────────────┐
│validate_incar.py │  自动校验：tag 冲突、缺失必选项、ENCUT 合理性
└───────┬──────────┘
        ▼
  Write KPOINTS / POSCAR / POTCAR command
```

## Constraints

- **run_script ONLY** — this skill has NO reference files. Do NOT call `get_reference`.
- Scripts use `--flag value` syntax, NOT positional args.
- Task types: `scf` `relax` `band_structure` `dos` `md` `hybrid` `phonon` `gw` `optical` `neb`. There is NO `aimd` — use `md`.
- **NEVER guess ENCUT** — must come from `get_potcar.py`.

## Workflow

### Step 1 — get_potcar.py: 确定赝势与截断能

```bash
python scripts/get_potcar.py --elements "Fe,O"
python scripts/get_potcar.py --elements "Mo,S" --for-gw
```

Returns: recommended POTCAR variant per element, ENMAX, suggested ENCUT (1.3×ENMAX), and the `cat` command to assemble POTCAR. **Must be called first** — ENCUT depends on it.

### Step 2 — get_template.py: 按任务类型获取 INCAR 模板

```bash
python scripts/get_template.py --task-type relax
```

Returns: a JSON template listing required and recommended INCAR tags for the task type, plus POTCAR general rules. This is the skeleton — tells you **which tags need填**.

### Step 3 — get_tag_info.py: 查每个关键 tag 的规范

```bash
python scripts/get_tag_info.py --tags "ALGO,ISMEAR,SIGMA,EDIFF"
```

Returns: each tag's default, allowed values, type, unit, category, brief description, and notes. Use this to understand **what values are valid** before filling the template.

### Step 4 — search_wiki.py: 按场景查 wiki 最佳实践

```bash
python scripts/search_wiki.py --query "NiO DFT+U"
python scripts/search_wiki.py --query "surface dipole correction"
python scripts/search_wiki.py --query "AIMD liquid water" --max-results 3
```

Searches the 1320-page offline wiki by exact match → category → fuzzy title → fulltext. Use it to resolve **scenario-specific decisions**: which functional for this material, what U value for this element, whether to enable dipole correction, etc.

### Step 5 — Write INCAR

Combine all information: template skeleton + ENCUT from Step 1 + tag constraints from Step 3 + wiki best practices from Step 4. Write the complete INCAR file.

### Step 6 — validate_incar.py: 校验

```bash
python scripts/validate_incar.py --input-file /path/to/INCAR --task-type relax --enmax 400
python scripts/validate_incar.py --input-file /path/to/INCAR --task-type md --is-metal --enmax 520
```

Checks: tag conflicts, missing required tags, ENCUT reasonableness, ISMEAR/task-type compatibility, etc. If errors found, fix and re-validate.

### Step 7 — Write KPOINTS, POSCAR, POTCAR command

KPOINTS standard format (always 5 lines):
```
Automatic mesh
0
Gamma
 N1  N2  N3
 0   0   0
```

- MD large supercells (≥64 atoms): `1 1 1`
- Band structure: Line-mode with reciprocal coordinates

## Key rules

- **ENCUT**: Always from get_potcar.py. ISIF≥3 → ≥1.3×ENMAX.
- **ISMEAR**: Metals → 1, semiconductors/insulators → 0, accurate DOS → -5. **Never** -5 for MD.
- **Hybrid** (HSE06 etc.): ALGO=All or Damped. Never Fast/VeryFast.
- **DFT+U**: LDAU + LDAUTYPE + LDAUL + LDAUU + LDAUJ. LMAXMIX=4 (d) or 6 (f).
- **Meta-GGA** (SCAN/R2SCAN): LASPH=.TRUE.
- **MD**: ISYM=0, IBRION=0. Never ISMEAR=-5.
- **Band structure**: SCF first → non-SCF with ICHARG=11; or KPOINTS_OPT for hybrids.
- **NpT** (ISIF=3 in MD): PREC=Accurate, ENCUT≥1.3×ENMAX.
