# Bohrium 轮询失联放弃机制（lost 终态）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 Bohrium 后台轮询加一个放弃出口：连续失联超过阈值的活跃作业被原子置为新终态 `lost`（计入 terminal + failure），自动停表并流入既有交付链路告知用户；同时修复错误路径退避恒为 30 秒的缺陷。

**Architecture:** 失联判据 = `NOW() - COALESCE(last_polled_at, submitted_at) > 阈值`（`last_polled_at` 只在成功 poll 时刷新，天然是"上次成功联系时间"，无需新列）。判定内嵌在 `mark_poll_error` 的单条 UPDATE 里原子完成，状态不变量继续由 DAO 唯一写入口守住。`lost` 加入 `LEDGER_TERMINAL_STATUSES` 与 `LEDGER_FAILURE_STATUSES` 后，DAO 全部 SQL 谓词（插值生成）与交付链路（scan → decide → trigger_run → snapshot → ack）自动跟随，零改动复用。

**Tech Stack:** Python 3 + PyMySQL（raw SQL DAO）、MySQL 8.0.16+（CHECK 约束）、pytest（tests/dao 为真库测试，无 `.env.test` 则整组 SKIP）。

---

## 0. 设计裁定（评审已认可，执行者勿改）

1. **判据选连续失联时长**，不是 max_attempts（瞬时/永久错误混在一个计数器里没法定 N）也不是提交后总时长（会误杀合法的超长科研作业）。合法长作业每次 poll 成功都会刷新 `last_polled_at`，永远不触发。
2. **lost 必须同时进 TERMINAL 和 FAILURE 集合**：进 TERMINAL 才能停表并流入 `pending_terminal` 交付队列；进 FAILURE 才会触发 FIRST_FAILURE 快车道，agent 会向用户汇报"作业失联请人工确认"。这是方案的核心杠杆——交付链路零改动。
3. **`to_ledger_status` 永不产生 lost**：lost 不来自平台状态码映射，唯一写入点是 `mark_poll_error` 的失联分支。
4. **错误也推进退避**：`mark_poll_error` 对活跃行 `poll_count + 1`。修复"错误作业退避恒 30 秒"（poll_count 原来只在成功 poll 时递增）。
5. **MySQL UPDATE 的 SET 从左到右生效**（非标准 SQL 语义）：新 SQL 中 `status` 赋值必须放在最后，前面各列的 CASE 才能读到旧 status。现有 `apply_poll` 就是这么写的，照搬该模式。
6. **阈值配置**：环境变量 `BOHRIUM_POLL_LOST_AFTER_SECONDS`，默认 86400（24 小时，远大于最大退避 600 秒，不存在一次抖动就放弃）。由 poller 读取后作为必填 kwarg 传给 DAO（与 `backoff_seconds` 同风格）。**不设方法默认值**——项目禁止兼容兜底，所有调用方同步改。
7. **迁移走外部脚本**（项目规范）：存量库手动执行 `migrate_add_bohrium_jobs_lost_status.sql`，**必须先于代码部署**（运行时无兜底，CHECK 拒绝 lost 会直接报错）。新环境用更新后的 `create_bohrium_jobs_table.sql`。
8. **已知边界（接受，不实现）**：平台连续宕机超过阈值会批量置 lost（大事故本需人工善后，lost 会逐用户告知，比静默积压好）；平台新状态码映射成 unknown 时 HTTP 成功、`last_polled_at` 照常刷新，不触发 lost（新状态码本质需要升级映射代码）。

## 执行前提

- 工作区当前有另一项工作的未提交改动（mark_handled 死代码清理等）。**执行本计划前先把它们单独提交**，保证本计划每个 commit 只含本计划文件。
- **验证命令一律走 uv**（AGENTS.md 约定，不依赖系统 PATH）：pytest 在 `dev` extra → `uv run --extra dev pytest ...`；pre-commit 在主依赖 → `uv run pre-commit ...`。
- `tests/dao/`、`tests/services/test_bohrium_poller.py` 是真库测试：依赖 `.env.test` 的 MySQL 8.0.16+，库名必须是 `*_test`/`test_*`（conftest 会 DROP/CREATE `bohrium_jobs` 表）。无库环境整组 SKIP——此时 SQL 正确性靠评审兜底，不阻塞后续 task，但 Task 5 汇报时必须如实说明 SKIP。
- DAO 测试建表直接读 `src/sql/create_bohrium_jobs_table.sql`（见 `tests/dao/conftest.py` 与 `tests/services/test_bohrium_poller.py:17`），所以 Task 1 改 DDL 后真库测试自动用新约束。

