# Bohrium Job Ledger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增一张轻量级 `bohrium_jobs` 作业状态表，作为 Bohrium 作业的事实源；让 `BohriumTool` 在 submit/poll/download/kill 后写入状态，让 agent 通过 session 维度读取实时作业状态，并提供一个可测的后台 poller 核心。

**Architecture:** 分层落地，依赖方向严格为 `src/`（后端，可依赖 kernel）→ `matmaster/`（kernel，纯逻辑，**禁止** import `src/`）。
- 数据层：`src/dao/bohrium_jobs_table.py` 用 raw SQL（PyMySQL，同步）封装全部状态不变量；建表用外部 SQL 脚本 `src/sql/create_bohrium_jobs_table.sql`（手动执行，**不内联自动建表/迁移逻辑**）。
- 归一化层：`matmaster/bohrium/status.py` 增加纯函数 `to_ledger_status(code)`，被工具层与 poller 共用（kernel 纯逻辑，无 DB 依赖）。
- 端口层：`matmaster/context/ports.py` 定义 `BohriumJobLedgerPort`（写，同步）与已有的 `SessionJobsPort`（读，async）两个 Protocol；实现放 service 层 `src/services/bohrium_jobs_wiring.py`，闭包本轮身份字段。
- 注入：`AgentRunService` 构造两个 port → `AgentRunPorts` → kernel。写 port 经 `Exp._init_builtin_tools()` 注入 `BohriumTool` 构造器；读 port 经 `build_runtime_context_assembly()` 注入 context assembly。
- poller：`src/services/bohrium_poller.py` 仅实现可测核心（认领一批 → 现查 access_key → `get_job_detail` → 归一化 → 原子写回），**第一版不接独立进程入口**。

**Tech Stack:** Python 3.11+（uv 环境），PyMySQL（同步，`DictCursor`，`autocommit=False`），MySQL 8.0.16+（CHECK 约束 + `FOR UPDATE SKIP LOCKED`），pytest + pytest-asyncio（`asyncio_mode=auto`），python-dotenv（测试读 `.env.test`）。Black 行宽 88、isort `--profile black`、flake8（`--max-line-length=88`，忽略 E501/E203/B008/B036）、单文件 ≤ 1000 行（pre-commit 强制）。

---

## 关键架构决策（实现时必须遵守）

1. **依赖方向**：`matmaster/`（kernel）**不得** import `src/`。归一化函数因此放在 kernel 的 `matmaster/bohrium/status.py`（`src/` 可反向 import）；DAO 只接收已归一化的 `status` 字符串与布尔标志，自身不 import kernel 的归一化、也不承载业务判断。
2. **写 port 走构造器注入**（spec 明确）：`AgentRunService` 构造 `BohriumJobLedgerPort` 并闭包 `session_id`/`task_id`/`user_id`/`org_id` → 经 `AgentRunPorts.bohrium_job_ledger` → `Exp._init_builtin_tools()` 注入 `BohriumTool.__init__`。**不走 `runner_state`、不读 `run_meta`/`SESSIONS`/`HookExecutor`/临时 dict。**
3. **读 port 闭包身份字段**（方案 A，对齐 `_RunSessionEventHistory`）：`SessionJobsPort` 实现闭包 `user_id`/`org_id`，`SessionJobsQuery` 维持只带 `session_id` 不扩展，`ContextAssembler._load_jobs_or_empty` 不改。
4. **不变量集中在 DAO**：业务代码不得裸写 `status`/`next_poll_at`/`terminal_at`/`result_dir`。DAO 方法是唯一写入口，DB CHECK 约束是第二道防线。
5. **所有调度时间由 DB `NOW()` 计算**，不在 Python 侧用本地时间算 `next_poll_at`。时间列一律 `TIMESTAMP`（UTC 锚定）。
6. **不落库平台原始返回**：表只存归一化后的 `status`，不存 `status_code` 与任何原始 JSON（submit 响应 / `get_job_detail` 返回）。排障时用 job row 的 `user_id`/`org_id`/`project_id`/`sandbox`/`job_id` 现查一次 `get_job_detail` 取当前权威状态。因此 DAO 不写 JSON 列、不需要 redaction 与大小截断逻辑。
7. **禁止兼容/迁移内联**：表结构变更走 `src/sql/` 外部脚本；DTO（如 `SessionJobs`）直接改形状并同步所有消费者与测试，不保留旧字段兜底。

---

## File Structure

**新建文件**
- `src/sql/create_bohrium_jobs_table.sql` — `bohrium_jobs` 建表 DDL（spec 原样）。
- `src/dao/bohrium_jobs_table.py` — `BohriumJobsTable`（DAO，全部状态不变量）。
- `src/services/bohrium_jobs_wiring.py` — `BohriumJobLedgerPort` 实现 + `SessionJobsPort` 实现 + 构造工厂。
- `src/services/bohrium_poller.py` — `BohriumJobPoller` 可测核心（不接进程）。
- `tests/dao/conftest.py` — 真实 MySQL fixture（读 `.env.test`，建表 + 清表 + skip-on-unavailable）。
- `tests/dao/test_bohrium_jobs_table.py` — DAO 真实库测试。
- `tests/dao/test_bohrium_jobs_constraints.py` — CHECK/collation/时区不变量测试。
- `tests/dao/test_bohrium_jobs_claim.py` — `FOR UPDATE SKIP LOCKED` 并发认领测试。
- `tests/matmaster/bohrium/test_ledger_status.py` — `to_ledger_status` 归一化纯函数测试。
- `tests/services/test_bohrium_jobs_wiring.py` — 两个 port 实现的单测（mock DAO）。
- `tests/services/test_bohrium_poller.py` — poller 核心测试（真实 DAO + mock client/UserService）。
- `tests/matmaster/test_bohrium_ledger_injection.py` — 构造器/装配注入与架构边界测试。

**修改文件**
- `src/sql/README.md` — 新增 `create_bohrium_jobs_table.sql` 执行说明。
- `src/base/base_table.py` — `__init__` 增加可选 `db_config` 注入点（可测性）。
- `matmaster/bohrium/status.py` — 增加 `to_ledger_status` + `LedgerStatusDecision`。
- `matmaster/context/ports.py` — 增加 `BohriumJobLedgerPort`；`SessionJobs` 增加 `recent_terminal_jobs`。
- `matmaster/types/runtime_ports.py` — `AgentRunPorts` 增加 `bohrium_job_ledger` 与 `session_jobs` 字段。
- `matmaster/context/sources/session_jobs.py` — renderer 同时渲染 active + recent terminal。
- `matmaster/core/runtime_context_assembly.py` — 用 `ctx.request.ports.session_jobs` 替代硬编码空实现。
- `matmaster/core/exp.py` — `BohriumTool(...)` 注入 `job_ledger`。
- `matmaster/tools/builtin/bohrium_tool/tool.py` — `__init__` 接收 `job_ledger`；`_submit/_poll/_download/_kill` 集成 ledger 写入。
- `src/services/agent_run_service.py` — 构造并注入两个 port。
- `tests/matmaster/context/sources/test_session_jobs.py` — 更新 renderer 测试覆盖 recent terminal。

---

## 开发与验证约定

- **统一用 uv 环境**：所有命令用 `uv run ...`（例：`uv run pytest tests/dao/test_bohrium_jobs_table.py -v`）。不要用系统 `python`/`pytest`。
- **真实库测试前置**：阶段 0/1/4 的 DB 测试需要本地 docker 起的 MySQL，连接来自 `.env.test`。运行前确保该 MySQL 已启动；未启动时这些测试会 `pytest.skip`（不是失败）。纯逻辑测试（归一化、renderer、port wiring mock）无需 MySQL。
- **每个 Task 末尾 commit**；commit message 用 Conventional Commits（如 `feat(bohrium-ledger): ...`）。
- **每次改完代码跑 pre-commit 关注点**：单文件 ≤ 1000 行、black/isort/flake8。`src/dao/bohrium_jobs_table.py` 若接近行数上限，可把查询 helper 拆到同目录新文件。

---

## Phase 0 — 迁移脚本与真实 MySQL 测试基础设施

### Task 0.1：建表 SQL 脚本

**Files:**
- Create: `src/sql/create_bohrium_jobs_table.sql`
- Modify: `src/sql/README.md`

- [ ] **Step 1: 写建表 DDL**（spec 原样，单条 CREATE TABLE）

`src/sql/create_bohrium_jobs_table.sql`：
```sql
-- Bohrium 作业状态表。新环境执行本脚本创建（与 create_chat_tables.sql 同级，手动执行）。
-- 需要 MySQL 8.0.16+（CHECK 约束强制执行）。
CREATE TABLE `bohrium_jobs` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,

    `session_id` VARCHAR(255) COLLATE utf8mb4_bin NOT NULL,
    `task_id` VARCHAR(255) COLLATE utf8mb4_bin NOT NULL,
    `user_id` VARCHAR(255) COLLATE utf8mb4_bin NOT NULL,
    `org_id` VARCHAR(255) COLLATE utf8mb4_bin NOT NULL,

    `job_id` VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
    `job_name` VARCHAR(255) NULL,
    `project_id` BIGINT UNSIGNED NOT NULL,
    `sandbox` TINYINT(1) NOT NULL DEFAULT 0,

    `status` VARCHAR(32) COLLATE utf8mb4_bin NOT NULL DEFAULT 'submitted',

    `poll_count` INT UNSIGNED NOT NULL DEFAULT 0,
    `next_poll_at` TIMESTAMP NULL,
    `last_polled_at` TIMESTAMP NULL,

    `submitted_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `terminal_at` TIMESTAMP NULL,
    `result_dir` VARCHAR(1024) NULL,

    `last_error` TEXT NULL,
    `last_error_at` TIMESTAMP NULL,

    `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    UNIQUE KEY `uk_owner_job_id` (`user_id`, `org_id`, `sandbox`, `job_id`),
    KEY `idx_poll_due` (`next_poll_at`, `id`),
    KEY `idx_session_active` (`user_id`, `org_id`, `session_id`, `submitted_at`),
    KEY `idx_session_recent` (`user_id`, `org_id`, `session_id`, `terminal_at`, `submitted_at`),

    CONSTRAINT `chk_sandbox` CHECK (`sandbox` IN (0, 1)),
    CONSTRAINT `chk_status` CHECK (`status` IN (
        'submitted', 'running', 'terminating', 'unknown',
        'finished', 'failed', 'stopped', 'downloaded'
    )),
    CONSTRAINT `chk_active_poll` CHECK (
        (`status` IN ('submitted', 'running', 'terminating', 'unknown') AND `next_poll_at` IS NOT NULL)
        OR
        (`status` IN ('finished', 'failed', 'stopped', 'downloaded') AND `next_poll_at` IS NULL)
    ),
    CONSTRAINT `chk_terminal_at` CHECK (
        (`status` IN ('submitted', 'running', 'terminating', 'unknown') AND `terminal_at` IS NULL)
        OR
        (`status` IN ('finished', 'failed', 'stopped', 'downloaded') AND `terminal_at` IS NOT NULL)
    ),
    CONSTRAINT `chk_downloaded_dir` CHECK (`status` <> 'downloaded' OR `result_dir` IS NOT NULL)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='Bohrium 作业状态表';
```

- [ ] **Step 2: 更新 README**

在 `src/sql/README.md` 的"新环境"列表追加一行（紧跟 `create_bohrium_nodes_table.sql` 之后）：
```markdown
- **Bohrium 作业状态**：执行 `create_bohrium_jobs_table.sql` 创建 `bohrium_jobs`（作业状态事实源，需 MySQL 8.0.16+）。删除 session 不级联删除本表（无 FK），retention 由独立策略处理。
```

- [ ] **Step 3: 用 .env.test 的 MySQL 验证建表**

Run（确保本地 docker MySQL 已起）：
```bash
set -a; source .env.test; set +a
uv run python -c "
import pymysql, pathlib
cfg=dict(host=__import__('os').getenv('MYSQL_HOST'),port=int(__import__('os').getenv('MYSQL_PORT','3306')),user=__import__('os').getenv('MYSQL_USER'),password=__import__('os').getenv('MYSQL_PASSWORD'),database=__import__('os').getenv('MYSQL_DATABASE'))
c=pymysql.connect(**cfg); cur=c.cursor()
cur.execute('DROP TABLE IF EXISTS bohrium_jobs')
cur.execute(pathlib.Path('src/sql/create_bohrium_jobs_table.sql').read_text().rstrip().rstrip(';'))
cur.execute('SELECT VERSION()'); print('mysql', cur.fetchone())
cur.execute('SHOW CREATE TABLE bohrium_jobs'); print('created OK')
c.commit(); c.close()
"
```
Expected: 打印 MySQL 版本（≥ 8.0.16）与 `created OK`，无报错。

- [ ] **Step 4: Commit**

```bash
git add src/sql/create_bohrium_jobs_table.sql src/sql/README.md
git commit -m "feat(bohrium-ledger): add bohrium_jobs table DDL and README"
```

---

### Task 0.2：BaseTable 支持注入 db_config（可测性）

**Files:**
- Modify: `src/base/base_table.py:26-32`
- Test: `tests/dao/test_base_table_db_config.py`

- [ ] **Step 1: 写失败测试**

`tests/dao/test_base_table_db_config.py`：
```python
from __future__ import annotations

from src.base.base_table import BaseTable


class _Probe(BaseTable):
    table_name = "probe_tbl"

    def init_table(self) -> None:  # 跳过真实连库
        return None


def test_base_table_uses_injected_db_config() -> None:
    injected = {"host": "h", "port": 1, "user": "u", "database": "d"}
    t = _Probe(db_config=injected)
    assert t.db_config is injected


def test_base_table_defaults_to_global_db_config() -> None:
    from src.utils.constant import DB_CONFIG

    t = _Probe()
    assert t.db_config is DB_CONFIG
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/dao/test_base_table_db_config.py -v`
Expected: FAIL，`_Probe(db_config=...)` 报 `TypeError: __init__() got an unexpected keyword argument 'db_config'`。

- [ ] **Step 3: 实现 db_config 注入**

把 `src/base/base_table.py:26-32` 的 `__init__` 改为：
```python
    def __init__(self, db_config: dict | None = None):
        # 检查子类是否定义了 table_name
        if self.table_name is None:
            raise ValueError(f"{self.__class__.__name__} 必须定义类属性 'table_name'")

        self.db_config = db_config if db_config is not None else DB_CONFIG
        self.init_table()
```
（仅新增可选参数，默认仍用全局 `DB_CONFIG`，所有现有 `XxxTable()` 调用行为不变。）

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/dao/test_base_table_db_config.py -v`
Expected: PASS（2 passed）。

- [ ] **Step 5: Commit**

```bash
git add src/base/base_table.py tests/dao/test_base_table_db_config.py
git commit -m "feat(bohrium-ledger): allow injecting db_config into BaseTable for tests"
```

---

### Task 0.3：真实 MySQL 测试 fixture

**Files:**
- Create: `tests/dao/conftest.py`
- Create: `tests/dao/__init__.py`（空文件，确保包可发现）

- [ ] **Step 1: 写 fixture（无独立测试，由 Phase 1 消费）**

`tests/dao/__init__.py`：空文件。

`tests/dao/conftest.py`：
```python
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pymysql
import pytest
from dotenv import dotenv_values

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SQL_FILE = _REPO_ROOT / "src" / "sql" / "create_bohrium_jobs_table.sql"


