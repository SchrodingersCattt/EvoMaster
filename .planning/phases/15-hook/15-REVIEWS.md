# Phase 15: Hook 系统异步化 — Cross-AI Review

**Reviewer:** GPT
**Date:** 2026-03-27
**Verdict:** BLOCK — 4 issues (3 P1, 1 P2)

---

## P1: 删除 ReplyQueueLike 会打断现有导入链

**File:** 15-02-PLAN.md (lines 316-330)
**Confidence:** 0.99

计划要求删除 `src/services/agent_run_service.py` 中的 `ReplyQueueLike`，同时明确把 `stream_service.py` 留到后续。但 `stream_service.py:22-26` 直接从 `agent_run_service` 导入 `ReplyQueueLike`，并在 `stream_service.py:181`、`stream_service.py:209`、`stream_service.py:213`、`stream_service.py:217`、`stream_service.py:285`、`stream_service.py:707`、`stream_service.py:721` 等多处使用。

按计划执行会先让 `stream_service` 的导入链失效。要么保留或迁移协议定义，要么把 `stream_service.py` 一并纳入本 plan。

---

## P1: HOOK-02 没有覆盖真实 confirmation 通路

**File:** 15-02-PLAN.md (lines 324-330)
**Confidence:** 0.96

计划把 `stream_service.py`、Redis reply queue 和更深的 src 适配整体延期，只更新 `agent_run_service.py` 里的注释与局部类型。但真实确认回复链路是：

```
chat_api.py (POST /confirmation_reply)
  -> stream_service.get_reply_queue()
    -> RedisReplyQueue / InMemoryReplyQueue
      -> agent_worker / run_agent_sync
```

如果不把这条链路一起改到 `resolve()/cancel()` 语义，新的 `ConfirmationHook` 只能停留在单元测试层，`HOOK-02` 并不会真正交付。

---

## P1: 计划遗漏其他同步 Hook 使用方

**File:** 15-01-PLAN.md (lines 262-282)
**Confidence:** 0.97

Plan 01 只迁移了 4 个测试文件，但 `run_*` 改成 `await hook.method()` 以后，任何仍保留 `sync def` 的 Hook 实现都会抛 `TypeError`。遗漏的同步 Hook：

1. **生产路径:** `matmaster/devshell/stream_hook.py` 的 `DevStreamHook`（5 个 sync 方法），在 `matmaster/devshell/runner.py:105-108` 被真实注入 kernel
2. **测试路径:** `tests/matmaster/core/test_agent.py` 中的 `SkipHook`、`StopHook`、`RecordingHook`、`ChunkRecordingHook`、`SegmentRecordingHook`、`GuardBlockRecorder` 共 6 个 sync Hook 类

按当前范围执行后，devshell 运行路径和全量测试 (`uv run pytest -x -q`) 都会失败。

---

## P2: resolve/cancel 仍有重复提交竞态

**File:** 15-02-PLAN.md (lines 217-242)
**Confidence:** 0.90

`resolve()` / `cancel()` 用 `if future is not None and not future.done()` 作为防护，但检查发生在调用线程，`future.set_result(...)` 通过 `call_soon_threadsafe` 在 loop 线程执行。

两次几乎同时到达的回复仍可能都通过检查并各自排入一个 `set_result` 回调，第二个回调会在 loop 线程里触发 `InvalidStateError`。

更稳妥的做法是把 done 检查放到 loop 线程里的 helper 中，或者在调度前原子地摘掉 pending future。

---

## 补充：VALIDATION.md 内部不一致

验证文档 `15-VALIDATION.md:45-47` 还挂着 `15-02-02` 和 `15-03-01` task ID，但当前执行计划只有 `01` 和 `02` 两个 plan，说明验证项至少有一部分没有明确归属。
