# Bohrium query 改造 + monitor 巡检单元 设计

- 日期：2026-06-08
- 取代：`docs/superpowers/specs/2026-06-07-bohrium-poll-service-design.md`（原 spec 把重心放在抽 `BohriumPollService` 共享层让前后台复用，偏离了「把长程监控从 agent 拆出去」的目标，废弃）

## 1. 背景

近期 Bohrium 作业闭环已落地三块基础设施（详见 `2026-06-05-bohrium-job-completion-scheduler-discussion.md`）：

- **Ledger 台账**：`bohrium_jobs` 表 + `BohriumJobsTable` DAO（唯一写入口，封装状态机不变量）。
- **poller 引擎**：`BohriumJobPoller.run_once()`（`src/services/bohrium_poller.py`）能 claim 到期作业、查平台、写回 ledger，但**全仓无调用方**——后台监控没有真正跑起来。
- **trigger_run 触发原语**：终态作业唤醒 agent 的原语已建，但与 poller 零联动。

当前 agent 监控 HPC 作业的方式是：`BohriumTool` 的 `poll` 动作做**阻塞短轮询**——`tool.py` 的 `_poll_with_short_loop` 内部 `while True` 每 5 秒查一次、最多阻塞 60 秒，直到作业离开 running 才返回。这把长程监控压在 agent 的工具调用里，占用 run 资源和时间。

本设计把长程监控从 agent 拆出去，做两件相对独立的事：

1. **前台**：tool `poll` → `query`，agent 调一次拿当前状态立即返回，不再阻塞循环。
2. **后台**：把 `BohriumJobPoller` 引擎接进独立 monitor 进程，由进程持续推进活跃作业到终态。

### 分工

monitor 进程的**外壳与部署**由同事在 `feat/matmaster-monitor-main` 上负责，已落地骨架（`7d6e1d18`）：

- `src/monitor/monitor_worker.py`：进程入口 `main()` + SIGTERM 优雅退出 + 心跳循环 `_run_monitor_loop()`。**当前为占位**，每轮只打印 heartbeat 日志；注释明确「后续接入真实监控逻辑时，在 `_run_monitor_loop` 内替换心跳为实际采集/巡检即可」。
- 配套：Dockerfile `--target monitor`、`.gitlab-ci.yml`、`ci/monitor-deploy.yml`（test → uat → online 部署流水线）、`src/utils/logger.py` 的 monitor 日志配置。进程与 API / Worker 共用同一代码库与镜像。

**本 spec 负责**：tool query 改造 + 填进进程循环的 bohrium 巡检逻辑（`BohriumMonitor.tick`）。进程外壳、循环框架、SIGTERM、日志、worker_id、部署一律不在本 spec 范围。

## 2. Goals / Non-Goals

### Goals

- tool `poll` → `query`：删除阻塞短轮询，单次查询立即返回当前状态，保留 ledger 写回 / download 提示 / sandbox `log_tail`。
- 提供 `BohriumMonitor` 巡检单元：封装 poller 构造 + 配置读取 + `run_once` + 进程级异常兜底，暴露单轮 `tick()`，供同事进程循环每轮调用。
- 两条链路只共享已有纯函数（`to_ledger_status` / `confirm_terminal_status` / `status_name`，都在 `matmaster/bohrium/status.py`），**不抽共享 service 层**。

### Non-Goals

- 不写 monitor 进程外壳（`main` / SIGTERM / 日志 / worker_id / 部署）——同事负责。
- 不碰 completion scheduler 那条线：monitor 把作业推进到终态、进 `pending_terminal` 待交付队列即止，**不调 `trigger_run`、不调 `mark_handled`**。
- 不改 DB schema，不动 `bohrium_jobs` 的 poll 动作词列名（`next_poll_at` / `poll_count` 保留）。
- 不抽 `BohriumPollService` 共享层（原 spec 废弃理由）。
- 不引入任何 inline 兼容/迁移/兜底逻辑；`poll` → `query` 直接迁移，不留 alias。

## 3. 架构：两条互不依赖的链路