def _test_db_config() -> dict[str, Any]:
    """优先用进程环境变量，回退 .env.test。"""
    values = dotenv_values(_REPO_ROOT / ".env.test")

    def pick(key: str, default: str) -> str:
        return os.getenv(key) or values.get(key) or default

    return {
        "host": pick("MYSQL_HOST", "localhost"),
        "port": int(pick("MYSQL_PORT", "3306")),
        "user": pick("MYSQL_USER", "root"),
        "password": pick("MYSQL_PASSWORD", "password"),
        "database": pick("MYSQL_DATABASE", "matmaster"),
        "charset": "utf8mb4",
        "cursorclass": pymysql.cursors.DictCursor,
        "autocommit": False,
    }


@pytest.fixture(scope="session")
def bohrium_jobs_db_config() -> dict[str, Any]:
    """连库 + DROP/CREATE bohrium_jobs；连不上则 skip 整个依赖它的测试。"""
    cfg = _test_db_config()
    try:
        conn = pymysql.connect(**cfg)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"bohrium_jobs DAO tests require MySQL from .env.test: {exc}")
    ddl = _SQL_FILE.read_text(encoding="utf-8").rstrip().rstrip(";")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT VERSION() AS v")
            version = str(cur.fetchone()["v"])
            cur.execute("DROP TABLE IF EXISTS `bohrium_jobs`")
            cur.execute(ddl)
        conn.commit()
    finally:
        conn.close()
    # 8.0.16+ 才强制 CHECK；低版本直接 skip，避免假绿。
    major_minor_patch = tuple(int(p) for p in version.split("-")[0].split(".")[:3])
    if major_minor_patch < (8, 0, 16):
        pytest.skip(f"bohrium_jobs needs MySQL >= 8.0.16, got {version}")
    return cfg


@pytest.fixture()
def jobs_table(bohrium_jobs_db_config: dict[str, Any]):
    """每个测试前 TRUNCATE，返回注入测试库配置的 BohriumJobsTable。"""
    from src.dao.bohrium_jobs_table import BohriumJobsTable

    table = BohriumJobsTable(db_config=bohrium_jobs_db_config)
    with table.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE `bohrium_jobs`")
        conn.commit()
    return table


@pytest.fixture()
def db_conn(bohrium_jobs_db_config: dict[str, Any]):
    """裸连接，给约束/并发测试直接执行 SQL 用。"""
    conn = pymysql.connect(**bohrium_jobs_db_config)
    try:
        yield conn
    finally:
        conn.close()
```

- [ ] **Step 2: 冒烟验证 fixture 能连库建表**

先放一个临时冒烟测试 `tests/dao/test_fixture_smoke.py`：
```python
def test_fixture_creates_table(db_conn) -> None:
    with db_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS c FROM bohrium_jobs")
        assert cur.fetchone()["c"] == 0
```

Run（MySQL 已起）: `uv run pytest tests/dao/test_fixture_smoke.py -v`
Expected: PASS（表存在且为空）。若 MySQL 未起：SKIPPED（不是 FAIL）。

- [ ] **Step 3: 删除冒烟测试**

```bash
git rm -f tests/dao/test_fixture_smoke.py 2>/dev/null || rm -f tests/dao/test_fixture_smoke.py
```

- [ ] **Step 4: Commit**

```bash
git add tests/dao/conftest.py tests/dao/__init__.py
git commit -m "test(bohrium-ledger): add real-MySQL fixtures reading .env.test"
```

---

## Phase 1 — DAO 与状态归一化

### Task 1.1：状态归一化纯函数 `to_ledger_status`

**Files:**
- Modify: `matmaster/bohrium/status.py`
- Test: `tests/matmaster/bohrium/test_ledger_status.py`

- [ ] **Step 1: 写失败测试**（覆盖 spec 映射表每一行 + unknown）

`tests/matmaster/bohrium/test_ledger_status.py`：
```python
from __future__ import annotations

import pytest

from matmaster.bohrium.status import LedgerStatusDecision, to_ledger_status


@pytest.mark.parametrize(
    "code,status,is_terminal",
    [
        (-10, "running", False),     # Prepared
        (0, "running", False),       # Pending
        (1, "running", False),       # Running
        (3, "running", False),       # Scheduling
        (8, "running", False),       # Uploading
        (9, "running", False),       # Wait
        (4, "terminating", False),   # Stopping
        (6, "terminating", False),   # Terminating
        (7, "terminating", False),   # Killing
        (2, "finished", True),       # Finished
        (-1, "failed", True),        # Failed
        (-2, "stopped", True),       # Deleted
        (5, "stopped", True),        # Stopped
        (999, "unknown", False),     # 无法解析
        (-999, "unknown", False),
    ],
)
def test_to_ledger_status_maps_platform_codes(
    code: int, status: str, is_terminal: bool
) -> None:
    decision = to_ledger_status(code)
    assert isinstance(decision, LedgerStatusDecision)
    assert decision.status == status
    assert decision.is_terminal is is_terminal


def test_terminating_and_unknown_keep_polling() -> None:
    # terminating / unknown 仍是活跃态：is_terminal=False（继续轮询）
    assert to_ledger_status(7).is_terminal is False
    assert to_ledger_status(123).is_terminal is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/matmaster/bohrium/test_ledger_status.py -v`
Expected: FAIL，`ImportError: cannot import name 'to_ledger_status'`。

- [ ] **Step 3: 实现归一化**

在 `matmaster/bohrium/status.py` 末尾追加（保持现有 `STATUS_MAP`/`status_name` 不动）：
```python
from dataclasses import dataclass


@dataclass(frozen=True)
class LedgerStatusDecision:
    """平台状态码归一化到 ledger 语义的结果。

    status: ledger 活跃/平台终态字符串（不含 submitted/downloaded，那是动作产生的）。
    is_terminal: True 表示平台计算终态（finished/failed/stopped），写 terminal_at 且停轮询。
    """

    status: str
    is_terminal: bool


_LEDGER_RUNNING_CODES = frozenset({-10, 0, 1, 3, 8, 9})
_LEDGER_TERMINATING_CODES = frozenset({4, 6, 7})
_LEDGER_STOPPED_CODES = frozenset({-2, 5})
_LEDGER_FINISHED_CODE = 2
_LEDGER_FAILED_CODE = -1


def to_ledger_status(code: int) -> LedgerStatusDecision:
    """把 Bohrium 平台状态码映射为 ledger status（见设计文档 Status Rules 映射表）。"""
    if code in _LEDGER_RUNNING_CODES:
        return LedgerStatusDecision("running", False)
    if code in _LEDGER_TERMINATING_CODES:
        return LedgerStatusDecision("terminating", False)
    if code == _LEDGER_FINISHED_CODE:
        return LedgerStatusDecision("finished", True)
    if code == _LEDGER_FAILED_CODE:
        return LedgerStatusDecision("failed", True)
    if code in _LEDGER_STOPPED_CODES:
        return LedgerStatusDecision("stopped", True)
    return LedgerStatusDecision("unknown", False)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/matmaster/bohrium/test_ledger_status.py -v`
Expected: PASS（全部 parametrize case 通过）。

- [ ] **Step 5: Commit**

```bash
git add matmaster/bohrium/status.py tests/matmaster/bohrium/test_ledger_status.py
git commit -m "feat(bohrium-ledger): add to_ledger_status normalization"
```

---

### Task 1.2：DAO 骨架 + `insert_submitted`

**Files:**
- Create: `src/dao/bohrium_jobs_table.py`
- Test: `tests/dao/test_bohrium_jobs_table.py`

- [ ] **Step 1: 写失败测试**（真实库：插入、查询、project_id 哨兵拒绝）

`tests/dao/test_bohrium_jobs_table.py`：
```python
from __future__ import annotations

import pytest


def _submit_kwargs(**over):
    base = dict(
        session_id="sess-1",
        task_id="ws_task1",
        user_id="user-1",
        org_id="org-1",
        job_id="12345",
        job_name="matmaster-job",
        project_id=42,
        sandbox=True,
    )
    base.update(over)
    return base


def test_insert_submitted_sets_active_invariants(jobs_table) -> None:
    jobs_table.insert_submitted(**_submit_kwargs())
    row = jobs_table.get_by_owner_job(
        user_id="user-1", org_id="org-1", sandbox=True, job_id="12345"
    )
    assert row["status"] == "submitted"
    assert row["next_poll_at"] is not None          # 活跃必须有 next_poll_at
    assert row["terminal_at"] is None                # 活跃 terminal_at 为 NULL
    assert row["next_poll_at"] == row["submitted_at"]  # 新作业即到期
    assert row["sandbox"] == 1
    assert row["project_id"] == 42


def test_insert_submitted_rejects_sentinel_project_id(jobs_table) -> None:
    with pytest.raises(ValueError):
        jobs_table.insert_submitted(**_submit_kwargs(project_id=-1))
    with pytest.raises(ValueError):
        jobs_table.insert_submitted(**_submit_kwargs(project_id=0))


def test_insert_submitted_upsert_is_idempotent(jobs_table) -> None:
    jobs_table.insert_submitted(**_submit_kwargs())
    # 重复 submit 同一 (user_id, org_id, sandbox, job_id) 不应抛错，也不新增第二行
    jobs_table.insert_submitted(**_submit_kwargs(job_name="renamed"))
    rows = jobs_table.list_all_for_test()
    assert len(rows) == 1


def test_unique_key_spans_owner_sandbox_jobid(jobs_table) -> None:
    jobs_table.insert_submitted(**_submit_kwargs(sandbox=True, job_id="999"))
    # 同 job_id 不同 sandbox -> 不同发号空间，应能共存
    jobs_table.insert_submitted(**_submit_kwargs(sandbox=False, job_id="999"))
    # 同 job_id 不同 org -> 应能共存
    jobs_table.insert_submitted(**_submit_kwargs(org_id="org-2", job_id="999"))
    rows = jobs_table.list_all_for_test()
    assert len(rows) == 3


def test_binary_collation_is_case_sensitive(jobs_table) -> None:
    jobs_table.insert_submitted(**_submit_kwargs(job_id="AbC123"))
    jobs_table.insert_submitted(**_submit_kwargs(job_id="abc123"))
    # 大小写不同 -> binary collation 视为不同 ID，两行共存
    rows = jobs_table.list_all_for_test()
    assert len(rows) == 2
    assert jobs_table.get_by_owner_job(
        user_id="user-1", org_id="org-1", sandbox=True, job_id="abc123"
    ) is not None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/dao/test_bohrium_jobs_table.py -v`
Expected: FAIL（`ModuleNotFoundError: src.dao.bohrium_jobs_table`），或 MySQL 未起时全部 SKIPPED。

- [ ] **Step 3: 实现 DAO 骨架 + insert_submitted**

`src/dao/bohrium_jobs_table.py`：
```python
"""Bohrium 作业状态表 DAO。

本模块是 bohrium_jobs 的唯一写入口，集中封装 spec 的状态不变量：
- 活跃态恒有 next_poll_at、terminal_at 为 NULL；终态反之。
- 单调性：downloaded 不被平台 poll 回退。
- 所有调度时间用 DB NOW() 计算，不在 Python 侧算。
业务代码不得裸写 status / next_poll_at / terminal_at / result_dir。
"""

from __future__ import annotations

import logging
from typing import Any

from src.base.base_table import BaseTable

logger = logging.getLogger(__name__)


class BohriumJobsTable(BaseTable):
    """bohrium_jobs DAO（raw SQL，同步 PyMySQL）。"""

    table_name = "bohrium_jobs"

    def init_table(self) -> None:
        # 建表走外部脚本 src/sql/create_bohrium_jobs_table.sql；这里仅检查存在性。
        super().init_table()

    # ---------- 写入：submit ----------

    def insert_submitted(
        self,
        *,
        session_id: str,
        task_id: str,
        user_id: str,
        org_id: str,
        job_id: str,
        job_name: str | None,
        project_id: int,
        sandbox: bool,
    ) -> None:
        """job/add 成功后写入。next_poll_at = submitted_at（新作业即到期）。

        project_id 必须 > 0：BohriumCredentials 解析失败默认哨兵 -1，UNSIGNED 列
        无法写入，必须在此拒绝而非让 DB 静默截断。
        """
        if project_id is None or int(project_id) <= 0:
            raise ValueError(
                f"bohrium_jobs.project_id must be > 0, got {project_id!r}"
            )
        sql = f"""
            INSERT INTO {self.table_name}
                (session_id, task_id, user_id, org_id, job_id, job_name,
                 project_id, sandbox, status, poll_count,
                 submitted_at, next_poll_at)
            VALUES
                (%s, %s, %s, %s, %s, %s,
                 %s, %s, 'submitted', 0,
                 NOW(), NOW())
            ON DUPLICATE KEY UPDATE
                session_id = VALUES(session_id),
                task_id = VALUES(task_id),
                job_name = VALUES(job_name),
                project_id = VALUES(project_id)
        """
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql,
                    (
                        session_id,
                        task_id,
                        user_id,
                        org_id,
                        job_id,
                        job_name,
                        int(project_id),
                        1 if sandbox else 0,
                    ),
                )
            conn.commit()

    # ---------- 读取辅助 ----------

    def get_by_owner_job(
        self, *, user_id: str, org_id: str, sandbox: bool, job_id: str
    ) -> dict[str, Any] | None:
        sql = f"""
            SELECT * FROM {self.table_name}
            WHERE user_id = %s AND org_id = %s AND sandbox = %s AND job_id = %s
        """
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (user_id, org_id, 1 if sandbox else 0, job_id))
                return cur.fetchone()

    def list_all_for_test(self) -> list[dict[str, Any]]:
        """仅供测试：返回全部行。"""
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT * FROM {self.table_name} ORDER BY id ASC")
                return list(cur.fetchall())
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/dao/test_bohrium_jobs_table.py -v`
Expected: PASS（MySQL 已起时全部通过；未起时 SKIPPED）。

- [ ] **Step 5: Commit**

```bash
git add src/dao/bohrium_jobs_table.py tests/dao/test_bohrium_jobs_table.py
git commit -m "feat(bohrium-ledger): BohriumJobsTable insert_submitted"
```

---

### Task 1.3：`apply_poll` 原子写回（单调性保护）

**Files:**
- Modify: `src/dao/bohrium_jobs_table.py`
- Test: `tests/dao/test_bohrium_jobs_table.py`

- [ ] **Step 1: 写失败测试**

在 `tests/dao/test_bohrium_jobs_table.py` 追加：
```python
def test_apply_poll_running_advances_next_poll(jobs_table) -> None:
    jobs_table.insert_submitted(**_submit_kwargs())
    jobs_table.apply_poll(
        user_id="user-1", org_id="org-1", sandbox=True, job_id="12345",
        status="running", is_terminal=False, backoff_seconds=30,
    )
    row = jobs_table.get_by_owner_job(
        user_id="user-1", org_id="org-1", sandbox=True, job_id="12345"
    )
    assert row["status"] == "running"
    assert row["poll_count"] == 1
    assert row["last_polled_at"] is not None
    assert row["next_poll_at"] is not None      # 仍活跃
    assert row["terminal_at"] is None


