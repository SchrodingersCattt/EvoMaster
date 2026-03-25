---
name: mcp-mat-xrd
description: 当需要进行 XRD 物相鉴定时调用本 skill。输入衍射数据，返回匹配的物相及置信度。
skill_type: mcp-loader
mcp_server: mat_xrd
---

# mat_xrd — XRD 物相鉴定

## MCP 服务器

- 传输协议: sse
- 地址: `http://root@pkfz1410356.bohrium.tech:50001/sse`

## 工具列表

| 工具名 | 类型 | 说明 |
|--------|------|------|
| xrd_phase_identification | sync | XRD 物相鉴定，输入衍射数据返回匹配物相及置信度 |

## 典型用法

- XRD 物相鉴定: `mat_xrd_xrd_phase_identification`

## 注意事项

- 仅注册 xrd_phase_identification 一个工具 (tool_include_only 过滤)
- executor 为 null，工具在本地同步执行
