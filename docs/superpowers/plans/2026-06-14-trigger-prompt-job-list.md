# Trigger 唤醒 Prompt 自带 job 清单 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **本文档关注步骤与契约，不写整段实现。** 文中保留的代码限于无法从上下文推断的契约性素材：spec 规定的措辞文案、section 的 key/tag/order、字段名与取值、签名变化、关键断言点。具体函数体由执行者对照**当前真实代码**落地——下方引用的行号是写作时的快照，落地前务必复核，以真实代码为准。

**Goal:** 让 `bohrium_completion` trigger run 的 prompt 自带完整 job 清单（pending terminal + active）与统一处理指令，取代单独注入的指向式 delivery section，使「待处理清单 == `snapshot.rows` == worker ack 的 row-id 段」在文本层显式成立。

**Architecture:** 双段拼接——monitor 侧 `render_prompt(reason)` 只产「原因头」(why)，worker 侧 delivery context 渲染「清单 + 处理指令」(what + how)。渲染分流靠新增的 `WorkspaceJobs.mode` 字段（`delivery` / `observation`）：delivery 出「清单(order 1200) + 处理指令(order 1300)」，observation 维持现状不出指令。section 顺序由现有 `SectionOrder`（`TURN_INSTRUCTION=1000` < `WORKSPACE_JOBS=1200` < `TURN_INSTRUCTION_LAST=1300`）天然支撑。

**Tech Stack:** Python ≥3.11、frozen dataclass、pytest（`uv run --extra dev pytest`，`asyncio_mode=auto`）、asyncio。

**Spec:** `docs/superpowers/specs/2026-06-14-trigger-prompt-job-list-design.md`（已定稿）。前置依赖 workspace job context section spec 已随 merge `5ccab4bb` 落地。

---

## 背景与核心不变量（摘自 spec §1.1 / §2）

worker 对所有成功 run **无条件** ack（`src/worker/agent_worker.py` 的 `run_success and delivery_snapshot is not None` → `confirm`），`confirm` 把 `snapshot.rows ∪ observed_terminal` 全标 `handled`。因此 prompt 的真实身份是「将被 ack 的工单」——必须把会被 ack 的 job 摆给 agent 并强制逐一处理，否则盲 ack、交付永久丢失。

worker 总 ack 范围是两段并集，两类边界性质不同（勿混为一个等式）：

- **`snapshot.rows`**：run 起点锁定的全量 pending terminal，run 成功就被无条件 ack。agent 不主动看就盲 ack——**本改造的防线就在这一段**：prompt pending 清单必须 == `snapshot.rows`。
- **`observed_terminal`**：run 内 agent 用工具主动 poll 到终态的 `(sandbox, job_id)`，confirm 经 `mark_handled_by_job_keys` ack。它是 agent 主动观察结果、天然「已看到」、无盲 ack 风险，**不需要 prompt 清单预先覆盖、本改造不触碰其行为**。

故本改造守住的等式：

```
prompt pending 清单  ==  snapshot.rows  ==  worker ack 的 row-id 段
worker 总 ack 范围   ==  snapshot.rows ∪ observed_terminal   （observed_terminal 行为不变）
```

active jobs 一并展示作上下文，但不在 ack 范围、不要求处理；不变量只约束 pending terminal。

---

## 文件结构与职责

| 文件 | 职责 | 本计划改动 |
| --- | --- | --- |
| `matmaster/context/ports.py` | `WorkspaceJobs` 数据对象 | 加 `mode` 字段（`delivery`/`observation`，默认后者） |
| `matmaster/context/sources/workspace_jobs.py` | 把 `WorkspaceJobs` 渲染成 context section | 按 `mode` 分流：delivery 出清单 + 处理指令两个 section；observation 维持现状 |
| `src/services/bohrium_jobs_wiring.py` | service 层把 DAO 包成 kernel 读/写 port | 两个读 port 各自填 `mode`；修复 delivery port 异常吞掉 pending 的盲 ack 洞 |
| `src/services/bohrium_completion_scheduler.py` | monitor 侧唤醒判定 + prompt 渲染 | `render_prompt` 瘦成 `(reason)`；删 `_DELIVERY_SCOPE_SUFFIX`；`_process_session` 删 counts/first_failed |
| `src/dao/bohrium_jobs_table.py` | bohrium_jobs 表 DAO | 删 `get_first_pending_failed`（失去唯一生产调用者） |

