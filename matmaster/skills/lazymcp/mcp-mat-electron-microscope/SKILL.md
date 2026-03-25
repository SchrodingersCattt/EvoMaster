---
name: mcp-mat-electron-microscope
description: 当需要分析电子显微镜图像时调用本 skill。支持 SEM/TEM 图像的自动识别与特征分析。
skill_type: mcp-loader
mcp_server: mat_electron_microscope
---

# mat_electron_microscope — 电子显微镜图像分析

## MCP 服务器

- 传输协议: sse
- 地址: `http://root@pkfz1410356.bohrium.tech:50002/sse`

## 工具列表

| 工具名 | 类型 | 说明 |
|--------|------|------|
| get_electron_microscope_recognize | sync | 电子显微镜图像识别与特征分析 |

## 典型用法

- SEM/TEM 图像自动分析: `mat_electron_microscope_get_electron_microscope_recognize`

## 注意事项

- 仅注册 get_electron_microscope_recognize 一个工具 (tool_include_only 过滤)
- executor 为 null，工具在本地同步执行
