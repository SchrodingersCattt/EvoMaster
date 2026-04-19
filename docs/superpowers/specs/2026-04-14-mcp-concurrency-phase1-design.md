# MCP 并发一期设计

## 1. 背景

当前项目中的 MCP tool 调用链已经允许上层并发发起多个 tool call，但在进入 `matmaster.mcp.manager._ManagedConn` 后，又会被 `_requests` 队列和 owner task 的逐个等待逻辑重新串行化。结果是：

- 同一 `server` 上的多个 tool 调用，即使来自同一轮并发 tool call，也会按 FIFO 排队执行
- 这种串行化发生在客户端侧，而不是由上层调度器或底层 `mcp` SDK 强制要求
- 当同一 `server` 上存在多个查询型、低副作用、可独立执行的 tool 时，当前实现会形成明显性能瓶颈

前期排查结论如下：

- `FullToolRunner.execute_batch()` 已经使用 `asyncio.gather` 并发执行已批准的 tool call
- `LazyMCPConnector.call_tool()` 也不会主动把这些请求压回串行
- 真正的瓶颈在 `matmaster/mcp/manager.py` 中 `_ManagedConn._run()` 对每个请求执行 `await conn.call_tool(...)`，导致一个连接同一时刻只有一个 in-flight 请求
- 本地 `.venv` 中的 `mcp` SDK 在 `mcp/shared/session.py` 的 `send_request()` 中采用 `request_id -> response_stream` 的响应路由机制
- 已通过最小实验验证：单个 `ClientSession` 上并发发出两个 `send_request()`，并以乱序响应返回时，结果仍能按正确 `request_id` 回填到各自调用方

因此，这次改造的核心不是给上层补并发，而是拆除客户端内部这层不必要的串行瓶颈。

## 2. 目标

本次一期改造目标如下：

- 支持同一 `MCP server` 上的受控并发调用，避免同一 `server` 的所有 tool 调用都进入单队列串行执行
- 保留当前 owner task 模型，确保 `MCPConnection.__aenter__` 与 `__aexit__` 始终由同一个 task 执行
- 通过配置控制并发策略，实现按 transport 和按 server 的渐进启用
- 保持现有 `LazyMCPConnector` 与 `MCPToolManager` 的对外接口基本不变，尽量减少上层改动
- 为后续第二阶段的连接池扩展预留结构空间，但一期不实现连接池

## 3. 非目标

本次改造不包含以下内容：

- 不实现每个 `server` 的多连接池
- 不修改 `ToolRunner` 的并发模型
- 不重构 `LazyMCPConnector` 的调用入口或 `Exp` 装配方式
- 不尝试一次性为所有 `server` 开启高并发
- 不解决服务端自身的线程安全、临时目录冲突、全局状态竞争等问题，只提供客户端侧的受控并发能力和回退手段

## 4. 现状与约束

### 4.1 当前行为

当前 `_ManagedConn` 的职责包含两部分：

- 在一个 long-lived owner task 中持有 `async with self._conn_ctx as conn`
- 从 `_requests` 队列中取请求，并逐个同步执行 `conn.call_tool(...)`

这样做虽然规避了 anyio cancel scope 跨 task 的历史 bug，但同时也把同一连接上的请求强制串行化。

### 4.2 必须保留的生命周期约束

本次改造必须保留以下硬约束：

- `MCPConnection.__aenter__` 与 `MCPConnection.__aexit__` 必须始终由同一个 owner task 执行
- 所有 `call_tool` 请求都必须先进入 `_ManagedConn` 的调度面，不能绕过 manager 直接操作原始连接
- `cleanup()` 开始后不得再接受新请求
- 已经开始执行的请求应在关闭预算内尽量自然完成，然后 owner task 再退出连接上下文

这几条约束直接对应历史 bug 的防线，不允许为追求并发而放松。

### 4.3 MCP SDK 并发前提与验证结论

一期方案依赖一个明确前提：底层 `mcp` SDK 允许同一 session 上存在多个并发中的请求，只要请求与响应能够通过 `request_id` 正确关联。