测试文件：
- `tests/matmaster/context/sources/test_workspace_jobs.py`（纯内存）— Task 1/2
- `tests/services/test_bohrium_jobs_wiring.py`（`MagicMock` 注入，不连库）— Task 3
- `tests/services/test_bohrium_completion_scheduler.py`（假对象注入，不连库）— Task 4
- `tests/dao/test_bohrium_jobs_delivery.py`（连 MySQL，无 `.env.test` 自动 skip）— Task 5
- `tests/matmaster/context/test_compositions.py`（纯内存）— Task 6 端到端

**任务顺序与依赖：** Task 1 →（Task 2 ∥ Task 3）→ Task 4 → Task 5 → Task 6。Task 4 删去 `_process_session` 对 `get_first_pending_failed` 的调用后，Task 5 才能安全删该 dao 方法；Task 6 端到端验收依赖 1/2，放最后做整体回归。

---

## 环境与约定（执行前必读）

- **跑测试用 `uv run --extra dev pytest ...`**：pytest 在 `pyproject.toml` 的 `optional-dependencies.dev` 里，uv 默认不装 extra，干净 worktree 必须带 `--extra dev` 否则缺依赖。勿用系统 Python/pip。
- **service / source / wiring 测试不连真实数据库**：全部用假对象 / `MagicMock` 注入。勿引入连真实 MySQL 的依赖（CI 跑 `pytest -n`，漏注入会偶发连库 flaky）。
- **dao 测试需 `.env.test`**：本地无该文件时 `tests/dao/` 自动 skip，此时靠 `rg` 残留检查 + CI 兜底（见 Task 5）。
- **commit 只 add 实现代码与测试，绝不 `git add docs/`**（项目硬约定：不向 `docs/` 做任何 git 提交，本 plan 文件不进版本库）。
- commit message 走仓库 conventional 风格（`feat(...)` / `refactor(...)`），署名按仓库规范。
- 禁止考虑兼容性：旧 `render_prompt` 文案与 `_DELIVERY_SCOPE_SUFFIX` 直接删除替换，不做内联兜底/迁移。

---

## 契约性素材（实现与测试都以此为准，不得自拟）

**`WorkspaceJobs.mode` 取值：** `"delivery"` | `"observation"`，默认 `"observation"`。

**section 契约：**

| section | key / tag | order（`SectionOrder`） | views | mode |
| --- | --- | --- | --- | --- |
| job 清单 | `workspace_jobs` | `WORKSPACE_JOBS`(1200) | `ALL_VIEWS`（维持现状） | 两种 mode 都出（非空时） |
| 处理指令 | `delivery_directive` | `TURN_INSTRUCTION_LAST`(1300) | `RUNTIME_ONLY_VIEWS`（见下注） | 仅 delivery、且 pending 非空时 |
| 原因头 | `current_instruction`（注意：turn instruction 的 tag 是 `current_instruction`，非 `turn_instruction`） | `TURN_INSTRUCTION`(1000) | `RUNTIME_ONLY_VIEWS` | 来自 `payload.user_prompt`，非本改造产出 |

> `delivery_directive` 取 `RUNTIME_ONLY_VIEWS`（非 `ALL_VIEWS`）：处理指令是 turn 时刻的一次性指令，性质同原因头，不应渗入 checkpoint 持久化视图，避免一条过时的逐一处理指令在后续 compaction 恢复时被复用。已核实这不影响 prompt 缓存——实时发往 LLM 的是 RUNTIME 视图渲染（`exp.py` 的 `to_message(ContextView.RUNTIME)`），`RUNTIME_ONLY_VIEWS` 与 `ALL_VIEWS` 在 RUNTIME 视图下渲染字节一致；缓存断点在 transport 层按 message role/位置放置（`anthropic_messages.py`），与 section 的 order/view 无关。

