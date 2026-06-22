# Workspace Job Context Section Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 bohrium job 的 context 从纯 session 视角改成「workspace 观察视图 + session+workspace 交付视图」双语义——用户主动 query 注入跨 session 的 `workspace_jobs` 观察，`bohrium_completion` trigger run 注入当前 `session + workspace` 的 delivery section（含待交付 job 详情、复用 `snapshot.rows`），而 delivery ack 永远限定在「当前 session + 当前 workspace」（两条 ack 路径都收紧）。

**Architecture:** 两条数据通路。**交付通路**：DAO 的 snapshot/两条 ack/聚合/失败查询全部补 `workspace` 维度，`DeliverySnapshot` 加 `workspace` 字段（保留既有 `observed_terminal`），worker 拍 `session+workspace` scoped 快照、run 成功后 confirm；scheduler 聚合 key、Redis NX 占位、trigger、`get_first_pending_failed` 都补 workspace 分组。**观察/上下文通路**：context 契约从 `SessionJobs` 整体迁移为 `WorkspaceJobs`（新增 `workspace`/`recent_terminal_jobs` 字段与渲染），wiring 按 worker 算出的 `job_context_mode` 返回三种 read port——`session_workspace_delivery`（trigger，active 收紧到 workspace + pending 复用 `snapshot.rows`）、`workspace_observation`（user query，跨 session 三查询）、或空视图兜底；`build_bohrium_jobs_ports` **保留** `delivery_snapshot` 参数（ledger 取 `observed_terminal`、delivery port 取 `rows`），新增 `job_context_mode` 决定 read port 形态。**不新增任何索引**（§6.4）。

**Tech Stack:** Python 3.10+ / 同步 PyMySQL DAO（raw SQL）/ frozen dataclass 端口契约 / FastAPI 服务层 / Redis 协调 / pytest（DAO 层真库集成，service/context 层 mock）/ uv 环境。

**Spec:** `docs/superpowers/specs/2026-06-13-workspace-job-context-section-design.md`（2026-06-14 经 snapshot/observed_terminal 重构与一轮 code review 修订后的版本）

---

## 执行须知

- 测试命令一律走仓库 uv 环境：`uv run pytest ...`。
- **本计划文件与 spec 同属 `docs/`，绝对不进 git 提交**（项目规则 CLAUDE.md）。每个 Task 的 `git add` 只 add `src/`、`matmaster/`、`tests/` 下的代码文件，绝不 add `docs/`。
- **DAO 真库测试需要 `.env.test`**（`tests/dao/conftest.py` 在无 `.env.test` 时整组 SKIP，要求 MySQL ≥ 8.0.16、库名含 `_test`）。本地若无 `.env.test`，Task 1/2/3/5 中的 DAO 真库测试会 SKIP 而非 FAIL——这是预期；真正的红→绿验证在 CI（CI 跑 `pytest -n 8`；service 层测试别漏注入依赖以免偶发连真库，见 [project_ci_xdist_lazy_dao_flake]）。service 层与 context 层测试（mock/纯单元）本地必须真实红→绿。
- 分支：当前在 `feat/bohrium_job`，延续即可；也可在 `test` 最新基线上另开 `feat/workspace-job-context`。**绝不把 test 分支合并到任何分支**。
- **所有 `Edit` 前先 `Read` 目标方法/类核对当前空白与缩进**：本计划行号基于核验时的基线，给出的 old/new 片段相对缩进正确，但绝对缩进、`except`/闭括号边界以实际文件为准（尤其 `bohrium_completion_scheduler.py` 的 `tick`/`_process_session` 替换，须确认替换范围不吞掉原有 `except Exception:` 块）。
- 本任务是 spec §9 批准的功能迁移：**迁移类改动复用/重命名现有测试（符号替换，净测试数不增），仅对新功能（workspace 隔离、observation 查询、mode 分流）按 spec §9 新增测试**。
- **禁止用 prompt 文案内容做断言保护**：本计划不得新增任何 prompt 字符串内容断言；`tests/services/test_bohrium_completion_scheduler.py` 里既有的 `render_prompt` 文案测试、tick 测试里的 prompt substring 断言也在 Task 2 删除。后续只验证结构化调用、mode、workspace、ack 范围、delivery 参数等稳定边界。

## 与 spec 的对齐要点 / 关键设计决策（实现前确认，已按本计划锁定）

1. **DAO 与 ack 方法一律不改名，只加 `workspace` 参数 + `AND workspace = %s` 谓词**（遵循 spec §5.2「对齐当前真实模块，不新造命名」、§6.1 用原名）。涉及 `list_pending_terminal_snapshot`、`mark_handled_by_ids`、`mark_handled_by_job_keys`、`query_session_active`、`get_first_pending_failed`。`bohrium_delivery_ack.snapshot`/`confirm` 同样保名加签名。理由：模块/方法名已表达语义，原地加参数改动面最小、净代码量不增。

2. **`DeliverySnapshot` 加 `workspace` 字段并保留 `observed_terminal`**。当前 `DeliverySnapshot` 已有 `observed_terminal: set[tuple[bool, str]]`（run 内前台 poll 填充），`confirm` 已有两条 ack 路径。本计划**不得删除** observed_terminal——这正是 spec §5.2/§6.1 要求收紧 workspace 的第二条 ack 路径。

3. **`build_bohrium_jobs_ports` 保留 `delivery_snapshot` 参数，新增 `job_context_mode`**（spec §8.3）。ledger 仍从 `delivery_snapshot.observed_terminal` 取集合做 run 内填充；trigger 的 delivery read port 仍复用 `snapshot.rows`。observation 模式下 read port 与 snapshot 解耦，但**参数依旧保留**（ledger 与 delivery port 需要）。**不可**像初稿那样去掉该参数。

4. **`job_context_mode` 取值为 `"session_workspace_delivery"`（trigger）/ `"workspace_observation"`（user query）**，由 worker 按 `origin == "bohrium_completion"` 计算（spec §5.1）。**不存在 `"none"` 模式**；workspace 为空 / identity 缺失 / mode 不匹配时由 wiring 内的空视图 read port 兜底。trigger run **必须**组装 `session + workspace` delivery section（spec §5.3、§11 第一条风险：「实现时不可把 trigger 退回 none」），否则 worker 无条件 confirm 会盲 ack。

5. **不新增任何索引**（spec §6.4）。三个 ID 列各 VARCHAR(255) utf8mb4_bin，完整三列已逼近 InnoDB 索引 key 长度上限，任何含完整 ID 列 + workspace 的复合索引都会 `key too long`。workspace 过滤靠现有索引（`idx_session_pending` / `idx_session_active` / `idx_pending_scan` / `uk_owner_job_id`）定位 + SQL 精确等值谓词回表完成。**无索引 Task、无 migration 脚本**。

6. **`build_bohrium_jobs_ports` 的 `job_context_mode` 默认 `"session_workspace_delivery"`**（最小泄露：只看本 session+workspace，且无 snapshot 时 pending 为空）；**`run_agent` 的 `job_context_mode` 默认 `"workspace_observation"`**（用户 query 是主路径）。worker 永远显式传，默认值仅服务测试与非 worker 入口。

7. **`WorkspaceJobsQuery` 保留 `session_id` 字段**。observation port 不使用它（用构造时注入的 `workspace` 跨 session 查询），保留只为最小化 `ContextAssembler` 改动（assembler 仍只有 session_id 可传）。

8. **`detail_limit` 沿用现有 env `BOHRIUM_DELIVERY_DETAIL_LIMIT`（默认 20）**，不新增配置位（符合 [feedback_prefer_subclass_over_config_flags] 别摊配置位）。delivery 模式下 `detail_limit` 来自 `snapshot.detail_limit`；observation 模式下 port 独立从 env 读，同时作为 pending/recent 查询 `limit` 与渲染 `detail_limit`。

9. **本阶段不改 `render_prompt` 与 `_DELIVERY_SCOPE_SUFFIX` 的生产文案**。spec §3/§5.3 把「trigger prompt 自带完整 job 列表替代 section」明确划归后续 prompt 设计；现状「概要 + suffix 指向 delivery section」仍有效（trigger 仍组装 section）。本计划只删除 prompt 文案内容断言，不新增新的 prompt 字符串保护测试。

## 任务依赖与绿色性

自底向上、每个 Task 结束时全绿（import 不断、相关测试通过）。

| Task | 通路 | 结束状态 |
|---|---|---|
| 1 | 交付 | DAO snapshot/两条 ack + `DeliverySnapshot`（含 workspace、保留 observed_terminal）、`snapshot`/`confirm`、worker snapshot 调用全部 `session+workspace` scoped |
| 2 | 交付 | DAO `scan_delivery_units` 按 workspace 分组（保留全部聚合字段）、`get_first_pending_failed` 加 workspace、scheduler 分组/Redis key/trigger/失败查询补 workspace |
| 3 | 观察 | DAO 新增 workspace observation 三查询（纯新增，暂无调用方） |
| 4 | 观察 | `SessionJobs` 全量迁移为 `WorkspaceJobs`（+字段 +渲染），wiring read port 仅改名不改逻辑 |
| 5 | 观察 | wiring 三种 read port + `job_context_mode`，`query_session_active` 加 workspace，run_agent/worker 接线（保留 delivery_snapshot、新增 mode、worker 读 origin） |
| 6 | — | 全量回归 + 人工验证 |

## 文件结构总览

| 文件 | 改动 | Task |
|---|---|---|
| `src/dao/bohrium_jobs_table.py` | `list_pending_terminal_snapshot`/`mark_handled_by_ids`/`mark_handled_by_job_keys` 各加 `workspace` 参数+谓词 | 1 |
| `src/services/bohrium_delivery_ack.py` | `DeliverySnapshot` +`workspace` 字段（保留 observed_terminal）；`snapshot(*, workspace)` 短路+构造；`confirm` 两条路径传 `workspace=snap.workspace` | 1 |
| `src/worker/agent_worker.py` | snapshot 调用传 `workspace`（Task 1）；读 `origin`、算 `job_context_mode`、run_agent kwargs 加 `job_context_mode`（保留 delivery_snapshot）（Task 5） | 1, 5 |
| `src/dao/bohrium_jobs_table.py` | `scan_delivery_units` 内层 DISTINCT/JOIN ON/`MIN(workspace)`→`t.workspace`/GROUP BY/ORDER BY 补 workspace（保留全部聚合字段）；`get_first_pending_failed` 加 workspace | 2 |
| `src/services/bohrium_completion_scheduler.py` | groups key、`_process_session` 签名、Redis NX key、trigger workspace、`get_first_pending_failed` 调用补 workspace | 2 |
| `src/dao/bohrium_jobs_table.py` | 新增 `query_workspace_active` / `query_workspace_pending_terminal` / `query_workspace_recent_terminal`（Task 3）；`query_session_active` 加 workspace（Task 5） | 3, 5 |
| `matmaster/context/ports.py` | `SessionJobs`→`WorkspaceJobs`（+`workspace`/`recent_terminal_jobs`）、`SessionJobsQuery`→`WorkspaceJobsQuery`、`SessionJobsPort`→`WorkspaceJobsPort`（`load_workspace_jobs`）、`ContextAssemblyPorts.session_jobs`→`workspace_jobs` | 4 |
| `matmaster/context/sections.py` | `SectionOrder.SESSION_JOBS`→`WORKSPACE_JOBS` | 4 |
| `matmaster/context/sources/session_jobs.py`→`workspace_jobs.py` | `SessionJobsSource`→`WorkspaceJobsSource`，渲染 workspace header + recent_terminal 组，tag/key→`workspace_jobs` | 4 |
| `matmaster/context/compositions.py` | import、`ContextCompositionInputs.workspace_jobs`、`_step_workspace_jobs` | 4 |
| `matmaster/context/assembly.py` | import、`_load_workspace_jobs_or_empty`、`workspace_jobs=` | 4 |
| `matmaster/types/runtime_ports.py` | import、`AgentRunPorts.workspace_jobs` | 4 |
| `matmaster/core/runtime_context_assembly.py` | import、`_EmptyWorkspaceJobsPort`、`workspace_jobs=` | 4 |
| `src/services/bohrium_jobs_wiring.py` | Task 4：import + read port 改名（`load_workspace_jobs`/返回 `WorkspaceJobs`）；Task 5：`query_session_active` 加 workspace、拆 delivery/observation/empty 三 port、`build_bohrium_jobs_ports` 加 `job_context_mode`（保留 delivery_snapshot） | 4, 5 |
| `src/services/agent_run_service.py` | Task 4：`workspace_jobs=` 装配点；Task 5：`run_agent` 加 `job_context_mode`（保留 delivery_snapshot）、build 调用传 mode | 4, 5 |

测试文件改动随各 Task 列出（含 2 个物理改名：`tests/matmaster/context/sources/test_session_jobs.py`、`tests/matmaster/test_runtime_context_assembly_session_jobs.py`）。

---

### Task 1: DAO 交付查询 + DeliverySnapshot 的 session+workspace 化（两条 ack 路径）

把 delivery 的「快照」与「两条 ack」三个 DAO 方法各加 `workspace` 参数与谓词；`DeliverySnapshot` 增加 `workspace` 字段（**保留** `observed_terminal`）；`snapshot`/`confirm` 同步（snapshot 加 `if not workspace: return None`、构造带 workspace；confirm 两条路径都传 `workspace=snap.workspace`）；worker 拍快照时传 payload 的 workspace。改完后 ack 范围从「整个 session」收紧为「当前 session + 当前 workspace」，两条路径对称。

