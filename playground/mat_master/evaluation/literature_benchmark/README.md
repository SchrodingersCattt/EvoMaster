# EvoMaster Mat_Master 计算材料文献复现计划（扩展版）

> 最后更新: 2026-02-20
>
> 本文档整理了 93 篇适合 Mat_Master 复现的开放获取计算材料科学文献，按计算类型分为 12 个大类。
> 所有文献均来自 **npj Computational Materials**、**Nature Communications** 或 **Chemical Science** 等 OA 期刊。
> 原文使用 VASP 的均可用 ABACUS 代替；简单后处理 EvoMaster 可自行编写代码完成。

## PDF 下载情况

`papers/` 目录下已下载 **84 篇** PDF 全文（含重复条目合并后的独立文件）。命名规则：`{类别编号}_{短标题}_{期刊缩写}{年份}.pdf`

重复条目已合并（K1 = E3, L5 = D4，仅保留一份 PDF）。

---

## 目录

- [A. 状态方程 / 晶格常数 / 体弹模量](#a-状态方程--晶格常数--体弹模量)
- [B. 弹性常数 / 力学性质](#b-弹性常数--力学性质)
- [C. 表面能 / 吸附能](#c-表面能--吸附能)
- [D. 缺陷形成能（空位 / 间隙 / 掺杂）](#d-缺陷形成能)
- [E. 电子结构 / 能带 / 态密度 / 带隙](#e-电子结构--能带--态密度--带隙)
- [F. 声子色散 / 热导率 / 热膨胀](#f-声子色散--热导率--热膨胀)
- [G. 分子动力学 / 力学变形 / 辐照](#g-分子动力学--力学变形)
- [H. 催化 / 吸附 / 反应路径](#h-催化--吸附--反应路径)
- [I. 电池 / 储能 / 离子扩散](#i-电池--储能--离子扩散)
- [J. 磁性 / 自旋](#j-磁性--自旋)
- [K. 二维材料](#k-二维材料)
- [L. 相稳定性 / 形成能 / 高熵合金](#l-相稳定性--形成能--高熵合金)
- [实施路线图](#实施路线图)

---

## A. 状态方程 / 晶格常数 / 体弹模量

| # | 文献简称 | 期刊 | 年份 | DOI / 链接 | 涉及结构 | 软件 | 核心结论 / 可复现内容 | 难度 |
|---|---------|------|------|-----------|----------|------|----------------------|------|
| A1 | Common workflows — EOS cross-verification | npj Comput. Mater. | 2021 | [10.1038/s41524-021-00594-6](https://doi.org/10.1038/s41524-021-00594-6) | Si, Al, GaAs, Fe, Ge, Ac 等 ~10 种 | QE, ABINIT, CP2K, VASP 等 11 个引擎 | 各代码 EOS 曲线高度一致；Birch-Murnaghan 拟合 V₀、B₀ | ★☆☆ |
| A2 | DFT precision verification | Nat. Rev. Phys. | 2023 | [10.1038/s42254-023-00655-3](https://doi.org/10.1038/s42254-023-00655-3) | 960 种原型立方化合物 (Z=1–96) | QE, ABINIT, VASP 等 | 全元素周期表 EOS 基准数据集 | ★☆☆ |
| A3 | Automated convergence optimization | npj Comput. Mater. | 2024 | [10.1038/s41524-024-01388-2](https://doi.org/10.1038/s41524-024-01388-2) | Si, Al 等标准测试体系 | Plane-wave DFT | 自动化收敛参数优化，无需手动调参 | ★☆☆ |
| A4 | Atomic stiffness for bulk modulus | Nat. Commun. | 2023 | [10.1038/s41467-023-39826-2](https://doi.org/10.1038/s41467-023-39826-2) | >100 万候选超硬材料 | VASP | 47 种超不可压缩晶体的 B₀ 预测 | ★★☆ |
| A5 | JARVIS-Leaderboard benchmark | npj Comput. Mater. | 2024 | [10.1038/s41524-024-01259-w](https://doi.org/10.1038/s41524-024-01259-w) | 数万种材料 | VASP (JARVIS-DFT) | 大规模材料性质基准：形成能、带隙、弹性 | ★☆☆ |
| A6 | UNEP-v1 — EOS for 16 metals | Nat. Commun. | 2024 | [10.1038/s41467-024-54554-x](https://doi.org/10.1038/s41467-024-54554-x) | Ag, Al, Au, Cr, Cu, Mg, Mo, Ni, Pb, Pd, Pt, Ta, Ti, V, W, Zr | VASP (DFT 基准) | 16 种金属 EOS 曲线、平衡体积 | ★☆☆ |
| A7 | MgO/GaAs/NaCl lattice dynamics benchmark | npj Comput. Mater. | 2024 | [10.1038/s41524-024-01437-w](https://doi.org/10.1038/s41524-024-01437-w) | MgO, GaAs, NaCl, BP, BaO 等 9 种立方结构 | VASP + HiPhive | 松弛后晶格常数、超胞力常数 | ★☆☆ |
| A8 | OQMD formation energy assessment | npj Comput. Mater. | 2015 | [10.1038/npjcompumats.2015.10](https://doi.org/10.1038/npjcompumats.2015.10) | ~30 万种化合物 | VASP | 形成能 MAE = 0.096 eV/atom vs 实验 | ★☆☆ |

---

## B. 弹性常数 / 力学性质

| # | 文献简称 | 期刊 | 年份 | DOI / 链接 | 涉及结构 | 软件 | 核心结论 / 可复现内容 | 难度 |
|---|---------|------|------|-----------|----------|------|----------------------|------|
| B1 | UNEP-v1 — elastic constants of 16 metals | Nat. Commun. | 2024 | [10.1038/s41467-024-54554-x](https://doi.org/10.1038/s41467-024-54554-x) | 16 种金属 (FCC/BCC/HCP) | VASP | C₁₁, C₁₂, C₄₄；B, G (Voigt-Reuss-Hill) | ★★☆ |
| B2 | Anisotropic T-dependent elastic constants | npj Comput. Mater. | 2025 | [10.1038/s41524-025-01765-5](https://doi.org/10.1038/s41524-025-01765-5) | 立方→三斜多种对称性材料 | VASP + Phonopy | ZSISA 方法：6–28 次声子计算得温度相关 Cij | ★★★ |
| B3 | Mg-Zn alloy strengthening phases | Sci. Rep. | 2025 | [10.1038/s41598-025-96708-x](https://doi.org/10.1038/s41598-025-96708-x) | MgZn₂, Mg₄Zn₇ 等 Mg-Zn 合金相 | VASP | 弹性各向异性、热力学稳定性 | ★★☆ |
| B4 | GSFE in Ni and Ni₃Al | Phys. Rev. Mater. | 2023 | (arXiv: 2303.16379) | Ni, Ni₃Al + 合金化元素 | VASP | 广义层错能 γ-surface，{111} 面不同滑移系 | ★★☆ |
| B5 | ML acceleration of SFE prediction | arXiv | 2024 | [arXiv:2405.04876](https://arxiv.org/abs/2405.04876) | FCC 金属及合金 | VASP + ML | 层错能预测加速 80×，与 DFT 一致 | ★★☆ |
| B6 | Dislocation mobility from atomistics | npj Comput. Mater. | 2024 | [10.1038/s41524-024-01394-4](https://doi.org/10.1038/s41524-024-01394-4) | BCC 金属 (W, Fe, Mo) | LAMMPS + ML 势 | 位错迁移率 vs 应力/温度 | ★★★ |
| B7 | Elastic anisotropy of molecular crystals | Nature Mater. | 2025 | [10.1038/s41563-025-02133-w](https://doi.org/10.1038/s41563-025-02133-w) | 分子晶体 | DFT-D | 弹性各向异性起源：分子间相互作用 | ★★★ |

---

## C. 表面能 / 吸附能

| # | 文献简称 | 期刊 | 年份 | DOI / 链接 | 涉及结构 | 软件 | 核心结论 / 可复现内容 | 难度 |
|---|---------|------|------|-----------|----------|------|----------------------|------|
| C1 | UNEP-v1 — surface energy of 16 metals | Nat. Commun. | 2024 | [10.1038/s41467-024-54554-x](https://doi.org/10.1038/s41467-024-54554-x) | 16 种金属 {111}/{110}/{100} 面 | VASP | γ(J/m²) for each surface; γ{111} < γ{100} < γ{110} | ★★☆ |
| C2 | H adsorption on FCC metal surfaces | Sci. Rep. | 2024 | [10.1038/s41598-024-71703-w](https://doi.org/10.1038/s41598-024-71703-w) | Ag, Au, Co, Cu, Ir, Ni, Pd, Pt, Rh 的 (100)(110)(111) | VASP | H 吸附能 vs 覆盖度；与 HER 电流密度线性相关 | ★★☆ |
| C3 | AdsorbML — ML-accelerated adsorption | npj Comput. Mater. | 2023 | [10.1038/s41524-023-01121-5](https://doi.org/10.1038/s41524-023-01121-5) | ~1000 种表面, ~10 万构型 | VASP | ML 加速吸附构型搜索 2000×；87% 准确率 | ★★★ |
| C4 | ML-driven adsorbate geometry optimization | npj Comput. Mater. | 2023 | [10.1038/s41524-023-01065-w](https://doi.org/10.1038/s41524-023-01065-w) | Rh(111), Rh(211) | VASP | ML 代理势实时训练，全局搜索吸附构型 | ★★★ |
| C5 | Automated surface facet analysis — Cs₂Te | npj Comput. Mater. | 2024 | [10.1038/s41524-024-01224-7](https://doi.org/10.1038/s41524-024-01224-7) | Cs₂Te 多晶型不同终端/Miller 指数 | VASP | 自动化表面性质计算 + Wulff 构型 | ★★☆ |
| C6 | OER catalyst screening — spinel oxides | Chem. Sci. | 2024 | [10.1039/D4SC00192C](https://doi.org/10.1039/D4SC00192C) | 6155 种三元尖晶石氧化物 | VASP | *O, *OH, *OOH 吸附能；Co₂.₅Ga₀.₅O₄ 过电位 220 mV | ★★★ |
| C7 | Atom-level OER on perovskite oxides | npj Comput. Mater. | 2024 | [10.1038/s41524-024-01273-y](https://doi.org/10.1038/s41524-024-01273-y) | 钙钛矿氧化物 ABO₃ | VASP | 表面 Bader 电荷、d-band center 与 OER 活性关联 | ★★★ |
| C8 | TiO₂/FeS₂ photocatalyst heterojunction | Phys. Chem. Chem. Phys. | 2024 | [10.1039/D3CP04453J](https://doi.org/10.1039/D3CP04453J) | TiO₂(anatase)/FeS₂ 异质结 | VASP | 能带对齐、功函数、电荷转移 | ★★☆ |
| C9 | Work function tuning in RuO₂/TiO₂ | Nat. Commun. | 2026 | [10.1038/s41467-026-69200-x](https://doi.org/10.1038/s41467-026-69200-x) | RuO₂/TiO₂ 外延异质结 | VASP | 功函数调控 >1 eV；界面极化 | ★★★ |
| C10 | Surface dipole engineering on oxides | Nat. Commun. | 2024 | [10.1038/s41467-024-45824-9](https://doi.org/10.1038/s41467-024-45824-9) | 混合导体氧化物 | VASP + XPS | 表面偶极工程：离子电位描述符 | ★★★ |

---

## D. 缺陷形成能

| # | 文献简称 | 期刊 | 年份 | DOI / 链接 | 涉及结构 | 软件 | 核心结论 / 可复现内容 | 难度 |
|---|---------|------|------|-----------|----------|------|----------------------|------|
| D1 | UNEP-v1 — vacancy formation of 16 metals | Nat. Commun. | 2024 | [10.1038/s41467-024-54554-x](https://doi.org/10.1038/s41467-024-54554-x) | 16 种金属单空位 | VASP | E_v (eV): W~3.3, Pt~1.2, Cu~1.1, Al~0.65 | ★★☆ |
| D2 | ML-accelerated point defect calculations | npj Comput. Mater. | 2024 | [10.1038/s41524-024-01303-9](https://doi.org/10.1038/s41524-024-01303-9) | CdSe_xTe_{1-x} 合金缺陷 | VASP | ML 预测缺陷构型，DFT 减少 73% | ★★★ |
| D3 | High-throughput defect benchmarking | npj Comput. Mater. | 2023 | [10.1038/s41524-023-01015-6](https://doi.org/10.1038/s41524-023-01015-6) | 245 个缺陷（多种半导体） | VASP | 半局域 DFT + 后验修正 vs HSE 金标准 | ★★★ |
| D4 | ABO₃ perovskite oxygen vacancy | Sci. Data | 2017 | [10.1038/sdata.2017.153](https://doi.org/10.1038/sdata.2017.153) | 5329 种 ABO₃ 钙钛矿 | VASP | 形成能、稳定性、氧空位形成能 | ★★☆ |
| D5 | O vacancy diffusion in SrTiO₃ | J. Chem. Phys. | 2016 | (OSTI: 1334419) | SrTiO₃ bulk | VASP | 氧空位扩散势垒 0.39–0.49 eV | ★★☆ |
| D6 | Perovskite surface vacancy instability | arXiv | 2025 | [arXiv:2504.15857](https://arxiv.org/abs/2504.15857) | SrTiO₃, BaTiO₃ (001) 面 | VASP | A 位空位 vs B 位空位稳定性差 1–2 eV | ★★★ |
| D7 | Strain-modulated defect in 2D materials | npj 2D Mater. | 2024 | [10.1038/s41699-024-00472-x](https://doi.org/10.1038/s41699-024-00472-x) | h-BN, graphene, MoSe₂, phosphorene | VASP | 外应变调控缺陷形成能 | ★★★ |
| D8 | Guidelines for point defect simulations | Nat. Rev. Mater. | 2025 | [10.1038/s41578-025-00879-y](https://doi.org/10.1038/s41578-025-00879-y) | 通用方法论 | — | 缺陷计算最佳实践指南 | ★☆☆ |

---

## E. 电子结构 / 能带 / 态密度 / 带隙

| # | 文献简称 | 期刊 | 年份 | DOI / 链接 | 涉及结构 | 软件 | 核心结论 / 可复现内容 | 难度 |
|---|---------|------|------|-----------|----------|------|----------------------|------|
| E1 | Perovskite ML screening — band gap | npj Comput. Mater. | 2024 | [10.1038/s41524-024-01270-1](https://doi.org/10.1038/s41524-024-01270-1) | 3159 种 B-位合金化 ABX₃ 钙钛矿 | VASP (PBEsol + PBE0) | CsPbI₃, CsSnI₃ 等带隙；PBE0+SOC 精确值 | ★★☆ |
| E2 | Hybrid halide perovskite monolayer design | npj Comput. Mater. | 2024 | [10.1038/s41524-024-01323-5](https://doi.org/10.1038/s41524-024-01323-5) | >400 种混合卤化物钙钛矿单层 | VASP | 带隙与 M-X-M 键角、M-X 键长的关联 | ★★☆ |
| E3 | G₀W₀ assessment for MoS₂ monolayer | npj Comput. Mater. | 2024 | [10.1038/s41524-024-01253-2](https://doi.org/10.1038/s41524-024-01253-2) | 单层 MoS₂ | VASP + YAMBO | G₀W₀ 带隙敏感度：几何、起点、SOC、库仑截断 | ★★★ |
| E4 | Band gap variation in layered oxides | Nat. Commun. | 2015 | [10.1038/ncomms7191](https://doi.org/10.1038/ncomms7191) | LaSrAlO₄ 层状氧化物 | VASP | 阳离子有序化使带隙变化 ~2 eV (200%) | ★★☆ |
| E5 | GW vs DFT band gap benchmark | npj Comput. Mater. | 2025 | [10.1038/s41524-025-01855-4](https://doi.org/10.1038/s41524-025-01855-4) | 数十种半导体 | VASP + GW | G₀W₀, QS-GW, QS-GŴ vs mBJ, HSE06 系统对比 | ★★★ |
| E6 | p-type monolayer WS₂ mobility | npj Comput. Mater. | 2024 | [10.1038/s41524-024-01417-0](https://doi.org/10.1038/s41524-024-01417-0) | WS₂ 单层 | VASP + EPW | 空穴迁移率 >1300 cm²/Vs；Boltzmann 输运 | ★★★ |
| E7 | TiO₂(001) surface electronic structure | Nat. Commun. | 2024 | [10.1038/s41467-024-46570-8](https://doi.org/10.1038/s41467-024-46570-8) | 锐钛矿 TiO₂(001)-(1×4) 重构面 | VASP | 配位环境 → 电子结构差异 | ★★★ |
| E8 | Band gap database (hybrid functional) | Sci. Data | 2020 | [10.1038/s41597-020-00723-8](https://doi.org/10.1038/s41597-020-00723-8) | 10481 种无机半导体 | VASP (HSE06) | RMSE = 0.36 eV vs 实验 | ★★☆ |
| E9 | ML-enhanced redox potential | npj Comput. Mater. | 2024 | [10.1038/s41524-024-01295-6](https://doi.org/10.1038/s41524-024-01295-6) | 氧化还原活性分子/离子 | VASP (PBE0) | 氧化还原电位 DFT + ML 联合预测 | ★★☆ |
| E10 | Dielectric tensor prediction | npj Comput. Mater. | 2024 | [10.1038/s41524-024-01450-z](https://doi.org/10.1038/s41524-024-01450-z) | 无机材料介电张量 | VASP (DFPT) | 电子/离子介电常数预测 | ★★☆ |
| E11 | Microwave dielectric materials | npj Comput. Mater. | 2024 | [10.1038/s41524-024-01366-8](https://doi.org/10.1038/s41524-024-01366-8) | 钨青铜结构等复杂体系 | VASP | 元素单元分解法计算介电性质 | ★★★ |
| E12 | NbAs Weyl semimetal surface conductance | npj Comput. Mater. | 2024 | [10.1038/s41524-024-01263-0](https://doi.org/10.1038/s41524-024-01263-0) | NbAs 拓扑半金属 | VASP + NEGF | 表面态主导弹道电导（达 70%） | ★★★ |
| E13 | Mixed perovskite band gap bowing | Mater. Adv. | 2025 | [10.1039/D4MA01050G](https://doi.org/10.1039/D4MA01050G) | CH₃NH₃Pb(I₁₋ₓBrₓ)₃ | QE | 全相对论 DFT 带隙 vs Br 含量二次行为 | ★★☆ |

---

## F. 声子色散 / 热导率 / 热膨胀

| # | 文献简称 | 期刊 | 年份 | DOI / 链接 | 涉及结构 | 软件 | 核心结论 / 可复现内容 | 难度 |
|---|---------|------|------|-----------|----------|------|----------------------|------|
| F1 | High-throughput lattice dynamics | npj Comput. Mater. | 2024 | [10.1038/s41524-024-01437-w](https://doi.org/10.1038/s41524-024-01437-w) | GaAs, NaCl, MgO, BP 等 30+ 种材料 | VASP + HiPhive + Phonopy | 声子色散、CTE、LTC；R² > 0.9 vs 实验 | ★★★ |
| F2 | Sampling-accelerated phonon scattering | npj Comput. Mater. | 2024 | [10.1038/s41524-024-01215-8](https://doi.org/10.1038/s41524-024-01215-8) | Si 等标准体系 | VASP | 热导率计算加速 10³–10⁴ 倍 | ★★★ |
| F3 | UNEP-v1 — phonon of 16 metals | Nat. Commun. | 2024 | [10.1038/s41467-024-54554-x](https://doi.org/10.1038/s41467-024-54554-x) | 16 种金属声子色散 | VASP (DFT 基准) | 声子频率 MAE 远低于 EAM | ★★☆ |
| F4 | Electron-phonon coupling verification | npj Comput. Mater. | 2025 | [10.1038/s41524-025-01587-5](https://doi.org/10.1038/s41524-025-01587-5) | 半导体带隙重整化 | ABINIT, QE, EPW | 多代码交叉验证：e-ph 自能、带隙修正 | ★★★ |
| F5 | Universal MLIP for phonons | npj Comput. Mater. | 2025 | [10.1038/s41524-025-01650-1](https://doi.org/10.1038/s41524-025-01650-1) | ~10000 种材料声子计算 | VASP + ML | 通用 MLIP 声子精度基准 | ★★★ |
| F6 | Phonon funneling in thin films | npj Comput. Mater. | 2024 | [10.1038/s41524-024-01364-w](https://doi.org/10.1038/s41524-024-01364-w) | 半导体薄膜 | VASP | 声子漏斗效应增强薄膜热导率 | ★★★ |
| F7 | THERMACOND code — Ge, GeSe, diamond | npj Comput. Mater. | 2025 | [10.1038/s41524-025-01673-8](https://doi.org/10.1038/s41524-025-01673-8) | Ge, GeSe, diamond | VASP + THERMACOND | 声子 BTE 热导率，不可约布里渊区 | ★★★ |
| F8 | Crystal-like transport in amorphous C | npj Comput. Mater. | 2025 | [10.1038/s41524-025-01625-2](https://doi.org/10.1038/s41524-025-01625-2) | 非晶碳 (~10⁵ 原子) | ML 势 + LAMMPS | κ~37 W/mK，传播振动模式主导 | ★★★ |
| F9 | Efficient thermoelectric transport — Mg₃Sb₂ | npj Comput. Mater. | 2024 | (Warwick: 182362) | Mg₃Sb₂ | VASP + EPW | 第一性原理电子输运，计算成本降至 10% | ★★★ |
| F10 | High-throughput anharmonic (773 crystals) | npj Comput. Mater. | 2025 | [10.1038/s41524-025-01920-y](https://doi.org/10.1038/s41524-025-01920-y) | 773 种立方/四方晶体 | VASP | 四声子散射普遍抑制热导率 | ★★★ |

---

## G. 分子动力学 / 力学变形

| # | 文献简称 | 期刊 | 年份 | DOI / 链接 | 涉及结构 | 软件 | 核心结论 / 可复现内容 | 难度 |
|---|---------|------|------|-----------|----------|------|----------------------|------|
| G1 | UNEP-v1 — MoTaVW HEA plasticity | Nat. Commun. | 2024 | [10.1038/s41467-024-54554-x](https://doi.org/10.1038/s41467-024-54554-x) | MoTaVW 难熔高熵合金 | GPUMD (NEP) | 拉伸变形、辐照损伤大规模 MD | ★★★ |
| G2 | CrCoFeNiMn HEA amorphization — DPMD | npj Comput. Mater. | 2025 | [10.1038/s41524-025-01561-1](https://doi.org/10.1038/s41524-025-01561-1) | CrCoFeNiMn Cantor 合金 | LAMMPS + DeepPotential | 不同冷却速率非晶化 → 力学性能优化 | ★★★ |
| G3 | Nuclear alloy scaling — pretrained LAM | npj Comput. Mater. | 2025 | [10.1038/s41524-025-01950-6](https://doi.org/10.1038/s41524-025-01950-6) | Ta-Nb-W-Mo-V 核用合金 | LAMMPS + LAM | 级联损伤、应力-应变行为 | ★★★ |
| G4 | General-purpose Ni potential | Commun. Mater. | 2024 | [10.1038/s43246-024-00603-3](https://doi.org/10.1038/s43246-024-00603-3) | Ni (FCC + HCP) | LAMMPS + DP | 弹性常数、声子、热膨胀 vs DFT | ★★☆ |
| G5 | Melting point prediction (20 elements) | Digital Discovery | 2024 | [10.1039/D4DD00069B](https://doi.org/10.1039/D4DD00069B) | 20 种元素 | LAMMPS + ML 势 | 熔点预测偏差 <18% vs 实验 | ★★☆ |
| G6 | UNEP-v1 — melting point of 16 metals | Nat. Commun. | 2024 | [10.1038/s41467-024-54554-x](https://doi.org/10.1038/s41467-024-54554-x) | 16 种金属 | GPUMD | 共存法确定 Tm，与实验对比 | ★★★ |
| G7 | Dislocation mobility — PI-GNN | npj Comput. Mater. | 2024 | [10.1038/s41524-024-01394-4](https://doi.org/10.1038/s41524-024-01394-4) | BCC W, Fe, Mo | LAMMPS | 位错迁移率函数拟合 | ★★★ |
| G8 | HEA phase transition under pressure | Sci. Rep. | 2024 | [10.1038/s41598-024-67422-x](https://doi.org/10.1038/s41598-024-67422-x) | NiCoFeCrAlW HEA | LAMMPS | BCC→FCC 相变 (<30 GPa) | ★★★ |

---

## H. 催化 / 吸附 / 反应路径

| # | 文献简称 | 期刊 | 年份 | DOI / 链接 | 涉及结构 | 软件 | 核心结论 / 可复现内容 | 难度 |
|---|---------|------|------|-----------|----------|------|----------------------|------|
| H1 | Fe-N₄ catalyst for CO₂ → CO | Nat. Commun. | 2023 | [10.1038/s41467-023-40667-2](https://doi.org/10.1038/s41467-023-40667-2) | Fe NP + Fe-N₄/C | VASP | *COOH 形成自由能、CO₂RR 机理 | ★★★ |
| H2 | W/WO₂ HER catalyst | Nat. Commun. | 2023 | [10.1038/s41467-023-41097-w](https://doi.org/10.1038/s41467-023-41097-w) | W/WO₂ 异质结 | VASP | H 吸附能、质子浓缩表面机制 | ★★☆ |
| H3 | Nitrate reduction on M-N-C | Nat. Commun. | 2023 | [10.1038/s41467-023-40174-4](https://doi.org/10.1038/s41467-023-40174-4) | 3d/4d/5d/f 金属 M-N₄ 位点 | VASP | 关联/解离吸附路径；NO₂⁻ 还原活性 | ★★★ |
| H4 | High-throughput biomass catalyst dataset | Sci. Data | 2024 | [10.1038/s41597-024-03872-2](https://doi.org/10.1038/s41597-024-03872-2) | 35 种表面（过渡金属 + 氧化物） | VASP | ~3000 种吸附构型/能量 | ★★☆ |
| H5 | Pd nanoparticle oxidative addition | Chem. Sci. | 2024 | [10.1039/D4SC00628C](https://doi.org/10.1039/D4SC00628C) | Pd NP 表面 | VASP + metadynamics | 芳基卤化物氧化加成：边缘 (111) 为活性位 | ★★★ |
| H6 | Barbaralyl cation rearrangement | Chem. Sci. | 2024 | [10.1039/D4SC04829F](https://doi.org/10.1039/D4SC04829F) | 分子（barbaralyl 碳正离子） | Gaussian / ORCA | 反应势垒 2.3 / 5.0 kcal/mol；AIMD 动力学 | ★★☆ |
| H7 | HAT barriers in proteins (ML) | Chem. Sci. | 2024 | [10.1039/D3SC03922F](https://doi.org/10.1039/D3SC03922F) | 蛋白质中 HAT 反应 | DFT (hybrid) | >17000 个反应势垒；GNN 预测 MAE < 3 kcal/mol | ★★★ |
| H8 | Cu-Ag dual HER catalyst | Nat. Commun. | 2023 | [10.1038/s41467-023-36142-7](https://doi.org/10.1038/s41467-023-36142-7) | Cu-Ag 电催化剂 | VASP | 甲醛氧化耦合析氢 | ★★★ |
| H9 | Urea synthesis from NO₃⁻ + CO₂ | Nat. Catal. | 2023 | [10.1038/s41929-023-01020-4](https://doi.org/10.1038/s41929-023-01020-4) | Zn/Cu 混合催化剂 | VASP | 接力催化机制；FE 75% | ★★★ |
| H10 | CeO₂ NP morphology under H₂O/CO₂ | Nanoscale | 2024 | [10.1039/D4NR01296H](https://doi.org/10.1039/D4NR01296H) | CeO₂ 纳米粒子各面 | VASP | 吸附强度 → 表面稳定性 → 形貌 | ★★★ |

---

## I. 电池 / 储能 / 离子扩散

| # | 文献简称 | 期刊 | 年份 | DOI / 链接 | 涉及结构 | 软件 | 核心结论 / 可复现内容 | 难度 |
|---|---------|------|------|-----------|----------|------|----------------------|------|
| I1 | Olivine LiFePO₄ DFT+U+V voltages | arXiv | 2022 | [arXiv:2203.15732](https://arxiv.org/abs/2203.15732) | LiMnPO₄, LiFePO₄ 等 | QE (DFT+U+V) | 嵌锂电压精确预测；氧化态数字化变化 | ★★☆ |
| I2 | Na-ion polyanion cathode dataset | Sci. Data | 2025 | [10.1038/s41597-025-05799-8](https://doi.org/10.1038/s41597-025-05799-8) | NaTMPO₄, Na₂TMSiO₄ 等 4 种钠正极 | VASP | 113,532 个 DFT 结构；电荷、AIMD 轨迹 | ★★★ |
| I3 | Li halide solid electrolyte design | Nat. Commun. | 2024 | [10.1038/s41467-024-45258-3](https://doi.org/10.1038/s41467-024-45258-3) | Li 卤化物固态电解质 | VASP + AIMD | 离子电位 → 设计原则；离子电导率 | ★★★ |
| I4 | Superionic halide electrolyte Li₃YCl₆ | Nat. Chem. | 2024 | [10.1038/s41557-024-01634-6](https://doi.org/10.1038/s41557-024-01634-6) | Li₃YCl₆, Li₃YCl₄.₅Br₁.₅ | VASP + AIMD | 阴离子集体运动触发超离子转变 | ★★★ |
| I5 | Li oxyhalide electrolyte conductivity | npj Comput. Mater. | 2024 | [10.1038/s41524-024-01346-y](https://doi.org/10.1038/s41524-024-01346-y) | Li₂.₅ZrCl₅.₅O₀.₅ 等 | VASP + AIMD | 非晶相离子电导率 ~40 mS/cm | ★★★ |
| I6 | Non-local interactions in Li electrolytes | Nat. Commun. | 2025 | [10.1038/s41467-025-56662-8](https://doi.org/10.1038/s41467-025-56662-8) | 硫银锗矿型电解质 | VASP (HSE06+MBD) | 非局域相互作用决定局部结构和扩散 | ★★★ |
| I7 | Intuitive cell voltage prediction | Nat. Commun. | 2014 | [10.1038/ncomms6559](https://doi.org/10.1038/ncomms6559) | LIB / SIB 正极材料 | VASP | 基于碱金属化晶体结构参数预测电压 | ★★☆ |
| I8 | NEB for Na diffusion in MgO | Chin. Phys. B | 2024 | (CPB: 127002) | MgO 中 Na 扩散 | VASP + NEB | NEB vs AIMD：扩散势垒 ~0.31 eV 一致 | ★★☆ |

---

## J. 磁性 / 自旋

| # | 文献简称 | 期刊 | 年份 | DOI / 链接 | 涉及结构 | 软件 | 核心结论 / 可复现内容 | 难度 |
|---|---------|------|------|-----------|----------|------|----------------------|------|
| J1 | Magnetic ground state determination | npj Comput. Mater. | 2024 | [10.1038/s41524-024-01202-z](https://doi.org/10.1038/s41524-024-01202-z) | NiO, FePS₃, FeP, MnF₂, FeCl₂, CuO | VASP | 线性自旋波稳定性条件 → 磁基态确定 | ★★★ |
| J2 | FeNi-based magnet ordering + MAE | npj Comput. Mater. | 2024 | [10.1038/s41524-024-01435-y](https://doi.org/10.1038/s41524-024-01435-y) | FeNi, FeNiPt, FeNiAl 等 L1₀ 合金 | VASP | 原子有序化 + 磁晶各向异性能 | ★★★ |
| J3 | Heusler/MgO perpendicular MA screening | npj Comput. Mater. | 2023 | [10.1038/s41524-023-01079-4](https://doi.org/10.1038/s41524-023-01079-4) | 27000 种四元 Heusler/MgO 异质结 | VASP | 垂直磁各向异性 PMA 高通量筛选 | ★★★ |
| J4 | Weyl node in MnSb₂Te₄ | Nat. Commun. | 2024 | [10.1038/s41467-024-53319-w](https://doi.org/10.1038/s41467-024-53319-w) | MnSb₂Te₄ 拓扑磁体 | VASP | Weyl 节点倾斜 → 手性输运 | ★★★ |
| J5 | Weyl ferromagnet (Cr,Bi)₂Te₃ | Nature | 2024 | [10.1038/s41586-024-08330-y](https://doi.org/10.1038/s41586-024-08330-y) | (Cr,Bi)₂Te₃ vdW 材料 | VASP | 异常 Hall 角 >0.5；拓扑相图 | ★★★ |
| J6 | AlxCrFeCoNi HEA short-range order | npj Comput. Mater. | 2024 | [10.1038/s41524-024-01445-w](https://doi.org/10.1038/s41524-024-01445-w) | AlₓCrFeCoNi HEA | VASP (KKR-CPA) | 化学短程序、L1₂/B2 有序化趋势 | ★★★ |

---

## K. 二维材料

| # | 文献简称 | 期刊 | 年份 | DOI / 链接 | 涉及结构 | 软件 | 核心结论 / 可复现内容 | 难度 |
|---|---------|------|------|-----------|----------|------|----------------------|------|
| K1 | G₀W₀ for MoS₂ (critical assessment) | npj Comput. Mater. | 2024 | [10.1038/s41524-024-01253-2](https://doi.org/10.1038/s41524-024-01253-2) | 单层 MoS₂ | VASP/YAMBO | 带隙 ~2.5 eV (G₀W₀)；计算细节影响 | ★★★ |
| K2 | High-throughput 2D screening — WS₂ | npj Comput. Mater. | 2024 | [10.1038/s41524-024-01417-0](https://doi.org/10.1038/s41524-024-01417-0) | WS₂ 单层 | VASP | p 型超高迁移率半导体候选 | ★★★ |
| K3 | Bilayer materials dataset (760 structures) | Sci. Data | 2023 | [10.1038/s41597-023-02146-7](https://doi.org/10.1038/s41597-023-02146-7) | TMDs, graphene, BN, silicene 双层 | VASP | 结构、电子、输运性质 | ★★☆ |
| K4 | MoS₂ on graphene self-intercalation | npj 2D Mater. | 2024 | [10.1038/s41699-024-00488-3](https://doi.org/10.1038/s41699-024-00488-3) | MoS₂/graphene | VASP | 自插层 → 带隙/能谷变化 | ★★★ |
| K5 | Strain-defect engineering in 2D | npj 2D Mater. | 2024 | [10.1038/s41699-024-00472-x](https://doi.org/10.1038/s41699-024-00472-x) | h-BN, graphene, MoSe₂, 黑磷 | VASP | 应变调控缺陷形成能 | ★★★ |
| K6 | Phonon in vdW MoS₂ bilayers | npj Comput. Mater. | 2024 | [10.1038/s41524-024-01315-5](https://doi.org/10.1038/s41524-024-01315-5) | MoS₂ 双层 + 纳米孔 | LAMMPS + VASP | SAW 探测声子聚焦、热力学行为 | ★★★ |
| K7 | Band edge optical transitions in monolayers | arXiv | 2024 | [arXiv:2409.18287](https://arxiv.org/abs/2409.18287) | ZrNCl, TiNBr, BiTeCl 等 | VASP | 直接带隙单层的光耦合强度 | ★★☆ |

---

## L. 相稳定性 / 形成能 / 高熵合金

| # | 文献简称 | 期刊 | 年份 | DOI / 链接 | 涉及结构 | 软件 | 核心结论 / 可复现内容 | 难度 |
|---|---------|------|------|-----------|----------|------|----------------------|------|
| L1 | Bayesian convex hull search | npj Comput. Mater. | 2024 | [10.1038/s41524-024-01391-7](https://doi.org/10.1038/s41524-024-01391-7) | Co-Ni, Zr-O, Ni-Al-Cr | VASP + CE | 贝叶斯优化减少凸包搜索 >30% | ★★★ |
| L2 | Exhaustive alloy search with ML | npj Comput. Mater. | 2024 | [10.1038/s41524-024-01452-x](https://doi.org/10.1038/s41524-024-01452-x) | 多组元合金 | VASP + LRP | 穷举搜索凸包上金属间化合物 | ★★★ |
| L3 | NbTaV alloy DFT dataset | Sci. Data | 2024 | [10.1038/s41597-024-03720-3](https://doi.org/10.1038/s41597-024-03720-3) | NbTa, NbV, TaV, NbTaV BCC 合金 | VASP | 3100–10500 个构型形成能 | ★★☆ |
| L4 | HEA phase fraction ML prediction | npj Comput. Mater. | 2024 | [10.1038/s41524-024-01335-1](https://doi.org/10.1038/s41524-024-01335-1) | 多种 HEA | VASP + ML | 传统 ML vs DNN 预测相分数 | ★★★ |
| L5 | ABO₃ perovskite formation energy (5329) | Sci. Data | 2017 | [10.1038/sdata.2017.153](https://doi.org/10.1038/sdata.2017.153) | 5329 种 ABO₃ 钙钛矿 | VASP | 形成能、稳定性；395 种热力学稳定 | ★★☆ |
| L6 | Superconductor Tc prediction | npj Comput. Mater. | 2024 | [10.1038/s41524-024-01475-4](https://doi.org/10.1038/s41524-024-01475-4) | 818 种动力学稳定材料 | QE + EPW | α²F(ω) → Tc 预测；DL 加速 | ★★★ |

---

## 实施路线图

### Phase 0: 能力验证 (1–2 天)

> 目标：验证 Mat_Master 端到端工作流可行性

| 序号 | 任务 | 来源 | 涉及结构 | 工具 | 产出 |
|------|------|------|----------|------|------|
| 0.1 | Al FCC EOS | A1/A6 | Al | ABACUS | V₀, B₀ |
| 0.2 | Si diamond EOS | A1 | Si | ABACUS | V₀, B₀ |
| 0.3 | Cu FCC 弹性常数 | B1 | Cu | ABACUS | C₁₁, C₁₂, C₄₄ |
| 0.4 | Al FCC 空位形成能 | D1 | Al (3×3×3) | ABACUS | E_v |

### Phase 1: 核心性质矩阵 (3–5 天)

> 目标：对 5–8 种典型材料覆盖 EOS + 弹性 + 表面 + 空位

| 序号 | 任务 | 来源 | 涉及结构 | 工具 | 产出 |
|------|------|------|----------|------|------|
| 1.1 | W BCC 弹性常数 | B1 | W | ABACUS | Cij, B, G |
| 1.2 | Cu {111}/{100}/{110} 表面能 | C1 | Cu slabs | ABACUS | γ (J/m²) |
| 1.3 | Au 表面能 | C1 | Au slabs | ABACUS | γ (J/m²) |
| 1.4 | Cu 空位形成能 | D1 | Cu (3×3×3) | ABACUS | E_v |
| 1.5 | CsPbI₃ 带隙 | E1 | CsPbI₃ | ABACUS | a, E_gap |
| 1.6 | CsSnI₃ 带隙 | E1 | CsSnI₃ | ABACUS | a, E_gap |
| 1.7 | CsPbBr₃ 带隙 | E1 | CsPbBr₃ | ABACUS | a, E_gap |
| 1.8 | GaAs EOS | A1 | GaAs | ABACUS | V₀, B₀ |

### Phase 2: 进阶计算 (5–10 天)

> 目标：声子、催化、MD 等复杂工作流

| 序号 | 任务 | 来源 | 涉及结构 | 工具 | 产出 |
|------|------|------|----------|------|------|
| 2.1 | MgO 声子色散 | F1/F3 | MgO | ABACUS + Phonopy | 声子谱 |
| 2.2 | GaAs 声子色散 | F1 | GaAs | ABACUS + Phonopy | 声子谱 |
| 2.3 | SrTiO₃ 氧空位形成能 | D4/D5 | SrTiO₃ (3×3×3) | ABACUS | E_v(O) |
| 2.4 | H 在 Cu(111) 吸附能 | C2 | Cu(111) slab + H | ABACUS | E_ads |
| 2.5 | H 在 Pt(111) 吸附能 | C2 | Pt(111) slab + H | ABACUS | E_ads |
| 2.6 | Fe BCC 弹性常数 (磁性) | B1 | Fe (spin-polarized) | ABACUS | Cij |
| 2.7 | LiFePO₄ 嵌锂电压 | I1 | LiFePO₄/FePO₄ | ABACUS/QE | ΔV |
| 2.8 | Cu-Ni 合金 MD 拉伸 | G4 | Cu₅₀Ni₅₀ | LAMMPS + DPA | σ-ε 曲线 |

### Phase 3: 大规模复现 (持续)

> 目标：系统覆盖上述 93 篇文献中的核心计算

按类别逐步推进，每类选取 2–3 篇代表性文献做全流程复现。

---

## Mat_Master 工具能力映射

| 计算类型 | MCP Server | 工具/技能 | 可否执行 |
|----------|-----------|----------|---------|
| 结构获取 (MP/OPTIMADE) | `mat_struct_db` | `structure-manager` | ✅ |
| 结构操作 (切面/超胞/掺杂) | `mat_sg` | `structure-manager` | ✅ |
| DFT — ABACUS | `mat_abacus` | `input-manual-helper` | ✅ (首选) |
| DFT — QE / CP2K / ABINIT | `mat_binary_calc` | `input-manual-helper` | ✅ |
| DFT — ORCA (分子) | `mat_binary_calc` | `input-manual-helper` | ✅ |
| EOS 拟合 (B-M) | — | `result-analysis` / 自写代码 | ✅ |
| 弹性常数 (应力-应变法) | `mat_abacus` | `result-analysis` / 自写代码 | ✅ |
| 声子 (Phonopy) | `mat_binary_calc` | 自写代码调用 phonopy | ✅ |
| MD — LAMMPS | `mat_binary_calc` | `input-manual-helper` | ✅ |
| ML 势 — DPA | `mat_dpa` | — | ✅ |
| NEB 过渡态 | `mat_abacus` | `input-manual-helper` | ✅ |
| 能带/DOS 后处理 | — | 自写 Python 代码 | ✅ |
| 表面/缺陷构建 | `mat_sg` | `structure-manager` | ✅ |
| 文献搜索 | `mat_sn` | `deep-survey` | ✅ |
| 结果可视化 | — | `result-analysis` | ✅ |
| VASP 执行 | ❌ (blocked) | 用 ABACUS 代替 | ⚠️ |

---

## 定量成功标准

| 性质 | 允许偏差 | 说明 |
|------|----------|------|
| 晶格常数 | < 1–2% | PBE vs PBEsol 系统差异 ~0.5–1% |
| 体弹模量 B | < 15% | 弹性性质对计算设置敏感 |
| 弹性常数 Cij | < 15% | 不同代码间本身有 ~10% 差异 |
| 表面能 γ | < 0.15 J/m² | 需关注 slab 层数收敛 |
| 空位形成能 Ev | < 0.3 eV | 超胞大小是主要误差来源 |
| 带隙 (PBE) | 定性正确 | PBE 系统低估 30–50%，关注趋势 |
| 声子频率 | < 15% | 对 k-mesh 和晶格常数敏感 |
| 吸附能 | < 0.2 eV | 赝势和 DFT 泛函差异 |
| MD 力学性质 | < 20% | 应变率和势函数依赖 |

---

## 风险矩阵

| 风险 | 影响 | 概率 | 缓解措施 |
|------|------|------|----------|
| ABACUS 赝势与 VASP PAW 不一致 | 数值偏差增大 | 中 | SG15 赝势，接受合理系统偏差 |
| 磁性体系 (Fe, Ni) SCF 不收敛 | 计算失败 | 中 | 设置合理初始磁矩和 smearing |
| 重元素 SOC 效应 (Pb, Bi, W) | 带隙偏差 | 中 | ABACUS 支持 SOC，需开启 |
| 声子计算超胞太大 | 计算资源不足 | 低 | 先做高对称材料 (MgO, NaCl) |
| DPA 不支持某些元素组合 | MD 无法运行 | 中 | 退而做已验证的二元体系 |
| 催化表面计算收敛困难 | 计算耗时长 | 中 | 使用 gamma-only k 点和合理截断能 |
| AIMD 计算资源需求大 | 无法完成 | 高 | 用 ML 势替代 AIMD |

---

## 附录：文献完整列表（按类别编号）

### A. 状态方程 / 晶格常数 / 体弹模量
- A1: Huber et al., npj Comput. Mater. 7, 136 (2021). DOI: 10.1038/s41524-021-00594-6
- A2: Bosoni et al., Nat. Rev. Phys. 6, 45 (2024). DOI: 10.1038/s42254-023-00655-3
- A3: npj Comput. Mater. (2024). DOI: 10.1038/s41524-024-01388-2
- A4: Nat. Commun. 14, 5726 (2023). DOI: 10.1038/s41467-023-39826-2
- A5: npj Comput. Mater. 10, 93 (2024). DOI: 10.1038/s41524-024-01259-w
- A6: Song et al., Nat. Commun. 15, 10208 (2024). DOI: 10.1038/s41467-024-54554-x
- A7: Zhu et al., npj Comput. Mater. 10, 265 (2024). DOI: 10.1038/s41524-024-01437-w
- A8: Kirklin et al., npj Comput. Mater. 1, 15010 (2015). DOI: 10.1038/npjcompumats.2015.10

### B. 弹性常数 / 力学性质
- B1–B7: 见上表

### C. 表面能 / 吸附能
- C1–C10: 见上表

### D. 缺陷形成能
- D1–D8: 见上表

### E. 电子结构 / 能带 / 带隙
- E1–E13: 见上表

### F. 声子 / 热导率 / 热膨胀
- F1–F10: 见上表

### G. 分子动力学 / 力学变形
- G1–G8: 见上表

### H. 催化 / 吸附 / 反应路径
- H1–H10: 见上表

### I. 电池 / 储能 / 离子扩散
- I1–I8: 见上表

### J. 磁性 / 自旋
- J1–J6: 见上表

### K. 二维材料
- K1–K7: 见上表

### L. 相稳定性 / 形成能 / 高熵合金
- L1–L6: 见上表

**总计：93 篇文献条目**