```text
前台（agent 主动看一眼，轻量、一次性）
  BohriumTool(action="query")
    -> 单次 get_job_detail + confirm_terminal_status
    -> _safe_ledger(record_poll)            # 顺手刷新台账
    -> 立即返回 ToolResult（status / download 提示 / sandbox log_tail）

后台（独立进程，长程、批量、自动）
  src/monitor/monitor_worker.py  [同事·进程外壳]
    main / SIGTERM / 日志 / _run_monitor_loop（while + stop_event + interval）
      └─每轮调─> BohriumMonitor.tick()       [本 spec·巡检单元]
                  -> BohriumJobPoller.run_once()   [已有·单轮引擎]
                       -> claim_due_batch (FOR UPDATE SKIP LOCKED)
                       -> 逐个 poll + apply_poll / mark_poll_error
```

两条链路**没有共享代码层**。唯一交汇点是 `bohrium_jobs` 台账状态本身：前台 query 写一次 ledger（`record_poll`）会把该作业的 `next_poll_at` 推后 30 秒（`_FOREGROUND_POLL_BACKOFF_SECONDS`），于是后台 monitor 下一轮 `claim_due_batch` 自然跳过刚被看过的作业。这是**状态驱动的隐式协调**，不是代码耦合，正好符合职责拆分目标。

分工边界：

| 部分 | 负责方 | 落点 |
|---|---|---|
| 进程外壳：`main` / SIGTERM / 日志 / worker_id / 循环框架 + 部署流水线 | 同事 | `src/monitor/`、CI |
| tool `poll` → `query` 改造 | 本 spec | `matmaster/tools/builtin/bohrium_tool/tool.py` |
| `BohriumMonitor` 巡检单元（嵌入 `_run_monitor_loop`） | 本 spec | `src/services/bohrium_poller.py` |
| 单轮 poll 引擎 `BohriumJobPoller.run_once` | 已有，保留 | `src/services/bohrium_poller.py` |

## 4. 前台：tool `poll` → `query`

改动集中在 `matmaster/tools/builtin/bohrium_tool/tool.py`。注意 `capabilities` 早已声明 `bohrium.query`（tool.py:244），而 action enum 仍叫 `poll`（tool.py:178）——本次改名是补齐这个既存的命名不一致。

| 位置 | 现状 | 改成 |
|---|---|---|
| action enum (tool.py:178) | `"poll"` | `"query"` |
| `description` (tool.py:166-168) | `submit / poll / download / kill` | `submit / query / download / kill` |
| `execute_with_context` 的 poll 分支 (tool.py:338-339) | `if action == "poll": return await self._poll_with_short_loop(...)` | **删除此分支**；`query` 走普通 `asyncio.to_thread(self._execute)` 路径，与 submit/download/kill 同流 |
| `_poll_with_short_loop` (tool.py:358-415) + 常量 `_POLL_INTERVAL` / `_POLL_MAX_WAIT` (tool.py:320-321) | 阻塞 while 循环 | **整段删除**（净代码下降 ~60 行） |
| `_execute` 的 `match` (tool.py:479-480) | `case "poll": return self._poll(arguments)` | `case "query": return self._query(arguments)` |
| `_poll` (tool.py:577-672) | 单次查询实现 | 重命名 `_query`，**逻辑原样保留**：`get_job_detail` + `confirm_terminal_status` + `_safe_ledger("record_poll", ...)` + download 提示 message + sandbox `_fetch_log_tail`；`result_dir` 仍拒绝并提示用 `download` |
| `_update_registry` (tool.py:449-451) | `if action == "poll":` | 改判 `"query"`；registry 内部方法 `update_poll` / `classify_poll_status` 作为**动作词保留不改名** |
| `_execute` 未知 action 提示 (tool.py:493) | `"...one of: submit, poll, download, kill..."` | 同步改 `query` |
| `prompt()` (tool.py:297-307) | poll 段写 "built-in waiting up to ~60s, call poll again to continue" | 重写 query 段（见下）；download/kill 段里引用 "poll" 的句子同步改 "query" |

`prompt()` 的 query 段改写要点（去掉一切「阻塞等待」语义）：

- query：一次性查询作业当前状态，**立即返回**，不阻塞、不内部等待。
- 提交作业后无需反复 query 死等；长程监控由后台自动进行，作业完成会在后续上下文中带出。
- query 仅在需要主动确认某个作业当下状态时调用，仍按单个 `job_id` 查询。

### 前台 query 写 ledger 的语义