## 文件清单

- Modify: `matmaster/bohrium/status.py:42-44`（两个元组 + 注释）
- Modify: `src/sql/create_bohrium_jobs_table.sql:42-58`（三个 CHECK 加 lost）
- Create: `src/sql/migrate_add_bohrium_jobs_lost_status.sql`（存量库迁移）
- Modify: `src/dao/bohrium_jobs_table.py:269-301`（mark_poll_error 签名 + SQL）
- Modify: `src/services/bohrium_poller.py`（模块 docstring、`BohriumJobPoller.__init__`、`_poll_one` 三处调用）
- Test: `tests/matmaster/bohrium/test_ledger_status.py`（+2 用例）
- Test: `tests/dao/test_bohrium_jobs_table.py`（改 2 用例 + 新 6 用例 + 1 helper）
- Test: `tests/services/test_bohrium_poller.py`（+1 helper + 2 用例）
- Test: `tests/dao/test_bohrium_jobs_delivery.py`（+1 helper + 2 守护用例）

---

### Task 1: 状态词汇表与 DDL/迁移脚本

**Files:**
- Modify: `matmaster/bohrium/status.py:42-44`
- Modify: `src/sql/create_bohrium_jobs_table.sql:42-58`
- Create: `src/sql/migrate_add_bohrium_jobs_lost_status.sql`
- Test: `tests/matmaster/bohrium/test_ledger_status.py`

- [ ] **Step 1.1: 写失败测试**

在 `tests/matmaster/bohrium/test_ledger_status.py` 末尾追加（注意该文件现有 import 只引了 `LedgerStatusDecision, to_ledger_status`，新测试自带 import）：

```python
def test_lost_is_terminal_failure_not_active() -> None:
    from matmaster.bohrium.status import (
        LEDGER_ACTIVE_STATUSES,
        LEDGER_FAILURE_STATUSES,
        LEDGER_TERMINAL_STATUSES,
    )

    assert "lost" in LEDGER_TERMINAL_STATUSES
    assert "lost" in LEDGER_FAILURE_STATUSES
    assert "lost" not in LEDGER_ACTIVE_STATUSES
    assert set(LEDGER_FAILURE_STATUSES) <= set(LEDGER_TERMINAL_STATUSES)


def test_to_ledger_status_never_emits_lost() -> None:
    from matmaster.bohrium.status import STATUS_MAP

    for code in [*STATUS_MAP, 999, -999]:
        assert to_ledger_status(code).status != "lost"
```

- [ ] **Step 1.2: 运行确认失败**

Run: `uv run --extra dev pytest tests/matmaster/bohrium/test_ledger_status.py -v`
Expected: `test_lost_is_terminal_failure_not_active` FAIL（`'lost' in LEDGER_TERMINAL_STATUSES` 断言失败）；`test_to_ledger_status_never_emits_lost` PASS（本来就不产生）。

- [ ] **Step 1.3: 实现**

(a) `matmaster/bohrium/status.py` 把 42-44 行：

```python
# ledger status 词汇表的唯一定义点；DAO 的 SQL 谓词由此插值生成。
LEDGER_ACTIVE_STATUSES = ("submitted", "running", "terminating", "unknown")
LEDGER_TERMINAL_STATUSES = ("finished", "failed", "stopped")
LEDGER_FAILURE_STATUSES = ("failed", "stopped")
```

改为：

```python
# ledger status 词汇表的唯一定义点；DAO 的 SQL 谓词由此插值生成。
# lost 不来自平台状态码映射：poller 连续失联超阈值时由 mark_poll_error 置位。
LEDGER_ACTIVE_STATUSES = ("submitted", "running", "terminating", "unknown")
LEDGER_TERMINAL_STATUSES = ("finished", "failed", "stopped", "lost")
LEDGER_FAILURE_STATUSES = ("failed", "stopped", "lost")
```

(b) `src/sql/create_bohrium_jobs_table.sql` 三个 CHECK（42-58 行）改为：

```sql
    CONSTRAINT `chk_status` CHECK (`status` IN (
        'submitted', 'running', 'terminating', 'unknown',
        'finished', 'failed', 'stopped', 'lost'
    )),
```

