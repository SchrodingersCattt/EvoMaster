# System Prompt 运行时 Environment 段设计

- Date: 2026-06-03
- Status: Draft
- Author: Kealdoom + Claude
- 基线:
  - 当前 checkout: `matmaster-evo`, 分支 `feat/bohrium_job`
  - system prompt 由 `SystemPromptBuilder` 按固定 section 顺序组装
- 影响范围:
  - `matmaster/context/system_prompt.py`(新增 section)
  - `matmaster/context/environment.py`(新建)
  - `matmaster/core/exp.py`(接线)
  - `tests/matmaster/context/test_environment.py`(新建)
  - 不改 `matmaster/exps/_base.toml`

## 1. 背景

当前 system prompt 缺少运行时环境信息。模型不知道:

- 当前日期(模型本身没有实时时钟)
- 工作目录的真实路径
- 命令实际执行的平台 / shell / OS

参照 Claude Code 等 coding agent, system prompt 通常有一段 Environment 描述运行上下文,
帮助模型生成正确的文件路径与 shell 命令, 并具备时间感知。本设计为 matmaster 增加这样
一段, 通过运行时动态注入。

### 1.1 用户确认的目的(决定字段范围)

经确认, 这段信息服务两个目的:

- 路径与命令正确性: 让模型知道工作目录、平台、shell, 从而生成正确的文件路径与 Bash 命令。
- 时间感知: 让模型知道当前日期, 用于科研记录、文件命名、时间相关推理。

不追求整体对齐 Claude Code, 也不单独强调本地/远程执行位置的感知。

## 2. 当前事实

### 2.1 加载与组装链路

- `_base.toml` 提供基础 `system_prompt`; `direct/planner/explore/verification` 只覆盖各自的
  `developer_instructions`, 共享这段基础 prompt。合并逻辑在 `matmaster/config/loader.py:238-242`。
- 最终由 `SystemPromptBuilder.build_system_prompt()` 组装(`matmaster/context/system_prompt.py:47-81`)。
- 组装顺序固定: `system_prompt → identity → skills → tools → memory → task`
  (`system_prompt.py:38-45`), 各 section 用 `\n\n---\n\n` 分隔。
- 文件头注释写明设计哲学: 高频变化的 section(task, memory)放最后, 以保持 prompt cache
  的稳定前缀(`system_prompt.py:3-5`)。
- 调用点在 `matmaster/core/exp.py:338-344`, 此时 `env`(ExecutionEnvironment)已在 scope 内。

### 2.2 执行环境是动态的

- `ExecutionEnvironment`(`matmaster/core/playground.py:51-86`)用 `session_type` 区分 local /
  远程, 用 `execution_workdir` 表示工具实际执行的目录。
- local session 在 agent 进程本机跑 subprocess(`matmaster/sessions/local.py`); 远程会被 Bohrium
  路径 rebind 成 SSH session、`execution_workdir` 换成远程路径(`playground.py:98-115`,
  `matmaster/sessions/ssh.py`)。
- `execution_workdir` 由 `model_validator` 保证非空, 默认等于 `str(workdir)`(`playground.py:88-96`)。

### 2.3 已知约束

- static prompt(system_prompt + developer_instructions)有 token 预算: 目标 12k、上限 15k
  (`tests/evaluation/test_exp_prompt_budget.py:23-24`)。
- system_prompt 参与 prompt caching, 处于缓存前缀位置(`matmaster/providers/openai_provider.py:575,596`)。
- 项目当前没有运行时占位符替换机制; `_base.toml` 里的 `<scratchpad-dir-path>` 是悬空占位符
  (py 代码无任何处理, 仅出现在 `_base.toml:62-63`)。本设计不处理它(见非目标)。

## 3. 目标与非目标

### 目标

- 在 system prompt 中增加一段运行时 Environment, 含: 工作目录、平台、shell、OS 版本、当前日期。
- 动态值(工作目录、日期)运行时注入; 平台/shell/OS 作为执行镜像的固定常量。
- 不影响 static prompt 的 token 预算, 不破坏会话内的 prompt cache 复用。

### 非目标

- 不处理 `<scratchpad-dir-path>` 悬空占位符(独立任务: 需先定义 scratchpad 路径的来源)。
- 不做 platform/shell/OS 的 local/remote 各自取值(用固定常量; 用户不需要 local 准确性)。
- 日期不带时分秒(会让该段每轮请求都变、破坏会话内缓存)。
- 不引入通用占位符替换机制。

## 4. 设计

采用方案 A: 新增独立 `environment` section + 固定常量。方案对比见第 7 节。

### 4.1 改 `SystemPromptBuilder`(`matmaster/context/system_prompt.py`)

