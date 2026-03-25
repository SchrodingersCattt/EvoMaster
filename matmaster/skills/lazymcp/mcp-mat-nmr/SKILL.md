---
name: mcp-mat-nmr
description: 当需要进行 NMR 波谱分析时调用本 skill。支持 NMR 数据库检索、化学位移预测和反向预测（从谱图推结构）。
skill_type: mcp-loader
mcp_server: mat_nmr
mcp_transport: sse
mcp_url: https://nmr-server-matmaster-uuid1764741165.appspace.bohrium.com/sse?token=1467bc01801642c09273966fcd04e3a6
---

# mat_nmr — NMR 波谱分析

## 工具列表

| 工具名 | 类型 | 说明 |
|--------|------|------|
| NMR_search_tool | sync | NMR 数据库检索 |
| NMR_predict_tool | sync | NMR 化学位移预测 |
| NMR_reverse_predict_tool | sync | NMR 反向预测，从谱图推断结构 |

## 典型用法

- 检索 NMR 参考数据: `mat_nmr_NMR_search_tool`
- 预测化学位移: `mat_nmr_NMR_predict_tool`
- 从谱图反向推断结构: `mat_nmr_NMR_reverse_predict_tool`

## 注意事项

- 仅注册 tool_include_only 中指定的三个工具
- executor 为 null，所有工具均在本地同步执行