该前提的依据有两层：

- 代码层：`mcp/shared/session.py` 的 `send_request()` 为每个请求分配独立 `request_id`，并维护独立的响应流
- 实验层：已在本地 `.venv` 中做最小实验，单个 `ClientSession` 上并发发出两个 `send_request()`，并由模拟 server 乱序返回响应，两个请求均成功完成且结果正确匹配

因此，一期方案可以建立在 单连接多路请求 的前提上。但这一前提只覆盖 SDK 层，不等价于所有 MCP server 或 tool 实现都天然可重入，所以 rollout 仍需保守。

## 5. 方案对比

### 5.1 方案 A：极简修复

做法：

- 直接移除 `_ManagedConn` 中的执行串行队列
- `call_tool()` 直接并发调用底层 `conn.call_tool(...)`
- 仅补一个简单的并发上限

优点：

- 改动最小
- 最快验证当前瓶颈是否确实由客户端串行导致

缺点：

- 容易破坏 owner task 生命周期边界
- 并发策略表达能力弱
- 对后续回退和扩展不友好

### 5.2 方案 B：受控并发一期

做法：

- 保留 `_ManagedConn`、owner task 和请求入口
- 将 `_requests` 从串行执行队列改为调度队列
- owner task 只负责接收请求、分派请求、等待 drain、退出连接上下文
- 真正的 `conn.call_tool(...)` 由 owner task 内部启动的受控子任务执行
- 通过 `Semaphore(max_inflight)` 限制同一连接上的最大并发数
- 支持两种模式：`serial` 和 `multiplex`

优点：

- 保留 enter/exit 同 task 的生命周期约束
- 能直接解决当前瓶颈
- 可以按 transport 和 server 渐进启用
- 为第二阶段连接池扩展预留空间

缺点：

- 比极简修复多一层调度状态与配置设计
- 需要明确处理 cleanup、关闭中的拒绝策略、异常隔离等边界

### 5.3 方案 C：连接池主导

做法：

- 每个 `server` 建多个 `_ManagedConn`
- 按轮询或最小负载选择连接
- 用多连接替代单连接多路复用

优点：

- 隔离性更强
- 如果服务端按 session 串行处理，也更容易继续提速

缺点：

- 复杂度明显更高
- 初始化、清理、错误恢复都会更复杂
- 当前阶段缺少足够证据表明必须直接上连接池

### 5.4 推荐方案

推荐采用方案 B：受控并发一期。

原因如下：

- 能精准解决当前已经确认的瓶颈
- 不会破坏 owner task 这一条历史上已经验证过的安全边界
- 可以通过配置快速回退单个 `server`
- 既不过度设计，也不把后续第二阶段堵死

| 方案 | 并发能力 | 改造成本 | 回退能力 |
|---|---|---|---|
| A 极简修复 | 中 | 低 | 弱 |
| B 受控并发一期 | 高 | 中 | 强 |
| C 连接池主导 | 更高 | 高 | 中 |

## 6. 推荐架构设计

### 6.1 核心思路

一期改造的本质不是删掉 queue，而是把 queue 从串行执行器改成受控并发调度器。

新的 `_ManagedConn` 仍然负责：

- 在 owner task 中执行连接 enter、startup 和 exit
- 提供统一的 `call_tool()` 请求入口
- 在关闭时统一等待 drain

但 owner task 不再对每个请求做完整的同步等待，而是：

- 从 `_requests` 取出请求
- 基于并发额度决定何时启动执行
- 将真正的 `conn.call_tool(...)` 放到内部受控子任务里执行
- 自己继续处理后续请求和关闭信号

### 6.2 调度模式

一期只支持两种调度模式：

- `serial`
  - 语义等价于 `max_inflight=1`
  - 用于 `stdio` 默认策略或已知不安全的 `server`
- `multiplex`
  - 单连接多路复用
  - 允许多个请求同时处于 in-flight 状态
  - 通过 `max_inflight > 1` 限制最大并发数

这两种模式共享同一个 `_ManagedConn` 实现，不分叉成两套代码路径。

