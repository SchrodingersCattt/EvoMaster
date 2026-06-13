# Bohrium delivery ack 缺口修复实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复两个 handled 标记缺口——run 内前台查询到的终态作业在 run 成功后被一并 ack（消除冗余唤醒），以及全失联卡死单元的失速兜底唤醒 STALLED。

**Architecture:** 缺陷一在 `DeliverySnapshot` 上加 run 级前台观察集，confirm 时按 snapshot 行 id 与观察集 (sandbox, job_id) 并集 ack，同时把 pending 渲染统一为冻结语义（删实时分支与 `query_session_pending_terminal`）。缺陷二在聚合扫描加 unknown 计数与最老 pending 年龄两列（SQL 内用 DB NOW 计算），`decide` 在 PROGRESS 之后加 STALLED 出口，保持纯函数与无状态闭环。

**Tech Stack:** Python 3 + PyMySQL（raw SQL DAO）、pytest（`uv run pytest`，DAO 真库测试需 `.env.test`，缺失自动 SKIP）。

**Spec:** `docs/superpowers/specs/2026-06-11-bohrium-delivery-ack-gaps-design.md`

**工作目录：本计划全部路径相对 matmaster-evo 仓库根。**

---

## 文件结构总览

| 文件 | 动作 | 职责 |
|---|---|---|
| `src/dao/bohrium_jobs_table.py` | 改 | 增 `mark_handled_by_job_keys`；删 `query_session_pending_terminal`；`scan_delivery_units` 增两列 |
| `src/services/bohrium_delivery_ack.py` | 改 | `DeliverySnapshot` 增 `observed_terminal`；snapshot 空 rows 语义；confirm 并集 ack |
| `src/services/bohrium_jobs_wiring.py` | 改 | ledger 写观察集；读 port 冻结渲染、删实时分支 |
| `src/services/bohrium_completion_scheduler.py` | 改 | Reason 重排增 STALLED；decide 增分支；prompt 增文案；config 增阈值；counts 增 unknown |
| `src/worker/agent_worker.py` | 不动 | confirm 调用条件不变 |
| `tests/dao/test_bohrium_jobs_delivery.py` | 改 | 新 ack 方法与聚合新列的真库测试 |
| `tests/dao/test_bohrium_jobs_table.py` | 改 | 删被删方法的测试、换探针 |
| `tests/services/test_bohrium_delivery_ack.py` | 改 | 空 rows 语义、并集 ack |
| `tests/services/test_bohrium_jobs_wiring.py` | 改 | 观察集写入、冻结渲染（删实时分支用例） |
| `tests/services/test_bohrium_completion_scheduler.py` | 改 | STALLED 判定/文案/tick |

测试命令统一形如 `uv run pytest tests/... -v`（pytest.ini 已设 pythonpath=.、asyncio auto）。DAO 测试无 `.env.test` 时整组 SKIP，属预期，不算失败。每个任务以提交收尾，提交信息末尾带：

```
Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
```

---

### Task 1: DAO `mark_handled_by_job_keys`

**Files:**
- Modify: `src/dao/bohrium_jobs_table.py`（`mark_handled_by_ids` 之后）
- Test: `tests/dao/test_bohrium_jobs_delivery.py`（文件末尾追加）

- [ ] **Step 1: 写失败测试**

在 `tests/dao/test_bohrium_jobs_delivery.py` 末尾追加（复用文件内既有 `_register_session`、`_seed_job` 夹具）：

