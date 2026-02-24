# Env - 执行环境管理

Env 是 EvoMaster 的环境组件，负责管理执行环境和作业调度。

## 目录结构

- `base.py` - Env 抽象基类，定义标准接口
- `local.py` - LocalEnv 实现，在本地直接执行命令
- `docker.py` - DockerEnv 实现，通过 Docker 容器提供隔离执行环境
- `ssh.py` - SSHEnv 实现，通过 SSH 连接远端容器（如 Bohrium 节点）
- `bohrium.py` - Bohrium 凭据与存储配置工具函数

## 核心类

### BaseEnv（base.py）
Env 的抽象基类，定义所有 Env 实现必须提供的接口：

- `setup()` / `teardown()` - 环境生命周期管理
- `get_session()` - 获取 Session 用于执行命令
- `submit_job(command, job_type)` - 提交作业
- `get_job_status(job_id)` - 查询作业状态
- `cancel_job(job_id)` - 取消作业

### LocalEnv（local.py）
本地环境实现，无需 Docker 或集群：

- 在本地直接执行命令
- 同步作业执行
- 支持作业状态查询
- 适合开发和测试阶段

### DockerEnv（docker.py）
基于 Docker 的环境实现：

- 使用 `docker exec` 在容器内执行命令
- 通过 tmux + PS1 提示符维持持久化 bash 状态
- 支持 `docker cp` 或卷挂载进行文件传输
- 定义了 `BashMetadata`、`PS1_PATTERN` 等被 SSHEnv 复用的常量

### SSHEnv（ssh.py）
基于 SSH 的底层环境实现，供 `SSHSession` 使用：

- 通过 paramiko 建立 SSH 连接，支持密码和密钥认证
- 复用 DockerEnv 的 tmux + PS1 机制（直接 import `BashMetadata`、`PS1_PATTERN`）
- 通过持久化 SFTP channel 进行文件操作（比 `ssh_exec cat` 更高效）
- 内置 keepalive 心跳（`transport.set_keepalive`）和 `_ensure_connected` 断线重连
- **不管理容器生命周期**：容器由外部后端负责，SSHEnv 只负责「连上去、用、断开」

## 架构关系

```
SSHSession (agent/session/ssh.py)
    └── SSHEnv (env/ssh.py)          ← 复用 BashMetadata / PS1_PATTERN
            ↑
        DockerEnv (env/docker.py)    ← 定义 tmux+PS1 机制
```

## 使用示例

### 本地环境

```python
from evomaster.env import LocalEnv, LocalEnvConfig

config = LocalEnvConfig(name="my_env")
env = LocalEnv(config)
env.setup()

try:
    session = env.get_session()
    result = session.exec_bash("python --version")
    print(result["stdout"])
finally:
    env.teardown()
```

### SSH 环境（通常通过 SSHSession 间接使用）

```python
from evomaster.env.ssh import SSHEnv, SSHEnvConfig
from evomaster.agent.session.ssh import SSHSessionConfig

session_cfg = SSHSessionConfig(
    host="192.168.1.100",
    password="secret",
    working_dir="/workspace",
)
env_cfg = SSHEnvConfig(session_config=session_cfg)
env = SSHEnv(env_cfg)
env.setup()

try:
    env.ssh_exec("echo hello")
    env.write_file_content("/workspace/out.txt", "data")
    content = env.read_file_content("/workspace/out.txt")
finally:
    env.teardown()
```

> 通常不需要直接使用 `SSHEnv`，推荐通过 `SSHSession` 使用，它封装了 `exec_bash` 的 tmux 轮询逻辑。

## 设计特点

1. **标准接口** - BaseEnv 定义统一的环境接口，便于替换实现
2. **机制复用** - SSHEnv 直接复用 DockerEnv 的 tmux + PS1 常量，不重复定义
3. **SFTP 优先** - SSH 文件操作走持久化 SFTP channel，避免每次 `exec cat`/`echo` 的开销
4. **断线恢复** - SSHEnv 在每次操作前调用 `_ensure_connected`，自动重建断开的连接
5. **作业管理** - 支持作业提交、状态查询、取消等操作
6. **上下文管理** - 实现了 Python 上下文管理器接口

## 配置参数

### EnvConfig（基础配置）
- `name` - 环境名称
- `session_config` - Session 配置

### LocalEnvConfig（本地环境配置）
- 继承 EnvConfig 的所有配置

### SSHEnvConfig（SSH 环境配置）
- `session_config` - 必填，传入 `SSHSessionConfig` 实例，包含 host/port/认证信息等

## 依赖

SSHEnv 需要 `paramiko`，作为可选依赖安装：

```bash
pip install "matmaster-evo[ssh]"
# 或直接
pip install "paramiko>=3.0"
```

## 后续扩展

可在此基础上实现：
- `KubernetesEnv` - 使用 Kubernetes 集群
- `RayEnv` - 使用 Ray 分布式框架