**Files:**
- Modify: `src/dao/bohrium_jobs_table.py`（`list_pending_terminal_snapshot` 414-435、`mark_handled_by_ids` 445-480、`mark_handled_by_job_keys` 482-520）
- Modify: `src/services/bohrium_delivery_ack.py`（`DeliverySnapshot` 22-37、`snapshot` 40-93、`confirm` 96-123）
- Modify: `src/worker/agent_worker.py`（snapshot 调用 417）
- Test: `tests/dao/test_bohrium_jobs_delivery.py`
- Test: `tests/dao/test_bohrium_jobs_table.py`
- Test: `tests/services/test_bohrium_delivery_ack.py`
- Test: `tests/test_agent_worker_snapshot_confirm.py`
- Test: `tests/services/test_bohrium_jobs_wiring.py`（`_snapshot` helper 同步 `DeliverySnapshot` 新字段）

- [ ] **Step 1.1: 写失败测试 —— ack service 层**

`tests/services/test_bohrium_delivery_ack.py`。先 `Read` 全文核对，再按下列规则改（helper `_row` 已含 `workspace="/share/project"`，`_sessions` 不变）：

**规则 A（snapshot 调用补 workspace）**：把以下 9 个测试里每处 `bohrium_delivery_ack.snapshot("sess-1", ...)` 调用，在 `"sess-1",` 之后补一行 `workspace="/share/project",`：`test_snapshot_holds_full_rows`(38-56)、`test_snapshot_reads_detail_limit_from_env`(59-66)、`test_snapshot_empty_rows_returns_object_not_none`(69-78)、`test_snapshot_returns_none_without_org_binding`(81-89)、`test_snapshot_rows_query_failure_degrades_to_empty_rows`(92-99)、`test_confirm_acks_exactly_snapshot_row_ids`(102-117)、`test_snapshot_detail_limit_defaults_when_env_unset`(135-142)、`test_snapshot_returns_none_when_session_missing`(145-153)、`test_snapshot_identity_lookup_failure_returns_none`(156-162)。

**规则 B（DeliverySnapshot 直接构造补 workspace）**：`test_confirm_propagates_failure_to_caller`(120-132)、`test_confirm_acks_union_of_rows_and_observed`(165-186)、`test_confirm_skips_dao_calls_for_empty_sets`(189-196) 里每处 `DeliverySnapshot(... session_id=..., ` 构造，在 `session_id=` 那行后补 `workspace="/share/project",`。

**规则 C（snapshot 调用断言补 workspace）**：`test_snapshot_holds_full_rows` 末尾对 `snap` 增断言 `assert snap.workspace == "/share/project"`；若该测试断言了 `table.list_pending_terminal_snapshot.call_args.kwargs`，把期望 dict 补 `"workspace": "/share/project"`。

**规则 D（confirm 断言补 workspace）**：`test_confirm_acks_exactly_snapshot_row_ids` 对 `table.mark_handled_by_ids.call_args.kwargs` 的期望补 `"workspace": "/share/project"`；`test_confirm_acks_union_of_rows_and_observed` 对 `mark_handled_by_ids` 与 `mark_handled_by_job_keys` 两处 `call_args.kwargs` 期望都补 `"workspace": "/share/project"`。

**新增测试**（workspace 缺失时短路、不查身份不查库）追加到文件末尾：

```python
def test_snapshot_returns_none_without_workspace():
    table = MagicMock()
    sessions = _sessions()
    assert (
        bohrium_delivery_ack.snapshot(
            "sess-1", workspace=None, sessions_service=sessions, jobs_table=table
        )
        is None
    )
    sessions.get_session.assert_not_called()
    table.list_pending_terminal_snapshot.assert_not_called()
```

- [ ] **Step 1.2: 写失败测试 —— DAO 真库层**

`tests/dao/test_bohrium_jobs_delivery.py`。先 `Read` 全文核对 helper（`_register_session(conn, *, ...)`、`_seed_job(jobs_table, *, ..., job_id="101")` 硬编码 `workspace="/share/project"`、`_shift_terminal_at(conn, *, ...)`）。

**规则**：把 `test_snapshot_returns_full_rows_failed_first_with_fields`(149-183)、`test_mark_handled_by_ids_idempotent_and_chunked`(186-221)、`test_mark_handled_by_job_keys_idempotent_and_session_scoped`(309-361) 中每处 `jobs_table.list_pending_terminal_snapshot(...)`、`jobs_table.mark_handled_by_ids(...)`、`jobs_table.mark_handled_by_job_keys(...)` 调用补 `workspace="/share/project"` 关键字参数。

`tests/dao/test_bohrium_jobs_table.py` 也有旧签名调用，必须在本 Task 同步迁移，避免 Task 1 后全量测试才暴露 `TypeError`：

- `test_lost_job_enters_pending_terminal_queue` 中的 `jobs_table.list_pending_terminal_snapshot(user_id="user-1", org_id="org-1", session_id="sess-1")` 补 `workspace="/share/project"`。

在文件末尾新增三个 workspace 隔离测试（`_seed_job` 不接 workspace 参数，另一 workspace 用 `insert_submitted` + `apply_poll` 直插）：

```python
def test_snapshot_excludes_other_workspace(jobs_table, sessions_shadow):
    _register_session(sessions_shadow)
    _seed_job(jobs_table, job_id="301", status="finished")  # /share/project
    jobs_table.insert_submitted(
        session_id="sess-1",
        invocation_id="inv-1",
        spawn_id=None,
        user_id="u1",
        org_id="o1",
        job_id="302",
        job_name="name-302",
        project_id=42,
        sandbox=False,
        input_dir="data/in",
        workspace="/share/other",
    )
    jobs_table.apply_poll(
        user_id="u1",
        org_id="o1",
        sandbox=False,
        job_id="302",
        status="finished",
        is_terminal=True,
        backoff_seconds=30,
    )

    rows = jobs_table.list_pending_terminal_snapshot(
        user_id="u1", org_id="o1", session_id="sess-1", workspace="/share/project"
    )
    assert [r["job_id"] for r in rows] == ["301"]


def test_mark_handled_by_ids_does_not_cross_workspace(jobs_table, sessions_shadow):
    _register_session(sessions_shadow)
    _seed_job(jobs_table, job_id="401", status="finished")  # /share/project
    rows = jobs_table.list_pending_terminal_snapshot(
        user_id="u1", org_id="o1", session_id="sess-1", workspace="/share/project"
    )
    ids = [r["id"] for r in rows]
    # 用错 workspace ack：一行都不命中
    affected = jobs_table.mark_handled_by_ids(
        user_id="u1",
        org_id="o1",
        session_id="sess-1",
        workspace="/share/other",
        row_ids=ids,
    )
    assert affected == 0
    still = jobs_table.list_pending_terminal_snapshot(
        user_id="u1", org_id="o1", session_id="sess-1", workspace="/share/project"
    )
    assert [r["id"] for r in still] == ids


def test_mark_handled_by_job_keys_does_not_cross_workspace(jobs_table, sessions_shadow):
    _register_session(sessions_shadow)
    _seed_job(jobs_table, job_id="501", status="finished")  # /share/project
    # 正确 workspace + 错 job_key 无效；错 workspace + 对 job_key 也无效
    affected_wrong_ws = jobs_table.mark_handled_by_job_keys(
        user_id="u1",
        org_id="o1",
        session_id="sess-1",
        workspace="/share/other",
        job_keys=[(False, "501")],
    )
    assert affected_wrong_ws == 0
    affected_ok = jobs_table.mark_handled_by_job_keys(
        user_id="u1",
        org_id="o1",
        session_id="sess-1",
        workspace="/share/project",
        job_keys=[(False, "501")],
    )
    assert affected_ok == 1
```

- [ ] **Step 1.3: 写失败测试 —— worker 层 + wiring helper**

`tests/test_agent_worker_snapshot_confirm.py`：`_run_one_round` 的内嵌 `fake_snapshot`（约 63 行）签名改为接受 keyword `workspace`，并记录：

```python
    def fake_snapshot(session_id, *, workspace=None):
        calls.append("snapshot")
        received["snapshot_workspace"] = workspace
        return snapshot_obj
```

`test_success_path_orders_snapshot_run_confirm_release`(91-96) 末尾追加一行断言（payload 无 `workspace` 字段，worker 传 `workspace=None`）：

```python
    assert received["snapshot_workspace"] is None
```

`tests/services/test_bohrium_jobs_wiring.py`：`_snapshot` helper(216-225) 的 `DeliverySnapshot(...)` 构造加 `workspace` 字段（本 Task 起该字段必填）：

```python
def _snapshot(rows):
    from src.services.bohrium_delivery_ack import DeliverySnapshot

    return DeliverySnapshot(
        user_id="u",
        org_id="o",
        session_id="s",
        workspace="/share/project",
        rows=tuple(rows),
        detail_limit=20,
    )
```

- [ ] **Step 1.4: 跑测试确认失败**

Run: `uv run pytest tests/services/test_bohrium_delivery_ack.py tests/test_agent_worker_snapshot_confirm.py -q`
Expected: FAIL —— `TypeError`：`snapshot()` 不接受 `workspace`、`DeliverySnapshot` 无 `workspace` 字段；confirm 断言缺 workspace。

（DAO 真库：`uv run pytest tests/dao/test_bohrium_jobs_delivery.py -q`，有 `.env.test` 时 FAIL 于 `list_pending_terminal_snapshot()` 不接受 `workspace`；无则 SKIP。）

- [ ] **Step 1.5: 实现 DAO 三方法补 workspace 参数与谓词**

`src/dao/bohrium_jobs_table.py`，先 `Read` 这三个方法核对当前文本，再做以下精确编辑（不改名、不碰 SELECT/ORDER BY 子句）。

`list_pending_terminal_snapshot`（414-435）三处：

签名行 `self, *, user_id: str, org_id: str, session_id: str` → 末尾加 `, workspace: str`。

WHERE 块：
```python
            WHERE user_id = %s AND org_id = %s AND session_id = %s
              AND terminal_at IS NOT NULL AND handled_at IS NULL
            ORDER BY
```
改为：
```python
            WHERE user_id = %s AND org_id = %s AND session_id = %s
              AND workspace = %s
              AND terminal_at IS NOT NULL AND handled_at IS NULL
            ORDER BY
```

execute 行 `cur.execute(sql, (user_id, org_id, session_id))` → `cur.execute(sql, (user_id, org_id, session_id, workspace))`。

`mark_handled_by_ids`（445-480）三处：

签名在 `session_id: str,` 后插入 `workspace: str,`。

WHERE 块：
```python
                    WHERE user_id = %s AND org_id = %s AND session_id = %s
                      AND id IN ({placeholders})
                      AND terminal_at IS NOT NULL
                      AND handled_at IS NULL
```
改为：
```python
                    WHERE user_id = %s AND org_id = %s AND session_id = %s
                      AND workspace = %s
                      AND id IN ({placeholders})
                      AND terminal_at IS NOT NULL
                      AND handled_at IS NULL
```

execute 元组 `(user_id, org_id, session_id, *chunk),` → `(user_id, org_id, session_id, workspace, *chunk),`。

`mark_handled_by_job_keys`（482-520）三处：

签名在 `session_id: str,` 后插入 `workspace: str,`。

WHERE 块：
```python
                    WHERE user_id = %s AND org_id = %s AND session_id = %s
                      AND (sandbox, job_id) IN ({placeholders})
                      AND terminal_at IS NOT NULL
                      AND handled_at IS NULL
```
改为：
```python
                    WHERE user_id = %s AND org_id = %s AND session_id = %s
                      AND workspace = %s
                      AND (sandbox, job_id) IN ({placeholders})
                      AND terminal_at IS NOT NULL
                      AND handled_at IS NULL
```

execute 元组 `(user_id, org_id, session_id, *flat),` → `(user_id, org_id, session_id, workspace, *flat),`。

- [ ] **Step 1.6: 实现 DeliverySnapshot + snapshot/confirm 补 workspace**

`src/services/bohrium_delivery_ack.py`。

`DeliverySnapshot`（22-37）字段块，在 `session_id: str` 后、`rows:` 前插入一行 `workspace: str`：
```python
    user_id: str
    org_id: str
    session_id: str
    workspace: str
    rows: tuple[dict[str, Any], ...]
    detail_limit: int
    observed_terminal: set[tuple[bool, str]] = field(default_factory=set)
```

`snapshot`（40-93）整体替换为（加 `workspace: str | None` 必填 kw、开头短路、构造带 workspace、DAO 调用带 workspace；身份解析与 rows 降级逻辑原样保留）：

```python
def snapshot(
    session_id: str,
    *,
    workspace: str | None,
    sessions_service: Any | None = None,
    jobs_table: Any | None = None,
) -> DeliverySnapshot | None:
    """解析身份并查询全量 pending terminal rows，限定 session + workspace。

    workspace 为空 → None（无有效 ack scope，与 identity 缺失对称）。
    身份不可解析（session 缺失 / org 未绑定 / 查库异常）→ None：既无法 ack 也
    无法渲染，未交付行下轮重投。rows 查询失败但身份正常 → 空 rows snapshot。
    """
    if not workspace:
        return None
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
            user_id=user_id,
            org_id=org_id,
            session_id=session_id,
            workspace=workspace,
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "bohrium delivery snapshot failed session_id=%s workspace=%s",
            session_id,
            workspace,
            exc_info=True,
        )
    return DeliverySnapshot(
        user_id=user_id,
        org_id=org_id,
        session_id=session_id,
        workspace=workspace,
        rows=tuple(rows),
        detail_limit=env_int("BOHRIUM_DELIVERY_DETAIL_LIMIT", 20),
    )
```

