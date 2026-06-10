# Bohrium 作业完成调度器（无状态闭环）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 `docs/superpowers/specs/2026-06-09-bohrium-completion-scheduler-design.md` 落地无状态唤醒闭环：monitor 进程按 ledger 聚合判定唤醒 agent run（attempt），worker 进程在 run 起点固化 delivery snapshot、run 成功收尾时 confirm（ack），全程零新增表、零持久调度态。

**Architecture:** 两条链路只经 `bohrium_jobs` ledger 协调。monitor 侧新增 `BohriumCompletionScheduler.tick()`（聚合扫描 → 逐 invocation 无状态 decide → session 合并 → identity/status/NX 三门 → `trigger_run`）；worker 侧新增 `bohrium_delivery_ack`（run 起点 snapshot 全量 pending、成功收尾在 `release_session_run` 之前 confirm）。context 渲染按 `detail_limit` 压缩详情但全量 job_id 始终可见。实现顺序：先 DAO/Redis 地基，再 renderer 与 worker ack 链路（独立可验证），最后 scheduler 与 monitor 接线。

**Tech Stack:** Python 3.13（仓库 `.venv`）、PyMySQL（raw SQL DAO）、redis-py、pytest（`asyncio_mode = "auto"`）、pre-commit（black --skip-string-normalization / isort --profile black / flake8 --max-line-length=88，**不是 ruff**）。

---

## 0. 执行须知（先读）

- **测试命令**统一用 `.venv/bin/python -m pytest`；格式化用 `pre-commit run --files <改动文件>`。
- **TDD**：本计划为新功能开发，spec §13 明确列出测试清单（用户全局规则中"严禁添加测试"仅针对移除兼容/删死代码类任务，不适用于此）。每个 task 先写失败测试再实现。
- **严禁兼容/兜底逻辑**：所有新代码只写终态语义。"snapshot 为 None 时走 `limit=5` 查询"是 spec 规定的双路径终态语义（无 pending 会话的常态路径），不是兼容分支。
- **`tests/dao/` 真库测试**：依赖 `.env.test` 的 MySQL（无则整组 SKIP，见 `tests/dao/conftest.py:15-50`）。无库环境下 Task 1 的"运行确认失败/通过"会显示 SKIP——此时 SQL 正确性靠评审 + 后续 service 层 fake 测试兜底，不阻塞后续 task。
- 每个 commit message 末尾附：`Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`。
- 开始前确认工作树干净（当前分支 `codex/provider-stage1` 上有 docs 改动，先提交或让用户处理）。
- 单文件行数上限 1000（pre-commit 钩子强制）。改动最大的 `bohrium_jobs_table.py` 完成后约 470 行，`bohrium_completion_scheduler.py` 约 300 行，均安全。

### 0.1 计划级裁定（spec 未明示处，已据代码事实裁定）

| 事项 | 裁定 | 依据 |
|------|------|------|
| spec §13 写 `tests/context/test_session_jobs_source.py`（新增） | 实际**扩展已存在的** `tests/matmaster/context/sources/test_session_jobs.py` | context 测试实际位于 `tests/matmaster/context/`，且该文件已存在 4 个回退路径测试 |
| spec §13 写 `tests/worker` | 新增 `tests/test_agent_worker_snapshot_confirm.py`（仓库根 tests/ 已有 `test_agent_worker_delivery.py` 同级先例） | `tests/worker/` 目录不存在；`tests/matmaster/worker/` 是 kernel 侧 |
| DAO 新增测试文件名 | `tests/dao/test_bohrium_jobs_delivery.py` | 与现有 `test_bohrium_jobs_claim.py` / `test_bohrium_jobs_constraints.py` 按主题分文件惯例一致 |
| NX 返回 `False`（已被占位）计入哪个计数 | `skipped_busy` | spec §6 summary 固定字段无专门计数；"已被其他实例占位"语义上即 busy。`None`（Redis 故障）才计 `skipped_redis` |
| `trigger_run` 返回 `busy` / `error` 的计数 | `busy` → `skipped_busy`，`error` → `errors` | spec §6.5 只说"跳过，不动 ledger"；按语义归桶 |
| decide 的 unit 形态 | DAO 返回 `list[dict]`，decide 直接下标访问 | 与现有 DAO 全 dict 返回风格一致；spec §5 伪码的 `unit.x` 是示意 |
| FINAL/PROGRESS prompt 的计数口径 | session 级合计（本 tick 扫描到的该 session 全部单元求和） | spec §6.4 明确写 `render_prompt(primary_reason, session_counts, ...)` |
| `detail_limit` 配置的读取点 | `bohrium_delivery_ack.snapshot()` 读 env 并存入 `DeliverySnapshot.detail_limit` 字段，wiring 直接取用 | 保持 wiring 无 env 读取；snapshot 是 delivery 参数的天然载体 |
| `_env_int` 复用 | 直接 `from src.services.bohrium_poller import _env_int` | spec §10 指明"沿用 bohrium_poller._env_int 模式"；DRY |
| `get_first_pending_failed` 查不到（竞态窗口） | prompt 字段降级为 `unknown`/`-`，照常触发 | 扫描后失败行可能被并发用户 run ack；触发仍正确（context 行才是权威） |
| scan SQL 的 `max_terminal_at` / `first_pending_terminal_at` | SELECT 保留（与 spec SQL 一致，后者支撑 ORDER BY 别名引用），Python 转换层原样透传不参与判定 | 判定无 `now`、无时间依赖（硬约束 1） |
| dao 测试中 `evo_chat_sessions` | 测试 fixture 建最小影子表（id/user_id/org_id/session_id 四列） | EXISTS 子查询只点查这三列；测试库由 conftest 强制 `*_test` 库名防护 |
| spec §13 monitor 测试含「单轮异常不退出」 | 不单独写该用例；由两个 tick「自吞异常、绝不抛」的单元契约组合保证 | scheduler 侧专测 `test_tick_swallows_scan_failure_and_returns_tick_failed`，poller 侧既有覆盖；循环体只调 tick()，tick 不抛则单轮失败无法令循环退出 |

### 0.2 关键代码事实速查（执行者零上下文需要，已逐条核实）

- **DAO** `src/dao/bohrium_jobs_table.py`（336 行）：`_AGENT_COLUMNS`（:43，字符串 `"job_id, job_name, status, sandbox, project_id, input_dir, workspace, submitted_at, last_polled_at, result_dir"`）、`_to_agent_job`（:199，内嵌局部函数 `_ts` 做时间格式化）、`mark_handled`（:184，生产从未调用）、`query_session_pending_terminal`（:231，`limit` 形参默认 5）、`get_by_owner_job`（:319）、`list_all_for_test`（:331）。构造支持 `BohriumJobsTable(db_config=...)` 注入。
- **Redis** `src/dao/redis_dao.py`：类名 `RedisDao`（:75），`__init__` 惰性无副作用（:78-80），`get_command_client`（:90，无 `REDIS_URL` 返回 None），`mark_dedup_key_nx`（:352-365，双态、不可区分故障与已占位），`get_redis_dao()`（:572）。
- **Monitor** `src/monitor/monitor_worker.py`（70 行）：`_run_monitor_loop`（:32-50），循环外 `runner = BohriumMonitor()`（:43），循环体 tick→log→wait（:44-48）。
- **Worker** `src/worker/agent_worker.py`：`from src.services.sessions_service import get_sessions_service` 等模块级 import（:20-23）；`_run_worker_loop`（:294）；`acquired = False`（:378）；acquire 成功 `acquired = True`（:405）；`run_agent_kwargs` dict（:434-449）；`asyncio.run(agent_run_service.run_agent(**run_agent_kwargs))`（:450）；finally 内 `if acquired: sessions_service.release_session_run(session_id, run_success=run_success)`（:512-515）；payload 变量名 `delivery` 已被 notify spec 占用（:347）。
- **run_agent** `src/services/agent_run_service.py`：签名 :230-246（最后一个参数 `workspace: str | None = None`）；`build_bohrium_jobs_ports(...)` 调用（:515-521）；`_resolve_session_identity`（:74）。
- **Wiring** `src/services/bohrium_jobs_wiring.py`（211 行）：`_RunSessionJobsPort`（:144-180，`load_session_jobs` 内 `limit=5` 硬编码于 :168）、`build_bohrium_jobs_ports`（:183-211）。
- **Kernel ports** `matmaster/context/ports.py`：`SessionJobs`（:98-105，frozen dataclass，两个 tuple 字段 + `empty()`）。
- **Renderer** `matmaster/context/sources/session_jobs.py`（46 行）：`from_jobs`（:21-33）。唯一调用点 `matmaster/context/compositions.py:87`（`_step_session_jobs`，不传额外参数）。
- **触发** `src/services/stream_service.py`：`TriggerResult`（:126-133，字段 status/task_id/invocation_id/dedup_key/reason）、`trigger_run`（:353-442）、`get_stream_service`（:895）。
- **会话** `src/services/sessions_service.py`：`get_session_status`（:335，返回 idle|active|waiting|failed，waiting reconcile 跨进程可见）、`get_session`（:372，返回 dict 含 user_id/org_id）、`get_sessions_service`（:580）。
- **DeliverySpec** `src/models/chat.py:365-368`（`notify: bool = True`）；worker 侧 `_should_notify_completion`（agent_worker.py:76-80）。
- **表名**：`evo_chat_sessions`（chat_sessions_table.py:66）；org_id 列由 `src/sql/migrate_add_session_bohrium_columns.sql` 增加。
- **真库 fixture** `tests/dao/conftest.py`：`bohrium_jobs_db_config`（session 级 DROP/CREATE bohrium_jobs，库名强制 `*_test`）、`jobs_table`（每测试 TRUNCATE）、`db_conn`（裸连接）。
- **pytest**：`pyproject.toml` 设 `asyncio_mode = "auto"`（async 测试函数无需装饰器）。

---

### Task 1: DAO 四个新方法（聚合扫描 / 全量 snapshot / 按 ids ack / 首失败）

**Files:**
- Modify: `src/dao/bohrium_jobs_table.py`
- Test: `tests/dao/test_bohrium_jobs_delivery.py`（新增）

- [ ] **Step 1.1: 写失败测试**

新建 `tests/dao/test_bohrium_jobs_delivery.py`：

