---
phase: 27-mcp-calculation
plan: 01
subsystem: mcp
tags: [mcp, connection, tool-manager, abc, factory-pattern]

# Dependency graph
requires:
  - phase: 26-tool
    provides: matmaster tool 原生化（ToolRegistry/BuiltinTool 已脱离 evomaster）
provides:
  - MCPConnection ABC + 三种传输子类（stdio/sse/http）
  - create_connection 工厂函数
  - 精简版 MCPToolManager（add_server/cleanup/_build_tools）
  - matmaster/mcp/ 包可独立 import，零 evomaster 依赖
affects: [27-02, 27-03, lazy_mcp, cache_mcp_schemas]

# Tech tracking
tech-stack:
  added: []
  patterns: [lightweight-dict-over-instances, factory-function, retry-with-timeout]

key-files:
  created:
    - matmaster/mcp/__init__.py
    - matmaster/mcp/connection.py
    - matmaster/mcp/manager.py

key-decisions:
  - "MCPToolManager._build_tools stores lightweight dicts instead of MCPTool instances"
  - "add_server uses simple sequential retry instead of runner task pattern"
  - "MCP_CONNECT_TIMEOUT moved from manager.py to connection.py as shared constant"

patterns-established:
  - "matmaster/mcp/ pattern: connection.py holds transport abstractions, manager.py holds orchestration"
  - "tools_by_server stores dict[str, dict[str, Any]] not MCPTool instances for decoupling"

requirements-completed: [MCP-01]

# Metrics
duration: 3min
completed: 2026-04-01
---

# Phase 27 Plan 01: MCP 原生子系统基础设施 Summary

**MCPConnection ABC + 三种传输 + create_connection 工厂 + 精简版 MCPToolManager，matmaster/mcp/ 包零 evomaster 依赖**

## Performance

- **Duration:** 3 min
- **Started:** 2026-04-01T10:41:15Z
- **Completed:** 2026-04-01T10:44:29Z
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments
- 搬入 MCPConnection ABC 及三种传输子类（Stdio/SSE/HTTP），零修改复制，保持与 evomaster 原版行为一致
- 实现精简版 MCPToolManager，只保留 add_server/_build_tools/cleanup 核心接口，不含 runner/reconnection/progress/registry 冗余功能
- _build_tools 完整保留五层过滤链（include_only -> sync_tools -> async dedup -> description propagation -> path_adaptor 标记）
- tools_by_server 存储轻量级 dict 而非 MCPTool 实例，为 Plan 03 的 LazyMCPTool 直连重写提供基础

## Task Commits

Each task was committed atomically:

1. **Task 1: 搬入 MCPConnection 并创建 matmaster/mcp 包** - `2d3e7f71` (feat)
2. **Task 2: 实现精简版 MCPToolManager** - `6f58be5a` (feat)

## Files Created/Modified
- `matmaster/mcp/__init__.py` - 包公开 API，导出 MCPConnection/create_connection/MCPToolManager
- `matmaster/mcp/connection.py` - MCPConnection ABC + StdioConnection/SSEConnection/HTTPConnection + create_connection 工厂 (227 行)
- `matmaster/mcp/manager.py` - 精简版 MCPToolManager，add_server 带重试 + _build_tools 五层过滤 + cleanup (315 行)

## Decisions Made
- MCPToolManager._build_tools 存储轻量级 dict 而非 MCPTool 实例，因为下游 LazyMCPTool 和 cache_mcp_schemas 需要的是 schema 信息而非执行对象
- add_server 采用简单顺序重试（3次，间隔2s），不使用 evomaster 的 runner task 长驻模式，因为 matmaster 场景中连接在 add_server 后即稳定使用
- MCP_CONNECT_TIMEOUT 常量放在 connection.py 而非 manager.py，因为它描述的是连接层行为

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- matmaster/mcp/ 包已创建，MCPConnection 和 MCPToolManager 可独立 import
- Plan 02 可以开始 calculation path adaptor 的 matmaster 原生化
- Plan 03 可以开始 LazyMCPTool 直连重写，使用 matmaster.mcp.connection 替代 evomaster MCP

## Self-Check: PASSED

All 4 files found. All 2 commits verified.

---
*Phase: 27-mcp-calculation*
*Completed: 2026-04-01*