`confirm`（96-123）两处 DAO 调用各加 `workspace=snap.workspace`（在 `session_id=snap.session_id,` 后）：

```python
def confirm(snap: DeliverySnapshot, *, jobs_table: Any | None = None) -> int:
    """按 snapshot rows 与前台观察集并集批量 ack，限定 session + workspace；
    空集短路，异常向上抛。

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
            workspace=snap.workspace,
            row_ids=tuple(int(j["id"]) for j in snap.rows),
        )
    if snap.observed_terminal:
        affected += table.mark_handled_by_job_keys(
            user_id=snap.user_id,
            org_id=snap.org_id,
            session_id=snap.session_id,
            workspace=snap.workspace,
            job_keys=tuple(snap.observed_terminal),
        )
    return affected
```

- [ ] **Step 1.7: 实现 worker snapshot 调用传 workspace**

`src/worker/agent_worker.py` 第 417 行：

```python
            delivery_snapshot = bohrium_delivery_ack.snapshot(session_id)
```

改为：

```python
            delivery_snapshot = bohrium_delivery_ack.snapshot(
                session_id, workspace=workspace
            )
```

（`workspace` 变量已在 350-353 行从 payload 解析，作用域可见。本 Task 不动 run_agent kwargs，仍传 `delivery_snapshot`。）

- [ ] **Step 1.8: 跑测试确认通过**

Run: `uv run pytest tests/services/test_bohrium_delivery_ack.py tests/test_agent_worker_snapshot_confirm.py tests/services/test_bohrium_jobs_wiring.py -q`
Expected: PASS（全部）。
Run（有 `.env.test`）: `uv run pytest tests/dao/test_bohrium_jobs_delivery.py tests/dao/test_bohrium_jobs_table.py -q` → PASS；否则 SKIP。

- [ ] **Step 1.9: Commit**

```bash
git add src/dao/bohrium_jobs_table.py src/services/bohrium_delivery_ack.py src/worker/agent_worker.py tests/dao/test_bohrium_jobs_delivery.py tests/dao/test_bohrium_jobs_table.py tests/services/test_bohrium_delivery_ack.py tests/test_agent_worker_snapshot_confirm.py tests/services/test_bohrium_jobs_wiring.py
git commit -m "feat(bohrium): scope delivery snapshot and both ack paths to session+workspace"
```

---

### Task 2: scan_delivery_units + get_first_pending_failed + scheduler 的 workspace 分组

`scan_delivery_units` 当前用 `MIN(t.workspace)` 把同 session 不同 workspace 的行揉成一个聚合单元（spec §11 病灶）；`get_first_pending_failed` WHERE 不含 workspace（review finding 3）。本 Task 把 scan 改成按 workspace 分组（**保留 `unknown_count` / `oldest_pending_age_seconds` / `max_pending_terminal_id` 等全部聚合字段**），给 `get_first_pending_failed` 补 workspace，并把 scheduler 的分组 key、`_process_session` 签名、Redis NX 占位、trigger workspace、失败查询调用都补 workspace 维度。

**Files:**
- Modify: `src/dao/bohrium_jobs_table.py`（`scan_delivery_units` 329-390、`get_first_pending_failed` 522-539）
- Modify: `src/services/bohrium_completion_scheduler.py`（分组 186-189、派发 192-201、`_process_session` 224-232、Redis key 268-269、`get_first_pending_failed` 调用 291-297、trigger 301-307）
- Test: `tests/dao/test_bohrium_jobs_delivery.py`
- Test: `tests/services/test_bohrium_completion_scheduler.py`

- [ ] **Step 2.1: 写失败测试 —— DAO scan 按 workspace 切分 + get_first_pending_failed 加 workspace**

`tests/dao/test_bohrium_jobs_delivery.py` 末尾追加：

```python
def test_scan_splits_same_session_by_workspace(jobs_table, sessions_shadow):
    _register_session(sessions_shadow)
    # 同 session 同 invocation，两个 workspace 各一终态未交付
    _seed_job(jobs_table, inv="inv-1", job_id="601", status="finished")  # /share/project
    jobs_table.insert_submitted(
        session_id="sess-1",
        invocation_id="inv-1",
        spawn_id=None,
        user_id="u1",
        org_id="o1",
        job_id="602",
        job_name="name-602",
        project_id=42,
        sandbox=False,
        input_dir="data/in",
        workspace="/share/other",
    )
    jobs_table.apply_poll(
        user_id="u1",
        org_id="o1",
        sandbox=False,
        job_id="602",
        status="finished",
        is_terminal=True,
        backoff_seconds=30,
    )

    units = jobs_table.scan_delivery_units(limit=10)

    workspaces = sorted(u["workspace"] for u in units)
    assert workspaces == ["/share/other", "/share/project"]
    for u in units:
        assert u["pending_terminal"] == 1  # 每个 unit 只统计自己 workspace 的行


def test_get_first_pending_failed_scoped_by_workspace(jobs_table, sessions_shadow):
    _register_session(sessions_shadow)
    _seed_job(jobs_table, inv="inv-1", job_id="701", status="failed")  # /share/project
    jobs_table.insert_submitted(
        session_id="sess-1",
        invocation_id="inv-1",
        spawn_id=None,
        user_id="u1",
        org_id="o1",
        job_id="702",
        job_name="name-702",
        project_id=42,
        sandbox=False,
        input_dir="data/in",
        workspace="/share/other",
    )
    jobs_table.apply_poll(
        user_id="u1",
        org_id="o1",
        sandbox=False,
        job_id="702",
        status="failed",
        is_terminal=True,
        backoff_seconds=30,
    )

    row_other = jobs_table.get_first_pending_failed(
        user_id="u1",
        org_id="o1",
        session_id="sess-1",
        workspace="/share/other",
        invocation_key="inv-1",
    )
    assert row_other is not None and row_other["job_id"] == "702"
    row_project = jobs_table.get_first_pending_failed(
        user_id="u1",
        org_id="o1",
        session_id="sess-1",
        workspace="/share/project",
        invocation_key="inv-1",
    )
    assert row_project is not None and row_project["job_id"] == "701"
```

- [ ] **Step 2.2: 写失败测试 / 删除 prompt 文案断言 —— scheduler Redis key、workspace 分组、失败查询**

`tests/services/test_bohrium_completion_scheduler.py`（helper `_unit` 默认 `workspace="/share/p"`，`_FakeStream.trigger_run` 记录全部 kw，`_FakeJobsTable.get_first_pending_failed` 记录 kw 到 `first_failed_calls`）。

先删除既有 prompt 文案内容保护：

- 从 import 列表删除 `render_prompt`。
- 删除 `# ---------- render_prompt ----------` 到 `# ---------- tick 编排（假对象注入） ----------` 之间整段内容，包括 `_SUFFIX` 和所有 `test_render_*` 测试。
- 删除 tick 测试中对 `stream.calls[0]["prompt"]` 的 substring 断言，例如 `全部 Bohrium 作业已结束`、`j-9`、`无法查询` 这类 prompt 文案内容断言。保留对 `trigger_run` 参数、`delivery`、workspace、Redis key、`get_first_pending_failed` 调用参数的断言。

`test_tick_merges_session_units_single_trigger_with_primary_reason`(311-339) 两个 unit 默认同 `workspace="/share/p"`，分组后仍合并为一次 trigger，但 Redis key 现含 workspace。把其 Redis key 断言行改为：

```python
    assert redis.calls[0]["key"] == "bohrium_delivery:u1:o1:s1:/share/p:12"
```

同时删除该测试里的 prompt 文案断言行，只保留 `delivery` 断言。

`test_tick_first_failure_fetches_job_info_into_prompt`(342-360) 重命名为 `test_tick_first_failure_fetches_scoped_job_info`，并把 `table.first_failed_calls` 的期望整体改为带 workspace 的结构化调用参数；删除该测试里的 prompt 文案断言：

```python
    assert table.first_failed_calls == [
        {
            "user_id": "u1",
            "org_id": "o1",
            "session_id": "s1",
            "workspace": "/share/p",
            "invocation_key": "inv-1",
        }
    ]
    assert stream.calls[0]["delivery"] == DeliverySpec(notify=False)
```

`test_tick_stalled_unit_triggers_without_notify` 删除 prompt 文案断言，只保留 `summary["triggered"] == 1` 与 `delivery == DeliverySpec(notify=False)` 等结构性断言。

在 `test_tick_merges_session_units_single_trigger_with_primary_reason` 之后新增「同 session 不同 workspace 各自独立占位/触发」：

```python
def test_tick_separates_workspaces_into_distinct_reservations():
    units = [
        _unit(
            workspace="/share/a",
            active=0,
            pending_terminal=1,
            succeeded=1,
            total=1,
            max_pending_terminal_id=5,
        ),
        _unit(
            workspace="/share/b",
            active=0,
            pending_terminal=1,
            succeeded=1,
            total=1,
            max_pending_terminal_id=9,
        ),
    ]
    sched, _, _, redis, stream = _scheduler(units)

    summary = sched.tick()

    # 两个 workspace 各占一次位、各触发一次（fake sessions 恒 idle）
    assert summary["triggered"] == 2
    assert {c["workspace"] for c in stream.calls} == {"/share/a", "/share/b"}
    assert sorted(c["key"] for c in redis.calls) == [
        "bohrium_delivery:u1:o1:s1:/share/a:5",
        "bohrium_delivery:u1:o1:s1:/share/b:9",
    ]
```

- [ ] **Step 2.3: 跑测试确认失败**

Run: `uv run pytest tests/services/test_bohrium_completion_scheduler.py -q`
Expected: FAIL —— Redis key 不含 workspace（现为 `bohrium_delivery:u1:o1:s1:12`）；`first_failed_calls[0]` 无 `workspace` 键；新测试因同 session 两 workspace 仍被合并而 `triggered==1`。

- [ ] **Step 2.4: 实现 scan_delivery_units 按 workspace 分组（保留全部聚合字段）**

`src/dao/bohrium_jobs_table.py`，先 `Read` `scan_delivery_units`（329-390）核对，再把其 `sql = f"""..."""` 块整体替换为下面这版——相对现状改四处：`MIN(t.workspace) AS workspace` → `t.workspace AS workspace`、内层 DISTINCT 加 `workspace`、JOIN ON 加 `AND pending.workspace = t.workspace`、GROUP BY 与 ORDER BY 加 workspace；`unknown_count` / `oldest_pending_age_seconds` / `max_pending_terminal_id` / `first_pending_terminal_at` 等聚合字段全部保留：

```python
        sql = f"""
            SELECT
                t.user_id,
                t.org_id,
                t.session_id,
                COALESCE(t.invocation_id, '')                        AS invocation_key,
                t.workspace                                          AS workspace,
                COUNT(*)                                             AS total,
                SUM(t.terminal_at IS NULL)                           AS active,
                SUM(t.terminal_at IS NOT NULL
                    AND t.handled_at IS NULL)                        AS pending_terminal,
                SUM(t.status IN ({_SQL_FAILURE}))                    AS failed_total,
                SUM(t.status IN ({_SQL_FAILURE})
                    AND t.handled_at IS NOT NULL)                    AS failed_handled,
                SUM(t.status = 'finished')                           AS succeeded,
                SUM(t.status = 'unknown')                            AS unknown_count,
                TIMESTAMPDIFF(SECOND,
                    MIN(CASE WHEN t.terminal_at IS NOT NULL
                             AND t.handled_at IS NULL
                        THEN t.terminal_at END),
                    NOW())                                           AS oldest_pending_age_seconds,
                MAX(t.terminal_at)                                   AS max_terminal_at,
                MAX(CASE WHEN t.terminal_at IS NOT NULL AND t.handled_at IS NULL
                         THEN t.id END)                              AS max_pending_terminal_id,
                MIN(CASE WHEN t.terminal_at IS NOT NULL AND t.handled_at IS NULL
                         THEN t.terminal_at END)                     AS first_pending_terminal_at
            FROM {self.table_name} t
            JOIN (
                SELECT DISTINCT user_id, org_id, session_id, workspace,
                       COALESCE(invocation_id, '') AS invocation_key
                FROM {self.table_name}
                WHERE terminal_at IS NOT NULL AND handled_at IS NULL
            ) pending
              ON pending.user_id        = t.user_id
             AND pending.org_id         = t.org_id
             AND pending.session_id     = t.session_id
             AND pending.workspace      = t.workspace
             AND pending.invocation_key = COALESCE(t.invocation_id, '')
            WHERE EXISTS (
                SELECT 1 FROM evo_chat_sessions s
                WHERE s.session_id = t.session_id
                  AND s.user_id    = t.user_id
                  AND s.org_id     = t.org_id
            )
            GROUP BY t.user_id, t.org_id, t.session_id, t.workspace,
                     COALESCE(t.invocation_id, '')
            HAVING pending_terminal > 0
            ORDER BY first_pending_terminal_at ASC, t.user_id ASC, t.org_id ASC,
                     t.session_id ASC, t.workspace ASC, invocation_key ASC
            LIMIT %s
        """
```

（`_to_delivery_unit` 已把 `row["workspace"]` 放进 unit，不必动。）