**四句原因头（`render_prompt(reason)`，spec §5.2，逐字）：**

- `FINAL`：`本会话的 Bohrium 作业批次已全部到达终态。`
- `PROGRESS`：`本会话又有 Bohrium 作业到达终态、结果待处理，仍有作业在运行。`
- `FIRST_FAILURE`：`本会话出现失败的 Bohrium 作业，仍有作业在运行。`
- `STALLED`：`本会话有 Bohrium 作业结果待处理，另有作业长时间无法查询状态。`

**delivery 清单组引子（定稿，逐字契约，测试逐字断言）：**

- pending 组：`以下 Bohrium 作业已结束、结果待处理（属于本轮交付确认范围）：`
- active 组：`以下 Bohrium 作业仍在运行（仅作上下文，无需处理）：`

**处理指令文案（spec §4.3，逐字）：**

> 请逐一拉取并核对以上已结束作业的结果：成功项汇总关键产出，失败项诊断原因，给出整体结论与下一步。处理完成即视为交付确认。

**overflow 涵盖句（spec §6，逐字，仅 pending 有 overflow 时追加在处理指令末尾）：**

> （末尾 overflow 摘要中的 job_ids 同属本批次，请按其 status 一并处理。）

---

## Task 1: `WorkspaceJobs` 加 `mode` 字段

**Files:** Modify `matmaster/context/ports.py`；Test `tests/matmaster/context/sources/test_workspace_jobs.py`

`mode` 驱动渲染层分流。默认 `"observation"` 是安全侧（不出处理指令），`empty()` 因此也是 observation。`Literal` 已在该文件 import。

- [ ] **Step 1: 写 failing test** — 断言 `WorkspaceJobs().mode` 与 `WorkspaceJobs.empty().mode` 均为 `"observation"`、`WorkspaceJobs(mode="delivery").mode` 为 `"delivery"`。
- [ ] **Step 2: 跑测试验证失败** — `uv run --extra dev pytest tests/matmaster/context/sources/test_workspace_jobs.py -k mode -v`；Expected: FAIL（`unexpected keyword argument 'mode'` / 无 `mode` 属性）。
- [ ] **Step 3: 加字段** — 在 `WorkspaceJobs` 字段末尾加 `mode: Literal["delivery", "observation"] = "observation"`。其余字段、`empty()` 不动。
- [ ] **Step 4: 跑测试验证通过** — `uv run --extra dev pytest tests/matmaster/context/sources/test_workspace_jobs.py -v`；Expected: PASS（新测试过；现有 12 个测试不传 `mode`、默认 observation，且此时 `from_jobs` 尚未读 `mode`，不受影响）。
- [ ] **Step 5: commit** — `git add matmaster/context/ports.py tests/matmaster/context/sources/test_workspace_jobs.py`，message 形如 `feat(context): add mode field to WorkspaceJobs`。

---

## Task 2: `WorkspaceJobsSource` 按 mode 分流渲染

**Files:** Modify `matmaster/context/sources/workspace_jobs.py`；Test `tests/matmaster/context/sources/test_workspace_jobs.py`

核心 Task。`from_jobs` 据 `jobs.mode` 分两条渲染路径。

**observation 路径——维持现状，逐字节不变**：`active + pending + recent` 三组、无组引子、产出单个 `workspace_jobs` section、不出处理指令。现有 12 个 observation 测试是回归保护，输出一旦变动即视为破坏。

**delivery 路径**：
- **清单 section**（`workspace_jobs` / order 1200）：两组——pending terminal（带 pending 引子）在前、active（带 active 引子）在后。复用现有「前 `detail_limit` 条逐条 JSON、其余压成一行 overflow 摘要（`count` / `by_status` / 全量 `job_ids`）」机制。
- **处理指令 section**（`delivery_directive` / order 1300）：内容为处理指令文案；**仅当 pending 非空时产出**（active 仍在跑、无结果可处理，不在指令范围）；**仅当 pending 组发生 overflow 时**在文案末尾追加 overflow 涵盖句（active 即便 overflow 也不涉及 ack，不触发涵盖句）。

