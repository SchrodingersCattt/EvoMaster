# Calculation path adaptor (bohr-agent-sdk)

与 MatMaster `private_callback` 对齐：**HTTPS 存储走 Bohrium 鉴权**；**executor** 规则见下。

## 注入参数

- **executor**（依配置 `mcp.calculation_executors`）：
  - 若工具名在该服务器的 `sync_tools` 中 → **固定** `type: local` + `inject_bohrium_executor`（与 MatMaster `LOCAL_EXECUTOR` 一致，鉴权进 `executor.env`），**不再**使用服务器级 dispatcher / `executor_map` 中该工具的配置。
  - 否则若该服务器配置了 `executor` 或 `executor_map` 模板 → 传 Bohrium executor（鉴权由 `evomaster.env.inject_bohrium_executor` 注入）。
  - 未配置或无模板 → `None`。
- **storage**：`get_bohrium_storage_config()`（来自 `evomaster.env.bohrium`），从 `.env` 的 `BOHRIUM_ACCESS_KEY`、`BOHRIUM_PROJECT_ID` 读取。
- **输入路径**：本地/workspace 文件在配置了 OSS 时上传并替换为 https URL 再调用 MCP。

## /workspace 映射

Agent 可能传入 `/workspace/Fe_bcc.cif`。本 adaptor 将 `/workspace/` 映射为当前 session 的 `workspace_path`，即 `workspace_path/Fe_bcc.cif`，再判断文件是否存在并上传 OSS。

## 依赖

- **运行环境**：与执行 `python run.py` 的进程相同。ConfigManager 在加载配置时从**项目根目录**查找并加载 `.env`，故 OSS/Bohrium 相关变量需写在**项目根目录的 .env** 中（或在该进程的 shell 里 export）。
- 环境变量：`OSS_ENDPOINT`、`OSS_BUCKET_NAME`、`OSS_ACCESS_KEY_ID`、`OSS_ACCESS_KEY_SECRET`（本地文件上传到 OSS 时必填）；Bohrium 鉴权见上。
- `pip install oss2`（已列入主依赖）。
- **Mat Master 本地脚本（结构构建、CIF 处理等）**：建议安装可选依赖 `pip install -e ".[calculation]"`，会安装 ase、pymatgen，便于 agent 在沙箱内运行 ASE/Pymatgen 脚本。
