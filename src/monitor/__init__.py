"""matmaster-monitor 进程：与 API/Worker 共用同一代码库与镜像（Dockerfile --target monitor）。

进程外壳（``monitor_worker``）每轮调 ``BohriumMonitor.tick()`` 推进活跃 Bohrium
作业到终态，并响应 SIGTERM 优雅退出，便于滚动发布。
"""