```sql
    CONSTRAINT `chk_active_poll` CHECK (
        (`status` IN ('submitted', 'running', 'terminating', 'unknown') AND `next_poll_at` IS NOT NULL)
        OR
        (`status` IN ('finished', 'failed', 'stopped', 'lost') AND `next_poll_at` IS NULL)
    ),
    CONSTRAINT `chk_terminal_at` CHECK (
        (`status` IN ('submitted', 'running', 'terminating', 'unknown') AND `terminal_at` IS NULL)
        OR
        (`status` IN ('finished', 'failed', 'stopped', 'lost') AND `terminal_at` IS NOT NULL)
    ),
```

（`chk_workspace_share_path`、`chk_sandbox`、`chk_handled_requires_terminal` 不动。）

(c) 新建 `src/sql/migrate_add_bohrium_jobs_lost_status.sql`（风格对齐 `migrate_add_bohrium_jobs_workspace.sql`——外部手动脚本，头注释写明操作时序）：

```sql
-- Add 'lost' terminal status to bohrium_jobs CHECK constraints.
-- This is an external/manual migration script. Run it BEFORE deploying code
-- that writes status='lost'; the runtime has no fallback when the CHECK
-- rejects the new value.
--
-- 'lost' semantics: an active job whose last successful poll (or submit, if
-- never polled) is older than BOHRIUM_POLL_LOST_AFTER_SECONDS is finalized
-- as lost (terminal + failure) by mark_poll_error and enters the delivery
-- queue like any other terminal job.

ALTER TABLE `bohrium_jobs`
    DROP CHECK `chk_status`,
    DROP CHECK `chk_active_poll`,
    DROP CHECK `chk_terminal_at`,
    ADD CONSTRAINT `chk_status` CHECK (`status` IN (
        'submitted', 'running', 'terminating', 'unknown',
        'finished', 'failed', 'stopped', 'lost'
    )),
    ADD CONSTRAINT `chk_active_poll` CHECK (
        (`status` IN ('submitted', 'running', 'terminating', 'unknown') AND `next_poll_at` IS NOT NULL)
        OR
        (`status` IN ('finished', 'failed', 'stopped', 'lost') AND `next_poll_at` IS NULL)
    ),
    ADD CONSTRAINT `chk_terminal_at` CHECK (
        (`status` IN ('submitted', 'running', 'terminating', 'unknown') AND `terminal_at` IS NULL)
        OR
        (`status` IN ('finished', 'failed', 'stopped', 'lost') AND `terminal_at` IS NOT NULL)
    );
```

- [ ] **Step 1.4: 运行确认通过（含回归）**

Run: `uv run --extra dev pytest tests/matmaster/bohrium/ tests/dao/ tests/services/test_bohrium_poller.py tests/monitor/ -v`
Expected: 全 PASS（无 `.env.test` 时 dao/poller 真库组 SKIP）。TERMINAL 集合扩大后 `apply_poll`/`mark_poll_error` 的终态保护谓词自动包含 lost，现有用例不受影响（尚无代码写入 lost）。

- [ ] **Step 1.5: Commit**

```bash
git add matmaster/bohrium/status.py src/sql/create_bohrium_jobs_table.sql \
  src/sql/migrate_add_bohrium_jobs_lost_status.sql \
  tests/matmaster/bohrium/test_ledger_status.py
git commit -m "feat(bohrium): add lost terminal status to ledger vocabulary and DDL"
```

---

### Task 2: DAO mark_poll_error 失联判定与退避修复

**Files:**
- Modify: `src/dao/bohrium_jobs_table.py:269-301`
- Test: `tests/dao/test_bohrium_jobs_table.py`

- [ ] **Step 2.1: 写失败测试**

(a) 现有两个用例补必填 kwarg（`test_mark_poll_error_marks_active_unknown` 与 `test_mark_poll_error_does_not_touch_terminal` 中的两处 `mark_poll_error(...)` 调用各加一行）：

```python
        lost_after_seconds=86400,
```

(b) 文件末尾追加 helper 与 6 个用例：