def test_apply_poll_terminal_sets_terminal_at_and_stops_polling(jobs_table) -> None:
    jobs_table.insert_submitted(**_submit_kwargs())
    jobs_table.apply_poll(
        user_id="user-1", org_id="org-1", sandbox=True, job_id="12345",
        status="finished", is_terminal=True, backoff_seconds=30,
    )
    row = jobs_table.get_by_owner_job(
        user_id="user-1", org_id="org-1", sandbox=True, job_id="12345"
    )
    assert row["status"] == "finished"
    assert row["next_poll_at"] is None          # 终态停轮询
    assert row["terminal_at"] is not None        # 终态有 terminal_at


def test_apply_poll_does_not_revert_terminal_to_active(jobs_table) -> None:
    jobs_table.insert_submitted(**_submit_kwargs())
    jobs_table.apply_poll(
        user_id="user-1", org_id="org-1", sandbox=True, job_id="12345",
        status="finished", is_terminal=True, backoff_seconds=30,
    )
    # 旧 poll / 平台抖动结果（running）到达，不得把终态写回活跃态
    jobs_table.apply_poll(
        user_id="user-1", org_id="org-1", sandbox=True, job_id="12345",
        status="running", is_terminal=False, backoff_seconds=30,
    )
    row = jobs_table.get_by_owner_job(
        user_id="user-1", org_id="org-1", sandbox=True, job_id="12345"
    )
    assert row["status"] == "finished"        # 终态单调，不回退
    assert row["next_poll_at"] is None
    assert row["terminal_at"] is not None
    assert row["poll_count"] == 2             # poll 事实仍计数


def test_apply_poll_does_not_revert_downloaded(jobs_table) -> None:
    jobs_table.insert_submitted(**_submit_kwargs())
    jobs_table.apply_download(
        user_id="user-1", org_id="org-1", sandbox=True, job_id="12345",
        platform_status="finished", platform_is_terminal=True,
        result_dir="results/run_12345",
    )
    # 旧 poll 结果（finished）到达，不得把 downloaded 写回 finished
    jobs_table.apply_poll(
        user_id="user-1", org_id="org-1", sandbox=True, job_id="12345",
        status="finished", is_terminal=True, backoff_seconds=30,
    )
    row = jobs_table.get_by_owner_job(
        user_id="user-1", org_id="org-1", sandbox=True, job_id="12345"
    )
    assert row["status"] == "downloaded"        # 单调，不回退
    assert row["result_dir"] == "results/run_12345"
    assert row["next_poll_at"] is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/dao/test_bohrium_jobs_table.py -k apply_poll -v`
Expected: FAIL（`AttributeError: 'BohriumJobsTable' object has no attribute 'apply_poll'`）。

- [ ] **Step 3: 实现 apply_poll**（spec 原子 CASE WHEN）

在 `BohriumJobsTable` 增加方法：
```python
    def apply_poll(
        self,
        *,
        user_id: str,
        org_id: str,
        sandbox: bool,
        job_id: str,
        status: str,
        is_terminal: bool,
        backoff_seconds: int,
    ) -> None:
        """poll 写回。原子保护：downloaded/终态不被回退；终态停轮询、补 terminal_at。

        status / is_terminal 由调用方经 to_ledger_status 归一化后传入。
        next_poll_at 用 DB NOW() + backoff 计算。
        """
        sql = f"""
            UPDATE {self.table_name}
            SET
                status = CASE
                    WHEN status IN ('finished', 'failed', 'stopped', 'downloaded')
                    THEN status ELSE %s END,
                last_polled_at = NOW(),
                poll_count = poll_count + 1,
                terminal_at = CASE
                    WHEN status IN ('finished', 'failed', 'stopped', 'downloaded')
                    THEN terminal_at
                    WHEN %s THEN COALESCE(terminal_at, NOW())
                    ELSE terminal_at
                END,
                next_poll_at = CASE
                    WHEN status IN ('finished', 'failed', 'stopped', 'downloaded')
                    THEN NULL
                    WHEN %s THEN NULL
                    ELSE NOW() + INTERVAL %s SECOND
                END
            WHERE user_id = %s AND org_id = %s AND sandbox = %s AND job_id = %s
        """
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql,
                    (
                        status,
                        is_terminal,
                        is_terminal,
                        int(backoff_seconds),
                        user_id,
                        org_id,
                        1 if sandbox else 0,
                        job_id,
                    ),
                )
            conn.commit()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/dao/test_bohrium_jobs_table.py -k "apply_poll or downloaded or terminal_to_active" -v`
Expected: PASS（注意此步依赖 Task 1.4 的 `apply_download`；若先做本任务，把 `test_apply_poll_does_not_revert_downloaded` 暂标 `@pytest.mark.skip(reason="needs apply_download (Task 1.4)")`，Task 1.4 完成后取消）。

- [ ] **Step 5: Commit**

```bash
git add src/dao/bohrium_jobs_table.py tests/dao/test_bohrium_jobs_table.py
git commit -m "feat(bohrium-ledger): apply_poll with monotonic terminal guards"
```

---

### Task 1.4：`apply_download` 与 `apply_kill`

**Files:**
- Modify: `src/dao/bohrium_jobs_table.py`
- Test: `tests/dao/test_bohrium_jobs_table.py`

- [ ] **Step 1: 写失败测试**

在 `tests/dao/test_bohrium_jobs_table.py` 追加：
```python
def test_apply_download_finished_sets_downloaded(jobs_table) -> None:
    jobs_table.insert_submitted(**_submit_kwargs())
    jobs_table.apply_download(
        user_id="user-1", org_id="org-1", sandbox=True, job_id="12345",
        platform_status="finished", platform_is_terminal=True,
        result_dir="results/run_12345",
    )
    row = jobs_table.get_by_owner_job(
        user_id="user-1", org_id="org-1", sandbox=True, job_id="12345"
    )
    assert row["status"] == "downloaded"
    assert row["result_dir"] == "results/run_12345"
    assert row["next_poll_at"] is None
    assert row["terminal_at"] is not None        # 首次确认终态时 COALESCE 补齐


def test_apply_download_failed_keeps_failure_status(jobs_table) -> None:
    jobs_table.insert_submitted(**_submit_kwargs())
    jobs_table.apply_download(
        user_id="user-1", org_id="org-1", sandbox=True, job_id="12345",
        platform_status="failed", platform_is_terminal=True,
        result_dir="results/run_12345",
    )
    row = jobs_table.get_by_owner_job(
        user_id="user-1", org_id="org-1", sandbox=True, job_id="12345"
    )
    assert row["status"] == "failed"             # 保留失败终态
    assert row["result_dir"] == "results/run_12345"
    assert row["terminal_at"] is not None
    assert row["next_poll_at"] is None


def test_apply_kill_sets_terminating_keeps_polling(jobs_table) -> None:
    jobs_table.insert_submitted(**_submit_kwargs())
    jobs_table.apply_kill(
        user_id="user-1", org_id="org-1", sandbox=True, job_id="12345"
    )
    row = jobs_table.get_by_owner_job(
        user_id="user-1", org_id="org-1", sandbox=True, job_id="12345"
    )
    assert row["status"] == "terminating"
    assert row["next_poll_at"] is not None        # 保留轮询以确认最终态
    assert row["terminal_at"] is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/dao/test_bohrium_jobs_table.py -k "apply_download or apply_kill" -v`
Expected: FAIL（缺 `apply_download` / `apply_kill`）。

- [ ] **Step 3: 实现 apply_download 与 apply_kill**

在 `BohriumJobsTable` 增加：
```python
    def apply_download(
        self,
        *,
        user_id: str,
        org_id: str,
        sandbox: bool,
        job_id: str,
        platform_status: str,
        platform_is_terminal: bool,
        result_dir: str,
    ) -> None:
        """download 成功后写回。

        - finished job：status='downloaded'、result_dir、next_poll_at=NULL，
          terminal_at = COALESCE(terminal_at, NOW())（首次确认终态时补齐）。
        - failed/stopped job：只补 result_dir，保留原失败终态；若此次首次确认
          平台终态，则一并补 terminal_at 并停轮询。
        platform_status / platform_is_terminal 来自 to_ledger_status。
        """
        if platform_status == "finished":
            new_status_sql = "'downloaded'"
        else:
            # 保留失败终态：若当前已是该失败态则不变，否则写平台终态。
            new_status_sql = "%s"
        sql = f"""
            UPDATE {self.table_name}
            SET
                status = CASE WHEN status = 'downloaded' THEN status
                              ELSE {new_status_sql} END,
                last_polled_at = NOW(),
                result_dir = %s,
                terminal_at = CASE
                    WHEN %s THEN COALESCE(terminal_at, NOW())
                    ELSE terminal_at
                END,
                next_poll_at = CASE WHEN %s THEN NULL ELSE next_poll_at END
            WHERE user_id = %s AND org_id = %s AND sandbox = %s AND job_id = %s
        """
        params: list[Any] = []
        if platform_status != "finished":
            params.append(platform_status)
        params.extend(
            [
                result_dir,
                platform_is_terminal,
                platform_is_terminal,
                user_id,
                org_id,
                1 if sandbox else 0,
                job_id,
            ]
        )
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(params))
            conn.commit()

    def apply_kill(
        self, *, user_id: str, org_id: str, sandbox: bool, job_id: str
    ) -> None:
        """sandbox kill 请求成功后写 terminating，保留 next_poll_at 以便后续确认。"""
        sql = f"""
            UPDATE {self.table_name}
            SET status = CASE
                    WHEN status IN ('finished', 'failed', 'stopped', 'downloaded')
                    THEN status ELSE 'terminating' END,
                next_poll_at = CASE
                    WHEN status IN ('finished', 'failed', 'stopped', 'downloaded')
                    THEN next_poll_at ELSE COALESCE(next_poll_at, NOW()) END
            WHERE user_id = %s AND org_id = %s AND sandbox = %s AND job_id = %s
        """
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (user_id, org_id, 1 if sandbox else 0, job_id))
            conn.commit()
```

- [ ] **Step 4: 跑测试确认通过**（含取消 Task 1.3 暂 skip 的用例）

Run: `uv run pytest tests/dao/test_bohrium_jobs_table.py -v`
Expected: PASS（全部）。若 Task 1.3 标了 skip，现在删除该 `@pytest.mark.skip` 再跑。

- [ ] **Step 5: Commit**

```bash
git add src/dao/bohrium_jobs_table.py tests/dao/test_bohrium_jobs_table.py
git commit -m "feat(bohrium-ledger): apply_download and apply_kill"
```

---

### Task 1.5：session 维度查询（active + recent terminal）

**Files:**
- Modify: `src/dao/bohrium_jobs_table.py`
- Test: `tests/dao/test_bohrium_jobs_table.py`

- [ ] **Step 1: 写失败测试**

在 `tests/dao/test_bohrium_jobs_table.py` 追加：
```python
def test_query_session_active_returns_active_only_sorted(jobs_table) -> None:
    jobs_table.insert_submitted(**_submit_kwargs(job_id="a1"))
    jobs_table.insert_submitted(**_submit_kwargs(job_id="a2"))
    jobs_table.apply_poll(
        user_id="user-1", org_id="org-1", sandbox=True, job_id="a2",
        status="finished", is_terminal=True, backoff_seconds=30,
    )
    active = jobs_table.query_session_active(
        user_id="user-1", org_id="org-1", session_id="sess-1"
    )
    ids = [j["job_id"] for j in active]
    assert ids == ["a1"]                  # a2 已终态，被排除
    # agent-facing 字段固定，且不暴露 user_id/org_id/原始 JSON
    j = active[0]
    assert set(j.keys()) == {
        "job_id", "job_name", "status", "sandbox",
        "project_id", "submitted_at", "last_polled_at", "result_dir",
        "last_error", "last_error_at",
    }
    assert j["sandbox"] is True


def test_query_session_recent_terminal(jobs_table) -> None:
    for jid in ["t1", "t2", "t3"]:
        jobs_table.insert_submitted(**_submit_kwargs(job_id=jid))
        jobs_table.apply_poll(
            user_id="user-1", org_id="org-1", sandbox=True, job_id=jid,
            status="finished", is_terminal=True, backoff_seconds=30,
        )
    recent = jobs_table.query_session_recent_terminal(
        user_id="user-1", org_id="org-1", session_id="sess-1", limit=5
    )
    assert len(recent) == 3
    assert all(j["status"] in {"finished", "failed", "stopped", "downloaded"} for j in recent)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/dao/test_bohrium_jobs_table.py -k query_session -v`
Expected: FAIL（缺方法）。

- [ ] **Step 3: 实现查询方法**

在 `BohriumJobsTable` 增加 agent-facing 投影常量与两个查询：
```python
    # agent-facing 固定字段投影（不含 user_id/org_id/原始 JSON）。
    _AGENT_COLUMNS = (
        "job_id, job_name, status, sandbox, project_id, "
        "submitted_at, last_polled_at, result_dir, last_error, last_error_at"
    )

    @staticmethod
    def _to_agent_job(row: dict[str, Any]) -> dict[str, Any]:
        def _ts(v: Any) -> str | None:
            return v.strftime("%Y-%m-%d %H:%M:%S") if v is not None else None

        return {
            "job_id": str(row["job_id"]),
            "job_name": row["job_name"],
            "status": row["status"],
            "sandbox": bool(row["sandbox"]),
            "project_id": int(row["project_id"]),
            "submitted_at": _ts(row["submitted_at"]),
            "last_polled_at": _ts(row["last_polled_at"]),
            "result_dir": row["result_dir"],
            "last_error": row["last_error"],
            "last_error_at": _ts(row["last_error_at"]),
        }

    def query_session_active(
        self, *, user_id: str, org_id: str, session_id: str
    ) -> list[dict[str, Any]]:
        sql = f"""
            SELECT {self._AGENT_COLUMNS} FROM {self.table_name}
            WHERE user_id = %s AND org_id = %s AND session_id = %s
              AND status IN ('submitted', 'running', 'terminating', 'unknown')
            ORDER BY submitted_at ASC
        """
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (user_id, org_id, session_id))
                return [self._to_agent_job(r) for r in cur.fetchall()]

    def query_session_recent_terminal(
        self, *, user_id: str, org_id: str, session_id: str, limit: int = 5
    ) -> list[dict[str, Any]]:
        sql = f"""
            SELECT {self._AGENT_COLUMNS} FROM {self.table_name}
            WHERE user_id = %s AND org_id = %s AND session_id = %s
              AND terminal_at IS NOT NULL
            ORDER BY terminal_at DESC, submitted_at DESC
            LIMIT %s
        """
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (user_id, org_id, session_id, int(limit)))
                return [self._to_agent_job(r) for r in cur.fetchall()]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/dao/test_bohrium_jobs_table.py -k query_session -v`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add src/dao/bohrium_jobs_table.py tests/dao/test_bohrium_jobs_table.py
git commit -m "feat(bohrium-ledger): session active/recent-terminal queries"
```

