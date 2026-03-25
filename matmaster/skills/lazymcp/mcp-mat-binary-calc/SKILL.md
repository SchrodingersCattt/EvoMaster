---
name: mcp-mat-binary-calc
description: 当需要为 CP2K/QE/ABINIT/LAMMPS/ORCA/PySCF/GROMACS 准备或提交计算任务时调用本 skill。各引擎有独立的 prepare 工具生成输入文件。
skill_type: mcp-loader
mcp_server: mat_binary_calc
---

# mat_binary_calc — 多引擎计算任务

## MCP 服务器

- 传输协议: http
- 地址: `http://gjao1318755.bohrium.tech:50002/mcp`

## 工具列表

| 工具名 | 类型 | 说明 |
|--------|------|------|
| prepare_abinit_job | sync | 准备 ABINIT 计算输入文件 |
| prepare_cp2k_job | sync | 准备 CP2K 计算输入文件 |
| prepare_qe_job | sync | 准备 Quantum ESPRESSO 计算输入文件 |
| prepare_lammps_job | sync | 准备 LAMMPS 计算输入文件 |
| prepare_orca_job | sync | 准备 ORCA 计算输入文件 |
| prepare_pyatb_job | sync | 准备 PyATB 计算输入文件 |
| submit_run_gromacs | async | 提交 GROMACS 分子动力学计算任务到 Bohrium |

## 典型用法

- 准备 LAMMPS 输入文件: `mat_binary_calc_prepare_lammps_job`
- 准备 CP2K 输入文件: `mat_binary_calc_prepare_cp2k_job`
- 提交 GROMACS 计算: `mat_binary_calc_submit_run_gromacs`

## 注意事项

- 仅注册 tool_include_only 中指定的 7 个工具
- 6 个 prepare_* 工具为同步执行，直接在本地生成输入文件
- submit_run_gromacs 为异步执行，任务提交到 Bohrium 集群
- 不同引擎使用不同 Docker 镜像，通过 executor_map 按工具名分发
- submit_run_gromacs 的 input_files 参数需要 OSS 上传 (path_params_by_tool)
