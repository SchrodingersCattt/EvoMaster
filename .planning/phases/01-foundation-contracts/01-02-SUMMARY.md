---
phase: 01-foundation-contracts
plan: 02
subsystem: bus
tags: [queue, threading, sse, event-bus, adapter-pattern]

# Dependency graph
requires:
  - phase: 01-foundation-contracts
    plan: 01
    provides: "16 BusEvent types (7 AgentEvent + 9 SystemEvent) with Pydantic discriminated union"
provides:
  - "MessageBus thread-safe synchronous event queue (queue.Queue wrapper)"
  - "QueueBridge SSE payload adapter converting all 16 BusEvent types to {source, type, content, ...extra} dicts"
  - "matmaster/bus/ package with re-exports"
affects: [02-agent-kernel, 05-integration-quality]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Synchronous queue.Queue for cross-thread event transport (ThreadPoolExecutor compatible)"
    - "isinstance dispatch for polymorphic event-to-dict conversion"
    - "Conditional field inclusion (only add optional fields when truthy/non-None)"

key-files:
  created:
    - matmaster/bus/__init__.py
    - matmaster/bus/queue.py
    - matmaster/bus/bridge.py
    - tests/matmaster/bus/__init__.py
    - tests/matmaster/bus/test_message_bus.py
    - tests/matmaster/bus/test_queue_bridge.py
  modified: []

key-decisions:
  - "QueueBridge.next_payload() returns base payload without session_id/task_id -- injected by agent_run_service"
  - "Single consumer pattern -- QueueBridge exclusively consumes from MessageBus via get()"
  - "Synchronous queue.Queue chosen over asyncio.Queue because agent runs in ThreadPoolExecutor"

patterns-established:
  - "Event bus pattern: MessageBus.emit() in producer thread, QueueBridge.next_payload() in consumer"
  - "SSE payload format: {source, type, content, ...extra} matching existing event_callback signature"
  - "Conditional extra fields: ThoughtEvent adds stream_state/stream_id/token_count/context only when present"

requirements-completed: [EBUS-01, EBUS-02]

# Metrics
duration: 4min
completed: 2026-03-21
---

# Phase 1 Plan 02: MessageBus and QueueBridge Summary

**Thread-safe synchronous MessageBus (queue.Queue) with QueueBridge adapter converting all 16 BusEvent types to SSE payload dicts**

## Performance

- **Duration:** 4 min
- **Started:** 2026-03-21T14:38:39Z
- **Completed:** 2026-03-21T14:43:17Z
- **Tasks:** 2
- **Files modified:** 6

## Accomplishments
- MessageBus with emit/get/get_nowait/pending/empty API, thread-safe via stdlib queue.Queue, verified with 10-thread x 100-event concurrency test
- QueueBridge converting all 16 BusEvent types to SSE payload dict format with correct content extraction per type (str/dict/None dispatch)
- ThoughtEvent conditionally includes stream_state, stream_id, token_count, context only when truthy
- McpServerStatusEvent includes mcp_phase/mcp_server/mcp_transport extra keys; McpConnectEvent includes mcp_phase
- 34 unit tests (8 MessageBus + 26 QueueBridge) covering all event types, FIFO ordering, threading, timeout

## Task Commits

Each task was committed atomically:

1. **Task 1: MessageBus synchronous event queue** - `4c5509d` (test, RED) + `d4889c3` (feat, GREEN)
2. **Task 2: QueueBridge SSE payload adapter** - `ee14814` (test, RED) + `4ebcc08` (feat, GREEN)

_Note: TDD tasks have separate test (RED) and implementation (GREEN) commits._

## Files Created/Modified
- `matmaster/bus/__init__.py` - Package exports: MessageBus, QueueBridge
- `matmaster/bus/queue.py` - MessageBus synchronous event queue (queue.Queue wrapper)
- `matmaster/bus/bridge.py` - QueueBridge SSE payload adapter with isinstance dispatch for 16 event types
- `tests/matmaster/bus/__init__.py` - Test package marker
- `tests/matmaster/bus/test_message_bus.py` - 8 tests: FIFO, threading (10x100), timeout, pending/empty
- `tests/matmaster/bus/test_queue_bridge.py` - 26 tests: all 16 event type conversions, conditional fields, integration

## Decisions Made
- QueueBridge.next_payload() returns base payload without session_id/task_id -- these session-level fields are injected by agent_run_service, keeping bus/bridge decoupled from session concept
- Single consumer pattern chosen (QueueBridge exclusively consumes) -- no subscribe/unsubscribe needed
- Synchronous queue.Queue over asyncio.Queue because agent runs in ThreadPoolExecutor (synchronous thread)

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- Phase 1 (Foundation Contracts) is fully complete: all contracts + bus infrastructure ready
- Phase 2 (Agent Kernel) can import MessageBus for event emission and all 16 event types
- Phase 5 (Integration) can verify end-to-end SSE flow through QueueBridge
- No blockers for downstream work

## Self-Check: PASSED

All 6 created files verified on disk. All 4 commit hashes (4c5509d, d4889c3, ee14814, 4ebcc08) verified in git log.

---
*Phase: 01-foundation-contracts*
*Completed: 2026-03-21*