- [ ] **Step 2.5: 实现 get_first_pending_failed 加 workspace**

`src/dao/bohrium_jobs_table.py`，`get_first_pending_failed`（522-539）三处：

签名 `self, *, user_id: str, org_id: str, session_id: str, invocation_key: str` → 在 `session_id: str,` 后插入 `workspace: str,`（即 `..., session_id: str, workspace: str, invocation_key: str`）。

WHERE 块：
```python
            WHERE user_id = %s AND org_id = %s AND session_id = %s
              AND COALESCE(invocation_id, '') = %s
```
改为：
```python
            WHERE user_id = %s AND org_id = %s AND session_id = %s
              AND workspace = %s
              AND COALESCE(invocation_id, '') = %s
```

execute 参数元组在 `session_id` 后、`invocation_key` 前补 `workspace`（与新签名顺序一致）。先 `Read` 确认该方法 execute 行的精确写法后对应补入。

- [ ] **Step 2.6: 实现 scheduler 的 workspace 分组**

`src/services/bohrium_completion_scheduler.py`。

分组与派发（186-201）整体替换为（分组键加 workspace，解包加 workspace，传给 `_process_session`）：

```python
        groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
        for unit in units:
            key = (
                unit["user_id"],
                unit["org_id"],
                unit["session_id"],
                unit["workspace"],
            )
            groups.setdefault(key, []).append(unit)

        failed_sessions: list[str] = []
        for (user_id, org_id, session_id, workspace), session_units in groups.items():
            try:
                self._process_session(
                    user_id,
                    org_id,
                    session_id,
                    workspace,
                    session_units,
                    summary,
                    failed_sessions,
                )
```

（保留 `for` 之后现状的 `except Exception:` 错误处理块原样。）

`_process_session` 签名（224-232）在 `session_id: str,` 后插入 `workspace: str,`：

```python
    def _process_session(
        self,
        user_id: str,
        org_id: str,
        session_id: str,
        workspace: str,
        session_units: list[dict[str, Any]],
        summary: dict[str, int],
        failed_sessions: list[str],
    ) -> None:
```

Redis NX key（268-269）替换为（key 含 workspace，使同 session 不同 workspace 各自独立占位）：

```python
        max_row_id = max(u["max_pending_terminal_id"] for u in session_units)
        key = (
            f"{_RESERVATION_KEY_PREFIX}{user_id}:{org_id}:{session_id}:"
            f"{workspace}:{max_row_id}"
        )
```

`get_first_pending_failed` 调用（291-297）补 `workspace=workspace`：

```python
        first_failed = None
        if primary_reason is Reason.FIRST_FAILURE:
            first_failed = self._jobs_table.get_first_pending_failed(
                user_id=user_id,
                org_id=org_id,
                session_id=session_id,
                workspace=workspace,
                invocation_key=primary_unit["invocation_key"],
            )
```

trigger（301-307）把 `workspace=primary_unit["workspace"]` 改为 `workspace=workspace`（用分组键 workspace；分组后同组 unit 的 workspace 已一致，但用分组键更显式）：

```python
        res = self._stream_service.trigger_run(
            session_id,
            prompt,
            origin="bohrium_completion",
            workspace=workspace,
            delivery=DeliverySpec(notify=primary_reason is Reason.FINAL),
        )
```

- [ ] **Step 2.7: 跑测试确认通过**

Run: `uv run pytest tests/services/test_bohrium_completion_scheduler.py -q`
Expected: PASS。
Run（有 `.env.test`）: `uv run pytest tests/dao/test_bohrium_jobs_delivery.py -q` → PASS；否则 SKIP。

- [ ] **Step 2.8: Commit**

```bash
git add src/dao/bohrium_jobs_table.py src/services/bohrium_completion_scheduler.py tests/dao/test_bohrium_jobs_delivery.py tests/services/test_bohrium_completion_scheduler.py
git commit -m "feat(bohrium): group delivery scan/scheduler/first-failure by workspace"
```

---

### Task 3: DAO workspace observation 三查询

新增三个跨 session、按 `user_id+org_id+workspace` 的观察查询，给 Task 5 的 observation read port 供数据。纯新增，本 Task 无生产调用方，靠 DAO 真库测试验证。复用 `_AGENT_COLUMNS` / `_SQL_ACTIVE` / `_to_agent_job`，与现有 `query_session_active` 同风格。

**Files:**
- Modify: `src/dao/bohrium_jobs_table.py`（在 `query_session_active` 213 之后插入三个新方法）
- Test: `tests/dao/test_bohrium_jobs_delivery.py`

- [ ] **Step 3.1: 写失败测试**

`tests/dao/test_bohrium_jobs_delivery.py` 末尾追加（跨 session 同 workspace 的观察；`_seed_job` 接受 `session=` 参数、workspace 恒 `/share/project`）：

```python
def test_query_workspace_active_spans_sessions(jobs_table, sessions_shadow):
    _register_session(sessions_shadow, session="sess-A")
    _register_session(sessions_shadow, session="sess-B")
    _seed_job(jobs_table, session="sess-A", job_id="601")  # active (submitted)
    _seed_job(jobs_table, session="sess-B", job_id="602")  # active (submitted)

    rows = jobs_table.query_workspace_active(
        user_id="u1", org_id="o1", workspace="/share/project"
    )
    assert sorted(r["job_id"] for r in rows) == ["601", "602"]


def test_query_workspace_pending_terminal_spans_sessions_with_limit(
    jobs_table, sessions_shadow
):
    _register_session(sessions_shadow, session="sess-A")
    _register_session(sessions_shadow, session="sess-B")
    _seed_job(jobs_table, session="sess-A", job_id="701", status="finished")
    _seed_job(jobs_table, session="sess-B", job_id="702", status="finished")

    rows = jobs_table.query_workspace_pending_terminal(
        user_id="u1", org_id="o1", workspace="/share/project", limit=10
    )
    assert sorted(r["job_id"] for r in rows) == ["701", "702"]

    limited = jobs_table.query_workspace_pending_terminal(
        user_id="u1", org_id="o1", workspace="/share/project", limit=1
    )
    assert len(limited) == 1


def test_query_workspace_recent_terminal_ignores_handled_and_orders_desc(
    jobs_table, sessions_shadow
):
    _register_session(sessions_shadow)
    _seed_job(jobs_table, job_id="801", status="finished")
    _seed_job(jobs_table, job_id="802", status="finished")
    _shift_terminal_at(sessions_shadow, job_id="801", seconds_ago=300)  # 更老
    _shift_terminal_at(sessions_shadow, job_id="802", seconds_ago=100)  # 更新
    # 把 801 标 handled——recent 仍应包含它（不受 handled_at 影响）
    snap_rows = jobs_table.list_pending_terminal_snapshot(
        user_id="u1", org_id="o1", session_id="sess-1", workspace="/share/project"
    )
    jobs_table.mark_handled_by_ids(
        user_id="u1",
        org_id="o1",
        session_id="sess-1",
        workspace="/share/project",
        row_ids=[r["id"] for r in snap_rows if r["job_id"] == "801"],
    )

    rows = jobs_table.query_workspace_recent_terminal(
        user_id="u1", org_id="o1", workspace="/share/project", limit=10
    )
    assert [r["job_id"] for r in rows] == ["802", "801"]  # terminal_at 倒序
```

- [ ] **Step 3.2: 跑测试确认失败**

Run（有 `.env.test`）: `uv run pytest tests/dao/test_bohrium_jobs_delivery.py -k query_workspace_ -q`
Expected: FAIL —— `AttributeError: 'BohriumJobsTable' object has no attribute 'query_workspace_active'`（无 `.env.test` 则 SKIP，实现仍按下文写，红→绿在 CI 验证）。

- [ ] **Step 3.3: 实现三个 observation 查询**

`src/dao/bohrium_jobs_table.py`：在 `query_session_active`（201-213）之后插入：

```python
    def query_workspace_active(
        self, *, user_id: str, org_id: str, workspace: str
    ) -> list[dict[str, Any]]:
        """workspace 观察视图：跨 session 的活跃作业。"""
        sql = f"""
            SELECT {self._AGENT_COLUMNS} FROM {self.table_name}
            WHERE user_id = %s AND org_id = %s AND workspace = %s
              AND status IN ({_SQL_ACTIVE})
            ORDER BY submitted_at ASC
        """
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (user_id, org_id, workspace))
                return [self._to_agent_job(r) for r in cur.fetchall()]

    def query_workspace_pending_terminal(
        self, *, user_id: str, org_id: str, workspace: str, limit: int
    ) -> list[dict[str, Any]]:
        """workspace 观察视图：跨 session 的未交付终态作业（观察用，非 ack 范围）。"""
        sql = f"""
            SELECT {self._AGENT_COLUMNS} FROM {self.table_name}
            WHERE user_id = %s AND org_id = %s AND workspace = %s
              AND terminal_at IS NOT NULL AND handled_at IS NULL
            ORDER BY terminal_at ASC, submitted_at ASC
            LIMIT %s
        """
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (user_id, org_id, workspace, int(limit)))
                return [self._to_agent_job(r) for r in cur.fetchall()]

    def query_workspace_recent_terminal(
        self, *, user_id: str, org_id: str, workspace: str, limit: int
    ) -> list[dict[str, Any]]:
        """workspace 观察视图：跨 session 的最近终态作业（不论 handled），按
        terminal_at 倒序，用于回答用户主动询问历史完成情况。"""
        sql = f"""
            SELECT {self._AGENT_COLUMNS} FROM {self.table_name}
            WHERE user_id = %s AND org_id = %s AND workspace = %s
              AND terminal_at IS NOT NULL
            ORDER BY terminal_at DESC, id DESC
            LIMIT %s
        """
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (user_id, org_id, workspace, int(limit)))
                return [self._to_agent_job(r) for r in cur.fetchall()]
```

- [ ] **Step 3.4: 跑测试确认通过**

Run（有 `.env.test`）: `uv run pytest tests/dao/test_bohrium_jobs_delivery.py -q` → PASS；否则 SKIP（CI 验证）。

- [ ] **Step 3.5: Commit**

```bash
git add src/dao/bohrium_jobs_table.py tests/dao/test_bohrium_jobs_delivery.py
git commit -m "feat(bohrium): add workspace observation queries (active/pending/recent)"
```

---

### Task 4: 把 SessionJobs 全量迁移为 WorkspaceJobs（+字段 +渲染）

一次性原子重命名 `SessionJobs` 家族为 `WorkspaceJobs`，给数据对象加 `workspace` / `recent_terminal_jobs` 字段、给渲染加 workspace 头部行与 recent_terminal 组、把 section tag/key 改为 `workspace_jobs`。重命名横跨 `matmaster/context`、`matmaster/types`、`matmaster/core` 与 `src/services`，任何半完成状态都会 import 断裂，因此**实现全部改完再跑测试**。本 Task 里 `bohrium_jobs_wiring` 的 read port **只改名、不改逻辑**（仍走 `query_session_active`(旧签名) + `snapshot.rows`，返回的 `WorkspaceJobs` 不设 `workspace`/`recent`，留默认）——真正的 observation/delivery 拆分在 Task 5。

> 命名映射：`SessionJobs`→`WorkspaceJobs`、`SessionJobsQuery`→`WorkspaceJobsQuery`、`SessionJobsPort`→`WorkspaceJobsPort`、`SessionJobsSource`→`WorkspaceJobsSource`、`load_session_jobs`→`load_workspace_jobs`、`SectionOrder.SESSION_JOBS`→`WORKSPACE_JOBS`、`_step_session_jobs`→`_step_workspace_jobs`、`_load_jobs_or_empty`→`_load_workspace_jobs_or_empty`、`_EmptySessionJobsPort`→`_EmptyWorkspaceJobsPort`、`EmptySessionJobsPort`→`EmptyWorkspaceJobsPort`（test 工具类）、字段/参数 `session_jobs`→`workspace_jobs`、tag/key `"session_jobs"`→`"workspace_jobs"`。`ContextAssemblyPorts`、`AgentRunPorts` 类名不变（仅字段改名）；`_RunSessionJobsPort` 类名本 Task 不改（Task 5 重写）。

**Files（生产）:**
- Modify: `matmaster/context/ports.py`（`SessionJobs` 99-106、`SessionJobsQuery` 109-111、`SessionJobsPort` 114-120、`ContextAssemblyPorts.session_jobs` 154）
- Modify: `matmaster/context/sections.py`（26）
- Rename+Modify: `matmaster/context/sources/session_jobs.py` → `workspace_jobs.py`
- Modify: `matmaster/context/compositions.py`（7、10、27、88-89、98、106、118）
- Modify: `matmaster/context/assembly.py`（16-23、191、199、206、236、250、264-269）
- Modify: `matmaster/types/runtime_ports.py`（18、182）
- Modify: `matmaster/core/runtime_context_assembly.py`（20-28、75-77、100）
- Modify: `src/services/bohrium_jobs_wiring.py`（import 11 + read port 方法名/返回类型 155-183）
- Modify: `src/services/agent_run_service.py`（`session_jobs=bohrium_jobs_port` 576）

**Files（测试）:**
- Rename+Modify: `tests/matmaster/context/sources/test_session_jobs.py` → `test_workspace_jobs.py`
- Rename+Modify: `tests/matmaster/test_runtime_context_assembly_session_jobs.py` → `test_runtime_context_assembly_workspace_jobs.py`
- Modify: `tests/matmaster/context/test_assembly.py`、`test_compositions.py`、`test_ports.py`
- Modify: `tests/matmaster/test_bohrium_ledger_injection.py`
- Modify: `tests/matmaster/test_runtime_spec.py`
- Modify: `tests/matmaster/integration/test_context_ports.py`、`test_history_checkpoint_recovery.py`
- Modify: `tests/services/test_bohrium_jobs_wiring.py`

