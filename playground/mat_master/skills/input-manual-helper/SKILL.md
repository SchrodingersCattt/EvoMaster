---
skill_type: operator
name: input-manual-helper
display_name: 计算输入文件助手（LSP 引擎版）
description: >
  基于 LSP（Language Server Protocol）架构的科学计算输入文件引擎。
  支持 5 个软件的输入文件生成、诊断、参数补全和文档查询：
  CP2K、ORCA、Quantum ESPRESSO、ABINIT、LAMMPS。
version: "2.0.0"
---

# Input Manual Helper Skill（LSP 引擎版）

> **跳过条件**：若用户已提供完整的、可直接运行的输入文件，**跳过本 Skill**，直接使用 **`bohrium-job`** Skill 提交。适用于所有支持的软件（CP2K、QE、ABINIT、LAMMPS、ORCA）。

## 概述

本 Skill 基于 **LSP（Language Server Protocol）** 引擎架构，提供对 5 种科学计算软件的全面支持：

```
用户意图
  │
  ▼
RenderIntent（task_type, params, structure_file）
  │
  ▼
SoftwareBackend（parse → diagnostics → completion → render）
  │
  ├─ render_input.py     生成输入文件
  ├─ diagnose_input.py   诊断/校验输入文件
  ├─ complete_param.py   参数补全
  └─ describe_param.py   参数文档查询
```

引擎核心流水线：
- **Schema**：每个软件的参数元数据（类型、范围、枚举值、文档）
- **Parser**：将输入文件文本解析为 `DocumentModel`（sections + params + ranges）
- **Diagnostics**：基于 Schema 做静态校验 + 物理规则检查
- **Completion**：按光标所在 section/context 返回参数建议
- **Renderer**：根据 `RenderIntent` 生成可运行的输入文件

## 支持的软件

| 软件 | 格式 | 默认测试体系 | 支持任务类型 |
|------|------|------------|------------|
| **CP2K** | `&SECTION ... &END` 嵌套 | Si 金刚石（GPW/PBE） | scf, opt, md, band |
| **ORCA** | `! keyword` + `%block` | H₂O 分子（B3LYP/def2-SVP） | scf, opt, freq, tddft |
| **Quantum ESPRESSO** | Fortran namelist + cards | Si 金刚石（pw.x SCF） | scf, relax, vc-relax, md, nscf, bands |
| **ABINIT** | 扁平 key-value | Si 金刚石（primitive FCC） | scf, relax, cellopt |
| **LAMMPS** | 命令式脚本 | LJ FCC（能量最小化） | minimize, md, nvt, npt |
| **ABACUS** | `INPUT_PARAMETERS` key-value + STRU + KPT | Si 金刚石（conventional cubic，LCAO） | scf, relax, cell-relax, nscf, band, dos, md |

## 脚本说明

### `render_input.py` — 生成输入文件

根据软件名和任务类型生成可运行的输入文件（含内建测试结构）。

```bash
# 基本用法
uv run python scripts/render_input.py --software qe --task scf

# 覆盖参数
uv run python scripts/render_input.py --software abinit --task scf \
  --param ecut=20 --param nstep=100

# 指定结构文件（需 pymatgen 可读）
uv run python scripts/render_input.py --software qe --task relax \
  --structure /path/to/structure.cif

# 输出到文件
uv run python scripts/render_input.py --software cp2k --task opt \
  -o output.inp
```

**参数：**
- `--software`：`cp2k` | `orca` | `qe` | `abinit` | `lammps` | `abacus`
- `--task`：任务类型（见支持软件表）
- `--param KEY=VALUE`：覆盖默认参数（可多次指定）
- `--structure PATH`：结构文件路径（pymatgen 可读格式）
- `-o / --output`：输出文件路径（默认 stdout）

### `diagnose_input.py` — 诊断输入文件

解析输入文件，输出 diagnostics（error/warning/info）列表。

```bash
# 诊断文件
uv run python scripts/diagnose_input.py --software qe --input input.in

# 从 stdin 读取（与 render 管道连用）
uv run python scripts/render_input.py --software qe 2>/dev/null | \
  uv run python scripts/diagnose_input.py --software qe --input -

# JSON 格式输出
uv run python scripts/diagnose_input.py --software abinit --input run.abi --format json
```

**参数：**
- `--software`：软件名
- `--input`：输入文件路径（`-` 表示 stdin）
- `--format`：`text`（默认）| `json`

**退出码：**
- `0`：无 error（可能有 warning/info）
- `1`：至少有一个 error 级别诊断

### `complete_param.py` — 参数补全

在指定行列位置返回参数补全建议。

```bash
uv run python scripts/complete_param.py --software qe --input input.in --line 5 --col 0
uv run python scripts/complete_param.py --software cp2k --input input.inp --line 10 --col 4
```

**参数：**
- `--line`：光标行号（1-based）
- `--col`：光标列号（0-based）
- `--limit`：返回条数上限（默认 20）

### `describe_param.py` — 参数文档查询

查询指定参数的文档（类型、默认值、范围、说明）。

```bash
uv run python scripts/describe_param.py --software qe --param ecutwfc
uv run python scripts/describe_param.py --software abinit --param ecut
uv run python scripts/describe_param.py --software cp2k --param CUTOFF
```

### `list_references.py` — 列出参考模板

列出 `references/` 目录下可用的参考模板文件。

```bash
uv run python scripts/list_references.py
uv run python scripts/list_references.py --software cp2k
```

### `validate_input.py` — 旧版验证（兼容保留）