---

### Task 1.6：`claim_due_batch`（FOR UPDATE SKIP LOCKED 短事务）

**Files:**
- Modify: `src/dao/bohrium_jobs_table.py`
- Test: `tests/dao/test_bohrium_jobs_claim.py`

- [ ] **Step 1: 写失败测试**（并发不相交 + 占位 + 终态不入队）

`tests/dao/test_bohrium_jobs_claim.py`：
```python
from __future__ import annotations

import pymysql


def _seed_active(jobs_table, n: int) -> None:
    for i in range(n):
        jobs_table.insert_submitted(
            session_id="sess-1", task_id="ws_t", user_id="user-1", org_id="org-1",
            job_id=f"j{i}", job_name=None, project_id=42, sandbox=True,
        )


def test_claim_due_batch_skips_terminal_jobs(jobs_table) -> None:
    _seed_active(jobs_table, 2)
    jobs_table.apply_poll(
        user_id="user-1", org_id="org-1", sandbox=True, job_id="j1",
        status="finished", is_terminal=True, backoff_seconds=30,
    )
    claimed = jobs_table.claim_due_batch(limit=50, claim_timeout_seconds=120)
    ids = {c["job_id"] for c in claimed}
    assert ids == {"j0"}                  # 终态 next_poll_at=NULL，天然不入队


def test_claim_due_batch_disjoint_under_concurrency(bohrium_jobs_db_config, jobs_table) -> None:
    _seed_active(jobs_table, 4)
    # 两个并发事务各自抢批，结果集必须不相交（SKIP LOCKED）。
    from src.dao.bohrium_jobs_table import BohriumJobsTable

    t_a = BohriumJobsTable(db_config=bohrium_jobs_db_config)
    t_b = BohriumJobsTable(db_config=bohrium_jobs_db_config)
    conn_a = pymysql.connect(**bohrium_jobs_db_config)
    conn_b = pymysql.connect(**bohrium_jobs_db_config)
    try:
        a = t_a._select_due_for_update(conn_a, limit=2)
        b = t_b._select_due_for_update(conn_b, limit=2)   # 跳过 A 锁住的行
        ids_a = {r["job_id"] for r in a}
        ids_b = {r["job_id"] for r in b}
        assert ids_a.isdisjoint(ids_b)
        assert len(ids_a) == 2 and len(ids_b) == 2
    finally:
        conn_a.rollback(); conn_a.close()
        conn_b.rollback(); conn_b.close()


def test_claim_places_future_next_poll(jobs_table) -> None:
    _seed_active(jobs_table, 1)
    claimed = jobs_table.claim_due_batch(limit=50, claim_timeout_seconds=120)
    assert len(claimed) == 1
    # 占位后立刻再抢，应抢不到（next_poll_at 已被推到未来）
    again = jobs_table.claim_due_batch(limit=50, claim_timeout_seconds=120)
    assert again == []


def test_claim_returns_poll_count_snapshot(jobs_table) -> None:
    _seed_active(jobs_table, 1)
    claimed = jobs_table.claim_due_batch(limit=50, claim_timeout_seconds=120)
    assert claimed and "poll_count" in claimed[0]
    assert claimed[0]["poll_count"] == 0   # poller 用它算 backoff
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/dao/test_bohrium_jobs_claim.py -v`
Expected: FAIL（缺 `claim_due_batch` / `_select_due_for_update`）。

- [ ] **Step 3: 实现短事务抢批**

在 `BohriumJobsTable` 增加（claim 用 job row 快照字段，供 poller 在事务外构造 context）：
```python
    _CLAIM_COLUMNS = (
        "id, session_id, user_id, org_id, project_id, job_id, sandbox, "
        "status, poll_count"
    )

    def _select_due_for_update(self, conn, *, limit: int) -> list[dict[str, Any]]:
        """在给定连接的事务内 SELECT ... FOR UPDATE SKIP LOCKED。不提交。"""
        sql = f"""
            SELECT {self._CLAIM_COLUMNS} FROM {self.table_name}
            WHERE next_poll_at <= NOW()
            ORDER BY next_poll_at ASC, id ASC
            LIMIT %s
            FOR UPDATE SKIP LOCKED
        """
        with conn.cursor() as cur:
            cur.execute(sql, (int(limit),))
            return list(cur.fetchall())

    def claim_due_batch(
        self, *, limit: int = 50, claim_timeout_seconds: int = 120
    ) -> list[dict[str, Any]]:
        """短事务：抢一批到期作业并把 next_poll_at 占位到未来，立即提交释放行锁。

        返回 job row 快照（含 user_id/org_id/project_id/sandbox/job_id），供
        poller 在事务外逐个调 get_job_detail。整体语义 at-least-once，写回需幂等。
        """
        with self.get_connection() as conn:
            try:
                rows = self._select_due_for_update(conn, limit=limit)
                if rows:
                    ids = [r["id"] for r in rows]
                    placeholders = ", ".join(["%s"] * len(ids))
                    conn.cursor().execute(
                        f"""
                        UPDATE {self.table_name}
                        SET next_poll_at = NOW() + INTERVAL %s SECOND
                        WHERE id IN ({placeholders})
                        """,
                        (int(claim_timeout_seconds), *ids),
                    )
                conn.commit()
                return rows
            except BaseException:
                conn.rollback()
                raise
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/dao/test_bohrium_jobs_claim.py -v`
Expected: PASS（并发用例验证 SKIP LOCKED 行集不相交）。

- [ ] **Step 5: Commit**

```bash
git add src/dao/bohrium_jobs_table.py tests/dao/test_bohrium_jobs_claim.py
git commit -m "feat(bohrium-ledger): claim_due_batch with FOR UPDATE SKIP LOCKED"
```

---

### Task 1.7：DB CHECK 约束与时区不变量测试

**Files:**
- Test: `tests/dao/test_bohrium_jobs_constraints.py`

- [ ] **Step 1: 写测试**（直接对裸连接写入，断言 DB 拒绝违规行）

`tests/dao/test_bohrium_jobs_constraints.py`：
```python
from __future__ import annotations

import pymysql
import pytest

_INSERT = """
    INSERT INTO bohrium_jobs
        (session_id, task_id, user_id, org_id, job_id, project_id, sandbox,
         status, next_poll_at, terminal_at, result_dir)
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
"""


def _insert(conn, **kw):
    with conn.cursor() as cur:
        cur.execute(
            _INSERT,
            (
                kw.get("session_id", "s"), kw.get("task_id", "t"),
                kw.get("user_id", "u"), kw.get("org_id", "o"),
                kw.get("job_id", "j"), kw.get("project_id", 1),
                kw.get("sandbox", 1), kw["status"],
                kw.get("next_poll_at"), kw.get("terminal_at"),
                kw.get("result_dir"),
            ),
        )
    conn.commit()


def test_chk_status_rejects_case_variant(db_conn) -> None:
    # utf8mb4_bin + chk_status 让大小写敏感：'Finished' 应被拒
    with pytest.raises(pymysql.err.OperationalError):
        _insert(db_conn, status="Finished", next_poll_at=None, terminal_at="2026-06-01 00:00:00")
    db_conn.rollback()


def test_chk_active_poll_requires_next_poll(db_conn) -> None:
    # 活跃态但 next_poll_at 为 NULL -> 拒绝
    with pytest.raises(pymysql.err.OperationalError):
        _insert(db_conn, status="running", next_poll_at=None, terminal_at=None)
    db_conn.rollback()


def test_chk_terminal_requires_terminal_at(db_conn) -> None:
    # 终态但 terminal_at 为 NULL -> 拒绝
    with pytest.raises(pymysql.err.OperationalError):
        _insert(db_conn, status="finished", next_poll_at=None, terminal_at=None)
    db_conn.rollback()


def test_chk_downloaded_requires_result_dir(db_conn) -> None:
    with pytest.raises(pymysql.err.OperationalError):
        _insert(
            db_conn, status="downloaded", next_poll_at=None,
            terminal_at="2026-06-01 00:00:00", result_dir=None,
        )
    db_conn.rollback()


def test_chk_sandbox_rejects_out_of_range(db_conn) -> None:
    with pytest.raises(pymysql.err.OperationalError):
        _insert(
            db_conn, status="running", next_poll_at="2026-06-01 00:00:00",
            terminal_at=None, sandbox=2,
        )
    db_conn.rollback()


def test_timestamp_utc_anchored_across_connection_timezones(bohrium_jobs_db_config) -> None:
    """不同连接时区下写入/读取 next_poll_at，next_poll_at<=NOW() 判定一致。

    TIMESTAMP 以 UTC 锚定，比较的绝对时刻不依赖连接 time_zone。
    """
    cfg = dict(bohrium_jobs_db_config)
    w = pymysql.connect(**cfg)
    try:
        with w.cursor() as cur:
            cur.execute("SET time_zone = '+00:00'")
            cur.execute(
                """
                INSERT INTO bohrium_jobs
                    (session_id, task_id, user_id, org_id, job_id, project_id,
                     sandbox, status, next_poll_at, terminal_at, result_dir)
                VALUES
                    ('s', 't', 'u', 'o', 'tz1', 1,
                     1, 'running', NOW() - INTERVAL 5 SECOND, NULL, NULL)
                """
            )
            # 直接用 DB NOW() 表达式写合法活跃行，避免 INSERT 阶段触发 chk_active_poll。
        w.commit()
    finally:
        w.close()

    for tz in ("+00:00", "+08:00", "-05:00"):
        c = pymysql.connect(**cfg)
        try:
            with c.cursor() as cur:
                cur.execute(f"SET time_zone = '{tz}'")
                cur.execute(
                    "SELECT (next_poll_at <= NOW()) AS due FROM bohrium_jobs "
                    "WHERE job_id='tz1'"
                )
                assert cur.fetchone()["due"] == 1   # 任何连接时区都判定为到期
        finally:
            c.close()
```

- [ ] **Step 2: 跑测试**

Run: `uv run pytest tests/dao/test_bohrium_jobs_constraints.py -v`
Expected: PASS（DB 拒绝违规写入并抛 `OperationalError`；时区用例在 3 个时区下 `due==1`）。

- [ ] **Step 3: Commit**

```bash
git add tests/dao/test_bohrium_jobs_constraints.py
git commit -m "test(bohrium-ledger): DB CHECK constraints and TIMESTAMP UTC invariants"
```

---

## Phase 2 — 写路径（BohriumJobLedgerPort + Tool 集成）

### Task 2.1：定义 `BohriumJobLedgerPort` Protocol

**Files:**
- Modify: `matmaster/context/ports.py`
- Test: `tests/matmaster/test_bohrium_ledger_injection.py`

- [ ] **Step 1: 写失败测试**

`tests/matmaster/test_bohrium_ledger_injection.py`：
```python
from __future__ import annotations

from typing import get_type_hints


def test_bohrium_job_ledger_port_has_sync_record_methods() -> None:
    from matmaster.context.ports import BohriumJobLedgerPort

    for name in ("record_submit", "record_poll", "record_download", "record_kill"):
        assert hasattr(BohriumJobLedgerPort, name)


def test_session_jobs_has_recent_terminal_jobs_field() -> None:
    from matmaster.context.ports import SessionJobs

    sj = SessionJobs.empty()
    assert sj.active_jobs == ()
    assert sj.recent_terminal_jobs == ()
    hints = get_type_hints(SessionJobs)
    assert "recent_terminal_jobs" in hints
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/matmaster/test_bohrium_ledger_injection.py -v`
Expected: FAIL（`ImportError: cannot import name 'BohriumJobLedgerPort'`）。

- [ ] **Step 3: 实现 Protocol 并扩展 SessionJobs**

在 `matmaster/context/ports.py` 中，把 `SessionJobs`（当前 98-104 行）改为：
```python
@dataclass(frozen=True)
class SessionJobs:
    active_jobs: tuple[JsonObject, ...] = ()
    recent_terminal_jobs: tuple[JsonObject, ...] = ()

    @classmethod
    def empty(cls) -> SessionJobs:
        return cls(active_jobs=(), recent_terminal_jobs=())
```

在 `SessionJobsPort`（当前 112-117 行）之后追加新 Protocol：
```python
class BohriumJobLedgerPort(Protocol):
    """Sync write-side port: BohriumTool 把作业生命周期同步到 bohrium_jobs。

    第一版同步接口（BohriumTool._submit/_poll/_download/_kill 是同步实现，
    由 execute_with_context 放线程池）。实现由 service 层注入并闭包本轮
    session_id/task_id/user_id/org_id；工具不感知这些身份字段。
    """

    def record_submit(
        self,
        *,
        job_id: str,
        job_name: str | None,
        project_id: int,
        sandbox: bool,
    ) -> None:
        raise NotImplementedError

    def record_poll(
        self,
        *,
        job_id: str,
        sandbox: bool,
        status_code: int,
    ) -> None:
        raise NotImplementedError

    def record_download(
        self,
        *,
        job_id: str,
        sandbox: bool,
        status_code: int,
        result_dir: str,
    ) -> None:
        raise NotImplementedError

    def record_kill(self, *, job_id: str, sandbox: bool) -> None:
        raise NotImplementedError
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/matmaster/test_bohrium_ledger_injection.py -v`
Expected: PASS（2 passed）。

- [ ] **Step 5: 跑现有 renderer 测试确认 SessionJobs 改动未破坏**

Run: `uv run pytest tests/matmaster/context/sources/test_session_jobs.py -v`
Expected: PASS（现有测试只构造 `SessionJobs(active_jobs=...)`，新增字段有默认值，不破坏）。

- [ ] **Step 6: Commit**

```bash
git add matmaster/context/ports.py tests/matmaster/test_bohrium_ledger_injection.py
git commit -m "feat(bohrium-ledger): BohriumJobLedgerPort + SessionJobs.recent_terminal_jobs"
```

---

### Task 2.2：`AgentRunPorts` 增加两个 port 字段

**Files:**
- Modify: `matmaster/types/runtime_ports.py:14, 158-175`
- Test: `tests/matmaster/test_bohrium_ledger_injection.py`

- [ ] **Step 1: 写失败测试**