```python
def test_mark_handled_by_job_keys_idempotent_and_session_scoped(
    jobs_table, sessions_shadow
):
    _register_session(sessions_shadow)
    _seed_job(jobs_table, job_id="301", status="finished")  # 本会话，终态
    _seed_job(jobs_table, job_id="302")  # 本会话，仍活跃
    _register_session(sessions_shadow, session="sess-2")
    _seed_job(jobs_table, session="sess-2", job_id="303", status="finished")  # 他会话

    # sandbox 维度不匹配 → 不命中（唯一键含 sandbox）
    assert (
        jobs_table.mark_handled_by_job_keys(
            user_id="u1",
            org_id="o1",
            session_id="sess-1",
            job_keys=[(True, "301")],
        )
        == 0
    )

    affected = jobs_table.mark_handled_by_job_keys(
        user_id="u1",
        org_id="o1",
        session_id="sess-1",
        job_keys=[(False, "301"), (False, "302"), (False, "303")],
    )

    # 只有本会话且已终态的 301 被标：302 活跃、303 属他会话
    assert affected == 1
    assert (
        jobs_table.list_pending_terminal_snapshot(
            user_id="u1", org_id="o1", session_id="sess-1"
        )
        == []
    )
    other = jobs_table.list_pending_terminal_snapshot(
        user_id="u1", org_id="o1", session_id="sess-2"
    )
    assert [r["job_id"] for r in other] == ["303"]  # 他会话交付不被吞

    # 幂等：重复 ack no-op；空集短路不触库
    assert (
        jobs_table.mark_handled_by_job_keys(
            user_id="u1",
            org_id="o1",
            session_id="sess-1",
            job_keys=[(False, "301")],
        )
        == 0
    )
    assert (
        jobs_table.mark_handled_by_job_keys(
            user_id="u1", org_id="o1", session_id="sess-1", job_keys=[]
        )
        == 0
    )
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/dao/test_bohrium_jobs_delivery.py::test_mark_handled_by_job_keys_idempotent_and_session_scoped -v`
Expected: FAIL，`AttributeError: ... no attribute 'mark_handled_by_job_keys'`（无 `.env.test` 则 SKIP——此时本任务在有测试库的环境执行，或接受 SKIP 并依赖 Task 7 全量验证）

- [ ] **Step 3: 实现**

在 `src/dao/bohrium_jobs_table.py` 的 `mark_handled_by_ids` 方法之后追加：

```python
    def mark_handled_by_job_keys(
        self,
        *,
        user_id: str,
        org_id: str,
        session_id: str,
        job_keys: Sequence[tuple[bool, str]],
        chunk_size: int = 500,
    ) -> int:
        """按 run 内前台观察到的 (sandbox, job_id) 批量 ack；幂等。

        session_id 约束是安全闸：apply_poll 按 owner+job_id 定位、不带 session，
        跨会话查询写终态到他会话的行，但 ack 只清本会话行，他会话应得的唤醒
        一个不少。返回实际更新行数；分块单事务提交。
        """
        keys = [(1 if sandbox else 0, str(job_id)) for sandbox, job_id in job_keys]
        if not keys:
            return 0
        affected = 0
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                for start in range(0, len(keys), int(chunk_size)):
                    chunk = keys[start : start + int(chunk_size)]
                    placeholders = ", ".join(["(%s, %s)"] * len(chunk))
                    flat = [v for pair in chunk for v in pair]
                    cur.execute(
                        f"""
                        UPDATE {self.table_name}
                        SET handled_at = NOW()
                        WHERE user_id = %s AND org_id = %s AND session_id = %s
                          AND (sandbox, job_id) IN ({placeholders})
                          AND terminal_at IS NOT NULL
                          AND handled_at IS NULL
                        """,
                        (user_id, org_id, session_id, *flat),
                    )
                    affected += cur.rowcount
            conn.commit()
        return affected
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/dao/test_bohrium_jobs_delivery.py -v`
Expected: 新用例 PASS，原有用例全 PASS（或环境无测试库时整组 SKIP）

- [ ] **Step 5: 提交**

```bash
git add src/dao/bohrium_jobs_table.py tests/dao/test_bohrium_jobs_delivery.py
git commit -m "feat(bohrium): DAO 增 mark_handled_by_job_keys——按作业键幂等 ack、session 约束防跨会话误吞"
```

---

### Task 2: DeliverySnapshot 观察集 + snapshot 空 rows 语义 + confirm 并集

**Files:**
- Modify: `src/services/bohrium_delivery_ack.py`（全文件重写主体）
- Test: `tests/services/test_bohrium_delivery_ack.py`

- [ ] **Step 1: 改写/新增测试**

对 `tests/services/test_bohrium_delivery_ack.py` 做以下修改。

删除 `test_snapshot_returns_none_when_no_pending_rows` 与
`test_snapshot_returns_none_on_query_failure_without_raising` 两个函数，原位替换为：

```python
def test_snapshot_empty_rows_returns_object_not_none():
    # 身份可解析即返回对象：空 rows 是合法交付边界（rows=()），观察集空集起步
    table = MagicMock()
    table.list_pending_terminal_snapshot.return_value = []
    snap = bohrium_delivery_ack.snapshot(
        "sess-1", sessions_service=_sessions(), jobs_table=table
    )
    assert snap is not None
    assert snap.rows == ()
    assert snap.observed_terminal == set()


def test_snapshot_rows_query_failure_degrades_to_empty_rows():
    # rows 查询失败但身份正常 → 空 rows snapshot：本轮不渲染存量 pending，
    # 观察集照常工作，未渲染行下轮重投
    table = MagicMock()
    table.list_pending_terminal_snapshot.side_effect = RuntimeError("db down")
    snap = bohrium_delivery_ack.snapshot(
        "sess-1", sessions_service=_sessions(), jobs_table=table
    )
    assert snap is not None and snap.rows == ()
```