`_query` 保留 `record_poll` ledger 写回（[bohrium_jobs_wiring.py:106-123](src/services/bohrium_jobs_wiring.py)）：经 `to_ledger_status` 归一化后调 `apply_poll`，用 `_FOREGROUND_POLL_BACKOFF_SECONDS = 30` 推 `next_poll_at`。这在新架构下依然合理——agent 主动 query 一次，顺手刷新台账状态，并让后台 monitor 30 秒内不重复查该作业。ledger 写失败不阻断用户可见 query 结果（`_safe_ledger` 吞异常）。

## 5. 后台：`BohriumMonitor` 巡检单元 + 对接同事进程

### 5.1 `BohriumMonitor`（新增，放 `src/services/bohrium_poller.py`，紧挨引擎）

巡检单元是一个薄封装，把「构造 poller + 读配置 + 调 `run_once` + 进程级异常兜底」收进一个对象，让同事进程一行接入：

```python
class BohriumMonitor:
    """monitor 进程的 bohrium 巡检单元：每轮 claim 到期作业、poll、写回 ledger。

    设计为可嵌入 src/monitor/monitor_worker.py 的 _run_monitor_loop：
    循环框架 / 退出信号 / 间隔 / 日志由进程外壳负责，本类只提供单轮 tick()。
    """

    def __init__(
        self,
        *,
        poller: BohriumJobPoller | None = None,
        limit: int | None = None,
        claim_timeout_seconds: int | None = None,
    ) -> None:
        self._poller = poller if poller is not None else BohriumJobPoller()
        self._limit = limit if limit is not None else _env_int("BOHRIUM_MONITOR_LIMIT", 50)
        self._claim_timeout = (
            claim_timeout_seconds
            if claim_timeout_seconds is not None
            else _env_int("BOHRIUM_MONITOR_CLAIM_TIMEOUT", 120)
        )

    def tick(self) -> dict[str, int]:
        """单轮巡检。吞 claim / DB 级异常，保证调用方循环不被打断。

        返回 BohriumJobPoller.run_once 的 summary（claimed / polled / errors）；
        本轮整体失败时返回 tick_failed=1，其余计数为 0。
        """
        try:
            return self._poller.run_once(
                limit=self._limit, claim_timeout_seconds=self._claim_timeout
            )
        except Exception:  # noqa: BLE001 — 进程级兜底：单轮失败不拖垮长跑进程
            logger.warning("bohrium monitor tick failed", exc_info=True)
            return {"claimed": 0, "polled": 0, "errors": 0, "tick_failed": 1}
```

要点：

- **复用 `BohriumJobPoller` 引擎**，不改它（`run_once` 已吞每个作业的异常，见 `_poll_one` 返回 bool）。`BohriumMonitor` 只在 `run_once` 外再包一层，兜 `claim_due_batch` 等批级异常。
- **不持有循环**：循环、`stop_event`、间隔、退出日志全归同事的 `_run_monitor_loop`。本类只做「一轮」。
- **多实例并发**靠 `claim_due_batch` 的 `FOR UPDATE SKIP LOCKED`，进程间零协调、不需要 Redis。
- `_env_int` 是本文件内的小 helper（读环境变量、非法值回退默认），不引第三方配置。

### 5.2 对接同事的 `_run_monitor_loop`（契约）

同事保留现有循环框架，只把每轮的 heartbeat 动作换成 `tick()`：

```python
# src/monitor/monitor_worker.py  _run_monitor_loop 内，接入后形态
from src.services.bohrium_poller import BohriumMonitor

runner = BohriumMonitor()                          # 循环外构造一次
while not _stop_event.is_set():
    summary = runner.tick()                        # 单轮 claim+poll+写回
    logger.info("matmaster-monitor: bohrium %s", summary)
    _stop_event.wait(timeout=_INTERVAL)
```

**接口契约（本 spec 交付面）**：`BohriumMonitor()` 无必填参数即可构造；`tick() -> dict[str, int]` 不抛异常。类名 / 方法名 / 返回 summary 形状以同事骨架定稿时对齐为准（见 §10）。

### 5.3 进程巡检间隔 ≠ 单作业 poll 频率

这两个频率正交，必须分清：

- **进程巡检间隔**（同事 `_run_monitor_loop` 的 `_INTERVAL`，当前心跳默认 30s）：进程多久 claim 一次「到期」作业。建议接入巡检后下调到 10s 量级，让到期作业被及时 claim。由同事进程参数控制，本 spec 仅给建议。
- **单作业 poll 频率**（DAO 层 `next_poll_at` 退避，`compute_poll_backoff`：30→60→…→600s）：同一个作业两次远端查询的最小间隔。