```python
"""scan_delivery_units / list_pending_terminal_snapshot / mark_handled_by_ids /
get_first_pending_failed 的真库测试（无 .env.test 则整组 SKIP）。"""

from __future__ import annotations

import pymysql
import pytest


@pytest.fixture()
def sessions_shadow(bohrium_jobs_db_config):
    """scan_delivery_units 的 EXISTS 谓词只点查 user_id/org_id/session_id，
    建最小影子表足够（测试库名由 conftest 强制 *_test，DROP 安全）。"""
    conn = pymysql.connect(**bohrium_jobs_db_config)
    try:
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS `evo_chat_sessions`")
            cur.execute(
                """
                CREATE TABLE `evo_chat_sessions` (
                    `id` BIGINT AUTO_INCREMENT PRIMARY KEY,
                    `user_id` VARCHAR(255) NULL,
                    `org_id` VARCHAR(255) NULL,
                    `session_id` VARCHAR(255) NOT NULL UNIQUE
                )
                """
            )
        conn.commit()
        yield conn
    finally:
        conn.close()


def _register_session(conn, *, session="sess-1", user="u1", org="o1"):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO evo_chat_sessions (user_id, org_id, session_id) "
            "VALUES (%s, %s, %s) "
            "ON DUPLICATE KEY UPDATE user_id=VALUES(user_id), org_id=VALUES(org_id)",
            (user, org, session),
        )
    conn.commit()


def _seed_job(
    jobs_table,
    *,
    session="sess-1",
    user="u1",
    org="o1",
    inv="inv-1",
    job_id="101",
    sandbox=False,
    status=None,
    handled=False,
):
    """插入一行；status 传 'finished'/'failed'/'stopped' 时推进到终态。"""
    jobs_table.insert_submitted(
        session_id=session,
        invocation_id=inv,
        spawn_id=None,
        user_id=user,
        org_id=org,
        job_id=job_id,
        job_name=f"name-{job_id}",
        project_id=42,
        sandbox=sandbox,
        input_dir="data/in",
        workspace="/share/project",
    )
    if status is not None:
        jobs_table.apply_poll(
            user_id=user,
            org_id=org,
            sandbox=sandbox,
            job_id=job_id,
            status=status,
            is_terminal=True,
            backoff_seconds=30,
        )
    if handled:
        jobs_table.mark_handled(
            user_id=user, org_id=org, sandbox=sandbox, job_id=job_id
        )


def _shift_terminal_at(conn, *, job_id, seconds_ago):
    """直接改 terminal_at 制造确定的时间序（apply_poll 走 NOW() 同秒并列）。"""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE bohrium_jobs SET terminal_at = NOW() - INTERVAL %s SECOND "
            "WHERE job_id = %s",
            (int(seconds_ago), job_id),
        )
    conn.commit()


def test_scan_aggregates_per_invocation(jobs_table, sessions_shadow):
    _register_session(sessions_shadow)
    # inv-1：1 失败终态未交付 + 1 活跃
    _seed_job(jobs_table, inv="inv-1", job_id="101", status="failed")
    _seed_job(jobs_table, inv="inv-1", job_id="102")
    # inv-2：1 成功终态未交付
    _seed_job(jobs_table, inv="inv-2", job_id="201", status="finished")

    units = jobs_table.scan_delivery_units(limit=10)

    assert [u["invocation_key"] for u in units] == ["inv-1", "inv-2"] or [
        u["invocation_key"] for u in units
    ] == ["inv-2", "inv-1"]
    by_key = {u["invocation_key"]: u for u in units}
    u1 = by_key["inv-1"]
    assert u1["total"] == 2 and u1["active"] == 1
    assert u1["pending_terminal"] == 1
    assert u1["failed_total"] == 1 and u1["failed_handled"] == 0
    assert u1["succeeded"] == 0
    assert u1["workspace"] == "/share/project"
    assert isinstance(u1["max_pending_terminal_id"], int)
    u2 = by_key["inv-2"]
    assert u2["total"] == 1 and u2["active"] == 0
    assert u2["pending_terminal"] == 1 and u2["succeeded"] == 1


def test_scan_excludes_owner_mismatch_rows(jobs_table, sessions_shadow):
    # session 当前 owner 是 (u1, o2)，ledger 行写于 o1 时期 → 必须被 EXISTS 滤掉
    _register_session(sessions_shadow, session="sess-1", user="u1", org="o2")
    _seed_job(jobs_table, org="o1", job_id="101", status="finished")

    assert jobs_table.scan_delivery_units(limit=10) == []


def test_scan_orders_oldest_pending_first_and_limits(jobs_table, sessions_shadow):
    for i, sess in enumerate(("sess-a", "sess-b", "sess-c"), 1):
        _register_session(sessions_shadow, session=sess)
        _seed_job(jobs_table, session=sess, job_id=str(100 + i), status="finished")
    # sess-c 最老，sess-a 最新
    _shift_terminal_at(sessions_shadow, job_id="103", seconds_ago=300)
    _shift_terminal_at(sessions_shadow, job_id="102", seconds_ago=200)
    _shift_terminal_at(sessions_shadow, job_id="101", seconds_ago=100)

    units = jobs_table.scan_delivery_units(limit=2)

    assert [u["session_id"] for u in units] == ["sess-c", "sess-b"]


def test_scan_null_invocation_groups_as_empty_key(jobs_table, sessions_shadow):
    _register_session(sessions_shadow)
    _seed_job(jobs_table, inv=None, job_id="101", status="finished")
    _seed_job(jobs_table, inv="inv-1", job_id="102", status="finished")

    units = jobs_table.scan_delivery_units(limit=10)

    assert sorted(u["invocation_key"] for u in units) == ["", "inv-1"]


def test_snapshot_returns_full_rows_failed_first_with_fields(
    jobs_table, sessions_shadow
):
    _register_session(sessions_shadow)
    for i in range(1, 7):  # 6 个成功 → 验证无 limit=5 截断
        _seed_job(jobs_table, job_id=str(100 + i), status="finished")
    _seed_job(jobs_table, job_id="200", status="failed")
    _shift_terminal_at(sessions_shadow, job_id="200", seconds_ago=600)  # 失败行最老

    rows = jobs_table.list_pending_terminal_snapshot(
        user_id="u1", org_id="o1", session_id="sess-1"
    )

    assert len(rows) == 7
    assert rows[0]["job_id"] == "200" and rows[0]["status"] == "failed"
    first = rows[0]
    # _AGENT_COLUMNS 全集 + id/invocation_id/terminal_at
    for key in (
        "job_id", "job_name", "status", "sandbox", "project_id", "input_dir",
        "workspace", "submitted_at", "last_polled_at", "result_dir",
        "id", "invocation_id", "terminal_at",
    ):
        assert key in first, f"missing field {key}"
    assert isinstance(first["id"], int)
    assert first["terminal_at"] is not None


def test_mark_handled_by_ids_idempotent_and_chunked(jobs_table, sessions_shadow):
    _register_session(sessions_shadow)
    for i in (1, 2, 3):
        _seed_job(jobs_table, job_id=str(100 + i), status="finished")
    rows = jobs_table.list_pending_terminal_snapshot(
        user_id="u1", org_id="o1", session_id="sess-1"
    )
    ids = [r["id"] for r in rows]

    # chunk_size=1 强制走分块路径；只标前两个
    affected = jobs_table.mark_handled_by_ids(
        user_id="u1", org_id="o1", session_id="sess-1",
        row_ids=ids[:2], chunk_size=1,
    )
    assert affected == 2
    remaining = jobs_table.list_pending_terminal_snapshot(
        user_id="u1", org_id="o1", session_id="sess-1"
    )
    assert [r["id"] for r in remaining] == [ids[2]]

    # 幂等：重复 ack 是 no-op
    assert (
        jobs_table.mark_handled_by_ids(
            user_id="u1", org_id="o1", session_id="sess-1", row_ids=ids[:2]
        )
        == 0
    )

    # 全部 handled 后该 session 不再出现在扫描里
    jobs_table.mark_handled_by_ids(
        user_id="u1", org_id="o1", session_id="sess-1", row_ids=ids
    )
    assert jobs_table.scan_delivery_units(limit=10) == []


def test_get_first_pending_failed_returns_earliest_unhandled(
    jobs_table, sessions_shadow
):
    _register_session(sessions_shadow)
    _seed_job(jobs_table, job_id="101", status="failed", handled=True)
    _seed_job(jobs_table, job_id="102", status="stopped")
    _seed_job(jobs_table, job_id="103", status="failed")
    _shift_terminal_at(sessions_shadow, job_id="102", seconds_ago=300)
    _shift_terminal_at(sessions_shadow, job_id="103", seconds_ago=100)

    row = jobs_table.get_first_pending_failed(
        user_id="u1", org_id="o1", session_id="sess-1", invocation_key="inv-1"
    )

    assert row == {"job_id": "102", "job_name": "name-102", "status": "stopped"}
```

- [ ] **Step 1.2: 运行确认失败**

```bash
.venv/bin/python -m pytest tests/dao/test_bohrium_jobs_delivery.py -v
```
预期：有 `.env.test` 时全部 FAIL（`AttributeError: ... has no attribute 'scan_delivery_units'`）；无库则全部 SKIP（继续 Step 1.3，正确性靠 SQL 评审）。

- [ ] **Step 1.3: 实现**

`src/dao/bohrium_jobs_table.py` 三处改动。

(a) 模块级时间格式化（放在 `_require_workspace` 之后），并让 `_to_agent_job` 改用它：

```python
def _format_ts(v: Any) -> str | None:
    return v.strftime("%Y-%m-%d %H:%M:%S") if v is not None else None
```

`_to_agent_job` 删除内嵌 `def _ts(...)`，两处 `_ts(` 调用改为 `_format_ts(`。

(b) 头部 import 增加：

```python
from collections.abc import Sequence
```

(c) 在 `get_by_owner_job` 之后、`list_all_for_test` 之前插入四个方法与两个转换 helper：

```python
    def scan_delivery_units(self, *, limit: int) -> list[dict[str, Any]]:
        """交付聚合扫描：逐 (owner, session, invocation) 统计，仅含 pending>0 单元。

        最老 pending 优先（防饥饿）；EXISTS 在 SQL 层滤掉 owner 与当前
        session row 不一致的行（org 切换/脏数据），否则它们会永久占据队首。
        """
        sql = f"""
            SELECT
                user_id,
                org_id,
                session_id,
                COALESCE(invocation_id, '')                          AS invocation_key,
                MIN(workspace)                                       AS workspace,
                COUNT(*)                                             AS total,
                SUM(terminal_at IS NULL)                             AS active,
                SUM(terminal_at IS NOT NULL AND handled_at IS NULL)  AS pending_terminal,
                SUM(status IN ('failed','stopped'))                  AS failed_total,
                SUM(status IN ('failed','stopped')
                    AND handled_at IS NOT NULL)                      AS failed_handled,
                SUM(status = 'finished')                             AS succeeded,
                MAX(terminal_at)                                     AS max_terminal_at,
                MAX(CASE WHEN terminal_at IS NOT NULL AND handled_at IS NULL
                         THEN id END)                                AS max_pending_terminal_id,
                MIN(CASE WHEN terminal_at IS NOT NULL AND handled_at IS NULL
                         THEN terminal_at END)                       AS first_pending_terminal_at
            FROM {self.table_name}
            WHERE EXISTS (
                SELECT 1 FROM evo_chat_sessions s
                WHERE s.session_id = {self.table_name}.session_id
                  AND s.user_id    = {self.table_name}.user_id
                  AND s.org_id     = {self.table_name}.org_id
            )
            GROUP BY user_id, org_id, session_id, COALESCE(invocation_id, '')
            HAVING pending_terminal > 0
            ORDER BY first_pending_terminal_at ASC, user_id ASC, org_id ASC,
                     session_id ASC, invocation_key ASC
            LIMIT %s
        """
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (int(limit),))
                rows = cur.fetchall()
        return [self._to_delivery_unit(r) for r in rows]

    @staticmethod
    def _to_delivery_unit(row: dict[str, Any]) -> dict[str, Any]:
        # PyMySQL 下 SUM 返回 Decimal，统一转 int；HAVING 保证 max_id 非 NULL
        return {
            "user_id": str(row["user_id"]),
            "org_id": str(row["org_id"]),
            "session_id": str(row["session_id"]),
            "invocation_key": str(row["invocation_key"]),
            "workspace": row["workspace"],
            "total": int(row["total"]),
            "active": int(row["active"]),
            "pending_terminal": int(row["pending_terminal"]),
            "failed_total": int(row["failed_total"]),
            "failed_handled": int(row["failed_handled"]),
            "succeeded": int(row["succeeded"]),
            "max_terminal_at": row["max_terminal_at"],
            "max_pending_terminal_id": int(row["max_pending_terminal_id"]),
            "first_pending_terminal_at": row["first_pending_terminal_at"],
        }

    def list_pending_terminal_snapshot(
        self, *, user_id: str, org_id: str, session_id: str
    ) -> list[dict[str, Any]]:
        """本轮 delivery 的权威集合：全量 pending terminal 行，失败/停止优先。

        无 limit——查询执行瞬间即交付边界；字段 = _AGENT_COLUMNS +
        id/invocation_id/terminal_at，保证换源不造成字段回归。
        """
        sql = f"""
            SELECT id, invocation_id, terminal_at, {self._AGENT_COLUMNS}
            FROM {self.table_name}
            WHERE user_id = %s AND org_id = %s AND session_id = %s
              AND terminal_at IS NOT NULL AND handled_at IS NULL
            ORDER BY
                (status IN ('failed','stopped')) DESC,
                terminal_at ASC, submitted_at ASC, id ASC
        """
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (user_id, org_id, session_id))
                rows = cur.fetchall()
        return [self._to_snapshot_job(r) for r in rows]

    @classmethod
    def _to_snapshot_job(cls, row: dict[str, Any]) -> dict[str, Any]:
        job = cls._to_agent_job(row)
        job["id"] = int(row["id"])
        job["invocation_id"] = row["invocation_id"]
        job["terminal_at"] = _format_ts(row["terminal_at"])
        return job

    def mark_handled_by_ids(
        self,
        *,
        user_id: str,
        org_id: str,
        session_id: str,
        row_ids: Sequence[int],
        chunk_size: int = 500,
    ) -> int:
        """按 snapshot row ids 批量 ack；幂等（handled_at IS NULL 谓词）。

        返回实际更新行数；分块单事务提交。
        """
        ids = [int(i) for i in row_ids]
        if not ids:
            return 0
        affected = 0
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                for start in range(0, len(ids), int(chunk_size)):
                    chunk = ids[start : start + int(chunk_size)]
                    placeholders = ", ".join(["%s"] * len(chunk))
                    cur.execute(
                        f"""
                        UPDATE {self.table_name}
                        SET handled_at = NOW()
                        WHERE user_id = %s AND org_id = %s AND session_id = %s
                          AND id IN ({placeholders})
                          AND terminal_at IS NOT NULL
                          AND handled_at IS NULL
                        """,
                        (user_id, org_id, session_id, *chunk),
                    )
                    affected += cur.rowcount
            conn.commit()
        return affected

    def get_first_pending_failed(
        self, *, user_id: str, org_id: str, session_id: str, invocation_key: str
    ) -> dict[str, Any] | None:
        """该 invocation 最早一个未交付失败作业（FIRST_FAILURE prompt 用）。"""
        sql = f"""
            SELECT job_id, job_name, status
            FROM {self.table_name}
            WHERE user_id = %s AND org_id = %s AND session_id = %s
              AND COALESCE(invocation_id, '') = %s
              AND status IN ('failed','stopped')
              AND handled_at IS NULL
            ORDER BY terminal_at ASC, id ASC
            LIMIT 1
        """
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (user_id, org_id, session_id, invocation_key))
                return cur.fetchone()
```

