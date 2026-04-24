---
name: pxrd-refinement
description: "Powder XRD (PXRD) refinement using GSAS-II. Pawley refinement to extract lattice parameters from a known cell+space group; Rietveld refinement to fit a full crystal structure starting from a CIF model."
skill_type: operator
depends_on: mcp-mat-xrd
---

# PXRD Refinement Skill

GSAS-II 驱动的粉末 X 射线衍射（PXRD）精修工作流。两个核心脚本：

- `gsas2_pawley.py` — **Pawley 精修**：已知空间群和大致晶胞时，从 PXRD 图谱中提取精确晶胞参数（a, b, c, α, β, γ, V）及其 ESD。**无需结构模型**。
- `gsas2_rietveld.py` — **Rietveld 精修**：已有 CIF 结构模型时，全谱拟合优化原子坐标、Uiso、占位因子并改进晶胞。

---

## 范围（Scope）— 必读

**这个 skill 做什么：**
- ✅ 输入 PXRD 图谱（.xy/.xye/.dat/.csv）+ 空间群 + 初始晶胞，输出精修后的晶胞参数（Pawley）
- ✅ 输入 PXRD 图谱 + CIF 模型，输出精修后的结构（晶胞、原子坐标、Uiso、R-factors）和更新的 CIF（Rietveld）
- ✅ 多温度 PXRD：批量对每个温度的图谱独立精修，输出每个温度的晶胞参数

**这个 skill 不做什么：**
- ❌ **热膨胀拟合**（V vs T 的线性回归、α_V、α_a 等）— 由 agent 拿到精修结果后自行做线性拟合
- ❌ **相变温度判定** — 由 agent 根据多温度精修结果自行判断
- ❌ **物相鉴定**（phase identification）— 用 `mcp-mat-xrd` 的 `xrd_phase_identification` 工具
- ❌ **单晶 XRD（SCXRD）** — 不在本 skill 范围
- ❌ **Le Bail 精修** — 当前未实现（如需要可扩展，但通常 Pawley 已够用）
- ❌ **多相 Rietveld**（混合相定量分析）— 当前脚本只支持单相 CIF

**为什么这样划分：** PXRD 精修是确定性的数值计算（给定输入 → 给定输出），适合封装成脚本。下游分析（热膨胀斜率、相变识别）是开放性的统计/物理判断，由 agent 直接做更灵活，没必要锁死成脚本。

---

## Trigger Conditions

- 用户给出 PXRD 数据（2θ vs intensity）+ 已知空间群 → Pawley
- 用户给出 PXRD 数据 + CIF 结构模型 → Rietveld
- 多温度 PXRD（VT-PXRD）需要每个温度的晶胞参数 → Pawley × N
- 用户提到 GSAS-II、Pawley、Rietveld、晶胞精修、lattice refinement

**不要触发本 skill：**
- 单晶 XRD / HKL 文件 / SHELX → 不在范围
- 仅做物相鉴定 → 用 mcp-mat-xrd

---

## Pawley 精修工作流

> **强制**：所有 PXRD Pawley 精修必须用 `gsas2_pawley.py`，不要自己写 GSAS-II 脚本。

**必需输入：**
- PXRD 数据文件（`.xye`/`.xy`/`.dat`/`.csv`/`.txt`）— 两列（2θ, intensity）
- 空间群（Hermann-Mauguin 字符串，如 `"P 21/c"`）
- 初始晶胞（粗略值即可，与真实值差 1-2% 都能收敛）
- 波长（默认 Cu Kα1 = 1.5406 Å）

### 用法

```
# 单个图谱：
python ${SKILL_DIR}/scripts/gsas2_pawley.py \
  --data pattern.xy \
  --space-group "P 21/c" \
  --cell "a=10.83,b=10.2,c=9.2,beta=99.0" \
  --wavelength 1.5406 \
  --dmin 2.0 \
  -o result.json

# 目录批量（多温度，每个温度一个文件）：
python ${SKILL_DIR}/scripts/gsas2_pawley.py \
  --data /path/to/patterns/ \
  --space-group "P 21/c" --cell "a=10.83,b=10.2,c=9.2,beta=99.0" \
  -o results.json

# 宽表 CSV（多温度在一个文件中，列对：Angle, T1, Angle, T2, ...）：
python ${SKILL_DIR}/scripts/gsas2_pawley.py \
  --data multi_temp.txt --wide-csv \
  --space-group "P 21/c" --cell "a=10.83,b=10.2,c=9.2,beta=99.0" \
  -o results.json
```

### 输出 JSON（单图谱）

```json
{
  "success": true, "file": "pattern.xy",
  "a": 10.826, "b": 10.172, "c": 9.197,
  "alpha": 90.0, "beta": 99.066, "gamma": 90.0,
  "volume": 1000.18,
  "a_esd": 0.001, "b_esd": 0.002, "c_esd": 0.001,
  "wR": 29.5, "n_reflections": 131
}
```

### 关键参数

| 参数 | 默认 | 说明 |
|------|------|------|
| `--dmin` | 2.0 Å | Pawley 反射最小 d-spacing。反射太多/SVD 报错时增到 2.5 |
| `--tmin`/`--tmax` | 8/50° | 拟合的 2θ 范围 |
| `--wavelength` | 1.5406 | X 射线波长（Å）。Mo Kα1=0.7093，AgKα1=0.5594 |
| `--gsas2-path` | `/root/g2full/GSAS-II/GSASII` | 镜像内路径，正常不用改 |
| `--instprm` | 自动生成 Cu Kα 模板 | 自定义 GSAS-II 仪器参数文件 |

