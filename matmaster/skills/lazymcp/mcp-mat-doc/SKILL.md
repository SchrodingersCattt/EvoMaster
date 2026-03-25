---
name: mcp-mat-doc
description: 当需要从 PDF 文档中提取材料科学数据时调用本 skill。自动识别并结构化提取论文中的晶体参数、性能数据等。
skill_type: mcp-loader
mcp_server: mat_doc
---

# mat_doc — PDF 文档数据提取

## MCP 服务器

- 传输协议: http
- 地址: `http://pfmx1355864.bohrium.tech:50001/mcp`

## 工具列表

| 工具名 | 类型 | 说明 |
|--------|------|------|
| extract_material_data_from_pdf | sync | 从 PDF 文档中提取材料科学数据 |

## 典型用法

- 从论文 PDF 提取晶体参数: `mat_doc_extract_material_data_from_pdf`

## 注意事项

- 仅注册 extract_material_data_from_pdf 一个工具 (tool_include_only 过滤)
- executor 为 null，工具在本地同步执行
