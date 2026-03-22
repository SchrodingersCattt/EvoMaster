# MatMaster Web (Bare Metal)

前端（Next.js）与后端（FastAPI）裸机运行，无需 Docker。

本文档描述的是 **本地调试** 用的 MatMaster Web 后端（默认 `BACKEND_PORT=50001`）。生产/集成用的平台 API 为仓库根目录的 `app.py` + `src/`（默认 8000，SSE + Redis + Worker），二者关系见根目录 [README-zh.md](../../README-zh.md) 中的「两套后端」。

**依赖与运行环境**：以仓库根目录 [pyproject.toml](../../pyproject.toml) 为准，在仓库根目录使用 **`uv`**（见根目录 [README-zh.md](../../README-zh.md) 与 [AGENTS.md](../../AGENTS.md)）。

## 启动

1. **后端**（在**仓库根目录**执行）：
   ```bash
   uv sync   # 首次或依赖变更时
   uv run python -m playground.mat_master.service.server
   ```
   端口由环境变量 `BACKEND_PORT` 控制（默认 `50001`）。等价于用 uvicorn 加载 `playground.mat_master.service.server.app:app`。

   若在 `playground/mat_master/service` 目录下且已正确配置 `PYTHONPATH`，也可使用：`uv run uvicorn server:app`（`server` 为同目录下的包，见 `server/__init__.py`）。

2. **前端**：
   ```bash
   cd playground/mat_master/frontend
   npm install
   npm run dev
   ```

3. **一键脚本**（在仓库根目录）：
   ```bash
   bash playground/mat_master/start_dev.sh
   ```
   Windows 可先设置 `BACKEND_PORT`（Git Bash 下默认可能为 `8000`，见脚本内说明），再在仓库根目录执行上述命令；前后端也可按 1、2 步分别启动。

- Dashboard: http://localhost:3000（若 `start_dev.sh` 中 `FRONTEND_PORT` 不同则以脚本输出为准）
- API: http://localhost:50001（或你设置的 `BACKEND_PORT`）
- 分享页示例: http://localhost:3000/share/demo_session

## 沙箱

后端固定 `run_dir` 为 `playground/mat_master`，workspace 为 `playground/mat_master/workspace`。