```python
def _age_job_silence(jobs_table, job_id: str, *, seconds: int) -> None:
    """把失联基准（submitted_at 与 last_polled_at，若有）拨老 seconds 秒。"""
    with jobs_table.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE bohrium_jobs SET "
                "submitted_at = NOW() - INTERVAL %s SECOND, "
                "last_polled_at = IF(last_polled_at IS NULL, NULL, "
                "NOW() - INTERVAL %s SECOND) "
                "WHERE job_id = %s",
                (int(seconds), int(seconds), job_id),
            )
        conn.commit()


def test_mark_poll_error_lost_after_continuous_silence(jobs_table) -> None:
    jobs_table.insert_submitted(**_submit_kwargs(job_id="e3"))
    _age_job_silence(jobs_table, "e3", seconds=7200)
    jobs_table.mark_poll_error(
        user_id="user-1",
        org_id="org-1",
        sandbox=True,
        job_id="e3",
        backoff_seconds=45,
        lost_after_seconds=3600,
    )
    row = jobs_table.get_by_owner_job(
        user_id="user-1", org_id="org-1", sandbox=True, job_id="e3"
    )
    assert row["status"] == "lost"
    assert row["terminal_at"] is not None
    assert row["next_poll_at"] is None
    assert row["poll_count"] == 1


def test_mark_poll_error_recent_success_blocks_lost(jobs_table) -> None:
    jobs_table.insert_submitted(**_submit_kwargs(job_id="e4"))
    jobs_table.apply_poll(
        user_id="user-1",
        org_id="org-1",
        sandbox=True,
        job_id="e4",
        status="running",
        is_terminal=False,
        backoff_seconds=30,
    )
    # submitted_at 久远但 last_polled_at 刚刷新：基准取 last_polled_at，不触发
    with jobs_table.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE bohrium_jobs SET submitted_at = NOW() - INTERVAL 7200 SECOND "
                "WHERE job_id = 'e4'"
            )
        conn.commit()
    jobs_table.mark_poll_error(
        user_id="user-1",
        org_id="org-1",
        sandbox=True,
        job_id="e4",
        backoff_seconds=45,
        lost_after_seconds=3600,
    )
    row = jobs_table.get_by_owner_job(
        user_id="user-1", org_id="org-1", sandbox=True, job_id="e4"
    )
    assert row["status"] == "unknown"
    assert row["next_poll_at"] is not None
    assert row["terminal_at"] is None
    assert row["poll_count"] == 2  # apply_poll 1 次 + 本次错误 1 次


def test_mark_poll_error_lost_when_last_success_is_old(jobs_table) -> None:
    jobs_table.insert_submitted(**_submit_kwargs(job_id="e5"))
    jobs_table.apply_poll(
        user_id="user-1",
        org_id="org-1",
        sandbox=True,
        job_id="e5",
        status="running",
        is_terminal=False,
        backoff_seconds=30,
    )
    _age_job_silence(jobs_table, "e5", seconds=7200)
    jobs_table.mark_poll_error(
        user_id="user-1",
        org_id="org-1",
        sandbox=True,
        job_id="e5",
        backoff_seconds=45,
        lost_after_seconds=3600,
    )
    row = jobs_table.get_by_owner_job(
        user_id="user-1", org_id="org-1", sandbox=True, job_id="e5"
    )
    assert row["status"] == "lost"
    assert row["next_poll_at"] is None


def test_mark_poll_error_increments_poll_count(jobs_table) -> None:
    jobs_table.insert_submitted(**_submit_kwargs(job_id="e6"))
    for _ in range(2):
        jobs_table.mark_poll_error(
            user_id="user-1",
            org_id="org-1",
            sandbox=True,
            job_id="e6",
            backoff_seconds=45,
            lost_after_seconds=86400,
        )
    row = jobs_table.get_by_owner_job(
        user_id="user-1", org_id="org-1", sandbox=True, job_id="e6"
    )
    assert row["poll_count"] == 2
    assert row["status"] == "unknown"


def test_lost_job_enters_pending_terminal_queue(jobs_table) -> None:
    jobs_table.insert_submitted(**_submit_kwargs(job_id="e7"))
    _age_job_silence(jobs_table, "e7", seconds=7200)
    jobs_table.mark_poll_error(
        user_id="user-1",
        org_id="org-1",
        sandbox=True,
        job_id="e7",
        backoff_seconds=45,
        lost_after_seconds=3600,
    )
    pending = jobs_table.query_session_pending_terminal(
        user_id="user-1", org_id="org-1", session_id="sess-1", limit=5
    )
    assert [j["job_id"] for j in pending] == ["e7"]
    assert pending[0]["status"] == "lost"


def test_apply_poll_does_not_revert_lost(jobs_table) -> None:
    # 置 lost 后，迟到的前台 poll 不得把它拉回活跃态（终态单调性）
    jobs_table.insert_submitted(**_submit_kwargs(job_id="e8"))
    _age_job_silence(jobs_table, "e8", seconds=7200)
    jobs_table.mark_poll_error(
        user_id="user-1",
        org_id="org-1",
        sandbox=True,
        job_id="e8",
        backoff_seconds=45,
        lost_after_seconds=3600,
    )
    jobs_table.apply_poll(
        user_id="user-1",
        org_id="org-1",
        sandbox=True,
        job_id="e8",
        status="running",
        is_terminal=False,
        backoff_seconds=30,
    )
    row = jobs_table.get_by_owner_job(
        user_id="user-1", org_id="org-1", sandbox=True, job_id="e8"
    )
    assert row["status"] == "lost"
    assert row["next_poll_at"] is None
```

