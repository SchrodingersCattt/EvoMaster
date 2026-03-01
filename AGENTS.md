# AGENTS.md — AI 编程助手项目约定

本文件为 AI 编程助手提供项目级约定与上下文，请在所有编辑与生成代码时遵守。

---

## Import 规范

**所有 import 必须放在文件最前面。**

- 每个源文件顶部的 import 应紧接在文件开头（可在 shebang、编码声明或 docstring 之后），且**不得**在 import 块之后、再在文件中间或函数/类内部插入新的 import。
- 新增依赖时，将 `import` / `from ... import ...` 统一放在文件顶部的 import 区域，并按项目既有风格分组排序（如：标准库 → 第三方 → 本地包）。

### ✅ 正确示例

```python
# 标准库
import asyncio
import json
from datetime import datetime

# 第三方
from fastapi import FastAPI

# 本地
from src.utils.logger import setup_logging

def main():
    ...
```

### ❌ 避免

```python
def main():
    import json  # 不要写在函数内部
    ...
```

```python
import os

SOME_CONST = 1

import sys  # 不要插在常量或代码中间
```

---

## 异常处理

**应用已在全局做了 error handler，各层异常可向上抛出，由统一异常处理返回给调用方。**

- **DAO 层**：不要用 try/except 捕获并吞掉异常。避免在 DAO 里 `except ...: logger.error(...); return False/0` 等写法，否则上层无法区分“业务无数据”与“数据库错误”。
- **服务层（如调用外部 HTTP 的 quota_service）**：可不在此处捕获，让异常向上抛出，由全局 handler 统一处理；若确有降级需求（如外部不可用时返回默认值），再在调用处或本层按需捕获并写明原因。

---

## bohr-agent-sdk 与本项目的关系

**bohr-agent-sdk**（[dptech-corp/bohr-agent-sdk](https://github.com/dptech-corp/bohr-agent-sdk)）是 Bohrium 官方的科学计算 Agent SDK，用于把科学计算程序封装成 MCP 标准服务。本仓库（matmaster-evo）作为 **MCP 客户端 / Agent 侧**，与基于 bohr-agent-sdk 部署的 **MCP Server** 配合使用。

### 角色划分

| 角色 | 本项目（matmaster-evo） | bohr-agent-sdk |
|------|-------------------------|----------------|
| 定位 | MCP 客户端：发起 CallTool，传 executor / storage 等参数 | MCP Server 侧：CalculationMCPServer 接收参数，执行/提交任务 |
| 鉴权注入 | 在 Path Adaptor 中注入：`inject_bohrium_executor`、`get_bohrium_storage_config`（用 session 的 access_key 等） | Server 侧用收到的 executor / storage 做 `init_executor`、`init_storage`，不负责鉴权来源 |
| 配置 | `mcp.calculation_executors`、`mcp.calculation_servers`（config.yaml） | Server 端自己的部署与工具实现 |

### 数据流（executor / storage）

1. **本仓库**：Path Adaptor（`evomaster/adaptors/calculation/path_adaptor.py`）根据 `calculation_executors` 解析出 executor 模板，经 `inject_bohrium_executor` 注入 access_key、project_id、user_id 及 `resources.envs`（如 `BOHRIUM_PROJECT_ID`）；storage 由 `get_bohrium_storage_config` 生成。二者写入工具参数 `args`，经 `MCPTool` → `mcp_connection.call_tool(tool_name, args)` 随 MCP 协议发出。
2. **MCP Server（bohr-agent-sdk）**：CalculationMCPServer 收到的 CallTool `arguments` 中包含 `executor`、`storage` 与业务参数。`submit_job` / `run_job` 中调用 `init_executor(executor)`、`init_storage(storage)`，用 executor 提交任务（DispatcherExecutor 或 LocalExecutor），用 storage 做输入下载/结果上传。

### 与本仓库直接相关的约定

- **executor 类型**：本仓库对 `executor.type == "dispatcher"` 注入 machine.remote_profile 与 resources.envs；对 `executor.type == "local"` 仅注入 `executor.env` 的 BOHRIUM_ACCESS_KEY 与 BOHRIUM_PROJECT_ID，供 bohr-agent-sdk 的 LocalExecutor 在本地运行时使用（`evomaster/env/bohrium.py`）。
- **配置结构**：executor 模板来自 `mcp_config.calculation_executors[server_name].executor` 或 `executor_map[tool_name]`；未出现在 `calculation_executors` 中的 server（如纯 DB 检索）不会注入 executor，仅会注入 storage（若在 `calculation_servers` 中）。
- **文档与兼容**：修改 Path Adaptor、executor/storage 结构或鉴权注入逻辑时，需考虑与 bohr-agent-sdk 的 CalculationMCPServer、DispatcherExecutor/LocalExecutor 及 storage 约定的兼容性；可参考 [bohr-agent-sdk 仓库](https://github.com/dptech-corp/bohr-agent-sdk) 的 `src/dp/agent/server/calculation_mcp_server.py` 与 `executor/`、`storage/` 实现。

---

## 其他约定

- **维护本文件**：在对话或开发过程中，若产生新的、值得固化的约定或逻辑（如架构决策、命名/用法约定、废弃说明等），应适时补充到 AGENTS.md，便于后续遵守。
- （可在此补充项目的其他通用约定，如测试、提交信息、目录结构等。）