文件末尾追加三个用例：

```python
def test_snapshot_identity_lookup_failure_returns_none():
    svc = MagicMock()
    svc.get_session.side_effect = RuntimeError("db down")
    assert (
        bohrium_delivery_ack.snapshot("s", sessions_service=svc, jobs_table=MagicMock())
        is None
    )


def test_confirm_acks_union_of_rows_and_observed():
    table = MagicMock()
    table.mark_handled_by_ids.return_value = 2
    table.mark_handled_by_job_keys.return_value = 1
    snap = bohrium_delivery_ack.DeliverySnapshot(
        user_id="u1",
        org_id="o1",
        session_id="s",
        rows=(_row(11, "a"), _row(12, "b")),
        detail_limit=20,
    )
    snap.observed_terminal.add((True, "J"))

    assert bohrium_delivery_ack.confirm(snap, jobs_table=table) == 3
    assert table.mark_handled_by_ids.call_args.kwargs["row_ids"] == (11, 12)
    kw = table.mark_handled_by_job_keys.call_args.kwargs
    assert kw == {
        "user_id": "u1",
        "org_id": "o1",
        "session_id": "s",
        "job_keys": ((True, "J"),),
    }


def test_confirm_skips_dao_calls_for_empty_sets():
    table = MagicMock()
    snap = bohrium_delivery_ack.DeliverySnapshot(
        user_id="u1", org_id="o1", session_id="s", rows=(), detail_limit=20
    )
    assert bohrium_delivery_ack.confirm(snap, jobs_table=table) == 0
    table.mark_handled_by_ids.assert_not_called()
    table.mark_handled_by_job_keys.assert_not_called()
```

既有 `test_confirm_acks_exactly_snapshot_row_ids`、`test_confirm_propagates_failure_to_caller`、
`test_snapshot_holds_full_rows`、detail_limit 两用例、身份缺失两用例保持不动
（`DeliverySnapshot` 直接构造点靠 `observed_terminal` 的 default_factory 兼容现有写法）。

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/services/test_bohrium_delivery_ack.py -v`
Expected: 新增/改写用例 FAIL（`observed_terminal` 字段不存在、空 rows 返回 None、
confirm 未调 `mark_handled_by_job_keys`），其余 PASS

- [ ] **Step 3: 实现**

改写 `src/services/bohrium_delivery_ack.py`：

模块 docstring 整体替换为：

```python
"""Worker 侧 delivery snapshot 与 ack（对所有 run 生效，不分 origin）。

- snapshot：run 起点（acquire 成功后、run_agent 前）解析身份并查询全量 pending
  terminal rows；查询执行瞬间即本轮交付边界（rows 可空），run 中途新终态的行
  留待下轮（at-least-once）。observed_terminal 收 run 内前台查询观察到的终态。
- confirm：run 成功收尾、release_session_run 之前，按 snapshot rows 与观察集
  并集批量 ack——ack 范围 = agent 看到范围（snapshot 行 ∪ 前台查询结果）。
handled_at 的唯一写入点在这里；poller 与 trigger enqueued 均不得 ack。
"""
```

import 行 `from dataclasses import dataclass` 改为
`from dataclasses import dataclass, field`。

`DeliverySnapshot` 整体替换为：

```python
@dataclass(frozen=True)
class DeliverySnapshot:
    """一次 run 的交付边界快照 + run 内前台观察集（worker 内存对象，不落表）。

    rows 持全量行、不预截断：展开几条详情由 renderer 按 detail_limit 决定。
    observed_terminal 元素为 (sandbox, job_id)；frozen 冻结字段绑定，不妨碍
    集合自身 add——写入发生在 run 内工具执行，confirm 读取在 run 结束后，
    无时间重叠。
    """

    user_id: str
    org_id: str
    session_id: str
    rows: tuple[dict[str, Any], ...]
    detail_limit: int
    observed_terminal: set[tuple[bool, str]] = field(default_factory=set)