- [ ] **Step 2.2: 运行确认失败**

Run: `uv run --extra dev pytest tests/dao/test_bohrium_jobs_table.py -v`
Expected: 新用例与改造后的现有 2 用例均 FAIL，报 `TypeError: mark_poll_error() got an unexpected keyword argument 'lost_after_seconds'`。（无 `.env.test` 时整文件 SKIP——则跳过本步与 2.4 的"确认失败/通过"，SQL 正确性靠评审与 Task 3 的 fake 测试兜底。）

- [ ] **Step 2.3: 实现**

`src/dao/bohrium_jobs_table.py` 整体替换 `mark_poll_error`（269-301 行）：

```python
    def mark_poll_error(
        self,
        *,
        user_id: str,
        org_id: str,
        sandbox: bool,
        job_id: str,
        backoff_seconds: int,
        lost_after_seconds: int,
    ) -> None:
        """poll/同步失败时：活跃作业标 unknown、计数并按 backoff 推进；连续失联
        （自上次成功 poll，无则自提交）超过 lost_after_seconds 的活跃作业原子置
        终态 lost——停表、补 terminal_at、进入交付队列。

        MySQL 的 UPDATE SET 从左到右生效：status 赋值必须放最后，前列的 CASE
        才能读到旧 status（与 apply_poll 同一模式）。
        """
        sql = f"""
            UPDATE {self.table_name}
            SET
                poll_count = CASE
                    WHEN status IN ({_SQL_ACTIVE})
                    THEN poll_count + 1 ELSE poll_count END,
                terminal_at = CASE
                    WHEN status IN ({_SQL_ACTIVE})
                         AND NOW() > COALESCE(last_polled_at, submitted_at)
                             + INTERVAL %s SECOND
                    THEN COALESCE(terminal_at, NOW())
                    ELSE terminal_at END,
                next_poll_at = CASE
                    WHEN status IN ({_SQL_ACTIVE})
                         AND NOW() > COALESCE(last_polled_at, submitted_at)
                             + INTERVAL %s SECOND
                    THEN NULL
                    WHEN status IN ({_SQL_ACTIVE})
                    THEN NOW() + INTERVAL %s SECOND
                    ELSE next_poll_at END,
                status = CASE
                    WHEN status IN ({_SQL_ACTIVE})
                         AND NOW() > COALESCE(last_polled_at, submitted_at)
                             + INTERVAL %s SECOND
                    THEN 'lost'
                    WHEN status IN ({_SQL_ACTIVE})
                    THEN 'unknown'
                    ELSE status END
            WHERE user_id = %s AND org_id = %s AND sandbox = %s AND job_id = %s
        """
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql,
                    (
                        int(lost_after_seconds),
                        int(lost_after_seconds),
                        int(backoff_seconds),
                        int(lost_after_seconds),
                        user_id,
                        org_id,
                        1 if sandbox else 0,
                        job_id,
                    ),
                )
            conn.commit()
```

注意：此时 `src/services/bohrium_poller.py` 的三处调用还没传新参数，poller 真库测试会暂时 FAIL——属预期，Task 3 修复。本 task 只验证 DAO 文件自身的测试。

- [ ] **Step 2.4: 运行确认通过**

Run: `uv run --extra dev pytest tests/dao/test_bohrium_jobs_table.py tests/dao/test_bohrium_jobs_delivery.py -v`
Expected: 全 PASS（delivery 文件不直接调 mark_poll_error，确认无连带回归）。