- [ ] **Step 4.1: 改 `matmaster/context/ports.py`**

把 `SessionJobs`（99-106）、`SessionJobsQuery`（109-111）、`SessionJobsPort`（114-120）整体替换为：

```python
@dataclass(frozen=True)
class WorkspaceJobs:
    workspace: str | None = None
    active_jobs: tuple[JsonObject, ...] = ()
    pending_terminal_jobs: tuple[JsonObject, ...] = ()
    recent_terminal_jobs: tuple[JsonObject, ...] = ()
    detail_limit: int | None = None

    @classmethod
    def empty(cls) -> WorkspaceJobs:
        return cls()


@dataclass(frozen=True)
class WorkspaceJobsQuery:
    session_id: str


@runtime_checkable
class WorkspaceJobsPort(Protocol):
    async def load_workspace_jobs(
        self,
        query: WorkspaceJobsQuery,
    ) -> WorkspaceJobs:
        raise NotImplementedError
```

把 `ContextAssemblyPorts`（151-154）的 `session_jobs` 字段改名：

```python
@dataclass(frozen=True)
class ContextAssemblyPorts:
    session_events: SessionEventsPort
    workspace_jobs: WorkspaceJobsPort | None = None
```

- [ ] **Step 4.2: 改 `matmaster/context/sections.py`**

第 26 行 `SESSION_JOBS = 1200` 改为：

```python
    WORKSPACE_JOBS = 1200
```

- [ ] **Step 4.3: 重命名并改写 source 文件**

```bash
git mv matmaster/context/sources/session_jobs.py matmaster/context/sources/workspace_jobs.py
```

把 `matmaster/context/sources/workspace_jobs.py` 全文替换为（import 改、类改名、`from_jobs` 加 workspace 头部行 + recent 组、`to_sections` 改 key/tag/order；`_render_group` 逐字保留）：

```python
from __future__ import annotations

import json
from dataclasses import dataclass

from matmaster.context.ports import WorkspaceJobs
from matmaster.context.sections import ALL_VIEWS, ContextSection, SectionOrder


@dataclass(frozen=True)
class WorkspaceJobsSource:
    """Renderer for the workspace job view: a workspace header line followed by
    active / pending-terminal / recent-terminal groups.

    The JSON-line shape is intentionally temporary; the Bohrium job ledger may
    later define stable fields and replace this renderer without treating the
    current string format as a product contract. In observation mode the groups
    are an observation view, NOT the delivery ack scope.
    """

    lines: tuple[str, ...] = ()

    @classmethod
    def from_jobs(cls, jobs: WorkspaceJobs) -> WorkspaceJobsSource:
        active = cls._render_group(
            "active_job", "active_overflow", jobs.active_jobs, jobs.detail_limit
        )
        pending = cls._render_group(
            "pending_terminal_job",
            "pending_terminal_overflow",
            jobs.pending_terminal_jobs,
            jobs.detail_limit,
        )
        recent = cls._render_group(
            "recent_terminal_job",
            "recent_terminal_overflow",
            jobs.recent_terminal_jobs,
            jobs.detail_limit,
        )
        body = active + pending + recent
        if not body:
            return cls(lines=())
        header = (f"workspace {jobs.workspace}",) if jobs.workspace else ()
        return cls(lines=header + body)

    @staticmethod
    def _render_group(
        prefix: str,
        overflow_tag: str,
        items: tuple,
        limit: int | None,
    ) -> tuple[str, ...]:
        """前 limit 条完整详情，其余压成一行溢出摘要；全量 job_id 始终可见。

        limit 为 None 时全量逐行，与历史行为一致。
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

    def to_sections(self) -> tuple[ContextSection, ...]:
        if not self.lines:
            return ()
        return (
            ContextSection(
                key="workspace_jobs",
                tag="workspace_jobs",
                content="\n".join(self.lines),
                order=SectionOrder.WORKSPACE_JOBS,
                views=ALL_VIEWS,
            ),
        )
```

- [ ] **Step 4.4: 改 `matmaster/context/compositions.py`**

import（7、10）改名：

```python
from matmaster.context.ports import WorkspaceJobs
```
```python
from matmaster.context.sources.workspace_jobs import WorkspaceJobsSource
```

字段（27）改名：

```python
    workspace_jobs: WorkspaceJobs = field(default_factory=WorkspaceJobs.empty)
```

step 函数（88-89）改名与改体：

```python
def _step_workspace_jobs(inputs: ContextCompositionInputs) -> tuple[ContextSection, ...]:
    return WorkspaceJobsSource.from_jobs(inputs.workspace_jobs).to_sections()
```

三个 composition 里的 `_step_session_jobs`（98、106、118）全部改为 `_step_workspace_jobs`（`replace_all` 安全）。

- [ ] **Step 4.5: 改 `matmaster/context/assembly.py`**

import 段（16-23）把 `SessionJobs,` / `SessionJobsQuery,` 改为按字母序排在 `UserInstructions` 之后的 `WorkspaceJobs,` / `WorkspaceJobsQuery,`：

```python
from matmaster.context.ports import (
    ContextAssemblyPorts,
    SessionEvent,
    SessionEventQuery,
    UserInstructions,
    WorkspaceJobs,
    WorkspaceJobsQuery,
)
```

两处 `session_jobs=jobs,`（206、250）改为 `workspace_jobs=jobs,`。

`_load_jobs_or_empty`（264-269）整体替换为：

```python
    async def _load_workspace_jobs_or_empty(self, session_id: str) -> WorkspaceJobs:
        if self._ports.workspace_jobs is None:
            return WorkspaceJobs.empty()
        return await self._ports.workspace_jobs.load_workspace_jobs(
            WorkspaceJobsQuery(session_id=session_id)
        )
```

三处调用 `self._load_jobs_or_empty(request.session_id)`（191、199、236）改为 `self._load_workspace_jobs_or_empty(request.session_id)`（`replace_all` 安全）。

- [ ] **Step 4.6: 改 `matmaster/types/runtime_ports.py`**

import（18）`SessionJobsPort,` → `WorkspaceJobsPort,`（按字母序调整位置）：

```python
    WorkspaceJobsPort,
```

字段（182）改名：

```python
    workspace_jobs: WorkspaceJobsPort | None = None
```

- [ ] **Step 4.7: 改 `matmaster/core/runtime_context_assembly.py`**

import（20-28）把 `SessionJobs,` / `SessionJobsQuery,` 改为 `WorkspaceJobs,` / `WorkspaceJobsQuery,`（按字母序排在 `UserInstructions` 之后）：

```python
from matmaster.context.ports import (
    ActiveSkill,
    ContextAssemblyPorts,
    SessionEvent,
    SkillResolver,
    UserInstructions,
    WorkspaceJobs,
    WorkspaceJobsQuery,
)
```

`_EmptySessionJobsPort`（75-77）整体替换：

```python
class _EmptyWorkspaceJobsPort:
    async def load_workspace_jobs(self, query: WorkspaceJobsQuery) -> WorkspaceJobs:
        return WorkspaceJobs.empty()
```

装配点（100）改：

```python
        workspace_jobs=ctx.request.ports.workspace_jobs or _EmptyWorkspaceJobsPort(),
```

- [ ] **Step 4.8: 改 `src/services/bohrium_jobs_wiring.py`（仅改名，不改逻辑）**

import（11）改：

```python
from matmaster.context.ports import WorkspaceJobs, WorkspaceJobsQuery
```

`_RunSessionJobsPort.load_session_jobs`（155-183）改方法名与返回类型，方法体里 `SessionJobs.empty()`→`WorkspaceJobs.empty()`、`SessionJobs(`→`WorkspaceJobs(`、日志字符串 `load_session_jobs`→`load_workspace_jobs`。即把该方法整体替换为：

```python
    async def load_workspace_jobs(self, query: WorkspaceJobsQuery) -> WorkspaceJobs:
        if not (self._user_id and self._org_id):
            return WorkspaceJobs.empty()
        try:
            table = self._table_ref.get()
            active = await asyncio.to_thread(
                table.query_session_active,
                user_id=self._user_id,
                org_id=self._org_id,
                session_id=query.session_id,
            )
            if self._snapshot is not None:
                pending: tuple[dict[str, Any], ...] = self._snapshot.rows
                detail_limit: int | None = self._snapshot.detail_limit
            else:
                pending = ()
                detail_limit = None
        except Exception:  # noqa: BLE001
            logger.warning(
                "load_workspace_jobs failed session_id=%s",
                query.session_id,
                exc_info=True,
            )
            return WorkspaceJobs.empty()
        return WorkspaceJobs(
            active_jobs=tuple(active),
            pending_terminal_jobs=tuple(pending),
            detail_limit=detail_limit,
        )
```

（`build_bohrium_jobs_ports` 返回类型注解里的 `_RunSessionJobsPort` 名不变，本 Task 不动。）

- [ ] **Step 4.9: 改 `src/services/agent_run_service.py`（装配点）**

第 576 行 `session_jobs=bohrium_jobs_port,` 改为：

```python
                        workspace_jobs=bohrium_jobs_port,
```

- [ ] **Step 4.10: 重命名并改写两个测试文件**

```bash
git mv tests/matmaster/context/sources/test_session_jobs.py tests/matmaster/context/sources/test_workspace_jobs.py
git mv tests/matmaster/test_runtime_context_assembly_session_jobs.py tests/matmaster/test_runtime_context_assembly_workspace_jobs.py
```

把 `tests/matmaster/context/sources/test_workspace_jobs.py` 全文替换为（符号改名 + tag/key/order 断言改名 + 新增 workspace 头部与 recent 渲染测试；detail_limit 系列逐字保留，仅 `SessionJobs`→`WorkspaceJobs`、`SessionJobsSource`→`WorkspaceJobsSource`）：

```python
from __future__ import annotations

from matmaster.context.ports import WorkspaceJobs
from matmaster.context.sections import ContextView, SectionOrder
from matmaster.context.sources.workspace_jobs import WorkspaceJobsSource


def test_workspace_jobs_empty_returns_no_sections() -> None:
    assert WorkspaceJobsSource.from_jobs(WorkspaceJobs.empty()).to_sections() == ()


def test_workspace_jobs_renders_active_and_pending_terminal() -> None:
    jobs = WorkspaceJobs(
        active_jobs=(
            {"job_id": "a2", "status": "running"},
            {"job_id": "a1", "status": "submitted"},
        ),
        pending_terminal_jobs=({"job_id": "t9", "status": "finished"},),
    )

    section = WorkspaceJobsSource.from_jobs(jobs).to_sections()[0]

    assert section.key == "workspace_jobs"
    assert section.tag == "workspace_jobs"
    assert section.order == SectionOrder.WORKSPACE_JOBS
    assert section.views == frozenset({ContextView.RUNTIME, ContextView.CHECKPOINT})
    assert section.content == (
        'active_job_1 {"job_id": "a2", "status": "running"}\n'
        'active_job_2 {"job_id": "a1", "status": "submitted"}\n'
        'pending_terminal_job_1 {"job_id": "t9", "status": "finished"}'
    )


def test_workspace_header_prefixes_groups_when_workspace_present() -> None:
    jobs = WorkspaceJobs(
        workspace="/share/w1",
        active_jobs=({"job_id": "a1", "status": "running"},),
        recent_terminal_jobs=({"job_id": "r1", "status": "finished"},),
    )
    section = WorkspaceJobsSource.from_jobs(jobs).to_sections()[0]
    assert section.content == (
        "workspace /share/w1\n"
        'active_job_1 {"job_id": "a1", "status": "running"}\n'
        'recent_terminal_job_1 {"job_id": "r1", "status": "finished"}'
    )


def test_workspace_header_omitted_when_no_jobs() -> None:
    # 有 workspace 但三组皆空：不渲染（空态返回 ()）
    jobs = WorkspaceJobs(workspace="/share/w1")
    assert WorkspaceJobsSource.from_jobs(jobs).to_sections() == ()


def test_recent_terminal_group_renders() -> None:
    jobs = WorkspaceJobs(recent_terminal_jobs=({"job_id": "r9", "status": "finished"},))
    section = WorkspaceJobsSource.from_jobs(jobs).to_sections()[0]
    assert section.content == (
        'recent_terminal_job_1 {"job_id": "r9", "status": "finished"}'
    )


def test_workspace_jobs_only_active_renders_without_terminal_lines() -> None:
    jobs = WorkspaceJobs(active_jobs=({"job_id": "a1", "status": "running"},))
    section = WorkspaceJobsSource.from_jobs(jobs).to_sections()[0]
    assert section.content == 'active_job_1 {"job_id": "a1", "status": "running"}'


def test_workspace_jobs_only_pending_terminal_renders() -> None:
    jobs = WorkspaceJobs(pending_terminal_jobs=({"job_id": "t1", "status": "failed"},))
    section = WorkspaceJobsSource.from_jobs(jobs).to_sections()[0]
    assert section.content == (
        'pending_terminal_job_1 {"job_id": "t1", "status": "failed"}'
    )


def _job(job_id: str, status: str = "finished", sandbox: bool = False) -> dict:
    return {"job_id": job_id, "status": status, "sandbox": sandbox}


def test_detail_limit_compresses_pending_with_overflow_summary() -> None:
    jobs = WorkspaceJobs(
        pending_terminal_jobs=(
            _job("f1", "failed"),
            _job("t1"),
            _job("t2"),
            _job("t3", "stopped"),
        ),
        detail_limit=2,
    )
    lines = WorkspaceJobsSource.from_jobs(jobs).lines

    assert lines[0].startswith('pending_terminal_job_1 {"job_id": "f1"')
    assert lines[1].startswith('pending_terminal_job_2 {"job_id": "t1"')
    assert len(lines) == 3
    assert lines[2] == (
        'pending_terminal_overflow '
        '{"by_status": {"finished": 1, "stopped": 1}, '
        '"count": 2, "job_ids": ["t2", "t3"]}'
    )


def test_detail_limit_compresses_active_independently() -> None:
    jobs = WorkspaceJobs(
        active_jobs=(
            _job("a1", "running"),
            _job("a2", "running"),
            _job("a3", "submitted"),
        ),
        pending_terminal_jobs=(_job("t1"),),
        detail_limit=1,
    )
    lines = WorkspaceJobsSource.from_jobs(jobs).lines

    assert lines[0].startswith("active_job_1 ")
    assert lines[1] == (
        'active_overflow '
        '{"by_status": {"running": 1, "submitted": 1}, '
        '"count": 2, "job_ids": ["a2", "a3"]}'
    )
    assert lines[2].startswith("pending_terminal_job_1 ")
    assert len(lines) == 3


def test_detail_limit_covers_all_ids_between_detail_and_overflow() -> None:
    all_ids = [f"j{i}" for i in range(7)]
    jobs = WorkspaceJobs(
        pending_terminal_jobs=tuple(_job(i) for i in all_ids),
        detail_limit=3,
    )
    lines = WorkspaceJobsSource.from_jobs(jobs).lines

    import json as _json

    detail_ids = [_json.loads(line.split(" ", 1)[1])["job_id"] for line in lines[:3]]
    overflow = _json.loads(lines[3].split(" ", 1)[1])
    assert detail_ids + overflow["job_ids"] == all_ids


def test_detail_limit_no_overflow_when_limit_covers_all() -> None:
    jobs = WorkspaceJobs(
        pending_terminal_jobs=(_job("t1"), _job("t2")),
        detail_limit=2,
    )
    lines = WorkspaceJobsSource.from_jobs(jobs).lines
    assert len(lines) == 2
    assert not any("overflow" in line for line in lines)


def test_overflow_job_ids_keep_same_job_id_across_sandboxes() -> None:
    jobs = WorkspaceJobs(
        pending_terminal_jobs=(
            _job("keep"),
            _job("dup", sandbox=False),
            _job("dup", sandbox=True),
        ),
        detail_limit=1,
    )
    lines = WorkspaceJobsSource.from_jobs(jobs).lines

    import json as _json

    overflow = _json.loads(lines[1].split(" ", 1)[1])
    assert overflow["count"] == 2
    assert overflow["job_ids"] == ["dup", "dup"]
```

