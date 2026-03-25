---
name: mcp-mat-dpa
description: 当需要使用 DPA 通用势进行分子动力学模拟或结构优化时调用本 skill。支持能量计算、结构弛豫、MD 模拟等。
skill_type: mcp-loader
mcp_server: mat_dpa
mcp_transport: http
mcp_url: https://dpa-uuid1750659890.appspace.bohrium.com/mcp?token=b2b94c52d10141e992514f9d17bcca23
---

# mat_dpa — DPA 通用势计算

## 工具列表

| 工具名 | 类型 | 说明 |
|--------|------|------|
| (MCP Server 注册的所有工具) | async | 所有工具均通过 Bohrium GPU 异步执行 |

## 典型用法

- DPA 势能量计算: 调用 MCP Server 暴露的能量计算工具
- 结构弛豫/优化: 调用 MCP Server 暴露的优化工具
- 分子动力学模拟: 调用 MCP Server 暴露的 MD 工具

## 注意事项

- 未配置 tool_include_only，注册 MCP Server 暴露的所有工具
- sync_tools 为空列表，所有工具均为异步执行，任务提交到 Bohrium GPU 集群
- 异步工具需要通过 monitor_job 轮询任务状态
- 使用 NVIDIA 4090 GPU 机型 (c16_m64_1 * NVIDIA 4090)