- [ ] **Step 2.5: Commit**

```bash
git add src/dao/bohrium_jobs_table.py tests/dao/test_bohrium_jobs_table.py
git commit -m "feat(dao): mark_poll_error finalizes silent jobs as lost and backs off errors"
```

---

### Task 3: poller 读取阈值并透传

**Files:**
- Modify: `src/services/bohrium_poller.py`
- Test: `tests/services/test_bohrium_poller.py`

- [ ] **Step 3.1: 写失败测试**

`tests/services/test_bohrium_poller.py` 末尾追加（fake table 模式对齐文件内现有 `_Table`）：

```python
def _make_capture_table(captured: list[dict]):
    class _Table:
        def claim_due_batch(self, *, limit: int, claim_timeout_seconds: int):
            return [
                {
                    "session_id": "sess-1",
                    "user_id": "user-1",
                    "org_id": "org-1",
                    "project_id": 42,
                    "job_id": "101",
                    "sandbox": False,
                    "workspace": "/share/project",
                    "status": "submitted",
                    "poll_count": 0,
                }
            ]

        def apply_poll(self, **kw):
            raise AssertionError(f"unexpected poll success: {kw}")

        def mark_poll_error(self, **kw):
            captured.append(kw)

    return _Table()


def test_poller_passes_lost_after_to_mark_error() -> None:
    captured: list[dict] = []
    poller = BohriumJobPoller(
        table=_make_capture_table(captured),
        get_access_key=lambda uid, oid: None,
        get_job_detail=lambda ctx, job_id: {"status": 1},
        base_url="https://x",
        lost_after_seconds=1234,
    )
    poller.run_once()
    assert captured[0]["lost_after_seconds"] == 1234


def test_poller_lost_after_defaults_from_env(monkeypatch) -> None:
    monkeypatch.setenv("BOHRIUM_POLL_LOST_AFTER_SECONDS", "555")
    captured: list[dict] = []
    poller = BohriumJobPoller(
        table=_make_capture_table(captured),
        get_access_key=lambda uid, oid: None,
        get_job_detail=lambda ctx, job_id: {"status": 1},
        base_url="https://x",
    )
    poller.run_once()
    assert captured[0]["lost_after_seconds"] == 555
```

- [ ] **Step 3.2: 运行确认失败**

Run: `uv run --extra dev pytest tests/services/test_bohrium_poller.py -v -k "lost_after"`
Expected: 两个新用例 FAIL（`__init__() got an unexpected keyword argument 'lost_after_seconds'`；第二个用例 `captured[0]` 缺 `lost_after_seconds` 键 KeyError）。

- [ ] **Step 3.3: 实现**

(a) `src/services/bohrium_poller.py:1` 模块 docstring 修正（顺手清掉陈旧表述）：

```python
"""Bohrium 后台轮询核心（monitor 进程的 tick 单元）。"""
```

(b) `BohriumJobPoller.__init__` 加参数并存储。`env_int` 已由文件头 `from src.utils.constant import env_int` 引入（`BohriumMonitor` 现有用法同源），无需新 import：

```python
    def __init__(
        self,
        *,
        table: Any | None = None,
        get_access_key: Callable[[str, str], str | None] | None = None,
        get_job_detail: Callable[..., dict[str, Any]] | None = None,
        base_url: str | None = None,
        lost_after_seconds: int | None = None,
    ) -> None:
```

`__init__` 体末尾追加：

```python
        self._lost_after = (
            lost_after_seconds
            if lost_after_seconds is not None
            else env_int("BOHRIUM_POLL_LOST_AFTER_SECONDS", 86400)
        )
```

(c) `_poll_one` 内三处 `self._table.mark_poll_error(...)` 调用（AK 不可用、detail 缺 status、get_job_detail 异常）各加：

```python
                lost_after_seconds=self._lost_after,
```

- [ ] **Step 3.4: 运行确认通过（含 poller 全文件回归）**

Run: `uv run --extra dev pytest tests/services/test_bohrium_poller.py tests/monitor/ -v`
Expected: 全 PASS（真库组在有 `.env.test` 时一并验证 Task 2 暂挂的三处调用已修复；无则 SKIP，fake 用例必须 PASS）。

- [ ] **Step 3.5: Commit**

```bash
git add src/services/bohrium_poller.py tests/services/test_bohrium_poller.py
git commit -m "feat(services): poller threads lost-after threshold into mark_poll_error"
```