在 `tests/matmaster/test_bohrium_ledger_injection.py` 追加：
```python
def test_agent_run_ports_carry_bohrium_and_session_jobs_ports() -> None:
    import dataclasses

    from matmaster.types.runtime_ports import AgentRunPorts

    fields = {f.name for f in dataclasses.fields(AgentRunPorts)}
    assert "bohrium_job_ledger" in fields
    assert "session_jobs" in fields
    # 默认 None，不破坏现有构造
    p = AgentRunPorts()
    assert p.bohrium_job_ledger is None
    assert p.session_jobs is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/matmaster/test_bohrium_ledger_injection.py -k agent_run_ports -v`
Expected: FAIL（`AssertionError`，字段不存在）。

- [ ] **Step 3: 增加字段**

在 `matmaster/types/runtime_ports.py` 第 14 行的 import 增加两个 port 类型：
```python
from matmaster.context.ports import (
    BohriumJobLedgerPort,
    SessionEvent,
    SessionEventQuery,
    SessionJobsPort,
)
```

在 `AgentRunPorts`（158-175 行）字段末尾追加：
```python
    bohrium_job_ledger: BohriumJobLedgerPort | None = None
    session_jobs: SessionJobsPort | None = None
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/matmaster/test_bohrium_ledger_injection.py -k agent_run_ports -v`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add matmaster/types/runtime_ports.py tests/matmaster/test_bohrium_ledger_injection.py
git commit -m "feat(bohrium-ledger): carry ledger + session_jobs ports on AgentRunPorts"
```

---

### Task 2.3：service 层 port 实现与构造工厂

**Files:**
- Create: `src/services/bohrium_jobs_wiring.py`
- Test: `tests/services/test_bohrium_jobs_wiring.py`
- Create: `tests/services/__init__.py`（若不存在，空文件）

- [ ] **Step 1: 写失败测试**（mock DAO，不连库）

`tests/services/test_bohrium_jobs_wiring.py`：
```python
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.services.bohrium_jobs_wiring import build_bohrium_jobs_ports


def test_record_submit_passes_identity_snapshot() -> None:
    table = MagicMock()
    ledger, _ = build_bohrium_jobs_ports(
        session_id="sess-1", task_id="ws_t", user_id="u1", org_id="o1", table=table
    )
    ledger.record_submit(
        job_id="12345", job_name="j", project_id=42, sandbox=True,
    )
    table.insert_submitted.assert_called_once()
    kw = table.insert_submitted.call_args.kwargs
    assert kw["session_id"] == "sess-1"
    assert kw["task_id"] == "ws_t"
    assert kw["user_id"] == "u1"
    assert kw["org_id"] == "o1"
    assert kw["job_id"] == "12345"
    assert kw["sandbox"] is True


def test_record_submit_fails_when_identity_missing() -> None:
    table = MagicMock()
    ledger, _ = build_bohrium_jobs_ports(
        session_id="sess-1", task_id="", user_id="u1", org_id="o1", table=table
    )
    with pytest.raises(ValueError):
        ledger.record_submit(
            job_id="1", job_name=None, project_id=1, sandbox=False,
        )
    table.insert_submitted.assert_not_called()   # 不写半截记录


def test_record_poll_fails_when_identity_missing() -> None:
    table = MagicMock()
    ledger, _ = build_bohrium_jobs_ports(
        session_id="sess-1", task_id="ws_t", user_id="", org_id="o1", table=table
    )
    with pytest.raises(ValueError):
        ledger.record_poll(job_id="1", sandbox=False, status_code=2)
    table.apply_poll.assert_not_called()


def test_record_poll_normalizes_status_code() -> None:
    table = MagicMock()
    ledger, _ = build_bohrium_jobs_ports(
        session_id="s", task_id="t", user_id="u", org_id="o", table=table
    )
    ledger.record_poll(job_id="1", sandbox=False, status_code=2)
    kw = table.apply_poll.call_args.kwargs
    assert kw["status"] == "finished"
    assert kw["is_terminal"] is True


@pytest.mark.asyncio
async def test_session_jobs_port_loads_active_and_recent() -> None:
    table = MagicMock()
    table.query_session_active.return_value = [{"job_id": "a"}]
    table.query_session_recent_terminal.return_value = [{"job_id": "t"}]
    _, jobs_port = build_bohrium_jobs_ports(
        session_id="s", task_id="t", user_id="u", org_id="o", table=table
    )
    from matmaster.context.ports import SessionJobsQuery

    result = await jobs_port.load_session_jobs(SessionJobsQuery(session_id="s"))
    assert result.active_jobs == ({"job_id": "a"},)
    assert result.recent_terminal_jobs == ({"job_id": "t"},)
    # 用闭包的 user_id/org_id 查询
    assert table.query_session_active.call_args.kwargs["user_id"] == "u"
    assert table.query_session_active.call_args.kwargs["org_id"] == "o"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/services/test_bohrium_jobs_wiring.py -v`
Expected: FAIL（`ModuleNotFoundError: src.services.bohrium_jobs_wiring`）。

- [ ] **Step 3: 实现 wiring**

`src/services/bohrium_jobs_wiring.py`：
```python
"""service 层把 bohrium_jobs DAO 包成 kernel 端口。

- _BohriumJobLedger 实现 BohriumJobLedgerPort（同步），闭包本轮身份字段。
- _RunSessionJobsPort 实现 SessionJobsPort（async，用 asyncio.to_thread 包同步 DAO）。
两者由 build_bohrium_jobs_ports 一起构造，共享同一个 BohriumJobsTable 实例。
"""

from __future__ import annotations

import asyncio
import logging
from matmaster.bohrium.status import to_ledger_status
from matmaster.context.ports import SessionJobs, SessionJobsQuery
from src.dao.bohrium_jobs_table import BohriumJobsTable

logger = logging.getLogger(__name__)

# 前台工具 poll 写回时的固定 backoff（后台 poller 用自己的阶梯 backoff）。
_FOREGROUND_POLL_BACKOFF_SECONDS = 30


class _BohriumJobLedger:
    def __init__(
        self,
        *,
        table: BohriumJobsTable,
        session_id: str,
        task_id: str,
        user_id: str,
        org_id: str,
    ) -> None:
        self._table = table
        self._session_id = session_id
        self._task_id = task_id
        self._user_id = user_id
        self._org_id = org_id

    def _require_identity(self) -> None:
        missing = [
            name
            for name, val in (
                ("session_id", self._session_id),
                ("task_id", self._task_id),
                ("user_id", self._user_id),
                ("org_id", self._org_id),
            )
            if not val
        ]
        if missing:
            raise ValueError(
                f"bohrium ledger missing identity fields: {', '.join(missing)}"
            )

    def record_submit(
        self,
        *,
        job_id: str,
        job_name: str | None,
        project_id: int,
        sandbox: bool,
    ) -> None:
        self._require_identity()
        self._table.insert_submitted(
            session_id=self._session_id,
            task_id=self._task_id,
            user_id=self._user_id,
            org_id=self._org_id,
            job_id=str(job_id),
            job_name=job_name,
            project_id=int(project_id),
            sandbox=bool(sandbox),
        )

    def record_poll(
        self,
        *,
        job_id: str,
        sandbox: bool,
        status_code: int,
    ) -> None:
        self._require_identity()
        decision = to_ledger_status(int(status_code))
        self._table.apply_poll(
            user_id=self._user_id,
            org_id=self._org_id,
            sandbox=bool(sandbox),
            job_id=str(job_id),
            status=decision.status,
            is_terminal=decision.is_terminal,
            backoff_seconds=_FOREGROUND_POLL_BACKOFF_SECONDS,
        )

    def record_download(
        self,
        *,
        job_id: str,
        sandbox: bool,
        status_code: int,
        result_dir: str,
    ) -> None:
        self._require_identity()
        decision = to_ledger_status(int(status_code))
        self._table.apply_download(
            user_id=self._user_id,
            org_id=self._org_id,
            sandbox=bool(sandbox),
            job_id=str(job_id),
            platform_status=decision.status,
            platform_is_terminal=decision.is_terminal,
            result_dir=result_dir,
        )

    def record_kill(self, *, job_id: str, sandbox: bool) -> None:
        self._require_identity()
        self._table.apply_kill(
            user_id=self._user_id,
            org_id=self._org_id,
            sandbox=bool(sandbox),
            job_id=str(job_id),
        )


class _RunSessionJobsPort:
    def __init__(
        self, *, table: BohriumJobsTable, user_id: str, org_id: str
    ) -> None:
        self._table = table
        self._user_id = user_id
        self._org_id = org_id

    async def load_session_jobs(self, query: SessionJobsQuery) -> SessionJobs:
        if not (self._user_id and self._org_id):
            return SessionJobs.empty()
        try:
            active = await asyncio.to_thread(
                self._table.query_session_active,
                user_id=self._user_id,
                org_id=self._org_id,
                session_id=query.session_id,
            )
            recent = await asyncio.to_thread(
                self._table.query_session_recent_terminal,
                user_id=self._user_id,
                org_id=self._org_id,
                session_id=query.session_id,
                limit=5,
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "load_session_jobs failed session_id=%s", query.session_id,
                exc_info=True,
            )
            return SessionJobs.empty()
        return SessionJobs(
            active_jobs=tuple(active),
            recent_terminal_jobs=tuple(recent),
        )


def build_bohrium_jobs_ports(
    *,
    session_id: str,
    task_id: str,
    user_id: str,
    org_id: str,
    table: BohriumJobsTable | None = None,
) -> tuple[_BohriumJobLedger, _RunSessionJobsPort]:
    """构造写 port 与读 port（共享一个 DAO 实例）。"""
    table = table if table is not None else BohriumJobsTable()
    ledger = _BohriumJobLedger(
        table=table,
        session_id=session_id,
        task_id=task_id,
        user_id=user_id,
        org_id=org_id,
    )
    jobs = _RunSessionJobsPort(table=table, user_id=user_id, org_id=org_id)
    return ledger, jobs
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/services/test_bohrium_jobs_wiring.py -v`
Expected: PASS（5 passed；这些用例 mock DAO，不连库）。

- [ ] **Step 5: Commit**

```bash
git add src/services/bohrium_jobs_wiring.py tests/services/test_bohrium_jobs_wiring.py tests/services/__init__.py
git commit -m "feat(bohrium-ledger): service-layer ledger + session jobs ports"
```

---

### Task 2.4：`AgentRunService` 构造并注入两个 port

**Files:**
- Modify: `src/services/agent_run_service.py:493-517`
- Test: `tests/services/test_bohrium_jobs_wiring.py`

- [ ] **Step 1: 写失败测试**（验证 user_id/org_id 快照解析）

在 `tests/services/test_bohrium_jobs_wiring.py` 追加：
```python
def test_session_identity_resolution_helper_uses_session_snapshot() -> None:
    """AgentRunService 用 ChatSessionsTable.get_session 拿 ledger owner 快照。"""
    from src.services import agent_run_service as ars

    captured = {}

    class _FakeSessions:
        def get_session(self, sid):
            captured["sid"] = sid
            return {"user_id": "user-from-db", "org_id": "org-from-db", "project_id": 7}

    user, org = ars._resolve_session_identity(
        "sess-1", sessions_table=_FakeSessions()
    )
    assert user == "user-from-db"
    assert org == "org-from-db"
    assert captured["sid"] == "sess-1"


def test_session_identity_resolution_prefers_explicit_run_user_id() -> None:
    from src.services import agent_run_service as ars

    class _FakeSessions:
        def get_session(self, sid):
            return {"user_id": "user-from-db", "org_id": "org-from-db"}

    user, org = ars._resolve_session_identity(
        "sess-1", user_id="user-from-run", sessions_table=_FakeSessions()
    )
    assert user == "user-from-run"
    assert org == "org-from-db"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/services/test_bohrium_jobs_wiring.py -k session_identity -v`
Expected: FAIL（`AttributeError: module ... has no attribute '_resolve_session_identity'`）。

- [ ] **Step 3: 实现身份解析 helper + 注入**

在 `src/services/agent_run_service.py` 顶部 import 区增加：
```python
from src.dao.chat_sessions_table import ChatSessionsTable
from src.services.bohrium_jobs_wiring import build_bohrium_jobs_ports
```

在模块级（靠近其他 helper，如 `_build_user_turn_context_writer` 附近）增加：
```python
def _resolve_session_identity(
    session_id: str,
    *,
    user_id: str | None = None,
    sessions_table=None,
) -> tuple[str, str]:
    """取提交时的 user_id/org_id 快照（bohrium_jobs 需要随 job 固化 owner）。"""
    table = sessions_table if sessions_table is not None else ChatSessionsTable()
    row = table.get_session(session_id) or {}
    resolved_user_id = str(user_id or row.get("user_id") or "")
    resolved_org_id = str(row.get("org_id") or "")
    return resolved_user_id, resolved_org_id
