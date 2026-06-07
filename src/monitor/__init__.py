"""matmaster-monitor 进程：与 API/Worker 共用同一代码库与镜像（Dockerfile --target monitor）。

当前为占位（假）实现，仅周期打印心跳日志、响应 SIGTERM 优雅退出，用于先把
构建 / 部署流水线（test → uat → online）跑通；后续在 ``monitor_worker`` 内填充真实监控逻辑。
"""