---

### Task 4: 交付链路守护测试（lost 计入失败聚合）

**Files:**
- Test: `tests/dao/test_bohrium_jobs_delivery.py`

零实现改动：Task 1 扩大集合后 `scan_delivery_units`/`get_first_pending_failed` 的 `{_SQL_FAILURE}`/`{_SQL_TERMINAL}` 插值自动包含 lost。本测试是语义守护——**预期一次通过**；若 FAIL 说明插值链路有缺口，按失败现场排查（不得改测试迁就）。

两个用例分别锚定 lost 进入 `decide()` 两个分支所需的聚合数字形态。注意 `decide()` 是纯函数、只看聚合计数、对 status 字符串不可见，其分支逻辑已由 `tests/services/test_bohrium_completion_scheduler.py` 的既有用例守护（`test_decide_final_when_all_terminal`、`test_decide_first_failure_fast_lane`）——链路由"DAO 聚合数字正确（本 task）+ decide 对数字的反应正确（既有）"两段拼接闭合，**不在 scheduler 层重复加 lost 用例**。

- [ ] **Step 4.1: 写守护测试**

`tests/dao/test_bohrium_jobs_delivery.py` 末尾追加（helper 沿用文件内 `_register_session`/`_seed_job`；owner 常量同文件惯例 u1/o1）：

```python
def _force_lost(jobs_table, *, job_id):
    """把活跃行拨老后经 mark_poll_error 置 lost（唯一合法写入路径）。"""
    with jobs_table.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE bohrium_jobs SET submitted_at = NOW() - INTERVAL 7200 SECOND "
                "WHERE job_id = %s",
                (job_id,),
            )
        conn.commit()
    jobs_table.mark_poll_error(
        user_id="u1",
        org_id="o1",
        sandbox=False,
        job_id=job_id,
        backoff_seconds=30,
        lost_after_seconds=3600,
    )


def test_scan_lost_only_unit_has_final_shape(jobs_table, sessions_shadow):
    # 全部作业失联：active 归零、pending_terminal 计入 → decide 判 FINAL。
    # 锚定本方案要修的核心病灶：失联作业不再以 active>0 永久压制 FINAL。
    _register_session(sessions_shadow)
    _seed_job(jobs_table, inv="inv-1", job_id="401")
    _force_lost(jobs_table, job_id="401")

    units = jobs_table.scan_delivery_units(limit=10)

    assert len(units) == 1
    unit = units[0]
    assert unit["pending_terminal"] == 1
    assert unit["active"] == 0
    assert unit["failed_total"] == 1
    assert unit["failed_handled"] == 0
    assert unit["succeeded"] == 0


def test_scan_lost_with_active_has_first_failure_shape(jobs_table, sessions_shadow):
    # 1 lost + 1 仍在跑：failed_total>0 且 failed_handled==0、active>0
    # → decide 判 FIRST_FAILURE；get_first_pending_failed 取到 lost 行供文案。
    _register_session(sessions_shadow)
    _seed_job(jobs_table, inv="inv-1", job_id="402")
    _seed_job(jobs_table, inv="inv-1", job_id="403")
    _force_lost(jobs_table, job_id="402")

    units = jobs_table.scan_delivery_units(limit=10)

    assert len(units) == 1
    unit = units[0]
    assert unit["total"] == 2
    assert unit["pending_terminal"] == 1
    assert unit["active"] == 1
    assert unit["failed_total"] == 1
    assert unit["failed_handled"] == 0

    first = jobs_table.get_first_pending_failed(
        user_id="u1", org_id="o1", session_id="sess-1", invocation_key="inv-1"
    )
    assert first is not None
    assert first["status"] == "lost"
```

- [ ] **Step 4.2: 运行确认通过**

Run: `uv run --extra dev pytest tests/dao/test_bohrium_jobs_delivery.py -v`
Expected: 全 PASS。两组聚合数字分别命中 `decide()` 的 FINAL 与 FIRST_FAILURE 分支（分支本身由既有 scheduler 用例守护）——调度器代码零改动。

- [ ] **Step 4.3: Commit**

```bash
git add tests/dao/test_bohrium_jobs_delivery.py
git commit -m "test(dao): guard lost jobs flow into delivery scan as failures"
```

---

### Task 5: 全量回归、lint 与汇报