```

在 `run_agent` 构造 `AgentRunRequest` 前（约 493 行 `agent_run_ctx = AgentRunContext(` 之前）增加：
```python
        _ledger_user_id, _org_id = _resolve_session_identity(
            session_id,
            user_id=user_id,
        )
        bohrium_ledger_port, bohrium_jobs_port = build_bohrium_jobs_ports(
            session_id=session_id,
            task_id=task_id,
            user_id=_ledger_user_id,
            org_id=_org_id,
        )
```

在 `AgentRunPorts(...)`（507-516 行）末尾追加两个字段：
```python
                bohrium_job_ledger=bohrium_ledger_port,
                session_jobs=bohrium_jobs_port,
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/services/test_bohrium_jobs_wiring.py -k session_identity -v`
Expected: PASS。

- [ ] **Step 5: 导入冒烟**

Run: `uv run python -c "from src.services.agent_run_service import AgentRunService; print('OK')"`
Expected: 打印 `OK`，无 import 环。

- [ ] **Step 6: Commit**

```bash
git add src/services/agent_run_service.py tests/services/test_bohrium_jobs_wiring.py
git commit -m "feat(bohrium-ledger): construct and inject ledger + session jobs ports in run_agent"
```

---

### Task 2.5：`BohriumTool` 构造器接收 `job_ledger` 并由 Exp 注入

**Files:**
- Modify: `matmaster/tools/builtin/bohrium_tool/tool.py:162`（类体内新增 `__init__`）
- Modify: `matmaster/core/exp.py:712`
- Test: `tests/matmaster/test_bohrium_ledger_injection.py`

- [ ] **Step 1: 写失败测试**

在 `tests/matmaster/test_bohrium_ledger_injection.py` 追加：
```python
def test_bohrium_tool_accepts_job_ledger() -> None:
    from pathlib import Path

    from matmaster.tools.builtin.bohrium_tool.tool import BohriumTool

    sentinel = object()
    bt = BohriumTool(session=None, workdir=Path("."), job_ledger=sentinel)
    assert bt._job_ledger is sentinel


def test_bohrium_tool_defaults_job_ledger_none() -> None:
    from pathlib import Path

    from matmaster.tools.builtin.bohrium_tool.tool import BohriumTool

    bt = BohriumTool(session=None, workdir=Path("."))
    assert bt._job_ledger is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/matmaster/test_bohrium_ledger_injection.py -k job_ledger -v`
Expected: FAIL（`TypeError: __init__() got an unexpected keyword argument 'job_ledger'`）。

- [ ] **Step 3: 给 BohriumTool 加 `__init__`，并在 Exp 注入**

在 `matmaster/tools/builtin/bohrium_tool/tool.py` 的 `class BohriumTool(BuiltinTool):` 类体内（ClassVar 定义之后、`_build_context` 之前，约 296 行前）新增：
```python
    def __init__(
        self,
        *,
        session: Any | None = None,
        workdir: Any | None = None,
        path_access_roots: Any = (),
        job_ledger: Any | None = None,
    ) -> None:
        super().__init__(
            session=session,
            workdir=workdir,
            path_access_roots=path_access_roots,
        )
        # BohriumJobLedgerPort | None；service 层注入，闭包本轮身份字段。
        self._job_ledger = job_ledger
```

在 `matmaster/core/exp.py:712` 把：
```python
            BohriumTool(session=env.session, workdir=env.workdir),
```
改为：
```python
            BohriumTool(
                session=env.session,
                workdir=env.workdir,
                job_ledger=ctx.request.ports.bohrium_job_ledger,
            ),
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/matmaster/test_bohrium_ledger_injection.py -k job_ledger -v`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add matmaster/tools/builtin/bohrium_tool/tool.py matmaster/core/exp.py tests/matmaster/test_bohrium_ledger_injection.py
git commit -m "feat(bohrium-ledger): inject job_ledger into BohriumTool via constructor"
```

---

### Task 2.6：`_submit` 集成 ledger 写入

**Files:**
- Modify: `matmaster/tools/builtin/bohrium_tool/tool.py:482-538`
- Test: `tests/matmaster/tools/builtin/test_bohrium_tool_ledger.py`

- [ ] **Step 1: 写失败测试**（fake ledger + patch 提交函数）

`tests/matmaster/tools/builtin/test_bohrium_tool_ledger.py`：
```python
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from matmaster.tools.builtin.bohrium_tool import tool as tmod


class _FakeLedger:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def record_submit(self, **kw):
        self.calls.append(("submit", kw))

    def record_poll(self, **kw):
        self.calls.append(("poll", kw))

    def record_download(self, **kw):
        self.calls.append(("download", kw))

    def record_kill(self, **kw):
        self.calls.append(("kill", kw))


def _ctx(sandbox: bool = True):
    return SimpleNamespace(
        sandbox=sandbox,
        credentials=SimpleNamespace(project_id=42, base_url="https://x"),
    )


def test_submit_records_ledger_after_job_add(monkeypatch) -> None:
    fake = _FakeLedger()
    bt = tmod.BohriumTool(session=None, workdir=Path("."), job_ledger=fake)
    monkeypatch.setattr(bt, "_build_context", lambda **kw: _ctx(sandbox=True))
    monkeypatch.setattr(bt, "_log_request_context", lambda **kw: None)
    monkeypatch.setattr(
        tmod,
        "submit_job_via_runtime",
        lambda **kw: SimpleNamespace(job_id="12345", raw_add_response={"jobId": "12345"}),
    )
    res = bt._submit({"input_dir": "in", "image": "img", "cmd": "run", "job_name": "jn"})
    assert res.status == "success"
    assert fake.calls[0][0] == "submit"
    kw = fake.calls[0][1]
    assert kw["job_id"] == "12345"
    assert kw["job_name"] == "jn"
    assert kw["project_id"] == 42
    assert kw["sandbox"] is True


def test_submit_ledger_failure_does_not_break_tool(monkeypatch) -> None:
    class _BoomLedger(_FakeLedger):
        def record_submit(self, **kw):
            raise RuntimeError("db down")

    bt = tmod.BohriumTool(session=None, workdir=Path("."), job_ledger=_BoomLedger())
    monkeypatch.setattr(bt, "_build_context", lambda **kw: _ctx())
    monkeypatch.setattr(bt, "_log_request_context", lambda **kw: None)
    monkeypatch.setattr(
        tmod, "submit_job_via_runtime",
        lambda **kw: SimpleNamespace(job_id="1", raw_add_response={}),
    )
    res = bt._submit({"input_dir": "in", "image": "img", "cmd": "run"})
    assert res.status == "success"   # ledger 失败被吞，工具仍成功
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/matmaster/tools/builtin/test_bohrium_tool_ledger.py -k submit -v`
Expected: FAIL（`fake.calls` 为空，`_submit` 尚未写 ledger）。

- [ ] **Step 3: 增加 `_safe_ledger` helper 并在 `_submit` 调用**

在 `BohriumTool` 类体内增加 helper（放在 `_build_context` 附近）：
```python
    def _safe_ledger(self, method: str, /, **kwargs: Any) -> None:
        """调用 ledger port，吞掉异常（ledger 写失败不阻断工具主流程）。"""
        if self._job_ledger is None:
            return
        try:
            getattr(self._job_ledger, method)(**kwargs)
        except Exception:  # noqa: BLE001
            logger.warning(
                "bohrium ledger %s failed job_id=%s",
                method,
                kwargs.get("job_id"),
                exc_info=True,
            )
```

在 `_submit` 成功分支（构造 success `ToolResult` 之前，约 515 行）插入：
```python
            self._safe_ledger(
                "record_submit",
                job_id=str(submitted.job_id),
                job_name=str(job_name),
                project_id=ctx.credentials.project_id,
                sandbox=ctx.sandbox,
            )
            return ToolResult(
                status="success",
                content=json.dumps(
                    {
                        "success": True,
                        "job_id": submitted.job_id,
                        "status": "Submitted",
                        "use_sandbox": ctx.sandbox,
                    },
                    ensure_ascii=False,
                ),
            )
```
（即在原有 `return ToolResult(...)` 之前加 `self._safe_ledger("record_submit", ...)`。）

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/matmaster/tools/builtin/test_bohrium_tool_ledger.py -k submit -v`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add matmaster/tools/builtin/bohrium_tool/tool.py tests/matmaster/tools/builtin/test_bohrium_tool_ledger.py
git commit -m "feat(bohrium-ledger): record submit into ledger after job/add"
```

---

### Task 2.7：`_poll` 集成 ledger 写入

**Files:**
- Modify: `matmaster/tools/builtin/bohrium_tool/tool.py:540-629`
- Test: `tests/matmaster/tools/builtin/test_bohrium_tool_ledger.py`

- [ ] **Step 1: 写失败测试**

追加：
```python
def test_poll_records_ledger_with_raw_code(monkeypatch) -> None:
    fake = _FakeLedger()
    bt = tmod.BohriumTool(session=None, workdir=Path("."), job_ledger=fake)
    monkeypatch.setattr(bt, "_build_context", lambda **kw: _ctx(sandbox=True))
    monkeypatch.setattr(bt, "_log_request_context", lambda **kw: None)
    monkeypatch.setattr(bt, "_fetch_log_tail", lambda *a, **k: "")
    monkeypatch.setattr(tmod, "get_job_detail", lambda ctx, job_id: {"status": 1})
    res = bt._poll({"job_id": "12345"})
    assert res.status == "success"
    poll_calls = [c for c in fake.calls if c[0] == "poll"]
    assert poll_calls and poll_calls[0][1]["status_code"] == 1
    assert poll_calls[0][1]["sandbox"] is True
    assert poll_calls[0][1]["job_id"] == "12345"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/matmaster/tools/builtin/test_bohrium_tool_ledger.py -k poll -v`
Expected: FAIL（无 poll 调用）。

- [ ] **Step 3: 在 `_poll` 写 ledger**

在 `_poll` 计算出最终 `code` / `detail_data`（即 `status_label = status_name(code)` 之后、构造 `result_payload` 之前，约 575 行）插入：
```python
        self._safe_ledger(
            "record_poll",
            job_id=str(job_id),
            sandbox=sandbox,
            status_code=int(code),
        )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/matmaster/tools/builtin/test_bohrium_tool_ledger.py -k poll -v`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add matmaster/tools/builtin/bohrium_tool/tool.py tests/matmaster/tools/builtin/test_bohrium_tool_ledger.py
git commit -m "feat(bohrium-ledger): record poll into ledger"
```

---

### Task 2.8：`_download` 集成 ledger 写入

**Files:**
- Modify: `matmaster/tools/builtin/bohrium_tool/tool.py:650-749`
- Test: `tests/matmaster/tools/builtin/test_bohrium_tool_ledger.py`

- [ ] **Step 1: 写失败测试**（finished → downloaded；failed → 保留失败 + result_dir）

追加：
```python
def test_download_finished_records_downloaded(monkeypatch) -> None:
    fake = _FakeLedger()
    bt = tmod.BohriumTool(session=None, workdir=Path("."), job_ledger=fake)
    monkeypatch.setattr(bt, "_build_context", lambda **kw: _ctx(sandbox=True))
    monkeypatch.setattr(bt, "_log_request_context", lambda **kw: None)
    monkeypatch.setattr(tmod, "get_job_detail", lambda ctx, job_id: {"status": 2})
    monkeypatch.setattr(
        tmod, "resolve_download_target",
        lambda **kw: SimpleNamespace(kind="local", staging_dir=Path("/tmp/x")),
    )
    monkeypatch.setattr(
        tmod, "download_job_artifacts",
        lambda **kw: (["log"], "tail"),
    )
    monkeypatch.setattr(tmod, "publish_download_target", lambda *a, **k: "results/run_12345")
    res = bt._download({"job_id": "12345", "result_dir": "results/run_12345"})
    assert res.status == "success"
    dl = [c for c in fake.calls if c[0] == "download"]
    assert dl and dl[0][1]["status_code"] == 2
    assert dl[0][1]["result_dir"] == "results/run_12345"


def test_download_failed_records_with_result_dir(monkeypatch) -> None:
    fake = _FakeLedger()
    bt = tmod.BohriumTool(session=None, workdir=Path("."), job_ledger=fake)
    monkeypatch.setattr(bt, "_build_context", lambda **kw: _ctx(sandbox=True))
    monkeypatch.setattr(bt, "_log_request_context", lambda **kw: None)
    monkeypatch.setattr(tmod, "get_job_detail", lambda ctx, job_id: {"status": -1})
    monkeypatch.setattr(
        tmod, "resolve_download_target",
        lambda **kw: SimpleNamespace(kind="local", staging_dir=Path("/tmp/x")),
    )
    monkeypatch.setattr(tmod, "download_job_artifacts", lambda **kw: (["log"], "tail"))
    monkeypatch.setattr(tmod, "publish_download_target", lambda *a, **k: "results/run_12345")
    res = bt._download({"job_id": "12345", "result_dir": "results/run_12345"})
    assert res.status == "error"          # 平台失败态，工具返回 error
    dl = [c for c in fake.calls if c[0] == "download"]
    assert dl and dl[0][1]["status_code"] == -1
    assert dl[0][1]["result_dir"] == "results/run_12345"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/matmaster/tools/builtin/test_bohrium_tool_ledger.py -k download -v`
Expected: FAIL（无 download 调用）。

- [ ] **Step 3: 在 `_download` 两个终态分支写 ledger**

在 `_download` 成功分支（`if code == SUCCESS_CODE:` 的 `return ToolResult(success...)` 之前）插入：
```python
        if code == SUCCESS_CODE:
            self._safe_ledger(
                "record_download",
                job_id=str(job_id),
                sandbox=sandbox,
                status_code=int(code),
                result_dir=report_dir,
            )
            return ToolResult(
                status="success",
                content=json.dumps(
                    {
                        "success": True,
                        "job_id": job_id,
                        "status": "Finished",
                        "result_dir": report_dir,
                        "files": files,
                        "log_tail": log_tail,
                    },
                    ensure_ascii=False,
                ),
            )
```
在失败分支（最后的 `return ToolResult(status="error", content=json.dumps({...failure...}))` 之前）插入：
```python
        self._safe_ledger(
            "record_download",
            job_id=str(job_id),
            sandbox=sandbox,
            status_code=int(code),
            result_dir=report_dir,
        )
        return ToolResult(
            status="error",
            content=json.dumps(
                {
                    "success": False,
                    "job_id": job_id,
                    "status": status_label,
                    "result_dir": report_dir,
                    "files": files,
                    "log_tail": log_tail,
                    "error": f"Job {status_label}.",
                },
                ensure_ascii=False,
            ),
        )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/matmaster/tools/builtin/test_bohrium_tool_ledger.py -k download -v`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add matmaster/tools/builtin/bohrium_tool/tool.py tests/matmaster/tools/builtin/test_bohrium_tool_ledger.py
git commit -m "feat(bohrium-ledger): record download (downloaded / failed+result_dir)"
```

---

### Task 2.9：`_kill` 集成 ledger 写入（仅 sandbox）

**Files:**
- Modify: `matmaster/tools/builtin/bohrium_tool/tool.py:751-794`
- Test: `tests/matmaster/tools/builtin/test_bohrium_tool_ledger.py`

- [ ] **Step 1: 写失败测试**（sandbox 成功写 terminating；非 sandbox 失败不写）

追加：
```python
def test_kill_sandbox_records_terminating(monkeypatch) -> None:
    fake = _FakeLedger()
    bt = tmod.BohriumTool(session=None, workdir=Path("."), job_ledger=fake)
    monkeypatch.setattr(bt, "_build_context", lambda **kw: _ctx(sandbox=True))
    monkeypatch.setattr(bt, "_log_request_context", lambda **kw: None)
    monkeypatch.setattr(tmod, "terminate_job", lambda ctx, job_id: {})
    res = bt._kill({"job_id": "12345"})
    assert res.status == "success"
    kills = [c for c in fake.calls if c[0] == "kill"]
    assert kills and kills[0][1]["job_id"] == "12345"
    assert kills[0][1]["sandbox"] is True


def test_kill_non_sandbox_does_not_record(monkeypatch) -> None:
    from matmaster.bohrium.errors import BohriumAPIError

    fake = _FakeLedger()
    bt = tmod.BohriumTool(session=None, workdir=Path("."), job_ledger=fake)
    monkeypatch.setattr(bt, "_build_context", lambda **kw: _ctx(sandbox=False))
    monkeypatch.setattr(bt, "_log_request_context", lambda **kw: None)

    def _boom(ctx, job_id):
        raise BohriumAPIError("kill is only supported in sandbox mode")

    monkeypatch.setattr(tmod, "terminate_job", _boom)
    res = bt._kill({"job_id": "12345"})
    assert res.status == "error"
    assert [c for c in fake.calls if c[0] == "kill"] == []   # 失败不写 ledger
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/matmaster/tools/builtin/test_bohrium_tool_ledger.py -k kill -v`
Expected: FAIL（无 kill 调用）。

- [ ] **Step 3: 在 `_kill` 成功分支写 ledger**

在 `_kill` 的 `response = terminate_job(ctx, job_id=job_id)` 成功之后、构造 success `ToolResult` 之前插入：
```python
        response = terminate_job(ctx, job_id=job_id)
        self._safe_ledger(
            "record_kill",
            job_id=str(job_id),
            sandbox=sandbox,
        )
        return ToolResult(
            status="success",
            content=json.dumps(
                {
                    "success": True,
                    "job_id": job_id,
                    "status": "Terminating",
                    "message": (
                        "Kill requested. The Bohrium kill RPC is "
                        "asynchronous — call "
                        f'Bohrium(action="poll", job_id={job_id!r}) '
                        "to confirm the job reaches a terminal state "
                        "(Stopped/Failed/Finished)."
                    ),
                    "response": response,
                },
                ensure_ascii=False,
            ),
        )
```
（`terminate_job` 在非 sandbox 时抛 `BohriumAPIError`，控制流进入 `except`，不会执行到 `record_kill`，因此非 sandbox kill 不写 ledger。）

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/matmaster/tools/builtin/test_bohrium_tool_ledger.py -v`
Expected: PASS（submit/poll/download/kill 全部通过）。

- [ ] **Step 5: Commit**

```bash
git add matmaster/tools/builtin/bohrium_tool/tool.py tests/matmaster/tools/builtin/test_bohrium_tool_ledger.py
git commit -m "feat(bohrium-ledger): record sandbox kill as terminating"
```

---

### Task 2.10：架构边界测试（kernel 不依赖 DAO）

**Files:**
- Test: `tests/matmaster/test_bohrium_ledger_injection.py`

- [ ] **Step 1: 写测试**

追加：
```python
def test_kernel_does_not_import_bohrium_jobs_dao() -> None:
    """matmaster/（kernel）不得 import src.dao.* —— DAO 不暴露给 kernel。

    写/读路径只经 Protocol（matmaster.context.ports）+ service 层注入。
    """
    import pathlib

    kernel_root = pathlib.Path(__file__).resolve().parents[2] / "matmaster"
    offenders = []
    for path in kernel_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "src.dao" in text or "from src." in text or "import src" in text:
            offenders.append(str(path))
    assert offenders == [], f"kernel must not import src.*: {offenders}"
```

- [ ] **Step 2: 跑测试**

Run: `uv run pytest tests/matmaster/test_bohrium_ledger_injection.py -k kernel_does_not -v`
Expected: PASS（本 plan 的 kernel 改动只 import `matmaster.bohrium.status` 等 kernel 内模块）。

- [ ] **Step 3: 跑 Phase 2 全量回归**

Run: `uv run pytest tests/matmaster/test_bohrium_ledger_injection.py tests/matmaster/tools/builtin/test_bohrium_tool_ledger.py tests/services/test_bohrium_jobs_wiring.py tests/matmaster/architecture/test_bohrium_runtime_boundaries.py -v`
Expected: PASS（新边界 + 注入 + 既有架构边界都通过）。

- [ ] **Step 4: Commit**

```bash
git add tests/matmaster/test_bohrium_ledger_injection.py
git commit -m "test(bohrium-ledger): assert kernel does not import src DAO"
```

---

## Phase 3 — 读路径（renderer 扩展 + context assembly 接通）

> 说明：`SessionJobs.recent_terminal_jobs`（Task 2.1）、`_RunSessionJobsPort`（Task 2.3）、`AgentRunPorts.session_jobs`（Task 2.2）与其注入（Task 2.4）已在 Phase 2 完成。本阶段补齐 renderer 呈现两类作业，并把 context assembly 从空实现切到真实 port。

### Task 3.1：`SessionJobsSource` renderer 渲染 active + recent terminal

**Files:**
- Modify: `matmaster/context/sources/session_jobs.py:21-28`
- Test: `tests/matmaster/context/sources/test_session_jobs.py`（重写）

- [ ] **Step 1: 重写 renderer 测试**

把 `tests/matmaster/context/sources/test_session_jobs.py` 整个替换为：
```python
from __future__ import annotations

from matmaster.context.ports import SessionJobs
from matmaster.context.sections import ContextView, SectionOrder
from matmaster.context.sources.session_jobs import SessionJobsSource


def test_session_jobs_empty_returns_no_sections() -> None:
    assert SessionJobsSource.from_jobs(SessionJobs.empty()).to_sections() == ()


def test_session_jobs_renders_active_and_recent_terminal() -> None:
    jobs = SessionJobs(
        active_jobs=(
            {"job_id": "a2", "status": "running"},
            {"job_id": "a1", "status": "submitted"},
        ),
        recent_terminal_jobs=(
            {"job_id": "t9", "status": "finished"},
        ),
    )

    section = SessionJobsSource.from_jobs(jobs).to_sections()[0]

    assert section.key == "session_jobs"
    assert section.tag == "session_jobs"
    assert section.order == SectionOrder.SESSION_JOBS
    assert section.views == frozenset({ContextView.RUNTIME, ContextView.CHECKPOINT})
    assert section.content == (
        'active_job_1 {"job_id": "a2", "status": "running"}\n'
        'active_job_2 {"job_id": "a1", "status": "submitted"}\n'
        'recent_terminal_job_1 {"job_id": "t9", "status": "finished"}'
    )


def test_session_jobs_only_active_renders_without_terminal_lines() -> None:
    jobs = SessionJobs(active_jobs=({"job_id": "a1", "status": "running"},))
    section = SessionJobsSource.from_jobs(jobs).to_sections()[0]
    assert section.content == 'active_job_1 {"job_id": "a1", "status": "running"}'


def test_session_jobs_only_recent_terminal_renders() -> None:
    jobs = SessionJobs(recent_terminal_jobs=({"job_id": "t1", "status": "failed"},))
    section = SessionJobsSource.from_jobs(jobs).to_sections()[0]
    assert section.content == 'recent_terminal_job_1 {"job_id": "t1", "status": "failed"}'
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/matmaster/context/sources/test_session_jobs.py -v`
Expected: FAIL（现 renderer 输出 `job_1 ...`，新断言要求 `active_job_1 ...` 且缺 recent terminal 行）。

- [ ] **Step 3: 扩展 renderer**

把 `matmaster/context/sources/session_jobs.py` 的 `from_jobs`（21-28 行）改为：
```python
    @classmethod
    def from_jobs(cls, jobs: SessionJobs) -> SessionJobsSource:
        active = tuple(
            f"active_job_{index} "
            f"{json.dumps(job, ensure_ascii=False, sort_keys=True)}"
            for index, job in enumerate(jobs.active_jobs, 1)
        )
        recent = tuple(
            f"recent_terminal_job_{index} "
            f"{json.dumps(job, ensure_ascii=False, sort_keys=True)}"
            for index, job in enumerate(jobs.recent_terminal_jobs, 1)
        )
        return cls(lines=active + recent)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/matmaster/context/sources/test_session_jobs.py -v`
Expected: PASS（4 passed）。

- [ ] **Step 5: Commit**

```bash
git add matmaster/context/sources/session_jobs.py tests/matmaster/context/sources/test_session_jobs.py
git commit -m "feat(bohrium-ledger): render active + recent terminal session jobs"
```

---

### Task 3.2：context assembly 用注入的 `session_jobs` port

**Files:**
- Modify: `matmaster/core/runtime_context_assembly.py:98-101`
- Test: `tests/matmaster/test_runtime_context_assembly_session_jobs.py`

- [ ] **Step 1: 写失败测试**（monkeypatch 重类，聚焦验证 port 选择逻辑）

`tests/matmaster/test_runtime_context_assembly_session_jobs.py`：
```python
from __future__ import annotations

import logging
from types import SimpleNamespace

import matmaster.core.runtime_context_assembly as rca


def _make_ctx(session_jobs_port) -> SimpleNamespace:
    return SimpleNamespace(
        request=SimpleNamespace(
            ports=SimpleNamespace(
                compaction=SimpleNamespace(history=None),
                session_jobs=session_jobs_port,
            ),
            user_instructions=None,
        ),
        environment=SimpleNamespace(
            session_id="sess-1",
            metadata=SimpleNamespace(task_id="ws_t"),
        ),
    )


def _patch_heavy(monkeypatch):
    captured: dict = {}

    class _FakeAssembler:
        def __init__(self, *, ports, session_context_factory, render_options) -> None:
            captured["ports"] = ports

    class _FakeCompactor:
        def __init__(self, **kwargs) -> None:
            captured["compactor_kwargs"] = kwargs

    monkeypatch.setattr(rca, "ContextAssembler", _FakeAssembler)
    monkeypatch.setattr(rca, "ContextCompactor", _FakeCompactor)
    return captured


def test_uses_injected_session_jobs_port(monkeypatch) -> None:
    captured = _patch_heavy(monkeypatch)
    fake_port = object()
    rca.build_runtime_context_assembly(
        llm_provider=object(),
        compaction=object(),
        ctx=_make_ctx(fake_port),
        skill_resolver=rca.empty_skill_resolver,
        spawn_id=None,
        logger=logging.getLogger("test"),
    )
    assert captured["ports"].session_jobs is fake_port


def test_falls_back_to_empty_port_when_none(monkeypatch) -> None:
    captured = _patch_heavy(monkeypatch)
    rca.build_runtime_context_assembly(
        llm_provider=object(),
        compaction=object(),
        ctx=_make_ctx(None),
        skill_resolver=rca.empty_skill_resolver,
        spawn_id=None,
        logger=logging.getLogger("test"),
    )
    port = captured["ports"].session_jobs
    assert isinstance(port, rca._EmptySessionJobsPort)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/matmaster/test_runtime_context_assembly_session_jobs.py -v`
Expected: FAIL（`test_uses_injected_session_jobs_port` 失败：当前硬编码 `_EmptySessionJobsPort()`，不会用 `fake_port`）。

- [ ] **Step 3: 用注入的 port**

把 `matmaster/core/runtime_context_assembly.py:98-101` 改为：
```python
    user_instructions = ctx.request.user_instructions or UserInstructions.empty()
    assembly_ports = ContextAssemblyPorts(
        session_events=history_port,
        session_jobs=ctx.request.ports.session_jobs or _EmptySessionJobsPort(),
    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/matmaster/test_runtime_context_assembly_session_jobs.py -v`
Expected: PASS（2 passed）。

- [ ] **Step 5: Commit**

```bash
git add matmaster/core/runtime_context_assembly.py tests/matmaster/test_runtime_context_assembly_session_jobs.py
git commit -m "feat(bohrium-ledger): wire real SessionJobsPort into context assembly"
```

---

## Phase 4 — Background poller 可测核心（不接进程）

> 第一版只实现核心类与 DAO 支撑，不建独立进程入口、不接部署。后续接线时只需在 `src/worker/` 加一个 `main()` 循环调用 `BohriumJobPoller.run_once()`。

### Task 4.1：DAO `mark_poll_error`

**Files:**
- Modify: `src/dao/bohrium_jobs_table.py`
- Test: `tests/dao/test_bohrium_jobs_table.py`

- [ ] **Step 1: 写失败测试**

在 `tests/dao/test_bohrium_jobs_table.py` 追加：
```python
def test_mark_poll_error_marks_active_unknown(jobs_table) -> None:
    jobs_table.insert_submitted(**_submit_kwargs(job_id="e1"))
    jobs_table.mark_poll_error(
        user_id="user-1", org_id="org-1", sandbox=True, job_id="e1",
        error="api down", backoff_seconds=45,
    )
    row = jobs_table.get_by_owner_job(
        user_id="user-1", org_id="org-1", sandbox=True, job_id="e1"
    )
    assert row["status"] == "unknown"
    assert row["last_error"] == "api down"
    assert row["last_error_at"] is not None
    assert row["next_poll_at"] is not None      # 仍轮询
    assert row["terminal_at"] is None


def test_mark_poll_error_does_not_touch_terminal(jobs_table) -> None:
    jobs_table.insert_submitted(**_submit_kwargs(job_id="e2"))
    jobs_table.apply_poll(
        user_id="user-1", org_id="org-1", sandbox=True, job_id="e2",
        status="finished", is_terminal=True, backoff_seconds=30,
    )
    jobs_table.mark_poll_error(
        user_id="user-1", org_id="org-1", sandbox=True, job_id="e2",
        error="late error", backoff_seconds=45,
    )
    row = jobs_table.get_by_owner_job(
        user_id="user-1", org_id="org-1", sandbox=True, job_id="e2"
    )
    assert row["status"] == "finished"          # 终态不被改成 unknown
    assert row["next_poll_at"] is None
    assert row["last_error"] == "late error"     # 但 last_error 仍记录
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/dao/test_bohrium_jobs_table.py -k mark_poll_error -v`
Expected: FAIL（`mark_poll_error` 不存在）。

- [ ] **Step 3: 实现 `mark_poll_error`**

（`_CLAIM_COLUMNS` 已在 Task 1.6 含 `poll_count`，poller 用它算 backoff。）在 `BohriumJobsTable` 新增方法：
```python
    def mark_poll_error(
        self,
        *,
        user_id: str,
        org_id: str,
        sandbox: bool,
        job_id: str,
        error: str,
        backoff_seconds: int,
    ) -> None:
        """poll/同步失败时记录错误。活跃作业标 unknown 并按 backoff 推进；
        明确终态不动 status / next_poll_at，仅记 last_error。"""
        sql = f"""
            UPDATE {self.table_name}
            SET last_error = %s,
                last_error_at = NOW(),
                status = CASE
                    WHEN status IN ('submitted', 'running', 'terminating', 'unknown')
                    THEN 'unknown' ELSE status END,
                next_poll_at = CASE
                    WHEN status IN ('submitted', 'running', 'terminating', 'unknown')
                    THEN NOW() + INTERVAL %s SECOND ELSE next_poll_at END
            WHERE user_id = %s AND org_id = %s AND sandbox = %s AND job_id = %s
        """
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql,
                    (
                        str(error)[:2000],
                        int(backoff_seconds),
                        user_id,
                        org_id,
                        1 if sandbox else 0,
                        job_id,
                    ),
                )
            conn.commit()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/dao/test_bohrium_jobs_table.py -k mark_poll_error -v`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add src/dao/bohrium_jobs_table.py tests/dao/test_bohrium_jobs_table.py
git commit -m "feat(bohrium-ledger): claim returns poll_count; add mark_poll_error"
```

---

### Task 4.2：`compute_poll_backoff` + `BohriumJobPoller.run_once`

**Files:**
- Create: `src/services/bohrium_poller.py`
- Test: `tests/services/test_bohrium_poller.py`

- [ ] **Step 1: 写失败测试**（真实 DAO + mock client / UserService）

`tests/services/test_bohrium_poller.py`：
```python
from __future__ import annotations

from src.services.bohrium_poller import BohriumJobPoller, compute_poll_backoff


def _submit_kwargs(**over):
    base = dict(
        session_id="sess-1", task_id="ws_t", user_id="user-1", org_id="org-1",
        job_id="p1", job_name=None, project_id=42, sandbox=False,
    )
    base.update(over)
    return base


def test_compute_poll_backoff_grows_and_caps() -> None:
    assert compute_poll_backoff(0) == 30
    assert compute_poll_backoff(1) == 60
    assert compute_poll_backoff(2) == 120
    assert compute_poll_backoff(99) == 600        # 上限


def test_poller_polls_due_job_and_writes_running(jobs_table) -> None:
    jobs_table.insert_submitted(**_submit_kwargs(job_id="p1"))
    poller = BohriumJobPoller(
        table=jobs_table,
        get_access_key=lambda uid, oid: "AK",
        get_job_detail=lambda ctx, job_id: {"status": 1},
        base_url="https://openapi.test.dp.tech",
    )
    summary = poller.run_once()
    assert summary["claimed"] == 1 and summary["polled"] == 1
    row = jobs_table.get_by_owner_job(
        user_id="user-1", org_id="org-1", sandbox=False, job_id="p1"
    )
    assert row["status"] == "running"
    assert row["poll_count"] == 1
    assert row["next_poll_at"] is not None


def test_poller_writes_terminal_and_stops_polling(jobs_table) -> None:
    jobs_table.insert_submitted(**_submit_kwargs(job_id="p2"))
    poller = BohriumJobPoller(
        table=jobs_table,
        get_access_key=lambda uid, oid: "AK",
        get_job_detail=lambda ctx, job_id: {"status": 2},   # Finished
        base_url="https://x",
    )
    poller.run_once()
    row = jobs_table.get_by_owner_job(
        user_id="user-1", org_id="org-1", sandbox=False, job_id="p2"
    )
    assert row["status"] == "finished"
    assert row["next_poll_at"] is None
    assert row["terminal_at"] is not None


def test_poller_skips_terminal_jobs(jobs_table) -> None:
    jobs_table.insert_submitted(**_submit_kwargs(job_id="p3"))
    jobs_table.apply_poll(
        user_id="user-1", org_id="org-1", sandbox=False, job_id="p3",
        status="finished", is_terminal=True, backoff_seconds=30,
    )
    calls = []
    poller = BohriumJobPoller(
        table=jobs_table,
        get_access_key=lambda uid, oid: (calls.append("ak"), "AK")[1],
        get_job_detail=lambda ctx, job_id: (calls.append("detail"), {"status": 2})[1],
        base_url="https://x",
    )
    summary = poller.run_once()
    assert summary["claimed"] == 0           # 终态 next_poll_at=NULL，不入队
    assert calls == []                       # 完全不调 Bohrium
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/services/test_bohrium_poller.py -v`
Expected: FAIL（`ModuleNotFoundError: src.services.bohrium_poller`）；MySQL 未起时 DB 用例 SKIPPED、`compute_poll_backoff` 用例仍 FAIL（缺模块）。

- [ ] **Step 3: 实现 poller 核心**

`src/services/bohrium_poller.py`：
```python
"""Bohrium 后台轮询核心（可测；第一版不接独立进程）。

run_once 抢一批到期作业（DAO claim_due_batch 用 FOR UPDATE SKIP LOCKED），
逐个用 job row 快照（user_id/org_id/project_id/sandbox）现查 access_key 并构造
BohriumContext，调 get_job_detail，按 to_ledger_status 归一化后原子写回。
不依赖进程内 JobRegistry、HTTP 请求或 evo_chat_sessions 当前值。
整体 at-least-once：写回经 DAO 原子条件保护 downloaded / 终态不被旧结果回退。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from matmaster.bohrium.status import to_ledger_status
from matmaster.bohrium.types import BohriumContext, BohriumCredentials
from src.dao.bohrium_jobs_table import BohriumJobsTable

logger = logging.getLogger(__name__)

_BASE_BACKOFF_SECONDS = 30
_MAX_BACKOFF_SECONDS = 600


def compute_poll_backoff(poll_count: int) -> int:
    """指数退避带上限：30, 60, 120, ... 封顶 600 秒。"""
    n = max(0, int(poll_count))
    return min(_BASE_BACKOFF_SECONDS * (2 ** min(n, 5)), _MAX_BACKOFF_SECONDS)


class BohriumJobPoller:
    def __init__(
        self,
        *,
        table: BohriumJobsTable | None = None,
        get_access_key: Callable[[str, str], str | None] | None = None,
        get_job_detail: Callable[..., dict[str, Any]] | None = None,
        base_url: str | None = None,
    ) -> None:
        self._table = table if table is not None else BohriumJobsTable()
        if get_access_key is None:
            from src.services.user_service import UserService

            get_access_key = UserService.get_bohrium_access_key
        if get_job_detail is None:
            from matmaster.bohrium.client import get_job_detail as _gjd

            get_job_detail = _gjd
        if base_url is None:
            from matmaster.bohrium.endpoints import get_bohrium_base_url

            base_url = get_bohrium_base_url()
        self._get_access_key = get_access_key
        self._get_job_detail = get_job_detail
        self._base_url = base_url

    def run_once(
        self, *, limit: int = 50, claim_timeout_seconds: int = 120
    ) -> dict[str, int]:
        claimed = self._table.claim_due_batch(
            limit=limit, claim_timeout_seconds=claim_timeout_seconds
        )
        ak_cache: dict[tuple[str, str], str | None] = {}
        polled = 0
        errors = 0
        for job in claimed:
            if self._poll_one(job, ak_cache):
                polled += 1
            else:
                errors += 1
        return {"claimed": len(claimed), "polled": polled, "errors": errors}

    def _poll_one(
        self, job: dict[str, Any], ak_cache: dict[tuple[str, str], str | None]
    ) -> bool:
        user_id = str(job["user_id"])
        org_id = str(job["org_id"])
        sandbox = bool(job["sandbox"])
        raw_job_id = str(job["job_id"])
        backoff = compute_poll_backoff(int(job.get("poll_count", 0)) + 1)

        key = (user_id, org_id)
        if key not in ak_cache:
            try:
                ak_cache[key] = self._get_access_key(user_id, org_id)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "poller get_access_key failed user=%s org=%s", user_id, org_id,
                    exc_info=True,
                )
                ak_cache[key] = None
        access_key = ak_cache[key]
        if not access_key:
            self._table.mark_poll_error(
                user_id=user_id, org_id=org_id, sandbox=sandbox, job_id=raw_job_id,
                error="Bohrium access_key unavailable", backoff_seconds=backoff,
            )
            return False

        try:
            ctx = self._build_ctx(job, access_key)
            job_id: int | str = raw_job_id if sandbox else int(raw_job_id)
            detail = self._get_job_detail(ctx, job_id=job_id)
            code = detail.get("status") if isinstance(detail, dict) else None
            if code is None:
                self._table.mark_poll_error(
                    user_id=user_id, org_id=org_id, sandbox=sandbox,
                    job_id=raw_job_id,
                    error="Bohrium detail missing status", backoff_seconds=backoff,
                )
                return False
            decision = to_ledger_status(int(code))
            self._table.apply_poll(
                user_id=user_id, org_id=org_id, sandbox=sandbox, job_id=raw_job_id,
                status=decision.status,
                is_terminal=decision.is_terminal,
                backoff_seconds=backoff,
            )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "poller get_job_detail failed job_id=%s: %s", raw_job_id, exc,
                exc_info=True,
            )
            self._table.mark_poll_error(
                user_id=user_id, org_id=org_id, sandbox=sandbox, job_id=raw_job_id,
                error=f"poll failed: {exc}", backoff_seconds=backoff,
            )
            return False

    def _build_ctx(
        self, job: dict[str, Any], access_key: str
    ) -> BohriumContext:
        cred = BohriumCredentials(
            access_key=access_key,
            project_id=int(job["project_id"]),
            user_id=None,
            user_no="",
            base_url=self._base_url,
        )
        return BohriumContext.from_credentials(
            cred, sandbox=bool(job["sandbox"]), source="poller"
        )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/services/test_bohrium_poller.py -v`
Expected: PASS（`compute_poll_backoff` 用例无需 MySQL；DB 用例在 MySQL 已起时通过）。

- [ ] **Step 5: Commit**

```bash
git add src/services/bohrium_poller.py tests/services/test_bohrium_poller.py
git commit -m "feat(bohrium-ledger): BohriumJobPoller core (claim/normalize/write-back)"
```

---

### Task 4.3：poller access_key 缓存与异常处理

**Files:**
- Test: `tests/services/test_bohrium_poller.py`

- [ ] **Step 1: 写测试**（缓存复用、detail 异常标 unknown、access_key 缺失）

在 `tests/services/test_bohrium_poller.py` 追加：
```python
def test_poller_caches_access_key_within_round(jobs_table) -> None:
    for jid in ["q1", "q2", "q3"]:
        jobs_table.insert_submitted(**_submit_kwargs(job_id=jid))
    ak_calls = []

    def _get_ak(uid, oid):
        ak_calls.append((uid, oid))
        return "AK"

    poller = BohriumJobPoller(
        table=jobs_table,
        get_access_key=_get_ak,
        get_job_detail=lambda ctx, job_id: {"status": 1},
        base_url="https://x",
    )
    poller.run_once()
    assert ak_calls == [("user-1", "org-1")]   # 同 (user,org) 一轮只查一次


def test_poller_marks_unknown_on_detail_exception(jobs_table) -> None:
    jobs_table.insert_submitted(**_submit_kwargs(job_id="q4"))

    def _boom(ctx, job_id):
        raise RuntimeError("api 500")

    poller = BohriumJobPoller(
        table=jobs_table,
        get_access_key=lambda uid, oid: "AK",
        get_job_detail=_boom,
        base_url="https://x",
    )
    summary = poller.run_once()
    assert summary["errors"] == 1
    row = jobs_table.get_by_owner_job(
        user_id="user-1", org_id="org-1", sandbox=False, job_id="q4"
    )
    assert row["status"] == "unknown"
    assert row["last_error"] is not None
    assert row["next_poll_at"] is not None       # 仍按 backoff 轮询


def test_poller_marks_error_when_access_key_missing(jobs_table) -> None:
    jobs_table.insert_submitted(**_submit_kwargs(job_id="q5"))
    detail_calls = []
    poller = BohriumJobPoller(
        table=jobs_table,
        get_access_key=lambda uid, oid: None,
        get_job_detail=lambda ctx, job_id: (detail_calls.append(1), {"status": 1})[1],
        base_url="https://x",
    )
    poller.run_once()
    row = jobs_table.get_by_owner_job(
        user_id="user-1", org_id="org-1", sandbox=False, job_id="q5"
    )
    assert row["last_error"] is not None
    assert detail_calls == []                    # 无 ak 时不调 get_job_detail
```

- [ ] **Step 2: 跑测试**

Run: `uv run pytest tests/services/test_bohrium_poller.py -v`
Expected: PASS（全部）。

- [ ] **Step 3: Commit**

```bash
git add tests/services/test_bohrium_poller.py
git commit -m "test(bohrium-ledger): poller ak cache and error handling"
```

---

## 最终验证

完成全部 Task 后，按此顺序做端到端验证。

- [ ] **全量纯逻辑测试（无需 MySQL）**

Run:
```bash
uv run pytest \
  tests/matmaster/bohrium/test_ledger_status.py \
  tests/matmaster/context/sources/test_session_jobs.py \
  tests/matmaster/test_bohrium_ledger_injection.py \
  tests/matmaster/test_runtime_context_assembly_session_jobs.py \
  tests/matmaster/tools/builtin/test_bohrium_tool_ledger.py \
  tests/services/test_bohrium_jobs_wiring.py \
  -v
```
Expected: 全部 PASS。

- [ ] **全量真实 MySQL 测试（先 docker 起 .env.test 的 MySQL）**

Run:
```bash
set -a; source .env.test; set +a
uv run pytest tests/dao/ tests/services/test_bohrium_poller.py -v
```
Expected: 全部 PASS（不再有 SKIPPED）。

- [ ] **既有 Bohrium 测试回归（确认改动未破坏现有工具/边界测试）**

Run:
```bash
uv run pytest \
  tests/matmaster/tools/builtin/ \
  tests/matmaster/architecture/test_bohrium_runtime_boundaries.py \
  tests/matmaster/context/ \
  -v
```
Expected: 全部 PASS。

- [ ] **import 冒烟（确认无循环 import）**

Run:
```bash
uv run python -c "from src.services.agent_run_service import AgentRunService; from src.services.bohrium_poller import BohriumJobPoller; from matmaster.core.exp import Exp; print('OK')"
```
Expected: 打印 `OK`。

- [ ] **pre-commit 卫生（行数 / 格式）**

Run:
```bash
uv run pre-commit run --files \
  src/sql/create_bohrium_jobs_table.sql src/sql/README.md \
  src/base/base_table.py src/dao/bohrium_jobs_table.py \
  src/services/bohrium_jobs_wiring.py \
  src/services/bohrium_poller.py matmaster/bohrium/status.py \
  matmaster/context/ports.py matmaster/types/runtime_ports.py \
  matmaster/context/sources/session_jobs.py \
  matmaster/core/runtime_context_assembly.py matmaster/core/exp.py \
  matmaster/tools/builtin/bohrium_tool/tool.py \
  src/services/agent_run_service.py \
  tests/dao/__init__.py tests/dao/conftest.py \
  tests/dao/test_base_table_db_config.py \
  tests/dao/test_bohrium_jobs_table.py \
  tests/dao/test_bohrium_jobs_constraints.py \
  tests/dao/test_bohrium_jobs_claim.py \
  tests/matmaster/bohrium/test_ledger_status.py \
  tests/matmaster/context/sources/test_session_jobs.py \
  tests/matmaster/test_bohrium_ledger_injection.py \
  tests/matmaster/test_runtime_context_assembly_session_jobs.py \
  tests/matmaster/tools/builtin/test_bohrium_tool_ledger.py \
  tests/services/__init__.py tests/services/test_bohrium_jobs_wiring.py \
  tests/services/test_bohrium_poller.py
```
Expected: 全部通过；特别确认 `src/dao/bohrium_jobs_table.py` 与 `tool.py` ≤ 1000 行（超限则把 ledger helper 等拆到同目录新文件）。若执行过程中新增或删除文件，必须把实际 changed files 全部纳入本命令，不只跑源码文件。

---

## 完成定义（Definition of Done）

- `bohrium_jobs` 建表脚本可在 MySQL 8.0.16+ 执行；DAO 集中封装全部状态不变量，DB CHECK 为第二道防线。
- `BohriumTool` 在 submit/poll/download/kill 成功后写 ledger，ledger 写失败不阻断工具；身份字段全部经构造器注入，工具不读 `run_meta`/`SESSIONS`/`HookExecutor`/临时 dict。
- agent context 通过 `SessionJobsPort` 读到实时 active + recent terminal 作业，renderer 呈现两类作业，且不向 agent 暴露 `user_id`/`org_id`/原始 JSON。
- `BohriumJobPoller.run_once()` 可在真实库上认领到期作业、归一化写回、保护 downloaded/终态单调性、按 backoff 重试；access_key 现查且一轮缓存复用。
- kernel（`matmaster/`）不 import `src/`；spec Testing Plan 列出的不变量（CHECK/collation/TIMESTAMP UTC/SKIP LOCKED/单调性/binary collation/terminal_at）均有对应测试。
