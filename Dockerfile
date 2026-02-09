FROM registry.dp.tech/public/python:3.13-slim

# 配置 apt 使用清华大学镜像源
RUN sed -i 's|http://deb.debian.org|http://mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources && \
    sed -i 's|https://deb.debian.org|https://mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources

# 安装 weasyprint 系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libharfbuzz0b \
    libpangocairo-1.0-0 \
    libffi-dev \
    shared-mime-info \
    fontconfig \
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

# 将字体文件复制到系统字体目录并刷新字体缓存
RUN mkdir -p /usr/share/fonts/truetype/noto && \
    cp /app/fonts/NotoSansCJK-*.ttc /usr/share/fonts/truetype/noto/ && \
    chmod 644 /usr/share/fonts/truetype/noto/*.ttc && \
    fc-cache -fv && \
    fc-list | grep -i "noto.*cjk" || echo "Warning: Font may not be registered"

# 暴露端口
EXPOSE 80

# 创建启动脚本
RUN echo '#!/bin/bash\n\nsource .venv/bin/activate\nexec gunicorn app:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:80 --preload' > /app/start.sh && \
    chmod +x /app/start.sh

# 启动命令
CMD ["/app/start.sh"]