- [ ] **Step 5.1: 全量回归**

Run: `uv run --extra dev pytest tests/dao/ tests/services/ tests/monitor/ tests/matmaster/ -v`
Expected: 全 PASS（无 `.env.test` 时真库组 SKIP，逐组记录 SKIP 数）。

- [ ] **Step 5.2: pre-commit**

```bash
uv run pre-commit run --files \
  matmaster/bohrium/status.py src/dao/bohrium_jobs_table.py \
  src/services/bohrium_poller.py \
  src/sql/create_bohrium_jobs_table.sql \
  src/sql/migrate_add_bohrium_jobs_lost_status.sql \
  tests/matmaster/bohrium/test_ledger_status.py \
  tests/dao/test_bohrium_jobs_table.py \
  tests/dao/test_bohrium_jobs_delivery.py \
  tests/services/test_bohrium_poller.py
```

Expected: 全部 Passed（black/isort 重排则重跑相关测试后 amend 或追加 `style:` commit）。

- [ ] **Step 5.3: 汇报**

向用户汇报：改动文件清单与各 commit、测试结果（含真库组是否 SKIP）、**部署时序要求**（存量环境必须先手动执行 `src/sql/migrate_add_bohrium_jobs_lost_status.sql` 再发版，monitor 与 worker/API 镜像都依赖新 CHECK）、可选配置 `BOHRIUM_POLL_LOST_AFTER_SECONDS`（默认 86400）、已接受边界（平台长宕机批量置 lost；unknown 映射的新状态码不触发 lost）。

---

## Self-Review 记录（计划作者已自查）

- **方案覆盖**：设计裁定 1/3 → Task 1 测试；2 → Task 1 实现 + Task 2 用例 e7 + Task 4；4 → Task 2 用例 e4/e6；5 → Task 2 SQL（status 末位赋值）；6 → Task 3；7 → Task 1(b)(c) + Task 5 汇报。
- **类型一致性**：`mark_poll_error` 新签名（Task 2 定义）与 Task 3 三处调用、Task 2/4 测试 kwargs 一致（`lost_after_seconds: int` 必填）；fake `_Table.mark_poll_error(**kw)` 兼容新 kwarg；`env_int` 来自 `src.utils.constant`，poller 文件头既有 import（`BohriumMonitor` 现有用法同源），无新 import。
- **占位符扫描**：无 TBD/TODO；所有代码步骤含完整代码、所有运行步骤含命令与预期。
- **时序自查**：Task 2 完成后 poller 真库测试暂挂（三处调用缺参）——已在 Step 2.3 显式声明，Task 3 闭合；Step 2.4 刻意只跑 DAO 两文件。Task 1 先行保证 conftest 建表含 lost CHECK，Task 2 真库用例才可写入 lost。
- **MySQL 语义**：SET 左到右求值已在 SQL 注释与裁定 5 双重声明；`poll_count` 自增不被后续 CASE 引用，列序安全；失联谓词只读 `last_polled_at`/`submitted_at`（本语句不更新），无顺序依赖。
- **外部 review 修订（2026-06-10）**：
  1. Task 3 片段由 `_env_int` 改为 `env_int`——计划初稿基于旧版 poller（模块内私有 `_env_int`），live code 已重构为 `src/utils/constant.py::env_int` 统一供 poller/scheduler/ack 使用，且 poller 同期改为"预取 AK + 线程池并发 poll"（fake table 测试经核对仍兼容：单作业无并发竞争，`list.append` 线程安全）。
  2. Task 4 由单用例拆为 FINAL 形态 + FIRST_FAILURE 形态两用例——原用例 1 lost 无 active，`decide()` 中 `active == 0` 先于 `failed_total` 判定，只能证明 FINAL 路径；FIRST_FAILURE 所需的"lost 与 active 并存"聚合形态未被锚定。修法采纳 DAO 层补形态用例；**未采纳**在 scheduler 层重复加 decide/lost 用例——`decide()` 对 status 字符串不可见，`test_decide_first_failure_fast_lane` 已守护该分支，重复用例无新增信息。
  3. 验证命令统一 uv 化（AGENTS.md 约定）：pytest 走 `uv run --extra dev pytest`（pytest 在 dev extra）；pre-commit 走 `uv run pre-commit`——review 建议的 `--extra dev` 形式经核对不必要，`pre-commit>=4.5.1` 在主依赖中。