- [ ] **Step 1.4: 运行确认通过（含既有 DAO 测试无回归）**

```bash
.venv/bin/python -m pytest tests/dao/ tests/services/test_bohrium_poller.py -v
```
预期：全 PASS（或无库环境全 SKIP）。`_to_agent_job` 的 `_format_ts` 重构由既有 `test_bohrium_jobs_table.py` 覆盖。

- [ ] **Step 1.5: Commit**

```bash
git add src/dao/bohrium_jobs_table.py tests/dao/test_bohrium_jobs_delivery.py
git commit -m "feat(dao): add bohrium delivery scan/snapshot/ack/first-failed queries

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Redis 三态 `try_reserve_nx`

**Files:**
- Modify: `src/dao/redis_dao.py`
- Test: `tests/dao/test_redis_dao_reserve_nx.py`（新增，纯单元、不连真 Redis）

- [ ] **Step 2.1: 写失败测试**

新建 `tests/dao/test_redis_dao_reserve_nx.py`：

```python
"""try_reserve_nx 三态语义：True=占位成功 / False=已被占位 / None=无 client 或异常。

区分 False 与 None 是 scheduler fail-closed（skipped_redis 计数 + 告警）的前提，
现有 mark_dedup_key_nx 双态无法区分，故新增而非改造。
"""

from __future__ import annotations

from src.dao.redis_dao import RedisDao


class _FakeClient:
    def __init__(self, result=True, exc=None):
        self.result = result
        self.exc = exc
        self.calls = []

    def set(self, key, value, nx=False, ex=None):
        if self.exc is not None:
            raise self.exc
        self.calls.append({"key": key, "value": value, "nx": nx, "ex": ex})
        return self.result


def _dao_with(monkeypatch, client):
    dao = RedisDao()  # __init__ 惰性，无副作用
    monkeypatch.setattr(dao, "get_command_client", lambda: client)
    return dao


def test_reserve_returns_true_on_first_set(monkeypatch):
    client = _FakeClient(result=True)
    dao = _dao_with(monkeypatch, client)
    assert dao.try_reserve_nx("k1", "1", ttl_sec=60) is True
    assert client.calls == [{"key": "k1", "value": "1", "nx": True, "ex": 60}]


def test_reserve_returns_false_when_already_held(monkeypatch):
    # redis-py 的 SET NX 未设上时返回 None
    dao = _dao_with(monkeypatch, _FakeClient(result=None))
    assert dao.try_reserve_nx("k1", "1", ttl_sec=60) is False


def test_reserve_returns_none_without_client(monkeypatch):
    dao = _dao_with(monkeypatch, None)
    assert dao.try_reserve_nx("k1", "1", ttl_sec=60) is None


def test_reserve_returns_none_on_exception(monkeypatch):
    dao = _dao_with(monkeypatch, _FakeClient(exc=RuntimeError("down")))
    assert dao.try_reserve_nx("k1", "1", ttl_sec=60) is None
```

- [ ] **Step 2.2: 运行确认失败**

```bash
.venv/bin/python -m pytest tests/dao/test_redis_dao_reserve_nx.py -v
```
预期：FAIL，`AttributeError: 'RedisDao' object has no attribute 'try_reserve_nx'`。

- [ ] **Step 2.3: 实现**

`src/dao/redis_dao.py`，在 `mark_dedup_key_nx`（:352-365）之后插入：

```python
    def try_reserve_nx(self, key: str, value: str, ttl_sec: int) -> bool | None:
        """三态 SET NX EX 占位：True=占位成功 / False=已被占位 / None=无 client 或异常。

        与 mark_dedup_key_nx 的区别：调用方需要区分「已被占位」与「Redis 不可用」
        （后者按 fail-closed skip 并计数告警）。key 由调用方自带前缀，不加 DEDUP_KEY_PREFIX。
        """
        client = self.get_command_client()
        if not client:
            return None
        try:
            result = client.set(key, value, nx=True, ex=int(ttl_sec))
        except Exception as e:
            logger.warning("Redis try_reserve_nx failed key=%s: %s", key, e)
            return None
        return bool(result)
```

- [ ] **Step 2.4: 运行确认通过**

```bash
.venv/bin/python -m pytest tests/dao/test_redis_dao_reserve_nx.py -v
```
预期：4 PASS。

- [ ] **Step 2.5: Commit**

```bash
git add src/dao/redis_dao.py tests/dao/test_redis_dao_reserve_nx.py
git commit -m "feat(dao): add tri-state try_reserve_nx for delivery reservation

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Renderer 详情压缩（`SessionJobs.detail_limit` + 溢出摘要）

**Files:**
- Modify: `matmaster/context/ports.py:98-105`
- Modify: `matmaster/context/sources/session_jobs.py`
- Test: `tests/matmaster/context/sources/test_session_jobs.py`（扩展现有文件）

**不碰**：`SessionJobsPort` Protocol 方法签名、`from_jobs` 签名、`compositions.py:87` 调用形态。

- [ ] **Step 3.1: 写失败测试**

在 `tests/matmaster/context/sources/test_session_jobs.py` 末尾追加（现有 4 个测试构造 `SessionJobs` 不传 `detail_limit` → 默认 None → 行为不变，它们必须保持绿，即"None 时与现状逐行一致"的回归证明）：

```python
def _job(job_id: str, status: str = "finished", sandbox: bool = False) -> dict:
    return {"job_id": job_id, "status": status, "sandbox": sandbox}


def test_detail_limit_compresses_pending_with_overflow_summary() -> None:
    jobs = SessionJobs(
        pending_terminal_jobs=(
            _job("f1", "failed"),
            _job("t1"),
            _job("t2"),
            _job("t3", "stopped"),
        ),
        detail_limit=2,
    )
    lines = SessionJobsSource.from_jobs(jobs).lines

    assert lines[0].startswith('pending_terminal_job_1 {"job_id": "f1"')
    assert lines[1].startswith('pending_terminal_job_2 {"job_id": "t1"')
    assert len(lines) == 3
    assert lines[2] == (
        'pending_terminal_overflow '
        '{"by_status": {"finished": 1, "stopped": 1}, '
        '"count": 2, "job_ids": ["t2", "t3"]}'
    )


def test_detail_limit_compresses_active_independently() -> None:
    jobs = SessionJobs(
        active_jobs=(_job("a1", "running"), _job("a2", "running"), _job("a3", "submitted")),
        pending_terminal_jobs=(_job("t1"),),
        detail_limit=1,
    )
    lines = SessionJobsSource.from_jobs(jobs).lines

    assert lines[0].startswith("active_job_1 ")
    assert lines[1] == (
        'active_overflow '
        '{"by_status": {"running": 1, "submitted": 1}, '
        '"count": 2, "job_ids": ["a2", "a3"]}'
    )
    # pending 共 1 条 ≤ limit=1：全展开、无溢出行
    assert lines[2].startswith("pending_terminal_job_1 ")
    assert len(lines) == 3


def test_detail_limit_covers_all_ids_between_detail_and_overflow() -> None:
    all_ids = [f"j{i}" for i in range(7)]
    jobs = SessionJobs(
        pending_terminal_jobs=tuple(_job(i) for i in all_ids),
        detail_limit=3,
    )
    lines = SessionJobsSource.from_jobs(jobs).lines

    import json as _json

    detail_ids = [
        _json.loads(line.split(" ", 1)[1])["job_id"] for line in lines[:3]
    ]
    overflow = _json.loads(lines[3].split(" ", 1)[1])
    # 硬规则：详情行 + 溢出 job_ids 合起来覆盖 snapshot 全量 id
    assert detail_ids + overflow["job_ids"] == all_ids


def test_detail_limit_no_overflow_when_limit_covers_all() -> None:
    jobs = SessionJobs(
        pending_terminal_jobs=(_job("t1"), _job("t2")),
        detail_limit=2,
    )
    lines = SessionJobsSource.from_jobs(jobs).lines
    assert len(lines) == 2
    assert not any("overflow" in line for line in lines)


def test_overflow_job_ids_keep_same_job_id_across_sandboxes() -> None:
    # 唯一键含 sandbox：同 job_id 可两行并存，计数与 id 列表以 row 为准、不去重
    jobs = SessionJobs(
        pending_terminal_jobs=(
            _job("keep"),
            _job("dup", sandbox=False),
            _job("dup", sandbox=True),
        ),
        detail_limit=1,
    )
    lines = SessionJobsSource.from_jobs(jobs).lines

    import json as _json

    overflow = _json.loads(lines[1].split(" ", 1)[1])
    assert overflow["count"] == 2
    assert overflow["job_ids"] == ["dup", "dup"]
```

- [ ] **Step 3.2: 运行确认失败**

```bash
.venv/bin/python -m pytest tests/matmaster/context/sources/test_session_jobs.py -v
```
预期：现有 4 个 PASS，新增 5 个 FAIL（`TypeError: ... unexpected keyword argument 'detail_limit'`）。

- [ ] **Step 3.3: 实现**

(a) `matmaster/context/ports.py` 的 `SessionJobs`（:98-105）加字段：

```python
@dataclass(frozen=True)
class SessionJobs:
    active_jobs: tuple[JsonObject, ...] = ()
    pending_terminal_jobs: tuple[JsonObject, ...] = ()
    detail_limit: int | None = None

    @classmethod
    def empty(cls) -> SessionJobs:
        return cls(active_jobs=(), pending_terminal_jobs=())
```

(b) `matmaster/context/sources/session_jobs.py` 的 `from_jobs` 整体替换为两段式：

```python
    @classmethod
    def from_jobs(cls, jobs: SessionJobs) -> SessionJobsSource:
        active = cls._render_group(
            "active_job", "active_overflow", jobs.active_jobs, jobs.detail_limit
        )
        pending = cls._render_group(
            "pending_terminal_job",
            "pending_terminal_overflow",
            jobs.pending_terminal_jobs,
            jobs.detail_limit,
        )
        return cls(lines=active + pending)

    @staticmethod
    def _render_group(
        prefix: str,
        overflow_tag: str,
        items: tuple,
        limit: int | None,
    ) -> tuple[str, ...]:
        """前 limit 条完整详情，其余压成一行溢出摘要；全量 job_id 始终可见。

        limit 为 None（无 delivery snapshot 的回退路径）时全量逐行，与历史行为一致。
        """
        if limit is None or len(items) <= limit:
            shown, rest = items, ()
        else:
            shown, rest = items[:limit], items[limit:]
        lines = tuple(
            f"{prefix}_{index} "
            f"{json.dumps(job, ensure_ascii=False, sort_keys=True)}"
            for index, job in enumerate(shown, 1)
        )
        if rest:
            by_status: dict[str, int] = {}
            for job in rest:
                status = str(job.get("status"))
                by_status[status] = by_status.get(status, 0) + 1
            summary = {
                "count": len(rest),
                "by_status": by_status,
                # 不按 job_id 去重：唯一键含 sandbox，计数与 ack 以 row 为准
                "job_ids": [str(job.get("job_id")) for job in rest],
            }
            lines += (
                f"{overflow_tag} "
                f"{json.dumps(summary, ensure_ascii=False, sort_keys=True)}",
            )
        return lines
```

- [ ] **Step 3.4: 运行确认通过（含 kernel 侧关联测试无回归）**

```bash
.venv/bin/python -m pytest tests/matmaster/context/ tests/matmaster/test_runtime_context_assembly_session_jobs.py -v
```
预期：全 PASS。

- [ ] **Step 3.5: Commit**

