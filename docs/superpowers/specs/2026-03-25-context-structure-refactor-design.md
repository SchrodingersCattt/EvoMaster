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

1. 自动加载 `_base.toml`（不存在则 log warning 并跳过）
2. **仅提取** `_base.toml` 中的 `system_prompt` 字段，不合并其他字段（避免 base 中意外存在的 `name`/`mode` 等字段污染 exp 配置）
3. `system_prompt` 合并逻辑：exp toml 定义则覆盖 base，否则用 base 值
4. `system_prompt` 与 `developer_instructions` 一样，pop 出来不做 `${VAR}` 展开
5. 删除 `mode_contract` 处理逻辑
6. exp 发现逻辑（error message 中的 `exps_dir.glob("*.toml")`）过滤掉 `_` 前缀文件，避免 `_base` 出现在可用 exp 列表中

新增 `load_base_system_prompt(exps_dir=None) -> str` 公开辅助函数：
- 加载 `_base.toml` 并返回 `system_prompt` 字段
- 供 devshell 等不经过 `load_exp_config()` 的入口使用
- 不存在则返回空字符串并 log warning

### `matmaster/core/context_builder.py`

- `SECTION_ORDER` 改为 `("system_prompt", "identity", "skills", "tools", "memory", "task")`
- `build()` 签名：新增 `system_prompt: str = ""`，删除 `mode_contract: str = ""`
- 新增 `_build_system_prompt()` 静态方法
- 删除 `_build_mode_contract()` 静态方法
- `_build_section()` 分发逻辑同步更新：新增 `system_prompt` 参数传递和 `if name == "system_prompt"` 分支，删除 `mode_contract` 相关分支和参数

### `matmaster/core/exp.py`

`build_runtime()` 调用 `builder.build()` 时：
- 新增 `system_prompt=self._config.system_prompt`
- 删除 `mode_contract=self._config.mode_contract`

### `matmaster/exps/direct.toml`

- 删除 `mode_contract = '''...'''` 段
- 将原 mode_contract 内容作为 `# Execution Mode` 小节合并入 `developer_instructions` 尾部

### `matmaster/devshell/config.py`

- 删除 `AgentConfig.mode_contract: str = ""`
- 新增 `AgentConfig.system_prompt: str = ""`（可选，允许 devshell 用户自定义覆盖）

### `matmaster/devshell/runner.py`

`_build_exp_config()` 变更：
- 删除 `mode_contract=config.agent.mode_contract` 传参
- 新增 `system_prompt` 传参：优先使用 `config.agent.system_prompt`，若为空则回退加载 `_base.toml`

回退加载逻辑：调用 `load_base_system_prompt(exps_dir)` 辅助函数（见 loader.py 变更），保证 devshell 手动构建 ExpConfig 的路径也能获得通用 system_prompt。

### 测试文件（6个）

同步更新以反映字段和参数变更：
- `tests/matmaster/config/test_exp.py`
- `tests/matmaster/config/test_loader.py`
- `tests/matmaster/core/test_context_builder.py`
- `tests/matmaster/core/test_exp.py`
- `tests/matmaster/integration/test_direct_toml_prompt.py`
- `tests/matmaster/devshell/test_runner.py`

**`test_loader.py` 必须覆盖的 loader 合并语义用例：**

1. `_base.toml` 存在且 exp toml 无 `system_prompt` → 使用 base 值
2. `_base.toml` 存在且 exp toml 有 `system_prompt` → exp 覆盖 base
3. `_base.toml` 不存在 → `system_prompt` 为空字符串，log warning
4. `_base.toml` 含 `system_prompt` 以外的字段（如 `name`）→ 被忽略，不污染 exp 配置
5. `_base.toml` 中 `system_prompt` 含 `${...}` 文本 → 保留原样，不做环境变量展开
6. exp 发现（error message）不包含 `_base` 在可用列表中

## Naming Note

`AgentRuntimeSpec.system_prompt`（`matmaster/types/runtime.py`）存储的是 `ContextBuilder.build()` 的最终拼接结果（完整系统提示）。本 spec 新增的 `system_prompt` section 是该完整提示的一个子段（来自 `_base.toml` 的通用前缀）。两者同名但语义不同，不会造成运行时冲突（`ContextBuilder.build()` 返回拼接后的完整字符串赋值给 `AgentRuntimeSpec.system_prompt`），但阅读代码时需注意区分。

## Not Changed

- `playground/mat_master/prompts/build_prompt.py` 及 `playground/mat_master/core/agent.py` — 旧架构 prompt 组装路径（Web 入口仍在使用）。属于 test 分支旧流程，本次重构仅覆盖 `matmaster/` 新架构路径。**已知的行为分歧：** 旧路径保留原有 mode_contract 逻辑，新路径改为 system_prompt + developer_instructions。待旧架构整体替换时统一。
- `.planning/` 目录下约 40+ 处 `mode_contract` 历史引用 — 规划文档，不影响运行时，不在本次变更范围内。
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
