---
name: sandbox-proxy
description: "Resolve sandbox outbound-network and package-source failures. Use when pip/conda/GitHub/HuggingFace downloads time out. Provides domestic mirror fallback, proxy on/off, and per-command bypass for overseas resources."
---

# Sandbox Network Proxy

Sandbox 默认**无出站 HTTP 代理**。镜像自带 `/etc/pip.conf` 阿里云源，国内 PyPI 已经很快。
当需要访问海外站点（GitHub、HuggingFace、pypi.org、Google Drive 等）时，启用代理；用完立即关闭以保持国内源速度。

不要让用户选择镜像或代理。除非用户明确指定，否则按以下顺序自动恢复。

## 恢复顺序

1. 先使用沙箱默认配置执行一次。对于 pip，可先确认实际配置：

   ```bash
   python -m pip config list -v
   python -m pip install <package>
   ```

2. 常规 PyPI 包出现超时或连接错误时，不开海外代理，仅对当前命令切换国内备用源：

   ```bash
   HTTP_PROXY= HTTPS_PROXY= http_proxy= https_proxy= \
     python -m pip install --proxy '' \
     --index-url https://pypi.tuna.tsinghua.edu.cn/simple <package>
   ```

3. 仅当目标资源必须从海外站点获取时，再按下文启用代理。完成后立即关闭。
4. 代理或镜像失败时最多重试 1-2 次；仍失败则保留原始错误并说明网络阻塞。

## Proxy On — 启用代理

一次性粘贴执行，幂等设置五项用户级代理配置：

```bash
# pip — 用户级代理（镜像级 /etc/pip.conf 阿里云源不受影响）
mkdir -p ~/.pip && cat > ~/.pip/pip.conf <<'EOF'
[global]
proxy=http://pai.ga.op.xdptech.com:3128
EOF

# conda / mamba
cat > ~/.condarc <<'EOF'
proxy_servers:
  http: http://pai.ga.op.xdptech.com:3128
  https: http://pai.ga.op.xdptech.com:3128
EOF

# wget
cat > ~/.wgetrc <<'EOF'
http_proxy = http://pai.ga.op.xdptech.com:3128
https_proxy = http://pai.ga.op.xdptech.com:3128
use_proxy = yes
EOF

# curl
cat > ~/.curlrc <<'EOF'
proxy = http://pai.ga.op.xdptech.com:3128
EOF

# git — global config
git config --global http.proxy http://pai.ga.op.xdptech.com:3128
git config --global https.proxy http://pai.ga.op.xdptech.com:3128
```

## Proxy Off — 关闭代理

```bash
# 删除用户级代理配置
rm -f ~/.pip/pip.conf ~/.condarc ~/.wgetrc ~/.curlrc

# 取消 git 全局代理
git config --global --unset http.proxy 2>/dev/null || true
git config --global --unset https.proxy 2>/dev/null || true

# 清除当前 shell 环境变量
unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy
```

## 副作用

代理启用时，**所有** HTTP/HTTPS 流量（包括国内 mirrors.aliyun.com）都会经过 `pai.ga.op.xdptech.com:3128`，国内访问会变慢。仅在需要海外访问时开启，完成后立即关闭。

代理偶尔出现 `503 / context deadline / TLS recv` 错误（长连接场景如 `git clone`、HuggingFace 下载）。重试通常可解决。

## 单命令绕过代理（代理开启时某次调用需要直连）

```bash
# wget
wget --no-proxy https://example.com/file

# curl
curl --noproxy '*' https://example.com/file

# git — 单次覆盖（空字符串=禁用）
git -c http.proxy= -c https.proxy= clone https://github.com/owner/repo

# pip — 显式空代理
pip install --proxy '' some-package

# 任意子进程（通过环境变量）
HTTP_PROXY= HTTPS_PROXY= http_proxy= https_proxy= <your-cmd>
```

## 使用建议

1. **先开代理** → 执行海外操作（git clone / pip install 海外包 / wget HuggingFace 模型）→ **立即关代理**
2. 如果遇到 503 或超时，重试 1-2 次即可
3. 国内源操作（pip install 常规包）无需开代理，默认源失败时按“恢复顺序”切换单次镜像