```bash
git add matmaster/context/ports.py matmaster/context/sources/session_jobs.py tests/matmaster/context/sources/test_session_jobs.py
git commit -m "feat(context): session_jobs detail_limit compression with overflow summary

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: `bohrium_delivery_ack`（DeliverySnapshot / snapshot / confirm）

**Files:**
- Create: `src/services/bohrium_delivery_ack.py`
- Test: `tests/services/test_bohrium_delivery_ack.py`（新增）

- [ ] **Step 4.1: 写失败测试**

新建 `tests/services/test_bohrium_delivery_ack.py`：

```python
"""DeliverySnapshot 的构造与 confirm 范围：snapshot 持全量 row/job ids 与行，
confirm 只 ack snapshot.row_ids（交付边界 = 查询执行瞬间）。"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.services import bohrium_delivery_ack


def _row(rid: int, job_id: str, status: str = "finished", inv: str | None = "inv-1"):
    # 形状 = BohriumJobsTable._to_snapshot_job 的输出（_AGENT_COLUMNS + 附加三字段）
    return {
        "job_id": job_id,
        "job_name": f"name-{job_id}",
        "status": status,
        "sandbox": False,
        "project_id": 42,
        "input_dir": "data/in",
        "workspace": "/share/project",
        "submitted_at": "2026-06-10 00:00:00",
        "last_polled_at": "2026-06-10 00:05:00",
        "result_dir": f"/share/project/out/{job_id}",
        "id": rid,
        "invocation_id": inv,
        "terminal_at": "2026-06-10 00:05:00",
    }


def _sessions(user="u1", org="o1"):
    svc = MagicMock()
    svc.get_session.return_value = {"user_id": user, "org_id": org}
    return svc


def test_snapshot_holds_full_ids_rows_and_counts():
    rows = [
        _row(11, "f1", status="failed"),
        _row(12, "t1"),
        _row(13, "t2", inv=None),
    ]
    table = MagicMock()
    table.list_pending_terminal_snapshot.return_value = rows

    snap = bohrium_delivery_ack.snapshot(
        "sess-1", sessions_service=_sessions(), jobs_table=table
    )

    assert snap.user_id == "u1" and snap.org_id == "o1"
    assert snap.session_id == "sess-1"
    assert snap.row_ids == (11, 12, 13)  # DAO 失败优先序原样保持
    assert snap.job_ids == ("f1", "t1", "t2")
    assert snap.rows == tuple(rows)
    assert snap.rows[0]["result_dir"] == "/share/project/out/f1"  # 取结果字段在场
    assert snap.status_counts == {"failed": 1, "finished": 2}
    assert snap.invocation_counts == {"inv-1": 2, "": 1}
    kw = table.list_pending_terminal_snapshot.call_args.kwargs
    assert kw == {"user_id": "u1", "org_id": "o1", "session_id": "sess-1"}


def test_snapshot_reads_detail_limit_from_env(monkeypatch):
    monkeypatch.setenv("BOHRIUM_DELIVERY_DETAIL_LIMIT", "7")
    table = MagicMock()
    table.list_pending_terminal_snapshot.return_value = [_row(1, "a")]
    snap = bohrium_delivery_ack.snapshot(
        "sess-1", sessions_service=_sessions(), jobs_table=table
    )
    assert snap.detail_limit == 7


def test_snapshot_returns_none_when_no_pending_rows():
    table = MagicMock()
    table.list_pending_terminal_snapshot.return_value = []
    assert (
        bohrium_delivery_ack.snapshot(
            "sess-1", sessions_service=_sessions(), jobs_table=table
        )
        is None
    )


def test_snapshot_returns_none_without_org_binding():
    svc = MagicMock()
    svc.get_session.return_value = {"user_id": "u1", "org_id": None}
    table = MagicMock()
    assert (
        bohrium_delivery_ack.snapshot("sess-1", sessions_service=svc, jobs_table=table)
        is None
    )
    table.list_pending_terminal_snapshot.assert_not_called()


def test_snapshot_returns_none_on_query_failure_without_raising():
    table = MagicMock()
    table.list_pending_terminal_snapshot.side_effect = RuntimeError("db down")
    assert (
        bohrium_delivery_ack.snapshot(
            "sess-1", sessions_service=_sessions(), jobs_table=table
        )
        is None
    )


def test_confirm_acks_exactly_snapshot_row_ids():
    table = MagicMock()
    table.list_pending_terminal_snapshot.return_value = [_row(11, "a"), _row(12, "b")]
    snap = bohrium_delivery_ack.snapshot(
        "sess-1", sessions_service=_sessions(), jobs_table=table
    )
    table.mark_handled_by_ids.return_value = 2

    assert bohrium_delivery_ack.confirm(snap, jobs_table=table) == 2
    kw = table.mark_handled_by_ids.call_args.kwargs
    assert kw == {
        "user_id": "u1",
        "org_id": "o1",
        "session_id": "sess-1",
        "row_ids": (11, 12),
    }


def test_confirm_propagates_failure_to_caller():
    # worker 层负责吞掉并继续 release；confirm 本身不掩盖失败
    table = MagicMock()
    table.mark_handled_by_ids.side_effect = RuntimeError("db down")
    snap = bohrium_delivery_ack.DeliverySnapshot(
        user_id="u1", org_id="o1", session_id="s",
        row_ids=(1,), job_ids=("a",), rows=(_row(1, "a"),),
        status_counts={"finished": 1}, invocation_counts={"inv-1": 1},
        detail_limit=20,
    )
    with pytest.raises(RuntimeError):
        bohrium_delivery_ack.confirm(snap, jobs_table=table)
```

- [ ] **Step 4.2: 运行确认失败**

```bash
.venv/bin/python -m pytest tests/services/test_bohrium_delivery_ack.py -v
```
预期：整文件收集 ERROR（`ModuleNotFoundError: No module named 'src.services.bohrium_delivery_ack'`，模块级 import 失败，单测不运行）。

- [ ] **Step 4.3: 实现**

新建 `src/services/bohrium_delivery_ack.py`：

```python
"""Worker 侧 delivery snapshot 与 ack（对所有 run 生效，不分 origin）。

- snapshot：run 起点（acquire 成功后、run_agent 前）查询全量 pending terminal
  rows；查询执行瞬间即本轮交付边界，run 中途新终态的行留待下轮（at-least-once）。
- confirm：run 成功收尾、release_session_run 之前，按 snapshot.row_ids 批量
  mark_handled——ack 范围 = agent 看到范围。
handled_at 的唯一写入点在这里；poller 与 trigger enqueued 均不得 ack。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from src.services.bohrium_poller import _env_int

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeliverySnapshot:
    """一次 run 的交付边界快照（worker 内存对象，不落表）。

    rows 持全量行、不预截断：展开几条详情由 renderer 按 detail_limit 决定。
    """

    user_id: str
    org_id: str
    session_id: str
    row_ids: tuple[int, ...]
    job_ids: tuple[str, ...]
    rows: tuple[dict[str, Any], ...]
    status_counts: dict[str, int]
    invocation_counts: dict[str, int]
    detail_limit: int


def snapshot(
    session_id: str,
    *,
    sessions_service: Any | None = None,
    jobs_table: Any | None = None,
) -> DeliverySnapshot | None:
    """查询全量 pending terminal rows；失败或空集返回 None（不阻断 run）。"""
    try:
        svc = sessions_service
        if svc is None:
            from src.services.sessions_service import get_sessions_service

            svc = get_sessions_service()
        row = svc.get_session(session_id)
        if not row:
            return None
        user_id = str(row.get("user_id") or "")
        org_id = str(row.get("org_id") or "")
        if not (user_id and org_id):
            return None
        table = jobs_table
        if table is None:
            from src.dao.bohrium_jobs_table import BohriumJobsTable

            table = BohriumJobsTable()
        rows = table.list_pending_terminal_snapshot(
            user_id=user_id, org_id=org_id, session_id=session_id
        )
        if not rows:
            return None
        status_counts: dict[str, int] = {}
        invocation_counts: dict[str, int] = {}
        for job in rows:
            status = str(job["status"])
            status_counts[status] = status_counts.get(status, 0) + 1
            inv = str(job.get("invocation_id") or "")
            invocation_counts[inv] = invocation_counts.get(inv, 0) + 1
        return DeliverySnapshot(
            user_id=user_id,
            org_id=org_id,
            session_id=session_id,
            row_ids=tuple(int(j["id"]) for j in rows),
            job_ids=tuple(str(j["job_id"]) for j in rows),
            rows=tuple(rows),
            status_counts=status_counts,
            invocation_counts=invocation_counts,
            detail_limit=_env_int("BOHRIUM_DELIVERY_DETAIL_LIMIT", 20),
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "bohrium delivery snapshot failed session_id=%s",
            session_id,
            exc_info=True,
        )
        return None


def confirm(snap: DeliverySnapshot, *, jobs_table: Any | None = None) -> int:
    """按 snapshot.row_ids 批量 mark_handled；异常向上抛，由调用方决定善后。"""
    table = jobs_table
    if table is None:
        from src.dao.bohrium_jobs_table import BohriumJobsTable

        table = BohriumJobsTable()
    return table.mark_handled_by_ids(
        user_id=snap.user_id,
        org_id=snap.org_id,
        session_id=snap.session_id,
        row_ids=snap.row_ids,
    )
```

- [ ] **Step 4.4: 运行确认通过**

```bash
.venv/bin/python -m pytest tests/services/test_bohrium_delivery_ack.py -v
```
预期：7 PASS。

- [ ] **Step 4.5: Commit**

```bash
git add src/services/bohrium_delivery_ack.py tests/services/test_bohrium_delivery_ack.py
git commit -m "feat(services): bohrium delivery snapshot/confirm module

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Wiring——`_RunSessionJobsPort` 持有 snapshot

**Files:**
- Modify: `src/services/bohrium_jobs_wiring.py:144-211`
- Test: `tests/services/test_bohrium_jobs_wiring.py`（扩展现有文件）

- [ ] **Step 5.1: 写失败测试**

在 `tests/services/test_bohrium_jobs_wiring.py` 末尾追加：

```python
def _snapshot(rows):
    from src.services.bohrium_delivery_ack import DeliverySnapshot

    return DeliverySnapshot(
        user_id="u",
        org_id="o",
        session_id="s",
        row_ids=tuple(r["id"] for r in rows),
        job_ids=tuple(r["job_id"] for r in rows),
        rows=tuple(rows),
        status_counts={},
        invocation_counts={},
        detail_limit=20,
    )


@pytest.mark.asyncio
async def test_jobs_port_serves_pending_from_snapshot_with_detail_limit() -> None:
    table = MagicMock()
    table.query_session_active.return_value = [{"job_id": "a"}]
    snap_rows = [
        {"id": 2, "job_id": "f1", "status": "failed"},
        {"id": 1, "job_id": "t1", "status": "finished"},
    ]
    _, jobs_port = build_bohrium_jobs_ports(
        session_id="s",
        invocation_id="inv",
        user_id="u",
        org_id="o",
        workspace=None,
        table=table,
        delivery_snapshot=_snapshot(snap_rows),
    )
    from matmaster.context.ports import SessionJobsQuery

    result = await jobs_port.load_session_jobs(SessionJobsQuery(session_id="s"))

    # pending 据 snapshot.rows（失败优先序原样），不再裸查 limit=5 定交付集合
    assert result.pending_terminal_jobs == tuple(snap_rows)
    assert result.detail_limit == 20
    table.query_session_pending_terminal.assert_not_called()
    # active 仍走实时查询（snapshot 只钉死 pending）
    assert result.active_jobs == ({"job_id": "a"},)
    table.query_session_active.assert_called_once()


@pytest.mark.asyncio
async def test_jobs_port_without_snapshot_keeps_legacy_read_path() -> None:
    table = MagicMock()
    table.query_session_active.return_value = []
    table.query_session_pending_terminal.return_value = [{"job_id": "t"}]
    _, jobs_port = build_bohrium_jobs_ports(
        session_id="s",
        invocation_id="inv",
        user_id="u",
        org_id="o",
        workspace=None,
        table=table,
    )
    from matmaster.context.ports import SessionJobsQuery

    result = await jobs_port.load_session_jobs(SessionJobsQuery(session_id="s"))

    assert result.pending_terminal_jobs == ({"job_id": "t"},)
    assert result.detail_limit is None
    assert table.query_session_pending_terminal.call_args.kwargs["limit"] == 5
```

- [ ] **Step 5.2: 运行确认失败**