### 6.3 `_ManagedConn` 新职责

建议 `_ManagedConn` 维护尽量少且职责不重叠的状态。推荐契约如下：

| 字段 | 唯一职责 | 写入者 | 读取者 | 不变式 |
|---|---|---|---|---|
| `_ready` | startup 完成通知 | owner task | `add_server()` / `call_tool()` | 只完成一次 |
| `_requests` | 调度入口队列 | `call_tool()` | owner task | 所有请求必须先入队 |
| `_closing` | 拒绝新请求的入口开关 | `cleanup()` | `call_tool()` | 一旦为 `True` 不再恢复 |
| `_close_requested` | 唤醒 owner task 进入 drain 的信号 | `cleanup()` | owner task | 只表示关闭请求已发出 |
| `_sem` | 并发额度控制 | child task | child task | 最大持有数不超过 `max_inflight` |
| `_active_tasks` | in-flight 请求的事实集合 | owner task / child task | cleanup / 观测 | `len(_active_tasks)` 等于当前 in-flight 数量 |
| `_drain_event` | 无排队且无 in-flight 的完成通知 | owner task / child task | cleanup | 仅在 drain 条件满足时置位 |
| `_fatal_error` | owner task 致命失败快照 | owner task | `call_tool()` / cleanup | 仅在不可恢复故障时写入 |

额外约束如下：

- 不再单独维护 `_inflight_count`，以 `len(_active_tasks)` 作为唯一 in-flight 事实来源，避免双计数不同步
- `_requests` 应为有界队列，而不是无限堆积
- `_sem` 只负责准入控制，不承担状态观测职责

### 6.4 最小可观测性

一期至少暴露以下运行态信号，供 rollout 阶段观测：

- 当前排队长度：`_requests.qsize()`
- 当前 in-flight 数：`len(_active_tasks)`
- 因 closing 被拒绝的新请求数
- 因队列满触发 backpressure 的拒绝数
- 请求成功数、失败数、取消数、强制关闭数
- cleanup drain 耗时

实现形式一期不强制要求完整 metrics 系统，但至少需要结构化日志或可测试的内部状态接口，能够区分以下阶段：

- admission
- spawn
- execute
- drain
- cancel
- force-close

## 7. 请求数据流

### 7.1 正常请求路径

请求路径保持现有入口不变：

1. `LazyMCPConnector.call_tool()` 将请求送到 manager loop
2. `MCPToolManager.call_tool()` 根据 `server_name` 找到对应 `_ManagedConn`
3. `_ManagedConn.call_tool()` 创建请求 `Future`，通过非阻塞入队将请求对象放入 `_requests`
4. owner task 在 `_run()` 中消费 `_requests`
5. owner task 为该请求创建内部执行子任务 `_execute_request(...)` 后立即继续循环，不在 owner task 中等待并发额度
6. `_execute_request(...)` 在 child task 内部 `await _sem.acquire()`，拿到额度后再调用 `conn.call_tool(...)`
7. 执行结果或异常写回该请求自己的 `Future`

为了避免 owner task 被信号量阻塞，`Semaphore` 的等待必须只发生在 child task 内部，而不能发生在 `_run()` 的主调度循环中。

若 `_requests` 已满，`call_tool()` 应立即以 `ManagedConnBackpressure` 失败，而不是在入口层无限等待。

### 7.2 为什么仍然满足 enter/exit 同 task

生命周期边界如下：

- `async with self._conn_ctx as conn` 只在 owner task 中执行
- owner task 在 startup 完成后进入调度循环
- owner task 只有在关闭信号到来且确认无待排队请求、无 in-flight 请求后，才退出连接上下文
- 内部执行子任务只使用已建立好的 `conn.call_tool(...)` 能力，不负责 `__aenter__` 或 `__aexit__`

因此，enter/exit 同 task 的历史约束被完整保留。

### 7.3 取消语义

一期取消语义采用保守定义，不尝试在协议层补全完整 request cancellation：