**实现提示（非完整代码，落地对照真实文件）**：现有 `_render_group` 只返回行；delivery 需要它额外告知「该组是否 overflow」以驱动涵盖句，并支持「组引子作为首行」。`WorkspaceJobsSource` 需要承载处理指令内容，供 `to_sections` 在清单之外产出第二个 section。observation 路径调用 `_render_group` 时不传引子、忽略 overflow 标志，保持原输出。Step 3 动手前先通读 `workspace_jobs.py` 当前实现：`_render_group` 若改为同时返回 overflow 标志（如 `(lines, overflowed)`）会改变返回型，observation 的两个调用点必须同步适配，12 个回归测试是这步的安全网。

- [ ] **Step 1: 写 delivery 渲染 failing tests**（断言点，复用文件内既有 `_job` helper）：
  - delivery 基本：`to_sections()` 返回 2 段；清单段 key/tag=`workspace_jobs`、order=`WORKSPACE_JOBS`，内容依次为 pending 引子 / pending 逐条 / active 引子 / active 逐条；指令段 key/tag=`delivery_directive`、order=`TURN_INSTRUCTION_LAST`、views=`RUNTIME_ONLY_VIEWS`、内容 == 处理指令文案。
  - overflow：pending 超 `detail_limit` 时，清单含 `pending_terminal_overflow` 行，且指令段以 overflow 涵盖句结尾。
  - 无 overflow：pending 未超限时指令段 == 处理指令文案、不含 overflow 句。
  - active 溢出不触发涵盖句（spec §6 设计点）：active 超 `detail_limit`、pending 未超限 → 清单含 active overflow 行，但指令段 == 处理指令文案、不含 overflow 涵盖句（active 不计入 ack）。
  - 核心不变量（渲染段）：pending 详情行的 job_id + overflow 行的 `job_ids` 合并 == 输入 `pending_terminal_jobs` 全集（顺序一致）。
  - 边界：pending 空 + active 非空 → 只出清单段、不出指令段；delivery 全空 → `to_sections()` 为 `()`。
  - observation 不回归：`mode="observation"` 下 pending 非空时只出 `workspace_jobs`、不出 `delivery_directive`。
- [ ] **Step 2: 跑测试验证失败** — `uv run --extra dev pytest tests/matmaster/context/sources/test_workspace_jobs.py -k "delivery or directive" -v`；Expected: FAIL（旧实现忽略 `mode`、无指令段）。
- [ ] **Step 3: 实现分流** — 按上文 observation/delivery 两路改 `from_jobs` / `_render_group` / `to_sections`，新增引子、处理指令、overflow 涵盖句三个文案常量（取契约素材中的逐字文本）。
- [ ] **Step 4: 跑全部 source 测试验证通过** — `uv run --extra dev pytest tests/matmaster/context/sources/test_workspace_jobs.py -v`；Expected: PASS（新 delivery 测试 + 现有 observation 测试全过）。
- [ ] **Step 5: commit** — `git add matmaster/context/sources/workspace_jobs.py tests/...test_workspace_jobs.py`，message 形如 `feat(context): render delivery job listing + directive, keep observation intact`。

---

## Task 3: 两个读 port 填 `mode` + 修复 delivery 异常吞 pending

**Files:** Modify `src/services/bohrium_jobs_wiring.py`；Test `tests/services/test_bohrium_jobs_wiring.py`

两件事：把 `mode` 传到渲染层；修一个会重新制造盲 ack 的洞。

**填 mode**：`_SessionWorkspaceDeliveryJobsPort` 构造 `WorkspaceJobs` 填 `mode="delivery"`，`_WorkspaceObservationJobsPort` 填 `mode="observation"`。

**盲 ack 修复（必做）**：当前 delivery port 的 `load_workspace_jobs` 把 `query_session_active`（会失败的 DB 调用）与「从 `self._snapshot` 取 pending / detail_limit」（纯内存、现成）放在**同一个 `try`**，active 一抛异常就整体 `return WorkspaceJobs.empty()`，把无辜的 pending 一起丢掉；而 worker 不看渲染结果、run 成功就 ack `snapshot.rows` → 清单空但仍 ack = 盲 ack。**改法**：把 pending / detail_limit 的取值移到 active 查询的 `try` **之外**（snapshot 是纯内存访问，不会抛）；active 查询失败只降级为 `active_jobs=()`、记一条 warning，**绝不因 active 失败丢 snapshot pending**。observation port 的 `except` 返回 `empty()` 无害（observation 不触发 worker ack），仅加 mode、不重构。

