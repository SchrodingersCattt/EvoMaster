---
name: mcp-mat-abacus
description: 当需要使用 ABACUS 进行第一性原理计算时调用本 skill。支持输入准备、参数修改、结构修改和结果收集。
skill_type: mcp-loader
mcp_server: mat_abacus
---

# mat_abacus — ABACUS 第一性原理计算

## MCP 服务器

- 传输协议: sse
- 地址: `http://toyl1410396.bohrium.tech:50001/sse`

## 工具列表

| 工具名 | 类型 | 说明 |
|--------|------|------|
| abacus_prepare | sync | 准备 ABACUS 计算输入文件 |
| abacus_modify_input | sync | 修改 ABACUS 输入参数 |
| abacus_modify_stru | sync | 修改 ABACUS 结构文件 |
| abacus_collect_data | sync | 收集 ABACUS 计算结果 |
| (其他 MCP Server 暴露的工具) | async | 通过 Bohrium CPU 集群异步执行 |

## 典型用法

- 准备 ABACUS 输入: `mat_abacus_abacus_prepare`
- 修改计算参数: `mat_abacus_abacus_modify_input`
- 修改结构文件: `mat_abacus_abacus_modify_stru`
- 收集计算结果: `mat_abacus_abacus_collect_data`

## 注意事项

- 未配置 tool_include_only，注册 MCP Server 暴露的所有工具
- sync_tools 中的 4 个工具在本地同步执行
- 其余工具通过 dispatcher 异步提交到 Bohrium CPU 集群 (c32_m128_cpu)
- 异步工具需要通过 monitor_job 轮询任务状态
