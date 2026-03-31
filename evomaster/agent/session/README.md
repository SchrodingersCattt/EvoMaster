# Session - Agent 与 Env 交互的介质

Session 是 Agent 与远程集群环境交互的中间层，提供统一的接口来执行命令、传输文件等。

## 目录结构

- `base.py` - Session 抽象基类，定义标准接口
- `local.py` - 本地 Session 实现，在本地直接执行命令
- `docker.py` - Docker Session 实现，使用 Docker 容器提供隔离的执行环境
- `ssh.py` - SSH Session 实现，通过 SSH 连接远端容器（如 Bohrium 节点）

## 核心类

### BaseSession（base.py）
Session 的抽象基类，定义所有 Session 实现必须提供的接口：

- `open()` / `close()` - 会话生命周期管理
- `exec_bash(command)` - 执行 Bash 命令
- `upload(local, remote)` - 上传文件
- `download(remote)` - 下载文件
- `read_file()` / `write_file()` - 文本文件读写
- `path_exists()` / `is_file()` / `is_directory()` - 路径检查

### LocalSession（local.py）
本地 Session 实现，在本地直接执行命令：

- 使用 subprocess 直接执行 bash 命令
- 文件操作为本地复制/读写
- 适合开发和测试
- 无需任何外部依赖（Docker、集群等）

### DockerSession（docker.py）
基于 Docker 的 Session 实现，提供隔离的执行环境：

- 使用 Docker 容器作为执行环境
- 通过 tmux 维持持久化的 bash 会话
- 支持环境变量、工作目录等状态保持
- 支持资源限制（内存、CPU）和卷挂载

### SSHSession（ssh.py）
基于 SSH 的 Session 实现，连接远端容器执行命令：

- 通过 paramiko 建立 SSH 连接，支持密码和密钥两种认证方式
- 复用 DockerSession 的 tmux + PS1 机制维持持久化 bash 状态
- 通过 SFTP 进行文件读写，比 `cat`/`echo` 重定向更高效可靠
- 内置 keepalive 心跳和断线自动重连
- **不管理容器生命周期**：容器由外部后端（如 Bohrium）负责分配和释放，SSHSession 只负责「连上去、用、断开」
- **不通过 config.yaml 静态配置**：由后端在运行时通过 `playground.attach_ssh_session()` 动态创建和挂载
- 敏感字段（`password`、`key_data`、`passphrase`）在 repr/日志中自动脱敏

## 使用示例

### 本地 Session

```python
from evomaster.agent.session import LocalSession, LocalSessionConfig

config = LocalSessionConfig(timeout=30)

with LocalSession(config) as session:
    result = session.exec_bash("python --version")
    print(result["stdout"])

    session.upload("/local/path", "/tmp/remote.py")
    content = session.download("/tmp/file.txt")
```

### Docker Session

```python
from evomaster.agent.session import DockerSession, DockerSessionConfig

config = DockerSessionConfig(
    image="python:3.11-slim",
    memory_limit="4g",
    cpu_limit=2.0,
)

with DockerSession(config) as session:
    result = session.exec_bash("python --version")
    print(result["stdout"])

    session.upload("/local/path", "/workspace/remote.py")
    content = session.download("/workspace/output.txt")
```

### SSH Session（通过 Playground 动态挂载）

SSH Session 不在 `config.yaml` 中静态配置，而是由后端在运行时动态挂载。
典型流程：后端创建 Bohrium 节点 → 获取 IP/密码 → 调用 `playground.attach_ssh_session()` → agent 在远端执行 → 结束后 `detach_session()` 恢复本地 session。

```python
# 后端代码示例（server.py / agent_run_service.py）
pg.attach_ssh_session(
    host="47.92.199.255",       # Bohrium 分配的节点 IP
    password="node-password",
    working_dir="/workspace",
)

# agent 运行期间，所有 tool 操作走 SSH 远程执行
# ...

# 运行结束后恢复本地 session
pg.detach_session()
pg._setup_session()
```

### SSH Session（直接使用，用于测试）

```python
from evomaster.agent.session import SSHSession, SSHSessionConfig

config = SSHSessionConfig(
    host="192.168.1.100",
    port=22,
    username="root",
    password="your-password",
    working_dir="/workspace",
    timeout=300,
)

with SSHSession(config) as session:
    result = session.exec_bash("nvidia-smi")
    print(result["stdout"])

    session.write_file("/workspace/run.py", "print('hello')")
    session.exec_bash("python /workspace/run.py")
    output = session.read_file("/workspace/run.py")
```

## 设计特点

1. **抽象接口** - BaseSession 定义标准接口，便于多种实现（本地、Docker、SSH 等）
2. **多种实现** - 支持本地、Docker、SSH 远端容器
3. **隔离环境** - Docker/SSH 容器提供完整的隔离执行环境，防止代码泄露
4. **持久化会话** - 使用 tmux 维持 bash 状态，支持长期实验（DockerSession 和 SSHSession 共用同一套机制）
5. **资源管理** - Docker 支持内存、CPU 等资源限制
6. **上下文管理** - 实现了 Python 上下文管理器接口
7. **动态挂载** - SSH Session 由后端在运行时通过 `playground.attach_ssh_session()` 动态创建，无需在 config.yaml 中静态配置

## 配置参数

### SessionConfig（基础配置）
- `timeout` - 命令执行超时时间（秒），默认 300
- `workspace_path` - 工作空间路径，默认 `/workspace`

### LocalSessionConfig（本地 Session 配置）
继承 `SessionConfig`，额外参数：
- `encoding` - 文件编码，默认 `utf-8`

### DockerSessionConfig（Docker Session 配置）
继承 `SessionConfig`，额外参数：
- `image` - Docker 镜像名称，默认 `python:3.11-slim`
- `container_name` - 容器名称，自动生成则为 None
- `memory_limit` - 内存限制，默认 `4g`
- `cpu_limit` - CPU 限制，默认 2.0
- `volumes` - 卷挂载 {主机路径: 容器路径}
- `env_vars` - 环境变量
- `auto_remove` - 容器结束后是否自动删除，默认 True

### SSHSessionConfig（SSH Session 配置）
继承 `SessionConfig`，额外参数：
- `host` - 远端主机 IP 或域名（必填）
- `port` - SSH 端口，默认 22
- `username` - SSH 用户名，默认 `root`
- `password` - SSH 密码（与 `key_file`/`key_data` 二选一）
- `key_file` - SSH 私钥文件路径（如 `~/.ssh/id_rsa`）
- `key_data` - SSH 私钥内容字符串（适合从环境变量注入）
- `passphrase` - 私钥密码
- `working_dir` - 远端工作目录，默认 `/workspace`
- `connect_timeout` - SSH 连接超时（秒），默认 10
- `keepalive_interval` - 心跳间隔（秒），默认 30；设为 0 禁用
- `max_retries` - 连接失败最大重试次数，默认 3

## 后续扩展

可在此基础上实现：
- `KubernetesSession` - Kubernetes 集群执行
- `RaySession` - Ray 分布式框架
