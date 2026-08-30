# Scoped run contracts

Scoped runs assemble the agent prompt in four layers:

1. the project-independent scoped system prompt;
2. runtime capabilities generated from the registered tool surface;
3. required run contracts resolved before the first LLM request;
4. the project task.

Use an agent configuration with prompt_profile set to scoped, execution_mode set to direct when routing is unnecessary, a required_contracts list, an absolute contract_config_file, and an explicit runtime_tool_allowlist.

The runner resolves every required contract through the loaded skill registry, checks that the entrypoint remains inside its package, and records the file SHA-256. Missing or unreadable contracts stop startup before an LLM call. A scoped run does not expose use_skill unless it is explicitly present in the runtime allowlist.

Each run owns one internal state file at _tmp/protocol_state.json. It records the contract and protocol hashes, phase, broad queries, locked finalists, synchronized targeted rounds, inspected sources, violations, and protocol pass state.

The hard run-contract gate executes before the legacy quality gate. It cannot be force-passed by finish_block_max. It checks search order and symmetry, required source fields, artifacts, the structured run_result.json, and each asynchronous job's registered lifecycle route. A native-lifecycle job is never sent through generic monitor_job or the generic status refresher.

Direct execution_mode bypasses the router LLM. Automatic routing remains available for other runs and uses only the actual runtime capability and skill lists.

Collectors must read primary_element and abstained from run_result.json. They must not infer a decision from Markdown headings or intermediate element mentions. Protocol failures remain failed observations and are not replaced with new samples.