### 已验证（在评测题 DFT_pxrd_001 上）

| 温度 | 精修值 | 参考值 | 容差 | 结果 |
|------|--------|--------|------|------|
| 303K | V=1000.18 | 999.81 | ±20 | ✓ |
| 303K | a=10.826 | 10.828 | ±0.05 | ✓ |
| 383K | V=1016.11 | 1022.73 | ±20 | ✓ |
| 413K | V=1037.09 | 1026.1 | ±20 | ✓ |

**已知不收敛/边界情况：** 363K（V=1035 vs 1011，超出 ±20 边界 ~4Å³）和 383K a 参数（HTP 相 cell setting 与参考值不同）。详见 `references/gsas2_refinement_guide.md` 的 Troubleshooting。

---

## Rietveld 精修工作流

> **强制**：所有 PXRD Rietveld 精修必须用 `gsas2_rietveld.py`。

**必需输入：**
- PXRD 数据文件
- 起始结构 CIF
- 波长

### 用法

```
# 标准（晶胞 + 峰形 + 原子坐标 + Uiso）：
python ${SKILL_DIR}/scripts/gsas2_rietveld.py \
  --data pattern.xye \
  --cif structure.cif \
  --wavelength 1.5406 \
  --refine-level standard \
  --export-cif refined.cif \
  -o result.json

# Basic（仅晶胞 + 背景 + 峰形，不动原子）：
python ${SKILL_DIR}/scripts/gsas2_rietveld.py \
  --data pattern.xye --cif structure.cif \
  --refine-level basic -o result.json

# Full（再加占位因子和各向异性 Uani）：
python ${SKILL_DIR}/scripts/gsas2_rietveld.py \
  --data pattern.xye --cif structure.cif \
  --refine-level full --export-cif refined.cif -o result.json
```

### 输出 JSON

```json
{
  "success": true,
  "refine_level": "standard",
  "cell": {"a": 5.4309, "b": 5.4309, "c": 5.4309, "volume": 160.15},
  "cell_esd": {"a": 0.0001, ...},
  "r_factors": {"Rp": 8.5, "Rwp": 11.2, "wR": 11.2},
  "n_atoms": 1,
  "atoms": [{"label": "Si1", "element": "Si", "x": 0.0, "y": 0.0, "z": 0.0, "occ": 1.0, "uiso": 0.005}],
  "cif_file": "refined.cif"
}
```

### 三种精修等级

| level | 精修参数 | 适用 |
|-------|---------|------|
| basic | 背景 + scale + 晶胞 + U,V,W | 检验 CIF 与数据基本匹配 |
| standard（默认） | basic + 原子坐标 + Uiso | 一般 Rietveld 任务 |
| full | standard + 占位 + Uani | 数据质量好（GOF<2）才用 |

### Rietveld 质量阈值

| 评级 | Rwp | GOF |
|------|-----|-----|
| 好 | <10% | 1-2 |
| 可接受 | 10-15% | 2-3 |
| 差（检查模型） | >15% | >3 |

### 已验证

Si 标准结构合成图谱（真实 a=5.4309）：扰动起始 CIF 至 a=5.41，精修后回到 a=5.43053（误差 0.00037 Å）。

---

## Hard Constraints

- **必用脚本**：Pawley 用 `gsas2_pawley.py`，Rietveld 用 `gsas2_rietveld.py`。**不要自己写 GSAS-II scriptable 代码**——已踩过的坑（手动生成 Pawley 反射、固定 histogram Scale、避免 SVD singularity 等）已封装在脚本里。
- **不许编造**：精修失败就如实报告，不要伪造晶胞或 R-factor。
- **报 ESD**：Pawley/Rietveld 输出都要带 ESD（脚本默认输出）。
- **不做下游分析**：本 skill 不做热膨胀拟合、相变判定、物相鉴定。这些 agent 拿到精修结果后自己做（线性回归/统计判断）。
- **多温度数据有相变时**：分相分别跑 Pawley，初始晶胞要分别给。不要把 LTP 和 HTP 混在一起。

---

## 生产环境

- **镜像**：`registry.dp.tech/dptech/dp/native/prod-19853/xrd-app:dev-260119`（GSAS-II 已预装在 `/root/g2full/GSAS-II/GSASII`）
- **执行方式**：通过 bohrium-job 提交到上述镜像
- **开发/调试机**：SSH `gqfj1207340.bohrium.tech`（同样路径）

---

## When to Use

| 用户意图 | 用什么 |
|---------|-------|
| "从 PXRD 精修晶胞参数" | `gsas2_pawley.py` |
| "变温 PXRD，每个温度的晶胞" | `gsas2_pawley.py` × N |
| "PXRD + CIF 全谱拟合 / Rietveld" | `gsas2_rietveld.py` |
| "从 PXRD 鉴定物相" | `mcp-mat-xrd.xrd_phase_identification`（不在本 skill） |
| "拟合热膨胀斜率 / 找相变温度" | agent 拿 Pawley 结果自己做线性回归（不在本 skill） |
| "解单晶结构 / HKL 文件" | 不在本 skill 范围 |