把 `tests/matmaster/test_runtime_context_assembly_workspace_jobs.py` 里的符号改名：先 `Read` 该文件，把 `session_jobs`→`workspace_jobs`、`_EmptySessionJobsPort`→`_EmptyWorkspaceJobsPort`、`load_session_jobs`→`load_workspace_jobs`、`SessionJobs`→`WorkspaceJobs`、`SessionJobsQuery`→`WorkspaceJobsQuery`、形参/局部名 `session_jobs_port`→`workspace_jobs_port`、测试名 `test_uses_injected_session_jobs_port`→`test_uses_injected_workspace_jobs_port` 全部对应替换。`rca._EmptySessionJobsPort` 改 `rca._EmptyWorkspaceJobsPort`。

- [ ] **Step 4.11: 改其余 context 测试（符号替换）**

每个文件先 `Read` 核对当前符号位置，再替换：

`tests/matmaster/context/test_assembly.py`：import `SessionJobs,`→`WorkspaceJobs,`（移到字母序位置，`UserInstructions` 之后）；mock port 的 `load_session_jobs`→`load_workspace_jobs`；`return SessionJobs(...)`→`WorkspaceJobs(...)`；两处 `session_jobs=`→`workspace_jobs=`。

`tests/matmaster/context/test_compositions.py`：import `SessionJobs`→`WorkspaceJobs`；`inputs.session_jobs`→`inputs.workspace_jobs`、`SessionJobs.empty()`→`WorkspaceJobs.empty()`；`session_jobs=SessionJobs(...)`→`workspace_jobs=WorkspaceJobs(...)`；断言列表里的 `"session_jobs"`→`"workspace_jobs"`；`session_jobs=SessionJobs.empty()`→`workspace_jobs=WorkspaceJobs.empty()`。

`tests/matmaster/context/test_ports.py`：import `SessionJobs,`→`WorkspaceJobs,`；测试名 `test_session_jobs_empty_returns_no_active_jobs`→`test_workspace_jobs_empty_returns_no_active_jobs`，`SessionJobs.empty()`→`WorkspaceJobs.empty()`；`ports.session_jobs`→`ports.workspace_jobs`。

`tests/matmaster/test_bohrium_ledger_injection.py`：测试名 `test_session_jobs_has_pending_terminal_jobs_field`→`test_workspace_jobs_has_pending_terminal_jobs_field`；import `SessionJobs`→`WorkspaceJobs`，`SessionJobs.empty()`、`get_type_hints(SessionJobs)` 同改；测试名 `..._and_session_jobs_ports`→`..._and_workspace_jobs_ports`，`"session_jobs" in fields`→`"workspace_jobs" in fields`，`p.session_jobs is None`→`p.workspace_jobs is None`。

`tests/matmaster/test_runtime_spec.py`：两处 kernel-facing 边界负向断言 `assert not hasattr(..., "session_jobs_port")` 改为 `assert not hasattr(..., "workspace_jobs_port")`。这不是保留旧名兼容，而是让重命名后的边界测试继续守住：kernel spec/resources 不应暴露 context assembly 内部 jobs port。

`tests/matmaster/integration/test_context_ports.py`：import `SessionJobs,`/`SessionJobsQuery,`→`WorkspaceJobs,`/`WorkspaceJobsQuery,`；`class EmptySessionJobsPort`→`class EmptyWorkspaceJobsPort`；`async def load_session_jobs`→`load_workspace_jobs`，形参类型 `SessionJobsQuery`→`WorkspaceJobsQuery`，返回类型 `SessionJobs`→`WorkspaceJobs`，`SessionJobs.empty()`→`WorkspaceJobs.empty()`。

`tests/matmaster/integration/test_history_checkpoint_recovery.py`：import `EmptySessionJobsPort,`→`EmptyWorkspaceJobsPort,`；`session_jobs=EmptySessionJobsPort()`→`workspace_jobs=EmptyWorkspaceJobsPort()`。

`tests/services/test_bohrium_jobs_wiring.py`：所有 `from matmaster.context.ports import SessionJobsQuery`→`WorkspaceJobsQuery`；所有 `jobs_port.load_session_jobs(SessionJobsQuery(session_id="s"))`→`jobs_port.load_workspace_jobs(WorkspaceJobsQuery(session_id="s"))`；涉及测试 `test_jobs_port_without_snapshot_renders_empty_pending`(142-160)、`test_jobs_port_serves_pending_from_snapshot_with_detail_limit`(228-254) 的断言里 `SessionJobs`→`WorkspaceJobs`（若有）。本 Task 这两个测试逻辑不变（active/pending 旧路径），Task 5 再重写为 delivery/observation。

- [ ] **Step 4.12: 跑全部受影响测试确认通过**

Run: `uv run pytest tests/matmaster/context/ tests/matmaster/test_runtime_context_assembly_workspace_jobs.py tests/matmaster/test_bohrium_ledger_injection.py tests/matmaster/test_runtime_spec.py tests/matmaster/integration/test_context_ports.py tests/matmaster/integration/test_history_checkpoint_recovery.py tests/services/test_bohrium_jobs_wiring.py -q`
Expected: PASS（全部）。

如有 `ImportError`/`AttributeError` 残留旧名，按报错 grep 修：`grep -rn "SessionJobs\|session_jobs\|SESSION_JOBS\|_EmptySessionJobsPort\|load_session_jobs" matmaster/ src/ tests/ | grep -v "_RunSessionJobsPort"`（应只剩 `_RunSessionJobsPort` 类名，留 Task 5 处理）。

- [ ] **Step 4.13: Commit**

```bash
git add matmaster/context/ matmaster/types/runtime_ports.py matmaster/core/runtime_context_assembly.py src/services/bohrium_jobs_wiring.py src/services/agent_run_service.py tests/matmaster/ tests/services/test_bohrium_jobs_wiring.py
git commit -m "refactor(context): rename SessionJobs to WorkspaceJobs, add workspace/recent fields and rendering"
```

---

### Task 5: wiring 三种 read port + job_context_mode 接线（保留 delivery_snapshot）

把 read port 从单一「snapshot/session 路径」拆为按 `job_context_mode` 选择的三种实现：`session_workspace_delivery`（trigger，active 收紧到 workspace + pending 复用 `snapshot.rows`）、`workspace_observation`（user query，跨 session 三查询）、空视图兜底。`query_session_active` 加 workspace 参数（delivery port 需要）。`build_bohrium_jobs_ports` **保留** `delivery_snapshot`（ledger 取 `observed_terminal`、delivery port 取 `rows`），新增 `job_context_mode`。`run_agent` 加 `job_context_mode`（**保留** `delivery_snapshot`），worker 读 `origin` 算 mode 并把 mode 传入 run_agent kwargs（**仍传** `delivery_snapshot`）。

**Files:**
- Modify: `src/dao/bohrium_jobs_table.py`（`query_session_active` 201-213 加 workspace）
- Modify: `src/services/bohrium_jobs_wiring.py`（import 1-19、read port 类 141-183、`build_bohrium_jobs_ports` 186-225）
- Modify: `src/services/agent_run_service.py`（`run_agent` 签名 251-269、build 调用 542-549）
- Modify: `src/worker/agent_worker.py`（payload 解析 354 之后、run_agent kwargs 445-462）
- Test: `tests/services/test_bohrium_jobs_wiring.py`
- Test: `tests/test_agent_worker_snapshot_confirm.py`
- Test: `tests/dao/test_bohrium_jobs_delivery.py`（`query_session_active` 加 workspace 的真库测试，若该方法已有测试）
- Test: `tests/dao/test_bohrium_jobs_table.py`（现有 `query_session_active` 基础测试同步旧签名迁移）

- [ ] **Step 5.1: 写失败测试 —— DAO query_session_active 加 workspace**

`tests/dao/test_bohrium_jobs_delivery.py` 末尾追加：

```python
def test_query_session_active_scoped_by_session_and_workspace(
    jobs_table, sessions_shadow
):
    _register_session(sessions_shadow)
    _seed_job(jobs_table, job_id="901")  # sess-1 /share/project active
    jobs_table.insert_submitted(
        session_id="sess-1",
        invocation_id="inv-1",
        spawn_id=None,
        user_id="u1",
        org_id="o1",
        job_id="902",
        job_name="name-902",
        project_id=42,
        sandbox=False,
        input_dir="data/in",
        workspace="/share/other",
    )

    rows = jobs_table.query_session_active(
        user_id="u1", org_id="o1", session_id="sess-1", workspace="/share/project"
    )
    assert [r["job_id"] for r in rows] == ["901"]
```

同时修改 `tests/dao/test_bohrium_jobs_table.py` 的现有测试 `test_query_session_active_returns_active_only_sorted`：`jobs_table.query_session_active(user_id="user-1", org_id="org-1", session_id="sess-1")` 调用补 `workspace="/share/project"`。该文件是现有 DAO 基础测试，不同步迁移会在全量测试阶段才暴露旧签名 `TypeError`。

- [ ] **Step 5.2: 写失败测试 —— wiring 三种 read port + mode**

`tests/services/test_bohrium_jobs_wiring.py`：先 `Read` 全文。把现有 `test_jobs_port_without_snapshot_renders_empty_pending`(142-160) 与 `test_jobs_port_serves_pending_from_snapshot_with_detail_limit`(228-254) 重写为 delivery-mode 测试，并新增 observation/empty mode 测试。在这两个测试位置附近替换/新增：