- 若调用方在请求尚未被 owner task 接管前取消对应 `Future`，该请求在出队时直接丢弃，不进入执行
- 若请求已被 owner task 接管但尚未拿到 `Semaphore`，child task 在真正执行前再次检查 `Future` 是否已取消，若已取消则直接退出
- 若请求已开始执行底层 `conn.call_tool(...)`，一期不发送协议级 `CancelledNotification`
- 已开始的底层调用继续执行到返回，若此时调用方的 `Future` 已取消或已完成，则执行结果只用于清理本地状态，不再回填给调用方

这样定义的原因是：

- 一期优先解决吞吐瓶颈与生命周期安全边界
- 当前 `MCPConnection.call_tool()` 包装层没有直接暴露可安全复用的 request-id 级取消接口
- 协议级取消可作为后续增强项单独设计

## 8. 并发控制与关闭语义

### 8.1 并发控制

每个 `_ManagedConn` 使用有界队列和 `Semaphore(max_inflight)` 共同完成准入控制。

行为定义如下：

- `serial` 模式下 `max_inflight=1`
- `multiplex` 模式下 `max_inflight` 由配置决定
- owner task 不直接串行等待每个请求完成
- 超出 `max_inflight` 的请求会在 child task 内等待 `Semaphore`，但不会阻塞 owner task 继续调度
- `_requests` 必须为有界队列，建议新增 `max_pending_requests`
- 当 `_requests` 已满时，`call_tool()` 不应无限等待，而应立即返回显式 backpressure 异常

### 8.2 关闭流程

`cleanup()` 采用两阶段关闭：

1. 进入 closing 状态
   - 设置 `_closing=True`
   - 后续新请求立即失败，返回明确错误
2. drain 已接收请求
   - owner task 接收到关闭哨兵后，不立即退出连接上下文
   - 先等待：
     - 已入队请求被全部接管
     - `len(_active_tasks) == 0`
   - 之后才退出 `async with`

### 8.3 强制关闭

关闭预算应拆成两个阶段：

- `_PER_CONN_SHUTDOWN_TIMEOUT`
  - 单个连接的总关闭预算
- `_HARD_CANCEL_GRACE`
  - 进入强制取消后，为 child task 留出的最后清理窗口

推荐语义如下：

```text
deadline = now + _PER_CONN_SHUTDOWN_TIMEOUT
set _closing = True
signal _close_requested
await drain until deadline
if still queued:
    fail queued requests with ManagedConnClosing
if still active:
    cancel all tasks in _active_tasks
    await them up to _HARD_CANCEL_GRACE
    fail any remaining request futures with ManagedConnDead
exit connection context in owner task
```

该流程的关键点是：

- 先尽量自然完成
- 再做本地 child task 取消
- 最后仍由 owner task 退出连接上下文

### 8.4 异常隔离

一期建议定义以下显式异常类型，用于区分不同失败来源：

- `ManagedConnClosing`
  - cleanup 已开始，拒绝新请求或终止尚未开始的排队请求
- `ManagedConnBackpressure`
  - 调度队列达到上限，请求未被受理
- `ManagedConnDead`
  - owner task 或连接进入不可恢复故障

每个请求必须独立完成：

- 单个请求失败，只影响自己的 `Future`
- 不应因为某个工具调用失败就将整个 `_ManagedConn` 判死
- 只有连接级故障或 owner task 崩溃时，才允许批量 fail 尚未完成请求

### 8.5 owner task 致命失败

若 owner task 顶层抛出不可恢复异常：

- 应将异常快照写入 `_fatal_error`
- 所有尚未完成的请求 `Future` 必须收到 `ManagedConnDead`
- manager 后续不应继续把该 `_ManagedConn` 视为健康连接
- cleanup 仍应尝试以 best-effort 方式收尾，但不得让调用方永久等待

## 9. 配置设计

### 9.1 配置结构

建议在 [config/mcp.yaml](/Users/kealdoom/Developer/dp/matmaster/matmaster-evo/config/mcp.yaml) 中新增。下面的数值仅作为 rollout 示例，不应被视为生产默认推荐值：

