# Context Structure Refactor: Universal System Prompt + mode_contract Removal

## Problem

当前 `ContextBuilder` 的所有提示词内容（identity, mode_contract）都来自 exp-specific 的 TOML 文件。没有通用的基础人设层，如果未来增加新的 exp，基础行为定义需要在每个 TOML 中重复。同时 `mode_contract` 仅两句话，与 `developer_instructions` 职责重叠。

## Design

### 1. 新增通用 system_prompt section

**新建 `matmaster/exps/_base.toml`**，存放所有 exp 共享的基础人设文本：

```toml
system_prompt = '''
（通用基础人设/行为指南，内容待填充）
'''
```

### 2. 删除 mode_contract

`direct.toml` 中 `mode_contract` 的两句话合并进 `developer_instructions`。`mode_contract` 字段、section、参数全部删除。

### 3. Section 顺序

变更前：
```
identity → mode_contract → skills → tools → memory → task
```

变更后：
```
system_prompt → identity → skills → tools → memory → task
```

`system_prompt` 作为最稳定的前缀排在最前，最大化 LLM prompt caching 收益。

## Changes

### `matmaster/exps/_base.toml`（新建）

存放 `system_prompt` 多行字符串。所有 exp 共享。

### `matmaster/config/exp.py`

- 新增 `system_prompt: str = ""`
- 删除 `mode_contract: str = ""`

### `matmaster/config/loader.py`

`load_exp_config()` 变更：

1. 自动加载 `_base.toml`（不存在则跳过）
2. `system_prompt` 合并逻辑：exp toml 定义则覆盖 base，否则用 base 值
3. `system_prompt` 与 `developer_instructions` 一样，pop 出来不做 `${VAR}` 展开
4. 删除 `mode_contract` 处理逻辑

### `matmaster/core/context_builder.py`

- `SECTION_ORDER` 改为 `("system_prompt", "identity", "skills", "tools", "memory", "task")`
- `build()` 签名：新增 `system_prompt: str = ""`，删除 `mode_contract: str = ""`
- 新增 `_build_system_prompt()` 静态方法
- 删除 `_build_mode_contract()` 静态方法
- `_build_section()` 分发逻辑同步更新

### `matmaster/core/exp.py`

`build_runtime()` 调用 `builder.build()` 时：
- 新增 `system_prompt=self._config.system_prompt`
- 删除 `mode_contract=self._config.mode_contract`

### `matmaster/exps/direct.toml`

- 删除 `mode_contract = '''...'''` 段
- 将原 mode_contract 内容合并入 `developer_instructions` 尾部

### `matmaster/devshell/config.py`

- 删除 `AgentConfig.mode_contract: str = ""`

### `matmaster/devshell/runner.py`

- 删除 `mode_contract=config.agent.mode_contract` 传参

### 测试文件（6个）

同步更新以反映字段和参数变更：
- `tests/matmaster/config/test_exp.py`
- `tests/matmaster/config/test_loader.py`
- `tests/matmaster/core/test_context_builder.py`
- `tests/matmaster/core/test_exp.py`
- `tests/matmaster/integration/test_direct_toml_prompt.py`
- `tests/matmaster/devshell/test_runner.py`

## Not Changed

- `playground/mat_master/prompts/build_prompt.py` — 旧架构 prompt builder，有独立的 `_mode_contract()` 函数。属于 test 分支旧流程，不在本次范围内。
- skills / tools / memory / task sections — 不变
- 对外调用方（`Exp.run()`、service 层）— 完全透明

## Final Assembled Prompt

```
# System                          ← _base.toml（所有 exp 共享前缀）
{通用基础人设}

---

# Identity                        ← direct.toml developer_instructions（exp 特有）
{Tool Usage / Behavior / Output Style / Remote Environment / Mode 行为}

---

# Skills
{skill meta 列表}

---

# Available Tools
{工具名 + description 列表}

---

# Memory                          ← 运行时注入（如有）

---

# Task Context                    ← 运行时注入（如有）
```