```

`snapshot` 整体替换为：

```python
def snapshot(
    session_id: str,
    *,
    sessions_service: Any | None = None,
    jobs_table: Any | None = None,
) -> DeliverySnapshot | None:
    """解析身份并查询全量 pending terminal rows。

    身份不可解析（session 缺失 / org 未绑定 / 查库异常）→ None：既无法 ack 也
    无法渲染，未交付行下轮重投。rows 查询失败但身份正常 → 空 rows snapshot。
    """
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
    except Exception:  # noqa: BLE001
        logger.warning(
            "bohrium delivery identity resolve failed session_id=%s",
            session_id,
            exc_info=True,
        )
        return None
    rows: list[dict[str, Any]] = []
    try:
        table = jobs_table
        if table is None:
            from src.dao.bohrium_jobs_table import get_bohrium_jobs_table

            table = get_bohrium_jobs_table()
        rows = table.list_pending_terminal_snapshot(
            user_id=user_id, org_id=org_id, session_id=session_id
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "bohrium delivery snapshot failed session_id=%s",
            session_id,
            exc_info=True,
        )
    return DeliverySnapshot(
        user_id=user_id,
        org_id=org_id,
        session_id=session_id,
        rows=tuple(rows),
        detail_limit=env_int("BOHRIUM_DELIVERY_DETAIL_LIMIT", 20),
    )
```

`confirm` 整体替换为：

```python
def confirm(snap: DeliverySnapshot, *, jobs_table: Any | None = None) -> int:
    """按 snapshot rows 与前台观察集并集批量 ack；空集短路，异常向上抛。

    两段均幂等（handled_at IS NULL 谓词），重叠行第二次更新落空。
    """
    if not (snap.rows or snap.observed_terminal):
        return 0
    table = jobs_table
    if table is None:
        from src.dao.bohrium_jobs_table import get_bohrium_jobs_table

        table = get_bohrium_jobs_table()
    affected = 0
    if snap.rows:
        affected += table.mark_handled_by_ids(
            user_id=snap.user_id,
            org_id=snap.org_id,
            session_id=snap.session_id,
            row_ids=tuple(int(j["id"]) for j in snap.rows),
        )
    if snap.observed_terminal:
        affected += table.mark_handled_by_job_keys(
            user_id=snap.user_id,
            org_id=snap.org_id,
            session_id=snap.session_id,
            job_keys=tuple(snap.observed_terminal),
        )
    return affected
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/services/test_bohrium_delivery_ack.py tests/test_agent_worker_snapshot_confirm.py -v`
Expected: 全 PASS（worker 测试零改动通过：snapshot/confirm 被 monkeypatch，时序断言不受影响）

- [ ] **Step 5: 提交**

```bash
git add src/services/bohrium_delivery_ack.py tests/services/test_bohrium_delivery_ack.py
git commit -m "feat(bohrium): DeliverySnapshot 增前台观察集，confirm 并集 ack，空 rows 成为合法交付边界"
```

---

### Task 3: wiring——观察集写入 + 冻结渲染（删实时分支）

**Files:**
- Modify: `src/services/bohrium_jobs_wiring.py`
- Test: `tests/services/test_bohrium_jobs_wiring.py`

- [ ] **Step 1: 改写/新增测试**

对 `tests/services/test_bohrium_jobs_wiring.py`：

删除 `test_session_jobs_port_loads_active_and_pending` 与
`test_jobs_port_without_snapshot_keeps_legacy_read_path` 两个函数（实时分支没了）；
同时删除 `test_jobs_port_serves_pending_from_snapshot_with_detail_limit` 中的过时断言行
`table.query_session_pending_terminal.assert_not_called()`（该方法 Task 4 将被删除，
留着会让零引用扫描失败）。原位替换为：

```python
@pytest.mark.asyncio
async def test_jobs_port_without_snapshot_renders_empty_pending() -> None:
    # 渲染统一为冻结语义：无 snapshot（身份降级）→ pending 恒空，active 仍实时
    table = MagicMock()
    table.query_session_active.return_value = [{"job_id": "a"}]
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

    assert result.active_jobs == ({"job_id": "a"},)
    assert result.pending_terminal_jobs == ()
    assert result.detail_limit is None
```

文件末尾追加：

```python
def test_record_poll_terminal_feeds_observed_set() -> None:
    table = MagicMock()
    snap = _snapshot([])
    ledger, _ = build_bohrium_jobs_ports(
        session_id="s",
        invocation_id="inv",
        user_id="u",
        org_id="o",
        workspace="/share/project",
        table=table,
        delivery_snapshot=snap,
    )
    ledger.record_poll(job_id="J", sandbox=True, status_code=2)  # finished → 终态
    ledger.record_poll(job_id="K", sandbox=False, status_code=1)  # running → 不收
    assert snap.observed_terminal == {(True, "J")}


