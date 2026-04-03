# Core Test Construction Consolidation Design

## Goal

在不扩大到全仓测试重构的前提下，收口 `tests/matmaster/core/` 中与 `ToolInstance`、`ToolCatalog` patch、`tool_executor(args, exec_ctx)` 契约相关的重复测试构造，降低 runtime 契约演进后测试辅助代码继续漂移的风险。

## Scope

本次只覆盖 `tests/matmaster/core/` 范围内直接服务于 Tool Runtime v2 的测试构造，不处理：

- `tests/matmaster/types/` 中只验证类型冻结或组合关系的最小构造
- `tests/matmaster/core/` 之外的 devshell、integration、tools 测试
- 生产代码中的 runtime 行为或公共 API 设计

## Current Problem

- `test_tool_runner.py` 和 `test_full_tool_runner.py` 中重复手写 `ToolInstance(...)`
- 多处测试手工 patch `catalog.get_tool`，但 patch 方式和 helper 形状不统一
- executor 签名已经收口到 `tool_executor(args, exec_ctx)`，但测试替身仍可能继续退回旧的单参数形式
- 当 runtime 新字段或执行上下文继续演进时，测试更容易出现“helper 过期导致假失败”，而不是暴露真实行为回归

## Chosen Approach

采用 `core` 目录内共享 helper 的轻量收口方案：

1. 新增一个只供 `tests/matmaster/core/` 使用的 helper 模块
2. 统一提供最常见的测试构造能力：
   - 构造 `ToolInstance`
   - 包装符合当前双参数契约的 executor
   - 用最小改动覆盖 `catalog.get_tool` 返回值替换
   - 构造带默认 topology / scheduler / policy 的 `FullToolRunner`
3. 现有测试文件按需迁移到共享 helper，但不强迫所有特殊场景都抽象化

## Why This Approach

- 比“每个文件各修各的”更能防止下一次签名漂移
- 比做完整 fixture / builder DSL 更轻，不会把测试代码重构成另一套框架
- 边界清晰，只影响 `core` 测试，不会把本次任务扩成跨目录清理

## Components

### Shared Helper Module

建议新增一个 `tests/matmaster/core/tool_runtime_test_helpers.py` 之类的模块，集中放：

- `make_tool_instance(...)`
- `make_executor(...)`
- `patch_catalog_tool(...)`
- `make_full_runner(...)`

其中 executor helper 必须默认生成 `async def executor(args, exec_ctx)` 形状，避免再出现旧签名。

### Targeted Call-Site Migration

优先迁移重复度最高、最容易漂移的调用点：

- `tests/matmaster/core/test_tool_runner.py`
- `tests/matmaster/core/test_full_tool_runner.py`

对 `test_structural_validation.py`、`test_tool_spec.py` 这类只需要单个 `ToolInstance` 的文件，仅在 helper 复用明显更简洁时才迁移，避免为了“统一”牺牲可读性。

## Data Flow

1. 测试通过共享 helper 构造 `ToolInstance`
2. helper 统一把 executor 包装成当前 runtime 契约
3. 需要替换 catalog 行为时，通过 helper 返回 patched catalog 或 patch 函数
4. runner 相关测试继续关注行为断言，而不是手写底层拼装细节

## Error Handling

- helper 只封装稳定重复模式，不吞掉测试中的异常
- 若某个测试需要非常规 executor / validator / policy，允许直接覆盖 helper 返回值，避免 helper 过度隐藏行为
- 若 helper 需要 patch `catalog.get_tool`，应保留 fallback 到原始 `get_tool` 的能力，减少误伤非目标工具

## Testing Strategy

收口完成后至少验证：

- `tests/matmaster/core/test_tool_runner.py`
- `tests/matmaster/core/test_full_tool_runner.py`
- 相关 helper 被使用后的 targeted 子集

如果 helper 触及 runner 构造公共路径，再补跑：

- `tests/matmaster/core/test_agent_kernel_stream.py`
- `tests/matmaster/core/test_exp_runtime_v2.py`

## Success Criteria

- `core` 目录内重复的 `ToolInstance(...)` / executor 契约拼装明显减少
- `tool_executor(args, exec_ctx)` 成为共享 helper 的唯一默认形状
- `test_tool_runner.py` 与 `test_full_tool_runner.py` 不再各自维护一套易漂移的底层拼装逻辑
- targeted 测试通过，且没有引入新的测试 helper 隐式行为
