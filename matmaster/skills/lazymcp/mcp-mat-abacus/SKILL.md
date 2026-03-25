---
name: mcp-mat-abacus
description: 当需要使用 ABACUS 进行第一性原理计算时调用本 skill。支持 SCF、结构弛豫、能带、DOS、声子、弹性常数、Bader 电荷、功函数、ELF、EOS、空位形成能、AIMD 等。
skill_type: mcp-loader
mcp_server: mat_abacus
---

# mat_abacus — ABACUS 第一性原理计算

## 连接方式

调用本 skill 后，下列工具已自动注册到你的工具列表中，可直接按工具名调用。无需手动连接 MCP 服务器。

## 工具列表

| 工具名 | 类型 | 说明 |
|--------|------|------|
| submit_abacus_calculation_scf | async | SCF 自洽场计算，支持 LCAO 基组、多种泛函 (PBE/PBEsol/LDA/SCAN/HSE/PBE0/R2SCAN)、DFT+U、自旋极化 |
| submit_abacus_do_relax | async | 结构弛豫/优化 |
| submit_abacus_cal_band | async | 能带结构计算，支持 PYATB 或 ABACUS NSCF |
| submit_abacus_dos_run | async | 态密度 (DOS) 和投影态密度 (PDOS) 计算 |
| submit_abacus_phonon_dispersion | async | 声子色散计算 (有限差分法，Phonopy + ABACUS) |
| submit_abacus_cal_elastic | async | 弹性常数计算 |
| submit_abacus_badercharge_run | async | Bader 电荷分析 |
| submit_abacus_cal_work_function | async | 功函数和静电势计算 |
| submit_abacus_cal_elf | async | 电子局域化函数 (ELF) 计算 |
| submit_abacus_eos | async | 状态方程 (EOS) 计算，Birch-Murnaghan 拟合（当前仅支持立方体系） |
| submit_abacus_vacancy_formation_energy | async | 空位形成能计算（当前仅支持非带电空位） |
| submit_abacus_run_md | async | AIMD 从头算分子动力学模拟 |
| query_job_status | async | 查询计算任务状态 (Running/Succeeded/Failed) |
| terminate_job | async | 终止计算任务 |
| get_job_results | async | 获取计算任务结果 |

## 典型用法

- SCF 计算: `mat_abacus_submit_abacus_calculation_scf`
- 结构弛豫: `mat_abacus_submit_abacus_do_relax`
- 能带计算: `mat_abacus_submit_abacus_cal_band`
- DOS 计算: `mat_abacus_submit_abacus_dos_run`
- 声子色散: `mat_abacus_submit_abacus_phonon_dispersion`
- 弹性常数: `mat_abacus_submit_abacus_cal_elastic`
- 查询任务: `mat_abacus_query_job_status`
- 获取结果: `mat_abacus_get_job_results`

## 注意事项

- 未配置 tool_include_only，注册 MCP Server 暴露的所有工具（共 15 个）
- 所有 submit_* 计算工具通过 dispatcher 异步提交到 Bohrium CPU 集群 (c32_m128_cpu)
- 异步工具提交后使用 query_job_status 轮询状态，完成后用 get_job_results 获取结果
- 配置中 sync_tools 声明了 4 个本地工具 (abacus_prepare/modify_input/modify_stru/collect_data)，但当前 MCP Server 未暴露这些工具，实际全部走异步执行
