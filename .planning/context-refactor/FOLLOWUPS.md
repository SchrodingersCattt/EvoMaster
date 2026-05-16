# Context Refactor Followups

## 议题 3: Preflight compaction 在 anchor turn 下的双层 `<current_instruction>` wrap

**现状（Phase 1 末态 / Phase 2C 末态）**

`matmaster/core/agent.py::_run_items` 内
`effective_current_input_context = replace(current_input_context, user_text=task)`。
该 `task` 在 anchor turn 下已经被 service 装配为
`<user_instructions>...</user_instructions>\n\n<current_instruction>...</current_instruction>`，
再传给 compactor 的 `build_current_instruction_block(effective_current_input_context)`
会包一次 `<current_instruction>` 外层，产出 base_messages[0] 含双层
`<current_instruction>` 包裹。

**回归基线**

Phase 1 / Phase 2A / Phase 2B / Phase 2C 末态行为一致；现存 snapshot
测试按这个 legacy 行为 pin。

**修复时机**

Phase 3 compactor cutover (`matmaster/core/context_compactor.py` 迁移到
`matmaster/context/compaction.py`) 时一并清理。compactor 改为调
`ContextAssembler.assemble_compaction(...)` 后，preflight wrap 由
`COMPACTED_COMPOSITION` 决定，`current_input_context` 不再被 compactor
直接读，wrap 行为自动正确。

**Phase 2C 内的处理**

不修。保留 Phase 1 末态 preflight 行为，以维持 Phase 2C 的行为不退化
验收门。FOLLOWUPS 记录是为 Phase 3 实现者提供 context。

**Resolution (Phase 3)**

Phase 3 moved compaction to `ContextAssembler.assemble_compaction(...)` and stopped
overwriting `CurrentInputContext.user_text` with the provider-facing `task` in
`AgentKernel._run_items`. Preflight compaction now renders exactly one
`<current_instruction>` block from raw current input.
