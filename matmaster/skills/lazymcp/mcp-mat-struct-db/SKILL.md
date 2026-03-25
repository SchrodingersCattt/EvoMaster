---
name: mcp-mat-struct-db
description: 当需要从数据库检索已知晶体结构时调用本 skill。支持按化学式、组成、材料 ID、原型检索，返回 CIF/POSCAR。
skill_type: mcp-loader
mcp_server: mat_struct_db
mcp_transport: http
mcp_url: https://mrdice-uuid1772180309.appspace.bohrium.com/mcp?token=a0d973885b6441eca858e165e90ae9c7
---

# mat_struct_db — 结构数据库检索

## 工具列表

| 工具名 | 类型 | 说明 |
|--------|------|------|
| fetch_structures_from_db | sync | 从数据库检索晶体结构，支持按化学式、组成、材料 ID、原型检索 |

## 典型用法

- 按化学式检索晶体结构: `mat_struct_db_fetch_structures_from_db`
- 按材料 ID 获取 CIF/POSCAR: `mat_struct_db_fetch_structures_from_db`

## 注意事项

- executor 类型为 local，仅注入 BOHRIUM_ACCESS_KEY / BOHRIUM_PROJECT_ID 到环境变量
- 仅使用同步工具，直接返回结果