- [ ] **Step 1: 写 failing tests**：
  - 现有 `test_delivery_mode_serves_active_and_pending_from_snapshot` 加断言 `result.mode == "delivery"`；现有 `test_observation_mode_reads_three_groups_cross_session` 加断言 `result.mode == "observation"`。
  - 新增盲 ack 防线测试：`query_session_active` 抛异常（`MagicMock` 的 `side_effect`）+ snapshot.rows 非空 → 返回 `mode="delivery"`、`active_jobs == ()`、`pending_terminal_jobs == snapshot.rows`（active 失败不丢 pending）。
- [ ] **Step 2: 跑测试验证失败** — `uv run --extra dev pytest tests/services/test_bohrium_jobs_wiring.py -v`；Expected: FAIL（delivery 的 mode 断言取到默认 `"observation"`；防线测试在旧实现下返回 `empty()`、pending 丢失）。
- [ ] **Step 3: 实现**（动手前先通读 `bohrium_jobs_wiring.py` 两个 port 当前的 `load_workspace_jobs`）— 按上文重构 delivery port 的 `load_workspace_jobs`（pending 取值移出 active 的 try、active 失败降级），两个 port 各填 mode。
- [ ] **Step 4: 跑测试验证通过** — `uv run --extra dev pytest tests/services/test_bohrium_jobs_wiring.py -v`；Expected: PASS（含 `test_observation_mode_empty_when_workspace_missing`：`empty()` 与空 port 返回值 mode 都是默认 observation，断言仍成立）。
- [ ] **Step 5: commit** — `git add src/services/bohrium_jobs_wiring.py tests/services/test_bohrium_jobs_wiring.py`，message 形如 `fix(bohrium): keep snapshot pending when active query fails; tag delivery/observation mode`。

---

## Task 4: scheduler 侧 `render_prompt` 瘦身 + `_process_session` 清理

**Files:** Modify `src/services/bohrium_completion_scheduler.py`；Test `tests/services/test_bohrium_completion_scheduler.py`

`render_prompt` 签名 `(reason, counts, first_failed)` → `(reason)`，只产四句定性原因头（契约素材逐字，无数字、无 job_id、无指向）。删 `_DELIVERY_SCOPE_SUFFIX` 常量。`_process_session` 同步删 `counts` 聚合、`get_first_pending_failed` 调用（`primary_unit` 随之失去用途，改为只取 `primary_reason`），保留 `primary_reason`（`DeliverySpec(notify=primary_reason is Reason.FINAL)` 仍需要）。签名变更与调用点必须**原子修改**（否则运行时 `TypeError`），故合并本 Task。按硬约定（瘦身/删死代码不新增测试），本 Task **不补 `render_prompt` 直接单测**；四句原因头的正确性由改后的 FIRST_FAILURE tick 测试（断言 `prompt` == 对应原因头）间接覆盖。代价：FINAL/PROGRESS/STALLED 三句的逐字正确性无专门断言——它们与 FIRST_FAILURE 同属一张 reason→文案静态映射，风险低，接受此覆盖度。

- [ ] **Step 1: 改测试预期（failing）**：
  - `_FakeJobsTable` 瘦身：删 `first_failed` 入参、`first_failed_calls`、`get_first_pending_failed`（删调用后不再被触达）。
  - 替换 `test_tick_first_failure_fetches_scoped_job_info`：改为断言 FIRST_FAILURE 仍触发一次 trigger、`delivery=DeliverySpec(notify=False)`、`prompt` == FIRST_FAILURE 原因头（期望文案逐字取契约素材，不再 fetch / 不点名 job_id）。
  - 简化 `test_tick_null_invocation_sentinel_unit_flows_through`：删 `first_failed_calls` 断言，保留「null invocation 单元能触发一次 trigger」。