```yaml
mcp_concurrency:
  defaults:
    # HTTP 服务默认允许单连接多路复用
    http:
      mode: multiplex
      max_inflight: 6
      max_pending_requests: 64
    # SSE 服务默认也可尝试多路复用
    sse:
      mode: multiplex
      max_inflight: 6
      max_pending_requests: 64
    # stdio 默认保守串行
    stdio:
      mode: serial
      max_inflight: 1
      max_pending_requests: 16

  servers:
    # 示例：检索型 server 可按需单独提高上限
    mat_doc:
      mode: multiplex
      max_inflight: 8
      max_pending_requests: 96
    # 示例：数据库检索型 server
    mat_struct_db:
      mode: multiplex
      max_inflight: 8
      max_pending_requests: 96
    # 示例：安全性未知或副作用较强的 server 先保持串行
    mat_nmr:
      mode: serial
      max_inflight: 1
      max_pending_requests: 16
```

### 9.2 优先级

建议按以下优先级解析：

1. `servers[server_name]`
2. `defaults[transport]`
3. 代码内置保守默认值

### 9.3 一期限制

一期只允许两种配置值：

- `serial`
- `multiplex`

配置中不暴露 `pool`，避免让配置语义超前于实现能力。

### 9.4 配置注入位置

并发配置建议通过 `matmaster.tools.lazy_mcp.configure_mcp_manager()` 注入 `MCPToolManager`，而不是让 manager 自己读取配置文件。

这样能够保持当前装配层与运行时职责边界：

- 配置解析留在 connector / 装配层
- manager 只消费已经解析好的运行参数

### 9.5 参数选择准则

一期建议按以下原则选择 `max_inflight` 与 `max_pending_requests`：

- `stdio`
  - 默认 `serial`
  - 只有在明确验证 server 可重入时才放开
- `http/sse` 查询型、低副作用 server
  - 可优先试 `multiplex`
  - `max_inflight` 从 4 或 6 起步，而不是直接拉满
- 平均执行时间长、服务端资源紧张或共享状态不明确的 server
  - 先保守设为 `serial`
- `max_pending_requests`
  - 应显著大于 `max_inflight`
  - 但必须是有限值，避免单个 server 无限制堆积 future 和请求对象

## 10. 渐进启用策略

一期不建议一次性为所有 `server` 放开并发。

建议 rollout 策略如下：

- 首批开启 `multiplex` 的对象优先选择 `http/sse`、检索型、低副作用、已知无共享状态的 `server`
- 对 calculation、文件副作用明显或安全性未知的 `server`，先保持 `serial`
- 通过真实压测、错误率和排队长度观察逐步扩大范围

出现问题时的回退方式：

- 对单个 `server` 将配置切回 `serial`
- 若出现广泛问题，再整体关闭 `http/sse` 的默认 `multiplex`
- 回退应优先走配置变更，而非立即回滚代码

rollout 期间重点观察以下信号：

- 同一 `server` 的 in-flight 是否长期贴近上限
- 队列长度是否持续增长
- `ManagedConnBackpressure` 是否频繁出现
- cleanup drain 耗时是否异常拉长
- 某个 `server` 在启用 `multiplex` 后是否出现错误率突增或结果串线

## 11. 测试策略

### 11.1 生命周期不变量测试

在现有 `tests/matmaster/mcp/test_manager_owner_task.py` 基础上演进，覆盖：

- `enter -> list_tools -> exit` 仍在同一个 owner task 中执行
- cleanup 时 `__aexit__` 不会被外部 task 直接触发
- 多个 in-flight 请求存在时，退出连接上下文的仍然是 owner task

### 11.2 SDK 前提验证

增加一个最小实验或测试，验证：

- 单个 `ClientSession` 上两个 `send_request()` 可以并发发出
- 模拟 server 乱序返回响应时，结果仍能正确路由
- 一个请求的完成不会阻断另一个请求的响应处理
- child task 并发执行请求时，不会重新引入 anyio cancel scope mismatch 类错误

### 11.3 并发行为测试