```python
@pytest.mark.asyncio
async def test_delivery_mode_serves_active_and_pending_from_snapshot() -> None:
    from matmaster.context.ports import WorkspaceJobsQuery

    table = MagicMock()
    table.query_session_active.return_value = [{"job_id": "a"}]
    snap = _snapshot([{"id": 1, "job_id": "t"}])
    _, jobs_port = build_bohrium_jobs_ports(
        session_id="s",
        invocation_id="inv",
        user_id="u",
        org_id="o",
        workspace="/share/project",
        job_context_mode="session_workspace_delivery",
        delivery_snapshot=snap,
        table=table,
    )

    result = await jobs_port.load_workspace_jobs(WorkspaceJobsQuery(session_id="s"))
    assert result.workspace == "/share/project"
    assert result.active_jobs == ({"job_id": "a"},)
    assert result.pending_terminal_jobs == ({"id": 1, "job_id": "t"},)
    assert result.recent_terminal_jobs == ()
    # delivery active 收紧到 session+workspace
    assert table.query_session_active.call_args.kwargs == {
        "user_id": "u",
        "org_id": "o",
        "session_id": "s",
        "workspace": "/share/project",
    }


@pytest.mark.asyncio
async def test_observation_mode_reads_three_groups_cross_session() -> None:
    from matmaster.context.ports import WorkspaceJobsQuery

    table = MagicMock()
    table.query_workspace_active.return_value = [{"job_id": "a"}]
    table.query_workspace_pending_terminal.return_value = [{"job_id": "p"}]
    table.query_workspace_recent_terminal.return_value = [{"job_id": "r"}]
    _, jobs_port = build_bohrium_jobs_ports(
        session_id="s",
        invocation_id="inv",
        user_id="u",
        org_id="o",
        workspace="/share/project",
        job_context_mode="workspace_observation",
        table=table,
    )

    result = await jobs_port.load_workspace_jobs(WorkspaceJobsQuery(session_id="s"))
    assert result.workspace == "/share/project"
    assert result.active_jobs == ({"job_id": "a"},)
    assert result.pending_terminal_jobs == ({"job_id": "p"},)
    assert result.recent_terminal_jobs == ({"job_id": "r"},)
    # 观察按 workspace 跨 session：不传 session_id
    assert table.query_workspace_active.call_args.kwargs == {
        "user_id": "u",
        "org_id": "o",
        "workspace": "/share/project",
    }


@pytest.mark.asyncio
async def test_observation_mode_empty_when_workspace_missing() -> None:
    from matmaster.context.ports import WorkspaceJobs, WorkspaceJobsQuery

    table = MagicMock()
    _, jobs_port = build_bohrium_jobs_ports(
        session_id="s",
        invocation_id="inv",
        user_id="u",
        org_id="o",
        workspace=None,
        job_context_mode="workspace_observation",
        table=table,
    )

    result = await jobs_port.load_workspace_jobs(WorkspaceJobsQuery(session_id="s"))
    assert result == WorkspaceJobs.empty()
    table.query_workspace_active.assert_not_called()
```

（ledger 测试 `test_record_poll_terminal_feeds_observed_set`(257-271)、`test_record_poll_without_snapshot_skips_observation`(274-285) 不传 `job_context_mode`，靠 build 默认 `"session_workspace_delivery"`；ledger 只依赖 `normalized_workspace` 与 `delivery_snapshot.observed_terminal`，不受 mode 影响，保持不变。）

- [ ] **Step 5.3: 写失败测试 —— worker origin→mode（保留 delivery_snapshot）**

`tests/test_agent_worker_snapshot_confirm.py`：`_run_one_round` 签名加 `origin=None`，payload 加 `"origin": origin`：

```python
def _run_one_round(
    monkeypatch,
    *,
    snapshot_obj,
    run_result,
    confirm_exc=None,
    run_agent_exc=None,
    origin=None,
):
```

payload 字面量加一行 `"origin": origin,`（在 `"delivery": {"notify": False},` 之后）。

`test_success_path_orders_snapshot_run_confirm_release`(91-96) 末尾追加（**保留** delivery_snapshot 断言，新增 mode 断言）：

```python
    assert received["delivery_snapshot"] is snap
    assert received["job_context_mode"] == "workspace_observation"
```

`test_none_snapshot_runs_without_confirm`(124-127) 末尾追加：

```python
    assert received["job_context_mode"] == "workspace_observation"
```

文件末尾新增：

```python
def test_bohrium_completion_origin_uses_delivery_mode(monkeypatch):
    snap = object()
    calls, received = _run_one_round(
        monkeypatch, snapshot_obj=snap, run_result=True, origin="bohrium_completion"
    )
    # trigger run 走 session+workspace delivery 模式（不跨 session 观察）
    assert received["job_context_mode"] == "session_workspace_delivery"
    # snapshot/confirm 不分 origin，仍照常拍与确认；delivery_snapshot 仍传入
    assert received["delivery_snapshot"] is snap
    assert calls == ["acquire", "snapshot", "run_agent", "confirm", "release:True"]
```

- [ ] **Step 5.4: 跑测试确认失败**

Run: `uv run pytest tests/services/test_bohrium_jobs_wiring.py tests/test_agent_worker_snapshot_confirm.py -q`
Expected: FAIL —— `build_bohrium_jobs_ports` 不接受 `job_context_mode`、read port 无 observation 分支、run_agent kwargs 无 `job_context_mode`。

- [ ] **Step 5.5: 实现 query_session_active 加 workspace**

`src/dao/bohrium_jobs_table.py`，`query_session_active`（201-213）整体替换为：

```python
    def query_session_active(
        self, *, user_id: str, org_id: str, session_id: str, workspace: str
    ) -> list[dict[str, Any]]:
        sql = f"""
            SELECT {self._AGENT_COLUMNS} FROM {self.table_name}
            WHERE user_id = %s AND org_id = %s AND session_id = %s
              AND workspace = %s
              AND status IN ({_SQL_ACTIVE})
            ORDER BY submitted_at ASC
        """
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (user_id, org_id, session_id, workspace))
                return [self._to_agent_job(r) for r in cur.fetchall()]
```

- [ ] **Step 5.6: 实现 wiring 三种 read port + mode**

`src/services/bohrium_jobs_wiring.py`。

import 段（1-19）替换为（加 `WorkspaceJobsPort`、`env_int`；**保留** `DeliverySnapshot`）：

```python
"""service 层把 bohrium_jobs DAO 包成 kernel 端口。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from matmaster.bohrium.status import to_ledger_status
from matmaster.context.ports import (
    WorkspaceJobs,
    WorkspaceJobsPort,
    WorkspaceJobsQuery,
)
from src.dao.bohrium_jobs_table import BohriumJobsTable, get_bohrium_jobs_table
from src.services.bohrium_delivery_ack import DeliverySnapshot
from src.services.session_directory_service import (
    SessionDirectoryError,
    normalize_remote_workspace_path,
)
from src.utils.constant import env_int

logger = logging.getLogger(__name__)
```

把 `_RunSessionJobsPort` 类（Task 4 后名为 `load_workspace_jobs` 的版本，141-183）整体替换为三个 port 类：

```python
class _SessionWorkspaceDeliveryJobsPort:
    """bohrium_completion trigger run 的 delivery read port：当前 session + 当前
    workspace 的 active（query_session_active 加 workspace）+ pending（复用
    snapshot.rows）。context 与 ack 同源——agent 看到即 ack，不盲确认。
    """

    def __init__(
        self,
        *,
        table_ref: _BohriumJobsTableRef,
        user_id: str,
        org_id: str,
        workspace: str,
        snapshot: DeliverySnapshot | None,
    ) -> None:
        self._table_ref = table_ref
        self._user_id = user_id
        self._org_id = org_id
        self._workspace = workspace
        self._snapshot = snapshot

    async def load_workspace_jobs(self, query: WorkspaceJobsQuery) -> WorkspaceJobs:
        try:
            table = self._table_ref.get()
            active = await asyncio.to_thread(
                table.query_session_active,
                user_id=self._user_id,
                org_id=self._org_id,
                session_id=query.session_id,
                workspace=self._workspace,
            )
            if self._snapshot is not None:
                pending: tuple[dict[str, Any], ...] = self._snapshot.rows
                detail_limit: int | None = self._snapshot.detail_limit
            else:
                pending = ()
                detail_limit = None
        except Exception:  # noqa: BLE001
            logger.warning(
                "load_workspace_jobs(delivery) failed session_id=%s workspace=%s",
                query.session_id,
                self._workspace,
                exc_info=True,
            )
            return WorkspaceJobs.empty()
        return WorkspaceJobs(
            workspace=self._workspace,
            active_jobs=tuple(active),
            pending_terminal_jobs=tuple(pending),
            detail_limit=detail_limit,
        )


class _WorkspaceObservationJobsPort:
    """用户主动 query 的 workspace 观察 read port：按 user_id+org_id+workspace
    跨 session 读 active / pending_terminal / recent_terminal 三组。

    观察视图，非 ack 范围；与 DeliverySnapshot 无耦合。
    """

    def __init__(
        self,
        *,
        table_ref: _BohriumJobsTableRef,
        user_id: str,
        org_id: str,
        workspace: str,
        detail_limit: int,
    ) -> None:
        self._table_ref = table_ref
        self._user_id = user_id
        self._org_id = org_id
        self._workspace = workspace
        self._detail_limit = detail_limit

    async def load_workspace_jobs(self, query: WorkspaceJobsQuery) -> WorkspaceJobs:
        try:
            table = self._table_ref.get()
            active, pending, recent = await asyncio.gather(
                asyncio.to_thread(
                    table.query_workspace_active,
                    user_id=self._user_id,
                    org_id=self._org_id,
                    workspace=self._workspace,
                ),
                asyncio.to_thread(
                    table.query_workspace_pending_terminal,
                    user_id=self._user_id,
                    org_id=self._org_id,
                    workspace=self._workspace,
                    limit=self._detail_limit,
                ),
                asyncio.to_thread(
                    table.query_workspace_recent_terminal,
                    user_id=self._user_id,
                    org_id=self._org_id,
                    workspace=self._workspace,
                    limit=self._detail_limit,
                ),
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "load_workspace_jobs(observation) failed workspace=%s",
                self._workspace,
                exc_info=True,
            )
            return WorkspaceJobs.empty()
        return WorkspaceJobs(
            workspace=self._workspace,
            active_jobs=tuple(active),
            pending_terminal_jobs=tuple(pending),
            recent_terminal_jobs=tuple(recent),
            detail_limit=self._detail_limit,
        )


class _EmptyWorkspaceJobsPort:
    """workspace 为空 / identity 缺失 / mode 不匹配时的空视图读 port。"""

    async def load_workspace_jobs(self, query: WorkspaceJobsQuery) -> WorkspaceJobs:
        return WorkspaceJobs.empty()
```

把 `build_bohrium_jobs_ports`（186-225）整体替换为（**保留** `delivery_snapshot`，新增 `job_context_mode`，按 mode 选 port；ledger 构造逻辑原样保留）：

```python
def build_bohrium_jobs_ports(
    *,
    session_id: str,
    invocation_id: str | None,
    user_id: str,
    org_id: str,
    workspace: str | None,
    job_context_mode: str = "session_workspace_delivery",
    spawn_id: str | None = None,
    delivery_snapshot: DeliverySnapshot | None = None,
    table: BohriumJobsTable | None = None,
    table_factory: Callable[[], BohriumJobsTable] = get_bohrium_jobs_table,
) -> tuple[_BohriumJobLedger | None, WorkspaceJobsPort]:
    """构造写 port 与读 port（共享同一个 DAO 实例）。

    job_context_mode：
      - "session_workspace_delivery"：trigger run 的 delivery 视图，当前
        session + workspace 的 active + pending（复用 snapshot.rows）。
      - "workspace_observation"：用户 query 的跨 session 观察视图。
    workspace 为空 / identity 缺失 / mode 不匹配时读 port 返回空视图。
    ledger 不受 mode 影响，仍按 submit-time workspace 创建，并接收
    delivery_snapshot.observed_terminal 供 run 内前台 poll 填充。
    """
    table_ref = _BohriumJobsTableRef(table=table, table_factory=table_factory)
    normalized_workspace = _normalize_ledger_workspace(workspace)
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
    jobs: WorkspaceJobsPort
    if normalized_workspace is None or not (user_id and org_id):
        jobs = _EmptyWorkspaceJobsPort()
    elif job_context_mode == "workspace_observation":
        jobs = _WorkspaceObservationJobsPort(
            table_ref=table_ref,
            user_id=user_id,
            org_id=org_id,
            workspace=normalized_workspace,
            detail_limit=env_int("BOHRIUM_DELIVERY_DETAIL_LIMIT", 20),
        )
    elif job_context_mode == "session_workspace_delivery":
        jobs = _SessionWorkspaceDeliveryJobsPort(
            table_ref=table_ref,
            user_id=user_id,
            org_id=org_id,
            workspace=normalized_workspace,
            snapshot=delivery_snapshot,
        )
    else:
        jobs = _EmptyWorkspaceJobsPort()
    return ledger, jobs
```

- [ ] **Step 5.7: 实现 agent_run_service 加 job_context_mode（保留 delivery_snapshot）**

`src/services/agent_run_service.py`。

`run_agent` 签名（251-269）在 `delivery_snapshot: DeliverySnapshot | None = None,`（267）之后插入一行（**不删** delivery_snapshot）：

```python
        delivery_snapshot: DeliverySnapshot | None = None,
        job_context_mode: str = "workspace_observation",
        cancel_controller: CancellationController | None = None,
```

build 调用（542-549）补 `job_context_mode=job_context_mode,`（**保留** `delivery_snapshot=delivery_snapshot,`）：

```python
        bohrium_ledger_port, bohrium_jobs_port = build_bohrium_jobs_ports(
            session_id=session_id,
            invocation_id=invocation_id,
            user_id=_ledger_user_id,
            org_id=_ledger_org_id,
            workspace=stage_result.workspace,
            job_context_mode=job_context_mode,
            delivery_snapshot=delivery_snapshot,
        )
```

（`DeliverySnapshot` import 第 47 行保留——run_agent 签名仍引用。）

- [ ] **Step 5.8: 实现 worker origin→mode、kwargs 加 job_context_mode（保留 delivery_snapshot）**

`src/worker/agent_worker.py`：在第 354 行 `delivery = payload.get('delivery')` 之后插入：