def test_record_poll_without_snapshot_skips_observation() -> None:
    table = MagicMock()
    ledger, _ = build_bohrium_jobs_ports(
        session_id="s",
        invocation_id="inv",
        user_id="u",
        org_id="o",
        workspace="/share/project",
        table=table,
    )
    ledger.record_poll(job_id="J", sandbox=False, status_code=2)
    table.apply_poll.assert_called_once()  # ledger 写入不受影响，仅不记观察
```

（`_snapshot` 夹具无需改动：`observed_terminal` 有 default_factory。）

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/services/test_bohrium_jobs_wiring.py -v`
Expected: 新用例 FAIL（无 snapshot 仍走实时查询返回 `({"job_id": "t"},)` 之类；
observed 集合为空），其余 PASS

- [ ] **Step 3: 实现**

对 `src/services/bohrium_jobs_wiring.py`：

`_BohriumJobLedger.__init__` 签名与体替换为（增末参；其余赋值保持原样）：

```python
    def __init__(
        self,
        *,
        table_ref: _BohriumJobsTableRef,
        session_id: str,
        invocation_id: str | None,
        user_id: str,
        org_id: str,
        workspace: str,
        spawn_id: str | None = None,
        observed_terminal: set[tuple[bool, str]] | None = None,
    ) -> None:
        self._table_ref = table_ref
        self._session_id = session_id
        self._invocation_id = invocation_id
        self._user_id = user_id
        self._org_id = org_id
        self._workspace = workspace
        self._spawn_id = spawn_id
        self._observed_terminal = observed_terminal
```

`record_poll` 替换为（apply_poll 成功后才记观察，失败的写入不产生 ack 资格）：

```python
    def record_poll(
        self,
        *,
        job_id: str,
        sandbox: bool,
        status_code: int,
    ) -> None:
        self._require_identity()
        decision = to_ledger_status(int(status_code))
        self._table_ref.get().apply_poll(
            user_id=self._user_id,
            org_id=self._org_id,
            sandbox=bool(sandbox),
            job_id=str(job_id),
            status=decision.status,
            is_terminal=decision.is_terminal,
            backoff_seconds=_FOREGROUND_POLL_BACKOFF_SECONDS,
        )
        if decision.is_terminal and self._observed_terminal is not None:
            self._observed_terminal.add((bool(sandbox), str(job_id)))
```

`_RunSessionJobsPort.load_session_jobs` 替换为（删 gather 与实时 pending 分支）：

```python
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
        except Exception:  # noqa: BLE001
            logger.warning(
                "load_session_jobs failed session_id=%s",
                query.session_id,
                exc_info=True,
            )
            return SessionJobs.empty()
        # 渲染统一冻结语义：pending = snapshot.rows（无 snapshot 即空集），
        # run 中途新终态不渲染不 ack、留待下轮；active 实时（当前还在跑什么）
        if self._snapshot is not None:
            pending: tuple[dict[str, Any], ...] = self._snapshot.rows
            detail_limit: int | None = self._snapshot.detail_limit
        else:
            pending = ()
            detail_limit = None
        return SessionJobs(
            active_jobs=tuple(active),
            pending_terminal_jobs=tuple(pending),
            detail_limit=detail_limit,
        )
```

文件顶部 import 增 `from typing import Any`（若已无其他 typing 引用则新增一行）。

`build_bohrium_jobs_ports` 里 ledger 构造改为：

```python
    ledger = (
        _BohriumJobLedger(
            table_ref=table_ref,
            session_id=session_id,
            invocation_id=invocation_id,
            user_id=user_id,
            org_id=org_id,
            workspace=normalized_workspace,
            spawn_id=spawn_id,
            observed_terminal=(
                delivery_snapshot.observed_terminal
                if delivery_snapshot is not None
                else None
            ),
        )
        if normalized_workspace is not None
        else None
    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/services/test_bohrium_jobs_wiring.py tests/services/test_bohrium_delivery_ack.py -v`
Expected: 全 PASS

- [ ] **Step 5: 提交**

```bash
git add src/services/bohrium_jobs_wiring.py tests/services/test_bohrium_jobs_wiring.py
git commit -m "feat(bohrium): record_poll 终态写入前台观察集，pending 渲染统一冻结语义、删实时分支"
```

---

### Task 4: 删除 `query_session_pending_terminal`（DAO + 测试）

**Files:**
- Modify: `src/dao/bohrium_jobs_table.py:215-229`（删方法）
- Modify: `tests/dao/test_bohrium_jobs_table.py`

- [ ] **Step 1: 删 DAO 方法**

