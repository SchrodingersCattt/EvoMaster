FROM registry.dp.tech/public/python:3.13-slim AS builder

# 配置 apt 使用清华大学镜像源
RUN sed -i 's|http://deb.debian.org|http://mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources && \
    sed -i 's|https://deb.debian.org|https://mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources

# git：镜像内 pre-commit install-hooks；curl/wget/unzip：容器内排障与脚本常用；libjemalloc2：start.sh 可选 LD_PRELOAD
# （当前 pyproject 未包含 weasyprint，故不装 Pango/Cairo/字体；若以后 HTML→PDF 再补依赖）
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    libjemalloc2 \
    unzip \
    wget \
    && rm -rf /var/lib/apt/lists/*

# 配置 pip 使用国内源
RUN pip config set global.index-url https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple/ && \
    pip config set global.trusted-host mirrors.tuna.tsinghua.edu.cn

# 设置工作目录
WORKDIR /app

# 将 models 目录放到 /app
#COPY models /app

# 安装 uv
RUN pip install uv

# 配置 uv 使用国内源
ENV UV_INDEX_URL=https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple/
ENV UV_TRUSTED_HOST=mirrors.tuna.tsinghua.edu.cn

# 首先只复制依赖相关文件
COPY pyproject.toml uv.lock README.md /app/

# 创建并激活虚拟环境，安装依赖
RUN uv venv && \
    . .venv/bin/activate && \
    uv pip install -e . && uv sync

# 设置 PATH 环境变量
ENV PATH="/app/.venv/bin:$PATH"

# 复制其余项目文件
COPY . /app/

# CI：在镜像内预建 pre-commit hook 环境，lint job 通过 docker run + 挂载仓库复用，减少 job 内访问 GitHub
RUN mkdir -p /app/.cache/pre-commit && \
    PRE_COMMIT_HOME=/app/.cache/pre-commit pre-commit install-hooks
ENV PRE_COMMIT_HOME=/app/.cache/pre-commit

# 暴露端口
EXPOSE 80

# 创建启动脚本：可选 LD_PRELOAD jemalloc 使空闲内存更易归还 OS（RSS 更易回落）
# -w 1：单进程，便于 tracemalloc baseline/diff 同进程、内存排查；需提高并发时可改为 -w 2 等
RUN echo '#!/bin/bash\nset -e\nsource .venv/bin/activate\nJEMALLOC=/usr/lib/$(uname -m)-linux-gnu/libjemalloc.so.2\nif [ -f "$JEMALLOC" ]; then export LD_PRELOAD="$JEMALLOC"; fi\nexec gunicorn app:app -w 1 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:80 --preload' > /app/start.sh && \
    chmod +x /app/start.sh

# ---------- 多 target：API 与 Worker 不同 CMD（启动命令写在 Dockerfile） ----------
# 构建：  API（默认）  docker build -t matmaster-evo:tag .  或  --target api
#        Worker        docker build --target worker -t matmaster-evo-worker:tag .

FROM builder AS worker
CMD ["python", "-m", "src.worker.agent_worker"]

FROM builder AS api
CMD ["/app/start.sh"]