```bash
.venv/bin/python -m pytest tests/services/test_bohrium_jobs_wiring.py -v
```
预期：现有测试 PASS，新增 2 个 FAIL（`TypeError: ... unexpected keyword argument 'delivery_snapshot'`）。

- [ ] **Step 5.3: 实现**

`src/services/bohrium_jobs_wiring.py` 三处改动。

(a) 头部 import 增加：

```python
from src.services.bohrium_delivery_ack import DeliverySnapshot
```

(b) `_RunSessionJobsPort` 整体替换为：

```python
class _RunSessionJobsPort:
    def __init__(
        self,
        *,
        table_ref: _BohriumJobsTableRef,
        user_id: str,
        org_id: str,
        snapshot: DeliverySnapshot | None = None,
    ) -> None:
        self._table_ref = table_ref
        self._user_id = user_id
        self._org_id = org_id
        self._snapshot = snapshot

    async def load_session_jobs(self, query: SessionJobsQuery) -> SessionJobs:
        if not (self._user_id and self._org_id):
            return SessionJobs.empty()
        try:
            table = self._table_ref.get()
            active = await asyncio.to_thread(
                table.query_session_active,
                user_id=self._user_id,
                org_id=self._org_id,
                session_id=query.session_id,
            )
            if self._snapshot is not None:
                # 本轮交付边界固定：compaction 再调时返回同一 snapshot 的 pending
                pending = self._snapshot.rows
                detail_limit: int | None = self._snapshot.detail_limit
            else:
                rows = await asyncio.to_thread(
                    table.query_session_pending_terminal,
                    user_id=self._user_id,
                    org_id=self._org_id,
                    session_id=query.session_id,
                    limit=5,
                )
                pending = tuple(rows)
                detail_limit = None
        except Exception:  # noqa: BLE001
            logger.warning(
                "load_session_jobs failed session_id=%s",
                query.session_id,
                exc_info=True,
            )
            return SessionJobs.empty()
        return SessionJobs(
            active_jobs=tuple(active),
            pending_terminal_jobs=tuple(pending),
            detail_limit=detail_limit,
        )
```

(c) `build_bohrium_jobs_ports` 签名加 kwarg 并传给 port（其余不动）：

```python
def build_bohrium_jobs_ports(
    *,
    session_id: str,
    invocation_id: str | None,
    user_id: str,
    org_id: str,
    workspace: str | None,
    spawn_id: str | None = None,
    delivery_snapshot: DeliverySnapshot | None = None,
    table: BohriumJobsTable | None = None,
    table_factory: Callable[[], BohriumJobsTable] = BohriumJobsTable,
) -> tuple[_BohriumJobLedger | None, _RunSessionJobsPort]:
```

末尾构造行改为：

```python
    jobs = _RunSessionJobsPort(
        table_ref=table_ref,
        user_id=user_id,
        org_id=org_id,
        snapshot=delivery_snapshot,
    )
```

- [ ] **Step 5.4: 运行确认通过**

```bash
.venv/bin/python -m pytest tests/services/test_bohrium_jobs_wiring.py -v
```
预期：全 PASS。

- [ ] **Step 5.5: Commit**

```bash
git add src/services/bohrium_jobs_wiring.py tests/services/test_bohrium_jobs_wiring.py
git commit -m "feat(services): jobs port serves pending from delivery snapshot

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Worker 接入（run 起点 snapshot → 透传 run_agent → 成功收尾 confirm 于 release 之前）

**Files:**
- Modify: `src/services/agent_run_service.py`（run_agent 签名 :230-246 + 透传 :515-521）
- Modify: `src/worker/agent_worker.py`（:378 / :405 / :434-449 / :512-515）
- Test: `tests/test_agent_worker_snapshot_confirm.py`（新增）

- [ ] **Step 6.1: 写失败测试**

新建 `tests/test_agent_worker_snapshot_confirm.py`：

```python
"""Worker 主循环的 delivery snapshot 时序（对所有 origin 的 run 生效）：

acquire → snapshot → run_agent(收到 snapshot) → [run 成功] confirm → release。
run 失败不 confirm；confirm 异常不阻断 release；snapshot 为 None 照常 run。
跑真实 _run_worker_loop 一轮（blpop 第二次返回 None + _drain_requested 退出）。
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.worker import agent_worker


def _run_one_round(
    monkeypatch,
    *,
    snapshot_obj,
    run_result,
    confirm_exc=None,
    run_agent_exc=None,
):
    """注入全部外部依赖，跑一轮循环，返回 (有序调用名列表, run_agent 收到的 kwargs)。"""
    calls: list[str] = []
    received: dict = {}

    payload = {
        "session_id": "sess-1",
        "task_id": "task-1",
        "user_prompt": "hi",
        # notify=False 跳过完成卡片/邮件分支，缩小注入面
        "delivery": {"notify": False},
    }

    fake_redis = MagicMock()
    fake_redis.blpop_agent_run_job.side_effect = [payload, None]
    fake_redis.is_stop_requested.return_value = False  # 取消桥轮询不得触发 cancel
    fake_redis.llen_agent_run_queue.return_value = 0
    monkeypatch.setattr(agent_worker, "get_redis_dao", lambda: fake_redis)

    fake_sessions = MagicMock()
    fake_sessions.try_acquire_session_run.side_effect = lambda sid: (
        calls.append("acquire"),
        (True, None),
    )[1]
    fake_sessions.get_session_user_id.return_value = "u1"
    fake_sessions.release_session_run.side_effect = (
        lambda sid, run_success: calls.append(f"release:{run_success}")
    )
    monkeypatch.setattr(agent_worker, "get_sessions_service", lambda: fake_sessions)

    async def fake_run_agent(**kwargs):
        calls.append("run_agent")
        received.update(kwargs)
        if run_agent_exc is not None:
            raise run_agent_exc
        return (run_result, 5, None)

    fake_ars = MagicMock()
    fake_ars.run_agent = fake_run_agent
    monkeypatch.setattr(agent_worker, "get_agent_run_service", lambda: fake_ars)

    def fake_snapshot(session_id):
        calls.append("snapshot")
        return snapshot_obj

    def fake_confirm(snap):
        calls.append("confirm")
        if confirm_exc is not None:
            raise confirm_exc
        return 1

    monkeypatch.setattr(agent_worker.bohrium_delivery_ack, "snapshot", fake_snapshot)
    monkeypatch.setattr(agent_worker.bohrium_delivery_ack, "confirm", fake_confirm)

    fake_user_service = MagicMock()
    fake_user_service.get_user_info_for_display.return_value = {
        "user_id": "u1",
        "nickname": "n",
        "email": "e",
    }
    monkeypatch.setattr(agent_worker, "UserService", fake_user_service)
    monkeypatch.setattr(agent_worker, "get_worker_registry_service", MagicMock())
    monkeypatch.setattr(agent_worker, "notify_post_async", lambda *a, **k: None)
    monkeypatch.setattr(agent_worker, "_drain_requested", True)

    agent_worker._run_worker_loop()
    return calls, received


def test_success_path_orders_snapshot_run_confirm_release(monkeypatch):
    snap = object()
    calls, received = _run_one_round(monkeypatch, snapshot_obj=snap, run_result=True)

    assert calls == ["acquire", "snapshot", "run_agent", "confirm", "release:True"]
    assert received["delivery_snapshot"] is snap


def test_failed_run_skips_confirm(monkeypatch):
    calls, _ = _run_one_round(monkeypatch, snapshot_obj=object(), run_result=False)
    assert calls == ["acquire", "snapshot", "run_agent", "release:False"]


def test_run_agent_exception_skips_confirm_and_releases_failed(monkeypatch):
    calls, _ = _run_one_round(
        monkeypatch,
        snapshot_obj=object(),
        run_result=True,
        run_agent_exc=RuntimeError("llm down"),
    )
    assert calls == ["acquire", "snapshot", "run_agent", "release:False"]


def test_confirm_failure_still_releases(monkeypatch):
    calls, _ = _run_one_round(
        monkeypatch,
        snapshot_obj=object(),
        run_result=True,
        confirm_exc=RuntimeError("db down"),
    )
    assert calls == ["acquire", "snapshot", "run_agent", "confirm", "release:True"]


def test_none_snapshot_runs_without_confirm(monkeypatch):
    calls, received = _run_one_round(monkeypatch, snapshot_obj=None, run_result=True)
    assert calls == ["acquire", "snapshot", "run_agent", "release:True"]
    assert received["delivery_snapshot"] is None
```

- [ ] **Step 6.2: 运行确认失败**

```bash
.venv/bin/python -m pytest tests/test_agent_worker_snapshot_confirm.py -v
```
预期：FAIL，`AttributeError: module 'src.worker.agent_worker' has no attribute 'bohrium_delivery_ack'`。

- [ ] **Step 6.3: 实现 run_agent 透传**

`src/services/agent_run_service.py`：

(a) 头部 import（紧邻 :44 的 `from src.services.bohrium_jobs_wiring import build_bohrium_jobs_ports`）：

```python
from src.services.bohrium_delivery_ack import DeliverySnapshot
```

(b) `run_agent` 签名（:230-246）在 `workspace` 参数后加：

```python
        workspace: str | None = None,
        delivery_snapshot: DeliverySnapshot | None = None,
    ) -> tuple[bool | tuple[bool, str], int, dict[str, Any] | None]:
```

(c) `build_bohrium_jobs_ports` 调用（:515-521）加一行：

```python
            bohrium_ledger_port, bohrium_jobs_port = build_bohrium_jobs_ports(
                session_id=session_id,
                invocation_id=invocation_id,
                user_id=_ledger_user_id,
                org_id=_ledger_org_id,
                workspace=stage_result.workspace,
                delivery_snapshot=delivery_snapshot,
            )
```

- [ ] **Step 6.4: 实现 worker 接入**

`src/worker/agent_worker.py` 四处改动。

(a) 头部 import（紧邻 `from src.services.agent_run_service import get_agent_run_service`）：

```python
from src.services import bohrium_delivery_ack
```

(b) :378 `acquired = False` 处补初始化（finally 可见）：

```python
        acquired = False
        delivery_snapshot = None
```

(c) :405 acquire 成功后（`acquired = True` 与 `_current_session_id = session_id` 之间或之后，run 起点、`run_agent` 之前）：

```python
            acquired = True
            _current_session_id = session_id
            # run 起点固化本轮交付边界；查询失败返回 None 不阻断 run
            delivery_snapshot = bohrium_delivery_ack.snapshot(session_id)
```

(d) `run_agent_kwargs`（:434-449）加一项：

```python
                    "bohrium_required": bool(bohrium_required or workspace),
                    "delivery_snapshot": delivery_snapshot,
                }
```

(e) finally 内（:512-515）改为——confirm 必须在 `release_session_run` 之前（status 仍 active 时完成 ack，关闭 idle-before-ack 竞态）：

```python
            if acquired:
                if run_success and delivery_snapshot is not None:
                    try:
                        bohrium_delivery_ack.confirm(delivery_snapshot)
                    except Exception:
                        logger.warning(
                            'Agent worker: bohrium delivery confirm failed '
                            'session_id=%s task_id=%s',
                            session_id,
                            task_id,
                            exc_info=True,
                        )
                sessions_service.release_session_run(
                    session_id, run_success=run_success
                )
```

（范围说明：:512-515 恰为 `if acquired:` 与 release 调用四行，本替换只覆盖这四行；紧随其后的 :516-575 完成通知 `try:` 块仍属同一 `if acquired:`，保持原位与缩进不动。）

- [ ] **Step 6.5: 运行确认通过（含既有 worker/触发测试无回归）**

```bash
.venv/bin/python -m pytest tests/test_agent_worker_snapshot_confirm.py tests/test_agent_worker_delivery.py tests/test_agent_run_trigger.py tests/matmaster/worker/ -v
```
预期：全 PASS。

- [ ] **Step 6.6: Commit**

```bash
git add src/services/agent_run_service.py src/worker/agent_worker.py tests/test_agent_worker_snapshot_confirm.py
git commit -m "feat(worker): delivery snapshot at run start, confirm before release

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: Scheduler 纯函数（Reason / SchedulerConfig / decide / render_prompt）

**Files:**
- Create: `src/services/bohrium_completion_scheduler.py`（本 task 只含纯函数部分）
- Test: `tests/services/test_bohrium_completion_scheduler.py`（新增，本 task 先覆盖纯函数）

- [ ] **Step 7.1: 写失败测试**

新建 `tests/services/test_bohrium_completion_scheduler.py`：

```python
"""完成调度器：decide 无状态判定 + prompt 渲染 + tick 编排（假对象注入，不连库）。"""

from __future__ import annotations

from src.services.bohrium_completion_scheduler import (
    Reason,
    SchedulerConfig,
    decide,
    render_prompt,
)


def _unit(**over):
    base = dict(
        user_id="u1",
        org_id="o1",
        session_id="s1",
        invocation_key="inv-1",
        workspace="/share/p",
        total=3,
        active=2,
        pending_terminal=1,
        failed_total=0,
        failed_handled=0,
        succeeded=1,
        max_pending_terminal_id=10,
    )
    base.update(over)
    return base


CFG = SchedulerConfig()  # segments=3, ttl=60, scan_limit=200


# ---------- decide ----------

def test_decide_none_when_no_pending():
    assert decide(_unit(pending_terminal=0), CFG) is None


def test_decide_final_when_all_terminal():
    assert decide(_unit(active=0, pending_terminal=1), CFG) is Reason.FINAL


def test_decide_final_preempts_first_failure_for_single_job_invocation():
    # 单 job invocation 直接失败：active==0 先命中，只发 final
    unit = _unit(
        total=1, active=0, pending_terminal=1, failed_total=1, failed_handled=0
    )
    assert decide(unit, CFG) is Reason.FINAL


def test_decide_first_failure_fast_lane():
    unit = _unit(total=3, active=2, pending_terminal=1, failed_total=1)
    assert decide(unit, CFG) is Reason.FIRST_FAILURE


def test_decide_first_failure_is_one_shot():
    # 已交付过失败（failed_handled>0）→ 不再走快车道；pending<step → None
    unit = _unit(
        total=9, active=6, pending_terminal=1, failed_total=2, failed_handled=1
    )
    assert decide(unit, CFG) is None


def test_decide_progress_threshold_is_ceil():
    # total=5, segments=3 → step=ceil(5/3)=2
    assert decide(_unit(total=5, active=3, pending_terminal=1), CFG) is None
    assert (
        decide(_unit(total=5, active=3, pending_terminal=2), CFG) is Reason.PROGRESS
    )


def test_progress_count_bounded_by_segments_with_ceil_total_5():
    """total=5 的完整生命周期：恰 2 次 progress（不退化成 4 次）+ 1 次 final。"""
    total, pending, progress_hits = 5, 0, 0
    for done in range(1, total + 1):
        pending += 1
        unit = _unit(total=total, active=total - done, pending_terminal=pending)
        reason = decide(unit, CFG)
        if reason is Reason.PROGRESS:
            progress_hits += 1
            pending = 0  # 成功 run 的 ack 翻篇
        elif reason is Reason.FINAL:
            pending = 0
    assert progress_hits == 2


def test_progress_count_bounded_by_segments_total_1000():
    """连续 ack 模拟：成功 progress 次数 ≤ segments，与 total 取大值无关。"""
    total, pending, progress_hits = 1000, 0, 0
    for done in range(1, total + 1):
        pending += 1
        unit = _unit(total=total, active=total - done, pending_terminal=pending)
        reason = decide(unit, CFG)
        if reason in (Reason.PROGRESS, Reason.FINAL):
            pending = 0
            if reason is Reason.PROGRESS:
                progress_hits += 1
    assert progress_hits <= CFG.progress_segments


# ---------- render_prompt ----------

_SUFFIX = "本轮交付为 session 级"


def test_render_final_prompt_has_counts_and_scope_suffix():
    prompt = render_prompt(
        Reason.FINAL,
        {"total": 10, "active": 0, "succeeded": 8, "failed_total": 2},
    )
    assert "成功 8/10" in prompt and "失败 2" in prompt
    assert _SUFFIX in prompt


def test_render_first_failure_prompt_carries_job_info_and_suffix():
    prompt = render_prompt(
        Reason.FIRST_FAILURE,
        {"total": 10, "active": 7, "succeeded": 2, "failed_total": 1},
        first_failed={"job_id": "j-9", "job_name": "dft-9", "status": "failed"},
    )
    assert "j-9" in prompt and "dft-9" in prompt and "7 个作业仍在运行" in prompt
    assert _SUFFIX in prompt


def test_render_first_failure_prompt_degrades_when_row_vanished():
    # §4d 查不到（竞态被并发 run ack）：降级文案，照常触发
    prompt = render_prompt(
        Reason.FIRST_FAILURE,
        {"total": 3, "active": 2, "succeeded": 0, "failed_total": 1},
        first_failed=None,
    )
    assert "unknown" in prompt and _SUFFIX in prompt


def test_render_progress_prompt_has_terminal_ratio_and_suffix():
    prompt = render_prompt(
        Reason.PROGRESS,
        {"total": 9, "active": 3, "succeeded": 6, "failed_total": 0},
    )
    assert "已终态 6/9" in prompt and "仍在运行 3" in prompt
    assert _SUFFIX in prompt
```

- [ ] **Step 7.2: 运行确认失败**

```bash
.venv/bin/python -m pytest tests/services/test_bohrium_completion_scheduler.py -v
```
预期：整文件收集 ERROR（`ModuleNotFoundError: No module named 'src.services.bohrium_completion_scheduler'`，模块级 import 失败，单测不运行）。

- [ ] **Step 7.3: 实现**

新建 `src/services/bohrium_completion_scheduler.py`：

```python
"""Bohrium 作业完成调度器：无状态闭环的 monitor 侧（attempt）。

