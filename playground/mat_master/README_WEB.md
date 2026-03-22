# MatMaster Web (Bare Metal)

前端（Next.js）与后端（FastAPI）裸机运行，无需 Docker。

本文档描述的是 **本地调试** 用的 MatMaster Web 后端（默认 `BACKEND_PORT=50001`）。生产/集成用的平台 API 为仓库根目录的 `app.py` + `src/`（默认 8000，SSE + Redis + Worker），二者关系见根目录 [README-zh.md](../../README-zh.md) 中的「两套后端」。

## 启动

1. **后端**（在项目根目录执行，确保能 import `playground.mat_master`）：
   ```bash
   pip install -r playground/mat_master/service/requirements.txt  # 若未安装
   cd playground/mat_master/service && python server.py
   ```
   或从项目根：
   ```bash
   python -m playground.mat_master.service.server
   ```
   需先 `pip install fastapi uvicorn websockets`，且当前环境已安装 evomaster。

2. **前端**：
   ```bash
   cd playground/mat_master/frontend
   npm install
   npm run dev
   ```

3. **一键脚本**（从项目根）：
   - Linux/macOS: `bash playground/mat_master/start_dev.sh`
   - Windows: 先在后端目录运行 `python server.py`，再在 frontend 目录运行 `npm run dev`；或使用 `playground/mat_master/start_dev.bat` 仅启动后端。

- Dashboard: http://localhost:3000
- API: http://localhost:50001
- 分享页: http://localhost:3000/share/demo_session

## 沙箱

后端固定 `run_dir` 为 `playground/mat_master`，workspace 为 `playground/mat_master/workspace`。