- [ ] **Step 2: 跑测试验证失败** — `uv run --extra dev pytest tests/services/test_bohrium_completion_scheduler.py -k "first_failure or null_invocation" -v`；Expected: FAIL（改后的 tick 测试因 `_process_session` 仍调已删的 `_FakeJobsTable.get_first_pending_failed` 报 `AttributeError`；即便不炸，旧 `render_prompt` 返回旧文案 + `_DELIVERY_SCOPE_SUFFIX`，FIRST_FAILURE 的 `prompt` == 原因头断言亦不符）。
- [ ] **Step 3: 实现** — 删 `_DELIVERY_SCOPE_SUFFIX`；`render_prompt` 改为按 `reason` 返回四句原因头之一（可用 reason→文案的映射表）；`_process_session` 删 counts 聚合与 `get_first_pending_failed` 调用、改为只取 `primary_reason`、`render_prompt(primary_reason)`，`DeliverySpec(notify=...)` 不动。
- [ ] **Step 4: 跑全部 scheduler 测试验证通过** — `uv run --extra dev pytest tests/services/test_bohrium_completion_scheduler.py -v`；Expected: PASS（改后的 tick 测试、全部 `decide`/其余 tick 测试）。
- [ ] **Step 5: commit** — `git add src/services/bohrium_completion_scheduler.py tests/services/test_bohrium_completion_scheduler.py`，message 形如 `refactor(bohrium): slim render_prompt to reason header, drop counts/first_failed`。

---

## Task 5: 删除 `get_first_pending_failed`（dao）+ 清理其测试

**Files:** Modify `src/dao/bohrium_jobs_table.py`；Test `tests/dao/test_bohrium_jobs_delivery.py`

Task 4 删去唯一生产调用后该方法成死代码（FIRST_FAILURE 原因头不再点名 job_id，spec §7 时序说明）。删方法 + 清理测试引用。`_SQL_FAILURE` 仍被 `list_pending_terminal_snapshot` 与 `scan_delivery_units` 共享（删 `get_first_pending_failed` 后仍有两个使用者），**不删**。删除场景的 TDD 退化为「先移除测试对该方法的引用，再删实现，跑剩余测试 + grep 零残留」。

- [ ] **Step 1: 清理 dao 测试对该方法的引用**：
  - 整删两个纯测该方法的用例：`test_get_first_pending_failed_returns_earliest_unhandled`、`test_get_first_pending_failed_scoped_by_workspace`。
  - 改 `test_scan_lost_with_active_has_first_failure_shape`：保留前半段 `scan_delivery_units` 的 unit 形状断言（有独立价值、依赖 `_force_lost`），仅删尾部 `get_first_pending_failed` 调用与其断言。
  - **保留不动**（被其他测试共享）：`sessions_shadow` fixture、`_register_session`、`_seed_job`、`_shift_terminal_at`、`_force_lost`。
- [ ] **Step 2: 删 dao 方法** — 删除 `bohrium_jobs_table.py` 中 `get_first_pending_failed` 整个方法定义，`_SQL_FAILURE` 保留。
- [ ] **Step 3: 验证零残留** — `rg -n "get_first_pending_failed" src/ tests/ matmaster/`；Expected: 无输出。
- [ ] **Step 4: 跑 dao 测试** — `uv run --extra dev pytest tests/dao/test_bohrium_jobs_delivery.py -v`；Expected: 有 `.env.test` 则 PASS；无则全 SKIPPED（此时以 Step 3 零残留为验收依据，真实库回归由 CI 兜底）。
- [ ] **Step 5: commit** — `git add src/dao/bohrium_jobs_table.py tests/dao/test_bohrium_jobs_delivery.py`，message 形如 `refactor(bohrium): drop get_first_pending_failed (no caller after prompt slim)`。

---

## Task 6: 端到端集成测试（验收标准 1：三段同屏同序）

**Files:** Test `tests/matmaster/context/test_compositions.py`（纯内存，已有「`apply()` → `render(view)` → 断言 tag」的现成模式）

