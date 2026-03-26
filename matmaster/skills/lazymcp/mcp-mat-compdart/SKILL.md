---
name: mcp-mat-compdart
description: 当需要进行多组分成分优化或遗传算法搜索时调用本 skill。支持 DART GA 成分空间探索和目标性能优化。
skill_type: mcp-loader
mcp_server: mat_compdart
---

# mat_compdart — 成分优化与遗传算法

## 连接方式

调用本 skill 后，下列工具已自动注册到你的工具列表中，可直接按工具名调用。无需手动连接 MCP 服务器。

## 工具列表

| 工具名 | 类型 | 说明 |
|--------|------|------|
| submit_run_dart_ga | async | 遗传算法成分优化，支持多目标优化、代理模型/线性混合目标、成分约束（元素分数范围）、初始种群 |
| query_job_status | async | 查询计算任务状态 (Running/Succeeded/Failed) |
| terminate_job | async | 终止计算任务 |
| get_job_results | async | 获取计算任务结果 |

## 典型用法

- 遗传算法成分优化: `mat_compdart_submit_run_dart_ga`
- 查询任务状态: `mat_compdart_query_job_status`
- 获取优化结果: `mat_compdart_get_job_results`

## 注意事项

- 未配置 tool_include_only，注册 MCP Server 暴露的所有工具（共 4 个）
- sync_tools 为空，所有工具均为异步执行，任务提交到 Bohrium GPU 集群
- 异步工具提交后使用 query_job_status 轮询状态，完成后用 get_job_results 获取结果
- 使用 NVIDIA 4090 GPU 机型 (c16_m64_1 * NVIDIA 4090)
