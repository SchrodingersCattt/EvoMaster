---
name: mcp-mat-struct-db
description: 当需要从数据库检索已知晶体结构时调用本 skill。支持按化学式、组成、材料 ID、原型检索，返回 CIF/POSCAR。
skill_type: mcp-loader
mcp_server: mat_struct_db
---

# mat_struct_db — 结构数据库检索

## 连接方式

调用本 skill 后，下列工具已自动注册到你的工具列表中，可直接按工具名调用。无需手动连接 MCP 服务器。

## 工具列表

| 工具名 | 类型 | 说明 |
|--------|------|------|
| fetch_structures_from_db | sync | 从多个数据库并行检索晶体结构，支持意图识别、按化学式/组成/材料 ID/原型检索，返回 CIF/POSCAR |
| query_job_status | async | 查询计算任务状态 (Running/Succeeded/Failed) |
| terminate_job | async | 终止计算任务 |
| get_job_results | async | 获取计算任务结果 |

## 典型用法

- 按化学式检索晶体结构: `mat_struct_db_fetch_structures_from_db`
- 查询任务状态: `mat_struct_db_query_job_status`
- 获取任务结果: `mat_struct_db_get_job_results`

## 注意事项

- 未配置 tool_include_only，注册 MCP Server 暴露的所有工具（共 4 个）
- fetch_structures_from_db 为同步工具（在 sync_tools 中），直接返回结果
- query_job_status/terminate_job/get_job_results 为任务管理工具，用于管理通过其他 MCP Server 提交的异步任务
- executor 类型为 local，仅注入 BOHRIUM_ACCESS_KEY / BOHRIUM_PROJECT_ID 到环境变量