从 `src/dao/bohrium_jobs_table.py` 整体删除 `query_session_pending_terminal`
方法（连同其 docstring，从 `def query_session_pending_terminal(` 到该方法
`return` 行止）。

- [ ] **Step 2: 改测试**

`tests/dao/test_bohrium_jobs_table.py`：

1. 整体删除 `test_query_session_pending_terminal` 函数；
2. `test_lost_job_enters_pending_terminal_queue` 中的探针调用

```python
    pending = jobs_table.query_session_pending_terminal(
        user_id="user-1", org_id="org-1", session_id="sess-1", limit=5
    )
```

替换为：

```python
    pending = jobs_table.list_pending_terminal_snapshot(
        user_id="user-1", org_id="org-1", session_id="sess-1"
    )
```

（其后两行断言 `job_id == "e7"`、`status == "lost"` 不变。）

- [ ] **Step 3: 全仓 grep 确认零引用**

Run: `git grep -n "query_session_pending_terminal"`
Expected: 无输出

- [ ] **Step 4: 跑测试**

Run: `uv run pytest tests/dao/ tests/services/ -v`
Expected: 全 PASS（或 DAO 组无测试库 SKIP）

- [ ] **Step 5: 提交**

```bash
git add src/dao/bohrium_jobs_table.py tests/dao/test_bohrium_jobs_table.py
git commit -m "refactor(bohrium): 删 query_session_pending_terminal——冻结渲染后唯一调用方消失"
```

---

### Task 5: 聚合扫描增 unknown_count 与 oldest_pending_age_seconds

**Files:**
- Modify: `src/dao/bohrium_jobs_table.py`（`scan_delivery_units` 的 SQL 与 `_to_delivery_unit`）
- Test: `tests/dao/test_bohrium_jobs_delivery.py`

- [ ] **Step 1: 写失败测试**

在 `tests/dao/test_bohrium_jobs_delivery.py` 末尾追加：

```python
def test_scan_exposes_unknown_count_and_pending_age(jobs_table, sessions_shadow):
    # 1 完成 + 2 失联：unknown 计数与最老 pending 年龄供 decide 判 STALLED
    _register_session(sessions_shadow)
    _seed_job(jobs_table, job_id="501", status="finished")
    _seed_job(jobs_table, job_id="502")
    _seed_job(jobs_table, job_id="503")
    for jid in ("502", "503"):  # mark_poll_error 是 unknown 的唯一合法写入路径
        jobs_table.mark_poll_error(
            user_id="u1",
            org_id="o1",
            sandbox=False,
            job_id=jid,
            backoff_seconds=30,
            lost_after_seconds=86400,
        )
    _shift_terminal_at(sessions_shadow, job_id="501", seconds_ago=600)

    units = jobs_table.scan_delivery_units(limit=10)

    assert len(units) == 1
    unit = units[0]
    assert unit["unknown_count"] == 2
    assert unit["active"] == 2
    assert unit["pending_terminal"] == 1
    # NOW() 与 shift 之间有秒级误差，给宽容窗
    assert 550 <= unit["oldest_pending_age_seconds"] <= 650
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/dao/test_bohrium_jobs_delivery.py::test_scan_exposes_unknown_count_and_pending_age -v`
Expected: FAIL，`KeyError: 'unknown_count'`

- [ ] **Step 3: 实现**

`src/dao/bohrium_jobs_table.py` 的 `scan_delivery_units` SQL 中，在
`SUM(t.status = 'finished')  AS succeeded,` 一行之后插入两列：

```sql
                SUM(t.status = 'unknown')                            AS unknown_count,
                TIMESTAMPDIFF(SECOND,
                    MIN(CASE WHEN t.terminal_at IS NOT NULL
                             AND t.handled_at IS NULL
                        THEN t.terminal_at END),
                    NOW())                                           AS oldest_pending_age_seconds,
```

`_to_delivery_unit` 返回 dict 中（`succeeded` 之后）增两键：

```python
            "unknown_count": int(row["unknown_count"]),
            # HAVING pending_terminal>0 保证 MIN 非 NULL，年龄列恒有值
            "oldest_pending_age_seconds": int(row["oldest_pending_age_seconds"]),
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/dao/test_bohrium_jobs_delivery.py -v`
Expected: 全 PASS

- [ ] **Step 5: 提交**

```bash
git add src/dao/bohrium_jobs_table.py tests/dao/test_bohrium_jobs_delivery.py
git commit -m "feat(bohrium): 交付聚合扫描增 unknown 计数与最老 pending 年龄（DB NOW 计算）"
```

---

