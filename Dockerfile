FROM registry.dp.tech/public/python:3.13-slim AS builder

# 构建期索引（uv/pip）；PATH、PRE_COMMIT_HOME 须在 venv 创建之后，见下文
ENV UV_INDEX_URL=https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple/ \
    UV_TRUSTED_HOST=mirrors.tuna.tsinghua.edu.cn

# 单层：镜像源、apt（git/curl 等）、pip 镜像、安装 uv
RUN sed -i 's|http://deb.debian.org|http://mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources && \
    sed -i 's|https://deb.debian.org|https://mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources && \
    apt-get update && apt-get install -y --no-install-recommends \
        curl \
        git \
        libjemalloc2 \
        unzip \
        wget \
    && rm -rf /var/lib/apt/lists/* \
    && pip config set global.index-url https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple/ \
    && pip config set global.trusted-host mirrors.tuna.tsinghua.edu.cn \
    && pip install uv

WORKDIR /app

COPY pyproject.toml uv.lock README.md /app/

RUN uv venv && \
    . .venv/bin/activate && \
    uv pip install -e . && uv sync

ENV PATH="/app/.venv/bin:$PATH" \
    PRE_COMMIT_HOME=/app/.cache/pre-commit

# 先 pre-commit 再全量 COPY，业务代码变更不使 hook 层失效
COPY .pre-commit-config.yaml .pre-commit /app/

RUN mkdir -p /app/.cache/pre-commit && pre-commit install-hooks

COPY . /app/

EXPOSE 80

RUN echo '#!/bin/bash\nset -e\nsource .venv/bin/activate\nJEMALLOC=/usr/lib/$(uname -m)-linux-gnu/libjemalloc.so.2\nif [ -f "$JEMALLOC" ]; then export LD_PRELOAD="$JEMALLOC"; fi\nexec gunicorn app:app -w 1 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:80 --preload' > /app/start.sh && \
    chmod +x /app/start.sh

# ---------- 多 target：API（默认）与 Worker ----------
FROM builder AS worker
CMD ["python", "-m", "src.worker.agent_worker"]

FROM builder AS api
CMD ["/app/start.sh"]