进程每 10s 巡检一次，并不会让每个作业每 10s 被 poll 一次——`claim_due_batch` 只取 `next_poll_at <= NOW()` 的作业，单作业的查询节奏由其退避决定。进程间隔只影响「到期后多快被捞起」，不影响单作业成本。

## 6. 配置

巡检单元只读两个环境变量（保守默认，只影响调度量，不碰状态机）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `BOHRIUM_MONITOR_LIMIT` | 50 | 每轮 `claim_due_batch` 上限 |
| `BOHRIUM_MONITOR_CLAIM_TIMEOUT` | 120s | claim 占位时长（claim 后多久内未写回则可被其他实例重抢） |

进程巡检间隔、错误退避、日志级别归同事进程外壳（如 `MONITOR_HEARTBEAT_INTERVAL`）。per-job 退避沿用 `compute_poll_backoff`。

## 7. 错误语义

- **单作业失败**（AK 缺失 / 平台 API 异常 / detail 缺 status）：`run_once` 内 `_poll_one` 已处理——调 `mark_poll_error` 让活跃作业标 `unknown` 并按 backoff 重试，本轮计入 `errors`，不影响同批其他作业。
- **批级失败**（`claim_due_batch` 抛 DB 异常）：`BohriumMonitor.tick()` 兜住，记 warning，返回 `tick_failed=1`，进程循环继续下一轮。**进程绝不因单轮异常退出。**
- **终态单调**：由 `BohriumJobsTable.apply_poll` 的 CASE 保护，业务层不重复实现「终态不回退」。
- **前台 query**：`_query` 内异常返回用户可见错误 ToolResult；ledger 写失败经 `_safe_ledger` 吞掉，不丢 query 结果。

## 8. 测试

功能改造，需要改 / 加必要测试，但克制（不为重命名堆测试）：

- `tests/matmaster/tools/builtin/test_bohrium_tool_poll.py` → 改为 query 语义：覆盖 query 一次性返回 Running / Finished、确认 `_poll_with_short_loop` 已删（无阻塞循环）。文件可一并更名为 `test_bohrium_tool_query.py`。
- `tests/matmaster/tools/builtin/test_bohrium_tool_ledger.py`：tool 路径 ledger 失败不破坏 ToolResult，action 名同步改 query。
- 其余引用 `poll` action 的 tool 测试随改名同步迁移：`test_bohrium_tool.py`、`test_bohrium_tool_prompt_rebalance.py`（prompt query 段断言）、`test_bohrium_tool_session_credentials.py`。
- `tests/services/test_bohrium_poller.py`：`run_once` 引擎已有覆盖，保留不动。
- `BohriumMonitor`：补一个轻量测试——`tick()` 正常透传 `run_once` summary；`tick()` 在 `run_once` 抛异常时吞掉并返回 `tick_failed=1`（用假 poller 注入）。

## 9. 迁移与清理（净代码）

- 删 `_poll_with_short_loop` + `_POLL_INTERVAL` / `_POLL_MAX_WAIT`（~60 行下降）。
- 新增 `BohriumMonitor` + `_env_int`（~30 行）。
- `_poll` → `_query`、action `poll` → `query` 直接迁移，不留 alias、不留兼容分支。
- 整体净代码基本持平或略降。

## 10. 与 `matmaster-monitor` 分支的协调

- 本 spec 的可交付（tool query 改造 + `BohriumMonitor`）**不依赖**同事进程 util（`worker_id` / `build_info` / `logger`），可独立实现与测试。
- 唯一对接面是 §5.2 的契约：`BohriumMonitor().tick() -> dict`。同事接入时只需在 `_run_monitor_loop` 内把 heartbeat 换成 `runner.tick()` + 日志。
- 接口最终命名（类名 / 方法名 / summary 字段）在同事骨架定稿时对齐；若同事偏好别的嵌入形态（如裸函数 `run_bohrium_monitor_tick(poller, ...)` 而非类），按其骨架调整，逻辑不变。
- 实施顺序：tool query 改造可立即独立推进；`BohriumMonitor` 可在同事骨架未定稿前先实现 + 单测，真正嵌入 `_run_monitor_loop` 这一步等同事进度。