```python
        origin = (payload.get('origin') or '').strip() or None
        job_context_mode = (
            'session_workspace_delivery'
            if origin == 'bohrium_completion'
            else 'workspace_observation'
        )
```

run_agent kwargs（445-462）在 `"delivery_snapshot": delivery_snapshot,`（461）之后插入一行（**保留** delivery_snapshot）：

```python
                "delivery_snapshot": delivery_snapshot,
                "job_context_mode": job_context_mode,
            }
```

- [ ] **Step 5.9: 跑测试确认通过**

Run: `uv run pytest tests/services/test_bohrium_jobs_wiring.py tests/test_agent_worker_snapshot_confirm.py -q`
Expected: PASS。
Run（有 `.env.test`）: `uv run pytest tests/dao/test_bohrium_jobs_delivery.py tests/dao/test_bohrium_jobs_table.py -q` → PASS；否则 SKIP。

确认 read port 已彻底按 mode 分流，且 delivery 通路（snapshot/ledger）仍完整：
Run: `grep -n "delivery_snapshot" src/services/bohrium_jobs_wiring.py src/services/agent_run_service.py src/worker/agent_worker.py`
Expected: 三个文件都仍引用 `delivery_snapshot`（保留，未误删）。

- [ ] **Step 5.10: Commit**

```bash
git add src/dao/bohrium_jobs_table.py src/services/bohrium_jobs_wiring.py src/services/agent_run_service.py src/worker/agent_worker.py tests/dao/test_bohrium_jobs_delivery.py tests/dao/test_bohrium_jobs_table.py tests/services/test_bohrium_jobs_wiring.py tests/test_agent_worker_snapshot_confirm.py
git commit -m "feat(bohrium): wire job_context_mode for delivery/observation read ports"
```

---

### Task 6: 全量回归 + 人工验证

- [ ] **Step 6.1: 全量回归**

Run: `uv run pytest tests/ -q`
Expected: 全部 PASS（无 `.env.test` 时 DAO 真库测试 SKIP）。重点受影响面：`tests/dao/test_bohrium_jobs_delivery.py`、`tests/dao/test_bohrium_jobs_table.py`、`tests/services/test_bohrium_*.py`、`tests/test_agent_worker_snapshot_confirm.py`、`tests/matmaster/context/`、`tests/matmaster/integration/`、`tests/matmaster/test_bohrium_ledger_injection.py`、`tests/matmaster/test_runtime_context_assembly_workspace_jobs.py`、`tests/matmaster/test_runtime_spec.py`。

- [ ] **Step 6.2: 残留旧名扫描**

Run: `grep -rn "SessionJobs\|session_jobs\|SESSION_JOBS\|_RunSessionJobsPort\|_EmptySessionJobsPort\|EmptySessionJobsPort\|load_session_jobs" src/ matmaster/ tests/ --include="*.py"`
Expected: 无输出（命名全部迁移；`_RunSessionJobsPort` 已在 Task 5 替换为三个 port 类）。若有命中，逐一修正（`docs/` 不在扫描范围、也不改）。

- [ ] **Step 6.3: lint / 导入自检**

Run: `uv run ruff check src/services/bohrium_jobs_wiring.py src/services/bohrium_delivery_ack.py src/services/agent_run_service.py src/services/bohrium_completion_scheduler.py src/worker/agent_worker.py src/dao/bohrium_jobs_table.py matmaster/context/ matmaster/types/runtime_ports.py matmaster/core/runtime_context_assembly.py`
Expected: 无未用 import、无未定义符号（确认 `WorkspaceJobs*` import 齐全、`DeliverySnapshot` 仍被 wiring/service 引用未误删、`env_int` 已 import）。

Run: `uv run python -c "import src.services.bohrium_jobs_wiring, src.services.agent_run_service, src.worker.agent_worker, src.services.bohrium_completion_scheduler, matmaster.context.assembly, matmaster.context.compositions, matmaster.core.runtime_context_assembly; print('imports OK')"`
Expected: `imports OK`

- [ ] **Step 6.4: 人工验证（需真实后端环境，结果记录到 PR 描述）**

对照 spec §10 验收标准与 §11 风险逐项验证：

1. **跨 session 可见（验收 1）**：在 workspace `/share/w1` 的 session A 提交 job 101；在同 workspace 的新 session B 主动 query。检查 B 的 context 含 `<workspace_jobs>` section（带 `workspace /share/w1` 头部），且能看到 job 101（跨 session）。
2. **org_id 非空（§11 首要嫌疑）**：DB 直查 `evo_chat_sessions` 对应行确认 `org_id` 非空——否则 observation 与 snapshot 恒空。
3. **workspace 归一化一致（§11）**：确认用户 query 的 payload workspace（`stream_service` normalize 请求路径）与 submit 时 ledger 行的 workspace（`execution_workdir` normalize）落在同一 normalized 串——否则 observation/ack 命不中行。
4. **trigger 注入 delivery section（验收 2、不盲 ack 验收 3）**：monitor 检测 job 101 终态后 trigger 回 session A。检查该 trigger run 的 context **含** `<workspace_jobs>` section，且只含 session A + /share/w1 的待交付 job 详情（**不跨 session**）；run 成功后 confirm 标 handled 的行集合 == section 展示的 pending rows。
5. **ack 限定 session+workspace（验收 3、4）**：session B query 成功后只 ack `session B + /share/w1`，不动 session A 的 job 101；session A trigger run 成功后只 ack `session A + /share/w1`。DB 查 `handled_at` 验证。
6. **多 session 同 workspace 不互相 ack（验收 5）**：两个 session 同时 query 同一 workspace，各自只 ack 自己 session 的 rows。
7. **切 workspace 不误 ack（验收 6，含 observed_terminal 路径）**：同一 session 切到新 workspace query，不 ack 旧 workspace 的 pending（两条 ack 路径都不越界）。
8. **trigger 仍回 submit session + workspace 分裂（验收 7）**：monitor trigger 目标是 submit session（A）；同 session 不同 workspace 各自独立成 delivery unit 触发。
9. **FIRST_FAILURE 数据 scope 一致（验收 8）**：`get_first_pending_failed` 的查询参数与返回 job 属于触发的那个 workspace；不对 prompt 文案内容做断言。
10. **现有索引足够、无 key-too-long（验收 9）**：observation/ack/scan 查询在现有索引下正确返回，无新增索引、无建表失败。

- [ ] **Step 6.5: 收尾**

确认工作树只含计划内代码改动（`git status`；`docs/` 下 spec 与本计划不入库）。`mark_handled_by_ids` 与 `mark_handled_by_job_keys` 是 `handled_at` 的唯二写入点，poller 与 trigger enqueue 仍不得 ack——回归确认无新增 ack 路径。然后走 superpowers:finishing-a-development-branch 流程（merge / PR 由用户决定，**不把 test 分支合并到任何分支**）。

---

## 自检记录（写计划时已核对）

**1. spec 覆盖**

| spec 节 | 对应 Task |
|---|---|
| §5.1 run 类型分流（origin→mode：`session_workspace_delivery`/`workspace_observation`） | Task 5（worker 读 origin 算 mode） |
| §5.2 delivery snapshot（session+workspace 范围、`DeliverySnapshot` 加 workspace 保留 observed_terminal、workspace 空→None/空 rows→snapshot） | Task 1 |
| §5.3 trigger 组装 session+workspace delivery section（不盲 ack、不退回 none） | Task 5（delivery port）；生产 prompt 文案不改，Task 2 删除 prompt 内容断言 |
| §5.4 用户 query 组装 workspace observation | Task 3（DAO 查询）+ Task 5（observation port） |
| §5.5 ack 规则（当前 session+workspace、两条路径） | Task 1（ack 方法/snapshot/confirm） |
| §6.1 收紧 session delivery 查询 + 两条 ack 路径补 workspace | Task 1 |
| §6.2 trigger delivery（query_session_active 加 workspace + 复用 snapshot.rows）/ workspace observation 三查询 | Task 5（query_session_active）+ Task 3（observation 三查询） |
| §6.3 scheduler 聚合补 workspace（scan 去 MIN、内层/ON/GROUP BY/ORDER BY、get_first_pending_failed、Redis key） | Task 2 |
| §6.4 复用现有索引、不新增（精确谓词） | 决策 5 + 各 DAO 查询的精确 `workspace = %s` 谓词；无索引 Task |
| §7.1 命名迁移 `SessionJobs`→`WorkspaceJobs` | Task 4 |
| §7.2 数据对象（`workspace`/`recent_terminal_jobs`/`empty()`） | Task 4 |
| §7.3 渲染（`<workspace_jobs>`、workspace header、三组区分、不暴露 user/org） | Task 4 |
| §8.1 worker（读 origin、snapshot 带 workspace、传 mode、confirm） | Task 1 + Task 5 |
| §8.2 AgentRunService（run_agent 加 mode、build 接 mode、保留 delivery_snapshot） | Task 5 |
| §8.3 bohrium_jobs_wiring（ledger write 不变、按 mode 两种 read port、保留 delivery_snapshot 参数） | Task 4（改名）+ Task 5（拆 port + mode） |
| §8.4 ContextAssembler 调度不变（仅 `_load_workspace_jobs_or_empty` 改名） | Task 4 |
| §9 测试计划（DAO/wiring/worker/context/交付语义） | 各 Task 内测试步骤 |
| §10 验收标准 1-9 | Task 6 人工验证 1-10 |
| §11 风险（盲 ack/索引 key 长度/scan MIN/get_first_pending_failed/worker snapshot/命名/observation 渲染不暗示 ack/org_id 非空） | Task 5 守住 delivery section / 决策 5 / Task 2 / Task 2 / Task 1 / Task 4 / Task 4 渲染 / Task 6 实测 |

**2. 占位符扫描**：全 Task 无 "TBD/TODO/类似 Task N/省略"；生产代码改动给出完整 old/new 或精确符号替换 + 行号；新增测试与新增实现给完整可运行代码；机械的「加 workspace 参数/符号改名」给明确规则 + 受影响函数清单 + 示例，并要求 executor 先 Read 核对。测试步骤均含可运行命令与预期。

**3. 类型一致性**：
- `DeliverySnapshot` 字段顺序 `user_id, org_id, session_id, workspace, rows, detail_limit, observed_terminal`（Task 1；observed_terminal 保留 `field(default_factory=set)`）；构造点 = `snapshot()`（Task 1）+ ack 测试（Task 1）+ wiring `_snapshot` helper（Task 1），三处一并改。
- DAO 方法名全计划保持原名加参数：`list_pending_terminal_snapshot` / `mark_handled_by_ids` / `mark_handled_by_job_keys` / `query_session_active` / `get_first_pending_failed` 各加 `workspace`（Task 1/5/2）；新增 `query_workspace_active` / `query_workspace_pending_terminal` / `query_workspace_recent_terminal`（Task 3，签名 `*, user_id, org_id, workspace[, limit]`）。
- `WorkspaceJobs(workspace, active_jobs, pending_terminal_jobs, recent_terminal_jobs, detail_limit)`、`WorkspaceJobsQuery(session_id)`、`WorkspaceJobsPort.load_workspace_jobs`（Task 4）在 ports / source / wiring / runtime / 测试间一致。
- `job_context_mode` 取值固定 `"session_workspace_delivery"` / `"workspace_observation"`（无 none）；worker 算（Task 5）、`run_agent` 默认 `"workspace_observation"`（Task 5）、`build_bohrium_jobs_ports` 默认 `"session_workspace_delivery"`（Task 5）——默认差异见决策 6，生产路径恒显式传递。
- `build_bohrium_jobs_ports` 保留 `delivery_snapshot` 参数（Task 5），返回 `tuple[_BohriumJobLedger | None, WorkspaceJobsPort]`；ledger 仍取 `delivery_snapshot.observed_terminal`，delivery port 取 `snapshot.rows`。
- `_SessionWorkspaceDeliveryJobsPort` / `_WorkspaceObservationJobsPort` / `_EmptyWorkspaceJobsPort`（wiring，Task 5）与 `_EmptyWorkspaceJobsPort`（core，Task 4）同名不同模块，各自局部，互不引用。
- Redis NX key 格式 `bohrium_delivery:{user_id}:{org_id}:{session_id}:{workspace}:{max_pending_terminal_id}`（Task 2 scheduler 与其测试断言一致）。

**4. 跨 Task 牵连已显式处理**：`DeliverySnapshot` 加字段牵动三个构造点（ack 生产 + ack 测试 + wiring `_snapshot` helper）在 Task 1 一并改；`query_session_active` 加 workspace 牵动其唯一调用方（delivery port）在 Task 5 同步改；`build_bohrium_jobs_ports` 加 mode 牵动的 ledger 测试靠 build 默认 `"session_workspace_delivery"` 免改；重命名牵动的 ~9 个测试文件在 Task 4 用符号替换清单逐一覆盖（含 2 个物理改名）；与初稿不同，本计划**不删** `query_session_active`、**不删** `delivery_snapshot` 参数、**不删** `observed_terminal`、**不建**索引/migration。

## Execution Handoff

计划已保存到 `docs/superpowers/plans/2026-06-13-workspace-job-context-section.md`（覆盖了基于 spec 初稿的过时版本）。两种执行方式：

1. **Subagent-Driven（推荐）** —— 每个 Task 派一个全新 subagent 实现，Task 间两段式 review，迭代快。本计划跨层、改动面大，按 Task 隔离 + review 最稳。
2. **Inline Execution** —— 本会话内用 superpowers:executing-plans 批量执行，带检查点 review。

选哪种？