三处改动, 全部复用现有机制:

1. `_TEXT_SECTION_HEADINGS` 增加 `"environment": "Environment"`。
2. `SYSTEM_SECTION_ORDER` 插入 `"environment"`, 位置在 `tools` 之后、`memory` 之前:
   `system_prompt → identity → skills → tools → environment → memory → task`。
   语义顺序自然(规则→身份→技能→工具→环境→记忆→任务), 且落在高频档前缘。
3. `build_system_prompt` 增加参数 `environment_context: str = ""`, 并入 `text_values`。
   空串时由现有 `_format_text_section` 逻辑自动跳过该 section。

### 4.2 新建 `matmaster/context/environment.py`

模块级常量(执行镜像的固定属性, 即用户确认的真实执行环境):

```python
EXECUTION_PLATFORM = "linux"
EXECUTION_SHELL = "/bin/bash"
EXECUTION_OS = "Ubuntu 24.04.2 LTS; kernel 5.10.134-18.0.10.lifsea8.x86_64"
```

纯函数(日期由调用方注入, 函数内部不调用 `now()`, 便于测试):

```python
def build_environment_section(*, execution_workdir: str, now: datetime) -> str:
    ...
```

时区后缀从 `now.tzinfo` 推导(传入什么时区, 后缀就对应), 保证函数自洽, 不在函数内硬编码时区。

### 4.3 输出格式

```text
You have been invoked in the following environment:
 - Working directory: {execution_workdir}
 - Platform: linux
 - Shell: /bin/bash
 - OS Version: Ubuntu 24.04.2 LTS; kernel 5.10.134-18.0.10.lifsea8.x86_64
 - Today is {YYYY-MM-DD} ({tz_label}).
```

在 prompt 中渲染为 `# Environment\n\n<上段>`。
日期行示例: `Today is 2026-06-03 (UTC+08:00).`

### 4.4 接线(`matmaster/core/exp.py:339`)

在已有的 `build_system_prompt` 调用处增加一个参数:

```python
from datetime import datetime
from zoneinfo import ZoneInfo

from matmaster.context.environment import build_environment_section

# ...
system_prompt = system_prompt_builder.build_system_prompt(
    registry,
    system_prompt=self._config.system_prompt,
    identity=self._config.developer_instructions,
    skill_registry=self._skill_registry,
    environment_context=build_environment_section(
        execution_workdir=env.execution_workdir,
        now=datetime.now(ZoneInfo("Asia/Shanghai")),
    ),
)
```

## 5. 缓存与 token 预算分析

- environment 段在单次会话内完全稳定: 日期到天(当天不变)、工作目录(session 内不变)、平台值
  (常量)。因此不破坏会话内的 cache 复用。跨会话/跨天才会不同, 而那种复用因 cache TTL
  (5 分钟~1 小时)本就不存在。
- 不改 `_base.toml` → static prompt 预算测试(`test_exp_prompt_budget.py`)完全不受影响。
  environment 段额外约 60-80 token, 属于动态档, 不计入 static 预算。
- 放置在 `memory`/`task` 之前的高频档前缘, 与文件既有的缓存哲学一致。

## 6. 测试

- 新建 `tests/matmaster/context/test_environment.py`:
  - 给定固定 `execution_workdir` + 固定带时区的 `now`, 断言输出包含全部字段、日期格式为
    `YYYY-MM-DD`、时区后缀为 `(UTC+08:00)`。
  - 断言纯函数完全由入参决定(传入不同 `now` 得到不同日期, 不依赖外部时钟)。
- 在 system_prompt 相关测试中增加断言:
  - `"environment"` 在 `SYSTEM_SECTION_ORDER` 中位于 `tools` 之后、`memory` 之前。
  - 传入 `environment_context` 时渲染出 `# Environment` 段; 空串时该段被跳过。

## 7. 方案对比与取舍记录

| 方案 | 描述 | 结论 |
|------|------|------|
| A(选用) | 独立 environment section + 固定常量 | 改动集中, 缓存与预算都不受牵连 |
| B | 平台值进 `ExecutionEnvironment` 结构化字段, local/remote 各自取值 | 最正确但改动大(playground/session/types); 用户不需要 local 准确性, 当前属 over-engineering; 可作为 A 的后续升级 |
| C | 写进 `_base.toml` + 占位符替换 | 动态内容混入静态块, 触发 token 预算测试, 违背文件缓存哲学 |

其它已确认取舍:

- 平台/shell/OS 用固定常量(执行镜像稳定), 不做远程探测。
- 日期到天、时区 Asia/Shanghai。
- scratchpad 悬空占位符不在本次范围。
