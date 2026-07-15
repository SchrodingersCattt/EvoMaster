# 删除 Bohrium Node 手动关机能力

## 目标

删除“立即关闭 Bohrium 会话节点”整条能力，避免设置页出现不需要的运维入口，
并避免后端保留无消费者的公开接口和业务方法。

## 范围

- 前端删除设置页的立即关机卡片、确认弹窗、API 调用和中英文文案。
- evo 删除手动关机 HTTP router、请求模型、`manual_stop` 方法及对应测试。
- 删除原生命周期设计和实施计划中关于手动关机的描述。
- tools-server 不变；其用户生命周期偏好和数据库迁移仍然保留。

## 保留行为

- `run_end`：最后一个 invocation lease 释放后自动 stop。
- `idle_timeout`：15 分钟、30 分钟或 2 小时后由 monitor 自动 stop。
- `keep_running`：MatMaster 不自动 stop。
- 设置页默认策略、逐轮弹窗、“记住此选择，不再询问”、共享 lease 和并发 fencing
  均保持不变。

## 验证

- 静态搜索确认不再存在手动关机路由、调用、模型和文案。
- evo 生命周期、请求快照、Worker 和回收器测试继续通过。
- 前端 chat 测试、改动文件 ESLint 和生产构建继续通过。
