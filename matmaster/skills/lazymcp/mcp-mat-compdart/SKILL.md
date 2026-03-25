---
name: mcp-mat-compdart
description: 当需要进行多组分成分优化或遗传算法搜索时调用本 skill。支持 DART GA 成分空间探索和目标性能优化。
skill_type: mcp-loader
mcp_server: mat_compdart
mcp_transport: http
mcp_url: https://dart-uuid1754393230.appspace.bohrium.com/mcp?token=b3a955c99823427683843616328023d8
---

# mat_compdart — 成分优化与遗传算法

## 工具列表

| 工具名 | 类型 | 说明 |
|--------|------|------|
| (MCP Server 注册的所有工具) | async | 所有工具均通过 Bohrium GPU 异步执行 |

## 典型用法

- DART GA 成分空间探索: 调用 MCP Server 暴露的遗传算法工具
- 多目标性能优化: 调用 MCP Server 暴露的优化工具

## 注意事项

- 未配置 tool_include_only，注册 MCP Server 暴露的所有工具
- sync_tools 为空列表，所有工具均为异步执行，任务提交到 Bohrium GPU 集群
- 异步工具需要通过 monitor_job 轮询任务状态
- 使用 NVIDIA 4090 GPU 机型 (c16_m64_1 * NVIDIA 4090)