旧版验证脚本，已被 `diagnose_input.py` 替代，但仍保留以兼容旧工作流。

```bash
uv run python scripts/validate_input.py --input_file input.in --software qe
```

> **注意**：新工作流请使用 `diagnose_input.py`，支持更多软件和更精确的诊断。

## Bohrium 提交

镜像、机型和运行命令的**权威来源**是 **bohrium-job** Skill。提交前务必先查阅：

```python
use_skill(skill_name="bohrium-job", action="get_info")
```

查阅 `## Software Reference` 表中对应软件的 Image、Machine 和 Command。

> ⚠️ **不要**从本 Skill 的文档中获取镜像名——本 Skill 不维护镜像信息。

### 赝势注意事项

**Quantum ESPRESSO**：
- `render_input.py` 输出的 `pseudo_dir` 已指向 bohrium-job SKILL.md 中 QE 镜像内置赝势路径，无需额外配置

**ABINIT**：
- ⚠️ `ppdirpath` **不是** ABINIT v9.10 的合法关键字，render 输出中已移除
- 运行前可能需手动复制赝势到工作目录（参见 bohrium-job SKILL.md 中的 ABINIT 说明）

**CP2K**：
- 使用内置 GTH 赝势（`GTH_POTENTIALS` 文件内置于镜像）
- 基组文件：`BASIS_MOLOPT`、`BASIS_ADMM`、`BASIS_ADMM_UZH`（内置）
- 无需额外配置

**ORCA**：
- 使用内置 def2-SVP 基组，无需额外赝势文件

**LAMMPS**：
- 默认使用 LJ 解析势，无需外部势函数文件
- 若使用 EAM/MEAM 等，需在 `pair_coeff` 中指定势函数文件路径

## 使用示例

### 示例 1：生成 QE SCF 输入并诊断

```bash
# 生成输入
uv run python scripts/render_input.py --software qe --task scf -o input.in

# 诊断（应无 error）
uv run python scripts/diagnose_input.py --software qe --input input.in
```

### 示例 2：生成 ABINIT 弛豫输入

```bash
uv run python scripts/render_input.py --software abinit --task relax \
  --param ecut=20 --param nstep=100 \
  -o run.abi
```

### 示例 3：管道验证（5 个软件批量）

```bash
for sw in cp2k orca qe abinit lammps; do
  echo "=== $sw ==="
  uv run python scripts/render_input.py --software $sw 2>/dev/null | \
    uv run python scripts/diagnose_input.py --software $sw --input -
done
```

### 示例 4：查询 QE 参数文档

```bash
uv run python scripts/describe_param.py --software qe --param ecutwfc
uv run python scripts/describe_param.py --software qe --param conv_thr
```

### 示例 5：CP2K 参数补全

```bash
# 在第 10 行第 4 列查询可用参数
uv run python scripts/complete_param.py --software cp2k \
  --input references/cp2k/minimal_periodic.inp \
  --line 10 --col 4
```

## 参数覆盖

通过 `--param KEY=VALUE` 可覆盖任意默认参数：

```bash
# QE：自定义截断能和 k 点
uv run python scripts/render_input.py --software qe --task scf \
  --param ecutwfc=50 --param ecutrho=400

# ABINIT：自定义截断能
uv run python scripts/render_input.py --software abinit --task scf \
  --param ecut=20 --param ngkpt="6 6 6"

# CP2K：自定义 SCF 精度
uv run python scripts/render_input.py --software cp2k --task scf \
  --param EPS_SCF=1.0E-8 --param MAX_SCF=100

# ORCA：使用不同泛函和基组
uv run python scripts/render_input.py --software orca --task opt \
  --param functional=PBE0 --param basis=def2-TZVP

# LAMMPS：使用不同系综
uv run python scripts/render_input.py --software lammps --task nvt \
  --param temp=300 --param run=50000
```

## 工作流（Agent 调用）

1. **确定软件和任务类型** — 从用户需求确定目标软件和计算类型
2. **检查是否已有输入文件** — 若用户已提供完整输入文件，直接跳至步骤 6
3. **调用 render_input.py** — 生成初始输入文件（含内建测试结构或用户结构文件）
4. **调用 diagnose_input.py** — 检查输入文件是否有 error
5. **根据诊断结果修正** — 若有 error，用 `--param` 覆盖修正后重新渲染；若只有 warning/info，判断是否需要调整
6. **提交到 Bohrium** — 使用 `bohrium-job` Skill，传入输入文件所在目录和上方表格中对应的运行命令

## 架构说明

```
engine/
├── schema.py       参数元数据注册表（SchemaRegistry, ParamTag）
├── document.py     文档模型（DocumentModel, ParsedSection, ParsedParam）
├── diagnostics.py  诊断数据结构（Diagnostic）
├── completion.py   补全数据结构（CompletionItem）
├── renderer.py     渲染意图（RenderIntent）
└── software/
    ├── base.py     SoftwareBackend 抽象基类
    ├── qe.py       Quantum ESPRESSO 后端
    ├── abinit.py   ABINIT 后端
    ├── cp2k.py     CP2K 后端
    ├── orca.py     ORCA 后端
    └── lammps.py   LAMMPS 后端
```

每个后端实现四个核心方法：
- `parse(text)` → `DocumentModel`
- `render(intent)` → `str`（输入文件文本）
- `get_diagnostics(doc, schema)` → `list[Diagnostic]`
- `get_completions(doc, line, col, schema)` → `list[CompletionItem]`