### Task 6: 调度器 STALLED——Reason 重排、decide 分支、文案、配置

**Files:**
- Modify: `src/services/bohrium_completion_scheduler.py`
- Test: `tests/services/test_bohrium_completion_scheduler.py`

- [ ] **Step 1: 改写/新增测试**

对 `tests/services/test_bohrium_completion_scheduler.py`：

`_unit()` 的 base dict 在 `max_pending_terminal_id=10,` 之前增两键：

```python
        unknown_count=0,
        oldest_pending_age_seconds=0,
```

decide 区段末尾（`test_progress_count_bounded_by_segments_total_1000` 之后）追加：

```python
def test_decide_stalled_when_all_actives_unknown_and_pending_aged():
    unit = _unit(
        total=10,
        active=7,
        pending_terminal=3,
        unknown_count=7,
        oldest_pending_age_seconds=900,
    )
    assert decide(unit, CFG) is Reason.STALLED


def test_decide_no_stalled_when_some_active_still_running():
    # 7 活跃中仅 6 失联：批次仍在推进，等 step/FINAL 是设计本意
    unit = _unit(
        total=10,
        active=7,
        pending_terminal=3,
        unknown_count=6,
        oldest_pending_age_seconds=3600,
    )
    assert decide(unit, CFG) is None


def test_decide_no_stalled_before_age_threshold():
    unit = _unit(
        total=10,
        active=7,
        pending_terminal=3,
        unknown_count=7,
        oldest_pending_age_seconds=899,
    )
    assert decide(unit, CFG) is None


def test_decide_progress_preempts_stalled_at_threshold():
    # step=ceil(10/3)=4：pending 达 step 走 PROGRESS，不进 STALLED 分支
    unit = _unit(
        total=10,
        active=6,
        pending_terminal=4,
        unknown_count=6,
        oldest_pending_age_seconds=3600,
    )
    assert decide(unit, CFG) is Reason.PROGRESS


def test_decide_first_failure_preempts_stalled():
    unit = _unit(
        total=10,
        active=7,
        pending_terminal=3,
        failed_total=1,
        unknown_count=7,
        oldest_pending_age_seconds=3600,
    )
    assert decide(unit, CFG) is Reason.FIRST_FAILURE


def test_reason_priority_order_for_session_merge():
    assert Reason.PROGRESS < Reason.STALLED < Reason.FIRST_FAILURE < Reason.FINAL
```

render_prompt 区段末尾追加：

```python
def test_render_stalled_prompt_states_unqueryable_jobs():
    prompt = render_prompt(
        Reason.STALLED,
        {"total": 10, "active": 7, "succeeded": 3, "failed_total": 0, "unknown": 7},
    )
    assert "3/10" in prompt
    assert "7 个作业状态长时间无法查询" in prompt
    assert "仍在运行" not in prompt  # 不沿用 PROGRESS 的误导措辞
    assert _SUFFIX in prompt
```

tick 区段末尾追加：

```python
def test_tick_stalled_unit_triggers_without_notify():
    units = [
        _unit(
            total=10,
            active=7,
            pending_terminal=3,
            succeeded=3,
            unknown_count=7,
            oldest_pending_age_seconds=900,
        )
    ]
    sched, _, _, _, stream = _scheduler(units)

    summary = sched.tick()

    assert summary["triggered"] == 1
    assert "无法查询" in stream.calls[0]["prompt"]
    assert stream.calls[0]["delivery"] == DeliverySpec(notify=False)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/services/test_bohrium_completion_scheduler.py -v`
Expected: 新用例 FAIL（`AttributeError: STALLED`），既有用例 PASS

- [ ] **Step 3: 实现**

对 `src/services/bohrium_completion_scheduler.py`：

模块 docstring 末段（非 final 上界一句）替换为：

```python
非 final 自动唤醒上界（per-invocation）= 1(first_failure) + N(progress_segments)，
与作业数无关：progress 阈值 step=ceil(total/N) 随 total 缩放，每次成功 progress
经 worker ack 至少消化 step 个 pending。STALLED 是常态上界外的异常态兜底：仅当
剩余活跃作业全部失联（unknown）且最老 pending 熟化超阈值时触发，每次触发须有
新终态出现并再次熟化，病态接口间歇恢复下至多每作业一次、间隔 ≥ 阈值。
```

`Reason` 替换为：

```python
class Reason(enum.IntEnum):
    """唤醒原因；数值即优先级，session 合并时取最高。"""

    PROGRESS = 1
    STALLED = 2
    FIRST_FAILURE = 3
    FINAL = 4
```