至少新增以下测试：

- `serial` 模式下多个请求仍然串行
- `multiplex` 模式下多个请求总耗时显著小于串行基线
- 峰值并发数不超过 `max_inflight`
- 高并发场景下 `_active_tasks` 不会无限增长

### 11.4 关闭、取消与异常隔离测试

至少覆盖：

- cleanup 开始后拒绝新请求
- cleanup 等待已启动请求完成
- 单个请求失败不影响其他并发请求
- 上层 `Future` 被取消时，未开始执行的请求会被跳过
- 异常路径下 `Semaphore` 不发生泄漏
- cleanup 时同时存在 queued、waiting-for-semaphore、in-flight 三类请求
- owner task 崩溃时所有未完成请求获得明确异常而非永久 pending

### 11.5 装配与配置测试

至少覆盖：

- `configure_mcp_manager()` 正确注入并发配置
- transport 默认配置与 server 覆盖优先级正确
- `LazyMCPConnector` 创建 manager 后能正确使用目标并发策略
- 队列上限与 backpressure 行为正确

## 12. 验收标准

一期验收标准定义如下：

1. 同一 `http/sse` `server` 上的多个请求可在 `max_inflight` 上限内并发执行
2. `serial` 模式行为与当前实现保持兼容
3. `MCPConnection` 的 enter/exit 生命周期仍由同一个 owner task 管理
4. cleanup 期间不接受新请求，已启动请求可在预算内完成
5. 受控基准测试中，并发总耗时应接近单请求耗时，而不是接近串行总和
6. 至少一个真实 MCP `server` 的压测结果显示并发耗时明显优于当前串行实现，且错误率不升高

建议验收分为两部分：

- 受控基准
  - 取 `N = max_inflight`
  - 构造 `N` 个服务端耗时近似相同的请求，每个请求处理时间记为 `T`
  - 改造后总 wall-clock time 应满足 `total <= 1.2 * T + client_overhead`
- 真实 server 压测
  - 采用 4 到 8 个同 `server` 并发请求
  - 对比改造前后总 wall-clock time
  - 以明显优于串行基线且错误率不升高为达标条件

## 13. 风险与后续扩展

### 13.1 一期主要风险

- 某些 `server` 或 tool 实现存在隐藏共享状态，之前只是被客户端串行化保护
- cleanup 语义处理不严谨时，可能造成关闭时挂死或过早退出
- 某些 `server` 即使放开单连接多路复用，收益仍然有限

### 13.2 应对方式

- 默认受控并发，而不是无限并发
- 通过 transport 默认值和 server 覆盖做保守 rollout
- 通过测试明确生命周期和关闭契约
- 通过配置快速回退到 `serial`

建议为每个风险绑定观测信号与回退动作：

| 风险 | 观测信号 | 首选回退动作 |
|---|---|---|
| server 隐藏共享状态 | 错误率突增、结果串线、文件冲突 | 将该 `server` 切回 `serial` |
| cleanup 路径不稳 | drain 时间异常、关闭挂住 | 降低 `max_inflight`，必要时整体回退 |
| 队列堆积 | `qsize()` 持续增长、频繁 backpressure | 降低入口并发或提高 server 配额 |
| 单连接收益不足 | wall-clock 改善有限 | 记录为二期连接池候选 |

### 13.3 二期扩展方向

若后续验证表明某些 `server` 在单 session 内仍然收益不足，或存在 session 级串行处理，可在二期引入：

- per-server 连接池
- 更细粒度的分类限流
- 按 tool 或副作用等级定制并发策略

但这些都不属于本次一期范围。

## 14. 结论

本次 MCP 并发一期设计选择保留 `_ManagedConn` 与 owner task 生命周期模型，将请求队列从串行执行器改造为受控并发调度器。

该方案能够在不破坏 enter/exit 同 task 这一关键安全边界的前提下，拆除当前客户端内部的串行瓶颈，并通过配置实现渐进启用与快速回退。它既足够聚焦，适合当前性能问题，也为后续第二阶段扩展到连接池保留了明确演进路径。