只回答一个问题：当前这些已终态、尚未交付给 agent 的作业，是否值得唤醒一次
agent run？不 poll 平台、不分析结果、不持有任何跨 tick 状态——唤醒决策仅从
bohrium_jobs 当前聚合快照推导（无 now、无持久调度态、enqueued 后不记录任何
状态：progress 是否"已发"由 worker ack 隐式表达）。

非 final 自动唤醒上界（per-invocation）= 1(first_failure) + N(progress_segments)，
与作业数无关：progress 阈值 step=ceil(total/N) 随 total 缩放，每次成功 progress
经 worker ack 至少消化 step 个 pending。
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass
from typing import Any

from src.services.bohrium_poller import _env_int

logger = logging.getLogger(__name__)

_RESERVATION_KEY_PREFIX = "bohrium_delivery:"

_DELIVERY_SCOPE_SUFFIX = (
    "本轮交付为 session 级：context 中全部 pending_terminal 详情行与"
    "溢出 job_ids 均在本次确认范围内，请一并查看处理。"
)


class Reason(enum.IntEnum):
    """唤醒原因；数值即优先级，session 合并时取最高。"""

    PROGRESS = 1
    FIRST_FAILURE = 2
    FINAL = 3


@dataclass(frozen=True)
class SchedulerConfig:
    progress_segments: int = 3
    reservation_ttl: int = 60
    scan_limit: int = 200

    @classmethod
    def from_env(cls) -> "SchedulerConfig":
        return cls(
            progress_segments=_env_int("BOHRIUM_DELIVERY_PROGRESS_SEGMENTS", 3),
            reservation_ttl=_env_int("BOHRIUM_DELIVERY_RESERVATION_TTL", 60),
            scan_limit=_env_int("BOHRIUM_DELIVERY_SCAN_LIMIT", 200),
        )


def decide(unit: dict[str, Any], cfg: SchedulerConfig) -> Reason | None:
    """无状态判定单个 (session, invocation) 聚合单元，三条全 ledger 推导。

    优先级 final > first_failure > progress；不重复发无需记账：final 经 ack
    pending→0、first_failure 经 ack failed_handled>0、progress 经 ack 回落到
    step 之下。
    """
    if unit["pending_terminal"] == 0:
        return None
    if unit["active"] == 0:
        return Reason.FINAL
    if unit["failed_total"] > 0 and unit["failed_handled"] == 0:
        return Reason.FIRST_FAILURE
    step = (unit["total"] + cfg.progress_segments - 1) // cfg.progress_segments
    if unit["pending_terminal"] >= step:
        return Reason.PROGRESS
    return None


def render_prompt(
    reason: Reason,
    counts: dict[str, int],
    first_failed: dict[str, Any] | None = None,
) -> str:
    """渲染唤醒 prompt；counts 为 session 级合计（tick 时刻聚合，run 实际执行时
    可能已漂移——context 行才是权威，文案不做绝对化承诺）。"""
    if reason is Reason.FINAL:
        body = (
            f"触发批次的全部 Bohrium 作业已结束："
            f"成功 {counts['succeeded']}/{counts['total']}，"
            f"失败 {counts['failed_total']}。请汇总结果并给出下一步。"
        )
    elif reason is Reason.FIRST_FAILURE:
        info = first_failed or {}
        job_id = info.get("job_id") or "unknown"
        job_name = info.get("job_name") or "-"
        status = info.get("status") or "failed"
        body = (
            f"Bohrium 作业 {job_id}（{job_name}）首次失败（{status}），"
            f"另有 {counts['active']} 个作业仍在运行。"
        )
    else:
        terminal = counts["total"] - counts["active"]
        body = (
            f"本会话又有 Bohrium 作业完成（已终态 {terminal}/{counts['total']}，"
            f"仍在运行 {counts['active']}）。请汇报进度。"
        )
    return body + _DELIVERY_SCOPE_SUFFIX
```

- [ ] **Step 7.4: 运行确认通过**

```bash
.venv/bin/python -m pytest tests/services/test_bohrium_completion_scheduler.py -v
```
预期：12 PASS。

- [ ] **Step 7.5: Commit**

```bash
git add src/services/bohrium_completion_scheduler.py tests/services/test_bohrium_completion_scheduler.py
git commit -m "feat(services): completion scheduler decide/render_prompt core

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: Scheduler tick 编排（三门 + session 合并 + summary）

**Files:**
- Modify: `src/services/bohrium_completion_scheduler.py`（追加 `BohriumCompletionScheduler`）
- Test: `tests/services/test_bohrium_completion_scheduler.py`（追加 tick 测试）

- [ ] **Step 8.1: 写失败测试**

在 `tests/services/test_bohrium_completion_scheduler.py` 末尾追加：

