# MatMaster Web (Bare Metal)

前端（Next.js）与后端（FastAPI）裸机运行，无需 Docker。

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
- API: http://localhost:8000  
- 分享页: http://localhost:3000/share/demo_session

## 沙箱

后端固定 `run_dir` 为 `playground/mat_master`，workspace 为 `playground/mat_master/workspace`。
