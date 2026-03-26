---
name: mcp-mat-sn
description: 当需要检索学术论文或文献证据时调用本 skill。提供增强版论文搜索，返回标题、摘要、DOI 等结构化结果。
skill_type: mcp-loader
mcp_server: mat_sn
---

# mat_sn — 学术论文检索

## 连接方式

调用本 skill 后，下列工具已自动注册到你的工具列表中，可直接按工具名调用。无需手动连接 MCP 服务器。

## 工具列表

| 工具名 | 类型 | 说明 |
|--------|------|------|
| search-papers-enhanced | sync | 增强版论文搜索，返回标题、摘要、DOI 等结构化结果 |

## 典型用法

- 检索特定主题的论文: `mat_sn_search-papers-enhanced`

## 注意事项

- 仅注册 search-papers-enhanced 一个工具 (tool_include_only 过滤)
- 使用 SSE 传输协议连接 MCP Server
- 不属于 calculation_servers，所有工具均为同步执行