```python
# ---------- tick 编排（假对象注入） ----------

import logging
from types import SimpleNamespace

from src.services.bohrium_completion_scheduler import BohriumCompletionScheduler


class _FakeJobsTable:
    """只实现 scheduler 用到的两个读方法；任何写方法被调用都会 AttributeError，
    这本身就是「enqueued 后不写任何持久状态」的守护。"""

    def __init__(self, units=(), first_failed=None):
        self.units = list(units)
        self.first_failed = first_failed
        self.scan_limits: list[int] = []
        self.first_failed_calls: list[dict] = []

    def scan_delivery_units(self, *, limit):
        self.scan_limits.append(limit)
        return list(self.units)

    def get_first_pending_failed(self, **kw):
        self.first_failed_calls.append(kw)
        return self.first_failed


class _FakeSessions:
    def __init__(self, *, session=None, status="idle"):
        self.session = (
            session
            if session is not None
            else {"user_id": "u1", "org_id": "o1"}
        )
        self.status = status

    def get_session(self, sid):
        return self.session

    def get_session_status(self, sid):
        return self.status


class _FakeRedis:
    def __init__(self, result=True):
        self.result = result
        self.calls: list[dict] = []

    def try_reserve_nx(self, key, value, ttl_sec):
        self.calls.append({"key": key, "value": value, "ttl_sec": ttl_sec})
        return self.result


class _FakeStream:
    def __init__(self, status="enqueued"):
        self.status = status
        self.calls: list[dict] = []

    def trigger_run(self, session_id, prompt, **kw):
        self.calls.append({"session_id": session_id, "prompt": prompt, **kw})
        return SimpleNamespace(status=self.status)


def _scheduler(units, *, table=None, sessions=None, redis=None, stream=None, cfg=None):
    table = table if table is not None else _FakeJobsTable(units)
    sessions = sessions if sessions is not None else _FakeSessions()
    redis = redis if redis is not None else _FakeRedis()
    stream = stream if stream is not None else _FakeStream()
    sched = BohriumCompletionScheduler(
        jobs_table=table,
        sessions_service=sessions,
        stream_service=stream,
        redis=redis,
        cfg=cfg or SchedulerConfig(),
    )
    return sched, table, sessions, redis, stream


def test_tick_triggers_final_with_notify_and_no_persistent_state():
    units = [_unit(active=0, pending_terminal=3, succeeded=3, total=3)]
    sched, table, _, redis, stream = _scheduler(units)

    summary = sched.tick()

    assert summary["scanned"] == 1 and summary["eligible"] == 1
    assert summary["triggered"] == 1 and summary["errors"] == 0
    call = stream.calls[0]
    assert call["session_id"] == "s1"
    assert call["origin"] == "bohrium_completion"
    assert call["workspace"] == "/share/p"
    assert call["delivery"] == {"notify": True}
    assert "dedup_key" not in call  # 占位已由 NX 接管
    assert redis.calls[0]["ttl_sec"] == 60


def test_tick_merges_session_units_single_trigger_with_primary_reason():
    units = [
        _unit(invocation_key="inv-a", active=0, pending_terminal=2,
              succeeded=2, total=2, max_pending_terminal_id=7),       # FINAL
        _unit(invocation_key="inv-b", active=1, pending_terminal=1,
              total=3, max_pending_terminal_id=12),                   # PROGRESS(step=1)
    ]
    sched, _, _, redis, stream = _scheduler(units)

    summary = sched.tick()

    assert summary["triggered"] == 1 and len(stream.calls) == 1
    assert len(redis.calls) == 1  # 同 session 两单元只占一次位
    # NX key 用 session 内 max_pending_terminal_id 高水位
    assert redis.calls[0]["key"] == "bohrium_delivery:u1:o1:s1:12"
    # primary = FINAL：文案 + notify
    assert "全部 Bohrium 作业已结束" in stream.calls[0]["prompt"]
    assert stream.calls[0]["delivery"] == {"notify": True}


def test_tick_first_failure_fetches_job_info_into_prompt():
    units = [
        _unit(total=3, active=2, pending_terminal=1, failed_total=1, succeeded=0)
    ]
    table = _FakeJobsTable(
        units, first_failed={"job_id": "j-9", "job_name": "dft", "status": "failed"}
    )
    sched, _, _, _, stream = _scheduler(units, table=table)

    sched.tick()

    assert table.first_failed_calls == [
        {
            "user_id": "u1",
            "org_id": "o1",
            "session_id": "s1",
            "invocation_key": "inv-1",
        }
    ]
    assert "j-9" in stream.calls[0]["prompt"]
    assert stream.calls[0]["delivery"] == {"notify": False}


def test_tick_null_invocation_sentinel_unit_flows_through():
    units = [_unit(invocation_key="", total=1, active=1, pending_terminal=1,
                   failed_total=1, succeeded=0)]
    table = _FakeJobsTable(units, first_failed=None)
    sched, _, _, _, stream = _scheduler(units, table=table)

    sched.tick()

    assert table.first_failed_calls[0]["invocation_key"] == ""
    assert len(stream.calls) == 1


def test_tick_identity_gate_skips_owner_changed_session():
    units = [_unit(active=0, pending_terminal=1)]
    sessions = _FakeSessions(session={"user_id": "u1", "org_id": "o-CHANGED"})
    sched, _, _, redis, stream = _scheduler(units, sessions=sessions)

    summary = sched.tick()

    assert summary["skipped_identity"] == 1 and summary["triggered"] == 0
    assert stream.calls == [] and redis.calls == []


def test_tick_status_gate_skips_busy_states():
    for status in ("active", "waiting"):
        units = [_unit(active=0, pending_terminal=1)]
        sched, _, _, _, stream = _scheduler(
            units, sessions=_FakeSessions(status=status)
        )
        summary = sched.tick()
        assert summary["skipped_busy"] == 1, status
        assert stream.calls == [], status


def test_tick_status_gate_failed_counts_and_warns_with_session_list(caplog):
    units = [_unit(active=0, pending_terminal=1)]
    sched, _, _, _, stream = _scheduler(units, sessions=_FakeSessions(status="failed"))

    with caplog.at_level(
        logging.WARNING, logger="src.services.bohrium_completion_scheduler"
    ):
        summary = sched.tick()

    assert summary["skipped_failed"] == 1 and stream.calls == []
    # 停摆唯一的发现通道：WARN + session 清单
    warn = [r for r in caplog.records if "stalled" in r.getMessage()]
    assert warn and "s1" in warn[0].getMessage()


def test_tick_nx_false_skips_as_busy():
    units = [_unit(active=0, pending_terminal=1)]
    sched, _, _, _, stream = _scheduler(units, redis=_FakeRedis(result=False))

    summary = sched.tick()

    assert summary["skipped_busy"] == 1 and stream.calls == []


def test_tick_nx_none_fail_closed_counts_redis_and_warns_once(caplog):
    # Redis 故障：禁止放行（放行只会产生孤儿 trigger 事件与排队通知）
    units = [
        _unit(session_id="s1", active=0, pending_terminal=1),
        _unit(session_id="s2", active=0, pending_terminal=1,
              max_pending_terminal_id=20),
    ]
    sched, _, _, _, stream = _scheduler(units, redis=_FakeRedis(result=None))

    with caplog.at_level(
        logging.WARNING, logger="src.services.bohrium_completion_scheduler"
    ):
        summary = sched.tick()

    assert summary["skipped_redis"] == 2 and stream.calls == []
    # tick 级聚合一条 WARN，不逐 session 刷日志
    redis_warns = [r for r in caplog.records if "fail-closed" in r.getMessage()]
    assert len(redis_warns) == 1


def test_tick_trigger_busy_and_error_do_not_touch_ledger():
    units = [_unit(active=0, pending_terminal=1)]
    sched, _, _, _, _ = _scheduler(units, stream=_FakeStream(status="busy"))
    summary = sched.tick()
    assert summary["skipped_busy"] == 1 and summary["triggered"] == 0

    sched2, _, _, _, _ = _scheduler(units, stream=_FakeStream(status="error"))
    summary2 = sched2.tick()
    assert summary2["errors"] == 1 and summary2["triggered"] == 0
    # _FakeJobsTable 无任何写方法：触达 ledger 会直接 AttributeError 炸测试


def test_tick_swallows_scan_failure_and_returns_tick_failed():
    class _BoomTable:
        def scan_delivery_units(self, *, limit):
            raise RuntimeError("db down")

    sched = BohriumCompletionScheduler(
        jobs_table=_BoomTable(),
        sessions_service=_FakeSessions(),
        stream_service=_FakeStream(),
        redis=_FakeRedis(),
        cfg=SchedulerConfig(),
    )
    summary = sched.tick()
    assert summary["tick_failed"] == 1
```

- [ ] **Step 8.2: 运行确认失败**

```bash
.venv/bin/python -m pytest tests/services/test_bohrium_completion_scheduler.py -v
```
预期：新增块的模块级 import 失败 → 整文件收集 ERROR（`ImportError: cannot import name 'BohriumCompletionScheduler'`），Task 7 的 12 个测试本轮也不运行——这即「实现前失败」的证明。

- [ ] **Step 8.3: 实现**

`src/services/bohrium_completion_scheduler.py` 末尾追加：

```python
class BohriumCompletionScheduler:
    """单轮调度单元；依赖惰性构造（monitor 进程循环外建一次），tick() 自吞所有
    异常、绝不抛。多实例安全依赖 identity + status + NX 三门（仍建议 replica=1）。"""

    def __init__(
        self,
        *,
        jobs_table: Any | None = None,
        sessions_service: Any | None = None,
        stream_service: Any | None = None,
        redis: Any | None = None,
        cfg: SchedulerConfig | None = None,
    ) -> None:
        self._jobs_table = jobs_table
        self._sessions_service = sessions_service
        self._stream_service = stream_service
        self._redis = redis
        self._cfg = cfg if cfg is not None else SchedulerConfig.from_env()

    def _ensure_deps(self) -> None:
        if self._jobs_table is None:
            from src.dao.bohrium_jobs_table import BohriumJobsTable

            self._jobs_table = BohriumJobsTable()
        if self._sessions_service is None:
            from src.services.sessions_service import get_sessions_service

            self._sessions_service = get_sessions_service()
        if self._stream_service is None:
            from src.services.stream_service import get_stream_service

            self._stream_service = get_stream_service()
        if self._redis is None:
            from src.dao.redis_dao import get_redis_dao

            self._redis = get_redis_dao()

    def tick(self) -> dict[str, int]:
        summary = {
            "scanned": 0,
            "eligible": 0,
            "triggered": 0,
            "skipped_identity": 0,
            "skipped_busy": 0,
            "skipped_failed": 0,
            "skipped_redis": 0,
            "errors": 0,
        }
        try:
            self._ensure_deps()
            units = self._jobs_table.scan_delivery_units(limit=self._cfg.scan_limit)
        except Exception:  # noqa: BLE001
            logger.warning("bohrium completion scheduler tick failed", exc_info=True)
            summary["tick_failed"] = 1
            return summary
        summary["scanned"] = len(units)

        groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        for unit in units:
            key = (unit["user_id"], unit["org_id"], unit["session_id"])
            groups.setdefault(key, []).append(unit)

        failed_sessions: list[str] = []
        for (user_id, org_id, session_id), session_units in groups.items():
            try:
                self._process_session(
                    user_id, org_id, session_id, session_units, summary,
                    failed_sessions,
                )
            except Exception:  # noqa: BLE001
                summary["errors"] += 1
                logger.warning(
                    "bohrium completion scheduler session failed session_id=%s",
                    session_id,
                    exc_info=True,
                )
        if summary["skipped_failed"]:
            # 停摆唯一的发现通道（run 级失败不自动重试，下一次用户交互自愈）
            logger.warning(
                "bohrium delivery stalled on failed sessions "
                "(no auto-retry; next user interaction self-heals): %s",
                failed_sessions,
            )
        if summary["skipped_redis"]:
            logger.warning(
                "bohrium delivery reservation unavailable, skipped %d session(s) "
                "this tick (fail-closed; resumes when redis recovers)",
                summary["skipped_redis"],
            )
        return summary

    def _process_session(
        self,
        user_id: str,
        org_id: str,
        session_id: str,
        session_units: list[dict[str, Any]],
        summary: dict[str, int],
        failed_sessions: list[str],
    ) -> None:
        eligible: list[tuple[Reason, dict[str, Any]]] = []
        for unit in session_units:
            reason = decide(unit, self._cfg)
            if reason is not None:
                eligible.append((reason, unit))
        if not eligible:
            return
        summary["eligible"] += 1

        # (a) identity 门：扫描已在 SQL 层过滤 owner，此门只兜扫描到触发
        # 之间 owner 又变更的竞态窗口
        session = self._sessions_service.get_session(session_id)
        if (
            not session
            or str(session.get("user_id") or "") != user_id
            or str(session.get("org_id") or "") != org_id
        ):
            summary["skipped_identity"] += 1
            return

        # (b) status 门：仅 idle 放行（跨进程互斥的主门，DB 状态跨进程可见）
        status = self._sessions_service.get_session_status(session_id)
        if status == "failed":
            summary["skipped_failed"] += 1
            failed_sessions.append(session_id)
            return
        if status != "idle":
            summary["skipped_busy"] += 1
            return

        # (c) NX 原子占位（fail-closed）：同 tick 多实例竞态的防御纵深。
        # row-id 高水位避免秒级 terminal_at 碰撞压住新完成作业；短 TTL 无需释放。
        max_row_id = max(u["max_pending_terminal_id"] for u in session_units)
        key = f"{_RESERVATION_KEY_PREFIX}{user_id}:{org_id}:{session_id}:{max_row_id}"
        reserved = self._redis.try_reserve_nx(
            key, "1", ttl_sec=self._cfg.reservation_ttl
        )
        if reserved is None:
            # Redis 不可用：NX 与 run 队列共用同一 Redis，放行产不出可用 run，
            # 只会残留孤儿 trigger 事件——skip 是背压而非关停
            summary["skipped_redis"] += 1
            return
        if reserved is False:
            summary["skipped_busy"] += 1
            return

        primary_reason, primary_unit = max(eligible, key=lambda e: e[0])
        counts = {
            "total": sum(u["total"] for u in session_units),
            "active": sum(u["active"] for u in session_units),
            "succeeded": sum(u["succeeded"] for u in session_units),
            "failed_total": sum(u["failed_total"] for u in session_units),
        }
        first_failed = None
        if primary_reason is Reason.FIRST_FAILURE:
            first_failed = self._jobs_table.get_first_pending_failed(
                user_id=user_id,
                org_id=org_id,
                session_id=session_id,
                invocation_key=primary_unit["invocation_key"],
            )
        prompt = render_prompt(primary_reason, counts, first_failed)
        # 不传 dedup_key：占位已由 NX 接管。多 invocation 不同 workspace 合并时
        # 只取 primary 的（已知限制，作业级信息仍在 context 行内可见）。
        res = self._stream_service.trigger_run(
            session_id,
            prompt,
            origin="bohrium_completion",
            workspace=primary_unit["workspace"],
            delivery={"notify": primary_reason is Reason.FINAL},
        )
        if res.status == "enqueued":
            # 不记录任何状态：progress 是否「已发」由 worker ack 隐式表达
            summary["triggered"] += 1
        elif res.status == "busy":
            summary["skipped_busy"] += 1
        else:
            summary["errors"] += 1
```