source 层只能证明清单/指令各自正确，证明不了原因头(1000)、清单(1200)、处理指令(1300)在最终 user message 里同屏同序。补一个端到端测试覆盖验收 1。

注意几个易错点：

- 原因头的 section tag 是 `current_instruction`（非 `turn_instruction`）且 views 为 `RUNTIME_ONLY_VIEWS`（仅 RUNTIME 可见），故端到端断言必须 `render(ContextView.RUNTIME)`。
- 三段相对序 1000 < 1200 < 1300 由各 section 固定的 order 属性保证，**与选哪个 composition 无关**；deferral 也不由 composition 决定，而由 `defer_turn_instruction` 输入位控制——它仅在 compaction 装配（`assemble_compaction`）置 True，普通 turn 装配恒为 False，故普通 turn 下原因头必在 1000、不会被推到 1300。
- 生产保真：`bohrium_completion` 唤醒的原因头每次是新 `user_prompt` 文本，其 hash 多半不匹配最近 anchor → 生产更可能走 `ANCHOR_COMPOSITION`（而非 `CONTINUATION_COMPOSITION`）。故主用例用 `ANCHOR_COMPOSITION` 贴近生产（它在三段前还有 `user_instructions`(10) 等 section，但不影响这三段的相对序）。
- 已知边界（本 plan 不额外保证）：compaction 装配会把原因头 defer 到 1300，与 `delivery_directive`(1300) 同序，由 stable sort 按插入序决；但 delivery 唤醒触发 compaction 属罕见，且 `delivery_directive` 设为 `RUNTIME_ONLY_VIEWS` 后不进 checkpoint 持久化（存档用 CHECKPOINT 视图），不构成存档污染。

- [ ] **Step 1: 写 failing tests**：
  - delivery 同序（主用例，用 `ANCHOR_COMPOSITION` 贴近生产）：`ANCHOR_COMPOSITION.apply(...)` 传入 `user_instructions_text` + 带原因头文本的 `TurnInput` + `WorkspaceJobs(mode="delivery", pending_terminal_jobs=..., active_jobs=...)`，`render(ContextView.RUNTIME)`，断言三个 tag 都出现且 `index("<current_instruction>") < index("<workspace_jobs>") < index("<delivery_directive>")`（前面虽有 `<user_instructions>` 等，不影响这三段相对序）。
  - observation 不出指令：同 `ANCHOR_COMPOSITION` 传 `mode="observation"`，`render(RUNTIME)` 断言出现 `<workspace_jobs>`、不出现 `<delivery_directive>`。
- [ ] **Step 2: 跑测试验证失败** — `uv run --extra dev pytest tests/matmaster/context/test_compositions.py -k "delivery or directive" -v`；Expected: FAIL（Task 2 实现前不出 `<delivery_directive>`；若 Task 2 已完成则此步可能直接 PASS，用于回归）。
- [ ] **Step 3: 实现** — 无需改产品代码；本 Task 仅新增测试（若 Step 2 已 PASS，说明 Task 2 的渲染 + composition 装配已正确，测试转为锁定回归）。
- [ ] **Step 4: 跑测试验证通过** — `uv run --extra dev pytest tests/matmaster/context/test_compositions.py -v`；Expected: PASS。
- [ ] **Step 5: 整体回归 + commit** — 先跑不连库的三件套：`uv run --extra dev pytest tests/matmaster/context/sources/test_workspace_jobs.py tests/services/test_bohrium_jobs_wiring.py tests/services/test_bohrium_completion_scheduler.py tests/matmaster/context/test_compositions.py -v`（Expected: PASS）；再 `git add tests/matmaster/context/test_compositions.py`，message 形如 `test(context): assert delivery turn renders reason/listing/directive in order`。

---

## 测试计划对照（spec §8 → 本计划）

