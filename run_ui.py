#!/usr/bin/env python3
"""启动 EvoMaster UI 服务

启动后打开浏览器访问 http://127.0.0.1:8765 ，输入任务指令即可运行，并可在页面上看到各模块执行状态与结果。

用法:
  python run_ui.py
  python run_ui.py --port 9000
"""

import argparse
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))


def main():
    parser = argparse.ArgumentParser(description="EvoMaster UI 服务")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址")
    parser.add_argument("--port", type=int, default=8765, help="端口")
    args = parser.parse_args()

    import uvicorn
    uvicorn.run(
        "ui.server:app",
        host=args.host,
        port=args.port,
        reload=False,
    )


if __name__ == "__main__":
    main()