- [ ] **Step 8.4: 运行确认通过**

```bash
.venv/bin/python -m pytest tests/services/test_bohrium_completion_scheduler.py -v
```
预期：23 PASS（Task 7 纯函数 12 个 + 本 task tick 编排 11 个）。

- [ ] **Step 8.5: Commit**

```bash
git add src/services/bohrium_completion_scheduler.py tests/services/test_bohrium_completion_scheduler.py
git commit -m "feat(services): completion scheduler tick with three gates and session merge

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 9: Monitor 进程接入 scheduler.tick()

**Files:**
- Modify: `src/monitor/monitor_worker.py:32-50`
- Test: `tests/monitor/test_monitor_worker.py`（修改现有测试 + 扩展）

- [ ] **Step 9.1: 改写测试（现有测试必须同步 stub scheduler，否则会构造真 scheduler 并尝试连库）**

`tests/monitor/test_monitor_worker.py` 整体替换为：

```python
"""matmaster-monitor 进程循环接入测试。

只验证「外壳 ↔ 巡检单元」的接缝：``_run_monitor_loop`` 每轮先后调用
``BohriumMonitor.tick()`` 与 ``BohriumCompletionScheduler.tick()``、各记一条
summary、收到 ``_stop_event`` 后干净退出。两个 tick 单元自身的行为（透传
summary / 吞异常）分别由 ``tests/services/test_bohrium_poller.py`` 与
``tests/services/test_bohrium_completion_scheduler.py`` 覆盖，这里不重复。
"""

from __future__ import annotations

import logging

from src.monitor import monitor_worker


def test_run_monitor_loop_ticks_poller_and_scheduler_each_round(monkeypatch, caplog):
    """循环每轮先 poller.tick 后 scheduler.tick，各记 summary，stop 后退出。"""
    ticks: list[str] = []

    class _StubMonitor:
        def tick(self) -> dict[str, int]:
            ticks.append("poll")
            # 一轮后请求退出，循环不再空转（真实进程靠 SIGTERM 置位）
            monitor_worker._stop_event.set()
            return {"claimed": 3, "polled": 2, "errors": 1}

    class _StubScheduler:
        def tick(self) -> dict[str, int]:
            ticks.append("delivery")
            return {"scanned": 5, "triggered": 1}

    monitor_worker._stop_event.clear()
    monkeypatch.setattr(monitor_worker, "BohriumMonitor", lambda: _StubMonitor())
    monkeypatch.setattr(
        monitor_worker, "BohriumCompletionScheduler", lambda: _StubScheduler()
    )
    monkeypatch.setattr(monitor_worker, "_TICK_INTERVAL", 0.0)

    try:
        with caplog.at_level(logging.INFO, logger="src.monitor.monitor_worker"):
            monitor_worker._run_monitor_loop()
    finally:
        monitor_worker._stop_event.clear()  # 复位模块级单例，避免污染后续测试

    # poller 设了 stop 之后本轮 scheduler 仍执行（先完成本轮再退出）
    assert ticks == ["poll", "delivery"]
    assert any(
        "bohrium" in r.getMessage() and "'claimed': 3" in r.getMessage()
        for r in caplog.records
    ), "应记录本轮 BohriumMonitor.tick() 返回的 summary"
    assert any(
        "delivery" in r.getMessage() and "'triggered': 1" in r.getMessage()
        for r in caplog.records
    ), "应记录本轮 BohriumCompletionScheduler.tick() 返回的 summary"
```

- [ ] **Step 9.2: 运行确认失败**

```bash
.venv/bin/python -m pytest tests/monitor/test_monitor_worker.py -v
```
预期：FAIL，`AttributeError: <module 'src.monitor.monitor_worker'> has no attribute 'BohriumCompletionScheduler'`。

- [ ] **Step 9.3: 实现**

`src/monitor/monitor_worker.py` 两处改动。

(a) import（:16 旁）：

```python
from src.services.bohrium_completion_scheduler import BohriumCompletionScheduler
from src.services.bohrium_poller import BohriumMonitor
```

(b) `_run_monitor_loop` 循环段（:43-48）改为（循环外各构造一次；两个 tick 都自吞异常，进程绝不因单轮失败退出）：

```python
    runner = BohriumMonitor()  # 循环外构造一次（惰性、无 DB、tick 不抛异常）
    scheduler = BohriumCompletionScheduler()  # 同上；判定纯 ledger 聚合，零持久态
    while not _stop_event.is_set():
        summary = runner.tick()  # 单轮 claim + poll + 写回 ledger
        logger.info('matmaster-monitor: bohrium %s worker_id=%s', summary, worker_id)
        delivery_summary = scheduler.tick()  # 聚合扫描 → 三门 → trigger_run
        logger.info(
            'matmaster-monitor: delivery %s worker_id=%s',
            delivery_summary,
            worker_id,
        )
        # wait 在收到 set() 时立即返回 True，可第一时间响应 SIGTERM
        _stop_event.wait(timeout=_TICK_INTERVAL)
```

同时更新模块 docstring 第一条（:5-6）为：

```
1. 每轮先调 ``BohriumMonitor.tick()`` 推进活跃 Bohrium 作业到终态（claim 到期作业、
   查平台、写回 ledger），再调 ``BohriumCompletionScheduler.tick()`` 对已终态未交付
   的批次按策略唤醒 agent run；两个 summary 日志同时充当进程存活证明；
```

- [ ] **Step 9.4: 运行确认通过**

```bash
.venv/bin/python -m pytest tests/monitor/test_monitor_worker.py -v
```
预期：PASS。

- [ ] **Step 9.5: Commit**

```bash
git add src/monitor/monitor_worker.py tests/monitor/test_monitor_worker.py
git commit -m "feat(monitor): wire completion scheduler tick into monitor loop

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 10: 全量验证与 spec 硬约束自检

**Files:** 无新改动（只验证；发现问题回到对应 task 修复后重新走本 task）

- [ ] **Step 10.1: 全量测试**

```bash
.venv/bin/python -m pytest tests/ -x -q
```
预期：全 PASS（dao 真库组无 `.env.test` 时 SKIP）。

- [ ] **Step 10.2: pre-commit**

```bash
pre-commit run --files \
  src/dao/bohrium_jobs_table.py src/dao/redis_dao.py \
  src/services/bohrium_delivery_ack.py src/services/bohrium_completion_scheduler.py \
  src/services/bohrium_jobs_wiring.py src/services/agent_run_service.py \
  src/worker/agent_worker.py src/monitor/monitor_worker.py \
  matmaster/context/ports.py matmaster/context/sources/session_jobs.py \
  tests/dao/test_bohrium_jobs_delivery.py tests/dao/test_redis_dao_reserve_nx.py \
  tests/services/test_bohrium_delivery_ack.py \
  tests/services/test_bohrium_completion_scheduler.py \
  tests/services/test_bohrium_jobs_wiring.py \
  tests/test_agent_worker_snapshot_confirm.py \
  tests/matmaster/context/sources/test_session_jobs.py \
  tests/monitor/test_monitor_worker.py
```
预期：全部 Passed（black/isort 如重排格式，重跑测试后 amend 对应提交或追加 `style:` commit）。

- [ ] **Step 10.3: "不碰清单"核验（spec §9）**

**基线不是 main**：本分支（codex/provider-stage1）领先 main 大量无关提交，bohrium_poller / stream_service / src/sql 等清单文件在 `main...HEAD` 上本就有大段历史差异，用 main 当基线必假性失败。以本计划首个 commit（Task 1，按 message 定位）的父提交为基线，在同一个 bash 会话内连续执行：

```bash
BASE=$(git log --reverse -F \
  --grep='feat(dao): add bohrium delivery scan/snapshot/ack/first-failed queries' \
  --format=%H | head -1)
git log --oneline "${BASE}^..HEAD" -- matmaster/agent.py matmaster/exp.py \
  matmaster/context/assembly.py matmaster/context/compositions.py \
  src/sql/ src/services/bohrium_poller.py src/services/stream_service.py
git diff "${BASE}^..HEAD" -- src/dao/redis_dao.py | grep "^-" | grep -v "^---"
```
预期：后两条命令均无输出（其一：本计划全部提交未触碰清单文件；其二：`redis_dao.py` 无删除行、只新增方法）。grep 无匹配时退出码为 1，属预期、不是失败。

- [ ] **Step 10.4: spec §12 硬约束逐条自检**

对照确认（每条在代码里指认落点）：

1. 调度器无状态 → `decide` 仅读 unit dict；`BohriumCompletionScheduler` 无任何写 ledger / 状态表的调用（fake table 无写方法的测试守护）。
2. 失败非无界旁路 → `decide` 中 `failed_handled == 0` 一次性快车道。
3. 非 final 唤醒上界 → `test_progress_count_bounded_by_segments_total_1000`。
4. snapshot 是确认边界 + 全量 id 可见 → `list_pending_terminal_snapshot` 无 limit；renderer 溢出摘要含全量剩余 id（`test_detail_limit_covers_all_ids_between_detail_and_overflow`）。
5. mark_handled 只在 run 成功消费后 → 全仓 `mark_handled_by_ids` 调用点仅 `bohrium_delivery_ack.confirm`；confirm 调用点仅 worker finally 成功分支（release 之前）。验证：`grep -rn "mark_handled_by_ids\|bohrium_delivery_ack.confirm" src/`。
6. 所有成功 run 不分 origin confirm → worker 主循环统一路径（`test_success_path_orders_snapshot_run_confirm_release` 走的就是普通 payload，无 origin 区分）。
7. handled_at 语义 → 仅 confirm 按 row_ids 置位。
8. 三门必过 → `_process_session` 顺序 identity → status → NX；fail-closed 测试 `test_tick_nx_none_fail_closed_*`。
9. at-least-once → snapshot 查询边界 + confirm 幂等（dao 测试）。
10. 两链路不共享运行态 → scheduler 与 ack 模块零互相 import（DTO 也不共享）。
11. renderer 不改 kernel → `from_jobs` 签名不变、`compositions.py:87` 零 diff（Step 10.3）。
12. 作业只经 run 内 ledger port 提交 → 本计划未新增任何提交路径（既有事实，无需改动）。

- [ ] **Step 10.5: 汇报**

向用户汇报：改动文件清单、测试结果（含 SKIP 的 dao 组说明）、已接受限制原样生效（run 级失败停摆 WARN 可观测、Redis 故障 fail-closed、worker crash 残留 active 无自动复位）。

---

## Self-Review 记录（计划作者已自查）

- **Spec 覆盖**：§4(a)(b)(c)(d)→Task 1；§6c 三态→Task 2；§8 renderer→Task 3；§7 snapshot/confirm→Task 4/6；§9 wiring/run_agent→Task 5/6；§5/§6 decide/tick→Task 7/8；monitor 接入→Task 9；§13 测试清单逐条映射到各 task 的测试代码；§10 四个配置项→`SchedulerConfig.from_env`（3 个）+ `DeliverySnapshot.detail_limit`（1 个）。§11 已接受限制均为"不实现"项，无对应 task。
- **类型一致性**：`DeliverySnapshot` 字段（Task 4 定义）与 Task 5 wiring、Task 6 worker、Task 8 无关（scheduler 不触 snapshot）一致；`SessionJobs.detail_limit`（Task 3）与 Task 5 构造一致；DAO 方法签名在 Task 1 定义后，Task 4/8 的 fake 与调用 kwargs 一致（`user_id/org_id/session_id/row_ids/invocation_key/limit`）。
- **无占位符**：全部步骤含完整代码/命令/预期输出。
- **外部 review 修订（2026-06-10）**：测试计数更正（Task 4/7/8 的测试文件实际为 7/12/11 个用例，Step 8.4 全文件 23）；Step 4.2/7.2/8.2 失败形态更正为整文件收集 ERROR（模块级 import 失败时单测不运行）；Step 10.3 基线由 main 改为计划首 commit 的父提交（分支领先 main 大量无关提交）；Task 6(e) 补充 :516-575 完成通知块保持不动的范围说明；§0.1 增补 monitor「单轮异常不退出」裁定；§0.2 三处文件行数对齐实测（336/70/46）。