| spec §8 测试项 | 落点 |
| --- | --- |
| delivery 渲染：原因头 + 清单 + 处理指令，顺序 1000/1200/1300 | 清单(1200)/指令(1300) 由 Task 2 断言 order；三段最终同屏同序由 Task 6 端到端断言 tag 顺序 |
| 核心不变量 pending 清单(含 overflow job_ids) == snapshot.rows == ack 的 row-id 段 | 三段拼成：snapshot.rows→port.pending（Task 3 现有 `test_delivery_mode_uses_snapshot_detail_limit`）；port.pending→清单全集（Task 2 不变量断言）；snapshot.rows→ack（现有 `confirm`/`mark_handled_by_ids` dao 测试，本改造不动）。`observed_terminal` 是独立 ack 入口、行为不变，不纳入此等式 |
| 防线补强：active 查询失败不得吞掉 snapshot pending | Task 3 盲 ack 防线测试 |
| overflow：超 `detail_limit` 追加涵盖句，不超不追加 | Task 2 overflow / 无 overflow 两测 |
| 四句原因头按 reason 正确，不含数字/不点名 job_id | Task 4 改后的 FIRST_FAILURE tick 测试断言 `prompt` == 原因头（按硬约定不新增直测；FINAL/PROGRESS/STALLED 三句无专门断言，属同张静态映射、风险低） |
| observation 不受影响：仍出观察 section、不出处理指令、措辞不暗示 ack | Task 2 现有 12 个 observation 回归 + 不出 directive 断言；Task 3 observation mode 断言；Task 6 observation 不出 `<delivery_directive>` |
| `render_prompt(reason)` 不再依赖 counts/first_failed | Task 4 render_prompt 仅传 `reason`；`_process_session` 删聚合后 tick 测试通过 |

## 验收标准对照（spec §9）

1. trigger user message 自带完整清单、无指向式割裂 — Task 2+3+4 合成，Task 6 端到端验证。
2. prompt pending 清单 == `snapshot.rows` == worker ack 的 row-id 段（含 overflow job_ids）；active 仅上下文、不计入 ack；`observed_terminal` 独立入口、行为不变 — 上表不变量行 + Task 3 防线。
3. 处理指令强制逐一处理、按 status 自适应 — Task 2 处理指令文案。
4. `render_prompt` 仅依赖 reason；counts/first_failed/`_DELIVERY_SCOPE_SUFFIX` 已删 — Task 4。
5. 用户 query 的 observation 行为不变 — Task 2/3/6 observation 回归。
6. monitor 侧净代码量下降 — Task 4 删聚合/取数/常量、Task 5 删 dao 方法。

## 自检结论（writing-plans self-review）

- **spec 覆盖**：§5.2→Task 4；§5.3→Task 2；§5.4→Task 1+3；§6→Task 2；§7 改动点 7 项映射 Task 1-5；§7 时序说明→Task 4 删调用 + Task 5 删方法；§8 顺序项→Task 6 端到端补强。无遗漏。
- **review 改进并入**：P1 盲 ack 洞→Task 3 防线修复 + 测试；P2(a) 等式混淆→背景区分 `snapshot.rows` 与 `observed_terminal` 两类边界；P2(b) 缺集成测试→Task 6；P3(a) Python 版本→改 ≥3.11；P3(b) 测试命令→统一 `uv run --extra dev pytest`。
- **二轮 review 改进并入（2026-06-14）**：F1 `delivery_directive` 取 `RUNTIME_ONLY_VIEWS`（已核实不伤 prompt 缓存：实时发 LLM 的是 RUNTIME 渲染、缓存断点按 message role 放置）；F2 Task 6 主用例改 `ANCHOR_COMPOSITION` 贴近生产、修正「deferral 由 composition 决定」的范畴错误；F3 组引子定稿为逐字契约；F4 补 active 溢出不触发涵盖句测试；F5 标注 compaction 同序为已知边界；F6 `_SQL_FAILURE` 使用者更正为两处；F7 Task 2/3 加先读真实文件提示；F8 按硬约定删去 Task 4 的 render_prompt 直接单测。
- **契约一致性**：`mode` 取值、section key/tag/order、四句原因头、处理指令与涵盖句文案集中在「契约性素材」一节，各 Task 与测试引用同一处，避免散落措辞漂移。
- **文档风格**：不写整段实现，行号为写作快照、落地前复核；执行者对照真实代码落地，避免被过时上下文误导。