`SchedulerConfig` 替换为：

```python
@dataclass(frozen=True)
class SchedulerConfig:
    progress_segments: int = 3
    reservation_ttl: int = 60
    scan_limit: int = 200
    stalled_after_seconds: int = 900

    @classmethod
    def from_env(cls) -> SchedulerConfig:
        return cls(
            progress_segments=env_int("BOHRIUM_DELIVERY_PROGRESS_SEGMENTS", 3),
            reservation_ttl=env_int("BOHRIUM_DELIVERY_RESERVATION_TTL", 60),
            scan_limit=env_int("BOHRIUM_DELIVERY_SCAN_LIMIT", 200),
            stalled_after_seconds=env_int(
                "BOHRIUM_DELIVERY_STALLED_AFTER_SECONDS", 900
            ),
        )
```

`decide` 替换为：

```python
def decide(unit: dict[str, Any], cfg: SchedulerConfig) -> Reason | None:
    """无状态判定单个 (session, invocation) 聚合单元，全部从 ledger 推导。

    优先级 final > first_failure > progress > stalled；不重复发无需记账：final
    经 ack pending→0、first_failure 经 ack failed_handled>0、progress 经 ack
    回落到 step 之下、stalled 经 ack pending→0 且再触发须新终态重新熟化。
    STALLED 取全部失联（unknown_count==active）而非存在失联：还有作业真实
    运行时等 step/FINAL 是批量节奏的设计本意。
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
    if (
        unit["unknown_count"] == unit["active"]
        and unit["oldest_pending_age_seconds"] >= cfg.stalled_after_seconds
    ):
        return Reason.STALLED
    return None
```

`render_prompt` 在 FIRST_FAILURE 分支（`elif reason is Reason.FIRST_FAILURE:`
块）之后、`else:`（PROGRESS）之前插入：

```python
    elif reason is Reason.STALLED:
        terminal = counts["total"] - counts["active"]
        body = (
            f"本会话有 {terminal}/{counts['total']} 个 Bohrium 作业已结束、"
            f"结果待处理；另有 {counts['unknown']} 个作业状态长时间无法查询"
            "（可能已被平台清理或接口持续异常）。请处理已有结果并检查这些作业。"
        )
```

`_process_session` 的 counts dict 增一键（`failed_total` 之后）：

```python
            "unknown": sum(u["unknown_count"] for u in session_units),
        }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/services/test_bohrium_completion_scheduler.py -v`
Expected: 全 PASS

- [ ] **Step 5: 提交**

```bash
git add src/services/bohrium_completion_scheduler.py tests/services/test_bohrium_completion_scheduler.py
git commit -m "feat(bohrium): 调度器增 STALLED 失速兜底——全失联且 pending 熟化即唤醒，常态上界不变"
```

---

### Task 7: 全量验证与验收口径核对

**Files:** 无新改动（只验证）

- [ ] **Step 1: 跑全部相关测试**

Run:
```bash
uv run pytest tests/services/test_bohrium_delivery_ack.py \
  tests/services/test_bohrium_jobs_wiring.py \
  tests/services/test_bohrium_completion_scheduler.py \
  tests/services/test_bohrium_poller.py \
  tests/test_agent_worker_snapshot_confirm.py \
  tests/test_agent_worker_delivery.py \
  tests/monitor/ tests/dao/ -v
```
Expected: 全 PASS（DAO 组无 `.env.test` 时 SKIP）

- [ ] **Step 2: 残留引用扫描**

Run: `git grep -n "query_session_pending_terminal"`
Expected: 无输出

- [ ] **Step 3: 验收口径逐条核对（对照 spec §7）**

1. 冗余唤醒消除 → Task 2/3（观察集 + 并集 ack）+ `test_record_poll_terminal_feeds_observed_set` + `test_confirm_acks_union_of_rows_and_observed`；
2. 跨会话不误吞 → Task 1 `test_mark_handled_by_job_keys_idempotent_and_session_scoped`；
3. 失速兜底时延 → Task 5/6 `test_scan_exposes_unknown_count_and_pending_age` + `test_decide_stalled_when_all_actives_unknown_and_pending_aged`；
4. 常态唤醒上界不变 → `test_decide_no_stalled_when_some_active_still_running` + 既有 `test_progress_count_bounded_by_segments_total_1000`；
5. 失败 run 不 ack → 既有 `test_failed_run_skips_confirm`（零改动通过即证）。

- [ ] **Step 4: 收尾**

全部通过后调用 superpowers:finishing-a-development-branch 技能决定合入方式。
