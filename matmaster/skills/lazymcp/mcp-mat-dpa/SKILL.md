---
name: mcp-mat-dpa
description: 当需要使用 DPA 通用势进行结构优化、分子动力学模拟、声子计算、弹性常数或 NEB 过渡态搜索时调用本 skill。
skill_type: mcp-loader
mcp_server: mat_dpa
---

# mat_dpa — DPA 通用势计算

## 连接方式

调用本 skill 后，下列工具已自动注册到你的工具列表中，可直接按工具名调用。无需手动连接 MCP 服务器。

## 工具列表

| 工具名 | 类型 | 说明 |
|--------|------|------|
| submit_optimize_structure | async | 使用 DP 模型优化晶体结构，支持多模型头 (Omat24/SPICE2/OC22/蛋白质/有机反应/MOF 等) |
| submit_calculate_phonon | async | 使用 DP 模型计算声子性质（热容、熵、自由能、声子谱、态密度） |
| submit_run_molecular_dynamics | async | 多阶段分子动力学模拟 (ASE 接口，非 LAMMPS) |
| submit_calculate_elastic_constants | async | 计算弹性常数（需结构已充分弛豫） |
| submit_run_neb | async | NEB (Nudged Elastic Band) 过渡态搜索，寻找两个弛豫结构间的最低能量路径 |
| query_job_status | async | 查询计算任务状态 (Running/Succeeded/Failed) |
| terminate_job | async | 终止计算任务 |
| get_job_results | async | 获取计算任务结果 |

## 典型用法

- 结构优化: `mat_dpa_submit_optimize_structure`
- 声子计算: `mat_dpa_submit_calculate_phonon`
- 分子动力学: `mat_dpa_submit_run_molecular_dynamics`
- 弹性常数: `mat_dpa_submit_calculate_elastic_constants`
- NEB 过渡态: `mat_dpa_submit_run_neb`
- 查询任务: `mat_dpa_query_job_status`
- 获取结果: `mat_dpa_get_job_results`

## 注意事项

- 未配置 tool_include_only，注册 MCP Server 暴露的所有工具（共 8 个）
- sync_tools 为空，所有工具均为异步执行，任务提交到 Bohrium GPU 集群
- 异步工具提交后使用 query_job_status 轮询状态，完成后用 get_job_results 获取结果
- 使用 NVIDIA 4090 GPU 机型 (c16_m64_1 * NVIDIA 4090)
- 支持多个预训练模型 (DPA2.4/DPA3.1/DPA3.2)，覆盖无机、有机、生物、催化等多个应用域
