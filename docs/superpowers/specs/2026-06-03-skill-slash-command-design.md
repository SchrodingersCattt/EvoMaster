# Skill Slash Command 直接触发设计

- Date: 2026-06-03
- Status: Draft (2026-06-03 修订: slash 触发采用 (a) 粘性语义, 与模型 `Skill` 工具激活一致;
  已激活 skill 重复触发去重; `SkillHitEvent.source="slash"`; 删除 `arguments` 字段与冗余 `SkillCommandInvocation` 类型(confirmed candidate 即 invocation);
  仅跨 Redis 的 candidate 用 Pydantic, 其余 DTO 用 frozen dataclass。compaction 与全文的
  交互拆到单独 PR。)
- Author: Kealdoom + Codex
- 基线:
  - 当前 checkout: `matmaster-evo`
  - 当前生产架构: API / Worker 分离, 任务通过 Redis job payload 传递
- 影响范围:
  - `src/services/stream_service.py`
  - `src/worker/agent_worker.py`
  - `src/services/agent_run_service.py`
  - `matmaster/core/run_context.py`
  - `matmaster/core/exp.py`
  - `matmaster/context/assembly.py`
  - `matmaster/context/compositions.py`
  - `matmaster/context/sections.py`
  - `matmaster/context/sources/`
  - `matmaster/tools/builtin/skill_tool.py`
  - `matmaster/devshell/repl.py`
  - tests under `tests/matmaster/services/`, `tests/matmaster/core/`,
    `tests/matmaster/context/`, `tests/matmaster/devshell/`

## 1. 背景

当前项目已经有 skill 系统, 也已经有 `SkillTool`:

- `SkillTool` 能按 skill name 从 `SkillRegistry` 读取完整 `SKILL.md` 文档。
- `SkillTool` 会把 `${SKILL_DIR}` 替换成当前 session 可用路径。
- `SkillTool` 会在 skill 或 dependency 声明 `mcp_server` 时触发 lazy MCP schema 注入。
- agent 执行 `Skill` 工具后, `agent_tool_dispatch` 会额外产出 `SkillHitEvent`。
- `resolve_turn_intent()` 会扫描历史 `skill_hit` 事件, 把过去激活过的 skill 恢复为
  后续轮次的 `active_skills`。

但是当前系统没有确定性的直接触发语法。`SkillTool.prompt()` 里虽然写了用户输入
`/<something>` 时模型应调用 `Skill` 工具, 但这只是 prompt 约定。用户输入 `/vasp` 后,
是否调用 `Skill` 仍取决于模型本轮的工具选择。

本设计引入服务端 slash command parser, 让 `/skill-name` 成为 agent 输入协议的一部分:

```text
/vasp
/vasp 帮我为 Si 结构生成 INCAR 和 KPOINTS
/lammps
帮我把这个结构跑 300 K NVT
```

目标不是替换现有自动 skill trigger, 而是在其之外增加一条用户显式触发路径。

## 2. 主流实现参考

调研到的主流 agent / coding agent 实现有共同模式:

- OpenAI Codex CLI 的 slash commands 先由交互层解析, `/skills` 用于选择 skill,
  选择结果会把对应 skill 上下文插入下一次请求。
  参考: <https://developers.openai.com/codex/cli/slash-commands>
- Claude Code 已把 custom commands 合并进 skills, skill 可由用户用 `/skill-name`
  直接调用, 也可通过 frontmatter 控制是否允许自动触发。
  参考: <https://code.claude.com/docs/en/skills>
- Gemini CLI custom commands 由用户目录和项目目录中的 TOML 文件注册, 支持命名空间、
  参数占位、文件注入和需要确认的 shell 注入。
  参考: <https://google-gemini.github.io/gemini-cli/docs/cli/custom-commands.html>
- Cursor commands 由 `.cursor/commands/*.md` 定义, 在 agent chat 中通过 `/`
  菜单调用。
  参考: <https://docs.cursor.com/en/agent/chat/commands>
- Continue prompts 可设置 `invokable: true`, 让 prompt 出现在 Chat、Plan、Agent
  mode 的 `/` 菜单中。
  参考: <https://docs.continue.dev/customize/deep-dives/prompts>

这些实现的关键不是具体文件格式, 而是边界:

1. slash command 由系统解析, 不是交给模型猜。
2. command registry 是权威来源, UI 自动补全只是体验层。
3. project scope 和 user scope 要有明确优先级与权限边界。
4. 参数 tail text 保留原文, 供 command prompt 或 skill instruction 消费。
5. 直接触发 prompt / skill 与执行 shell / tool 是两类能力, 后者必须走额外权限确认。

MatMaster 第一版只做 skill context 注入和 lazy MCP schema 激活, 不引入任何 shell
执行语义。

## 3. 当前事实

### 3.1 API 入口只生成 `TurnInput`

`ChatStreamService.prepare_send_message()` 当前把 `req.content` strip 后写入:

- `user_msg["content"]`
- `TurnInput.from_values(user_text=user_content, ...)`

随后 `generate_send_stream()` 把 `turn_input.to_payload()` 放进 Redis job。job 中没有
直接 skill invocation 字段。

### 3.2 Worker 不读取 skill invocation

`src/worker/agent_worker.py` 当前从 Redis job 中读取:

- `session_id`
- `task_id`
- `invocation_id`
- `user_prompt`
- `mode`
- `llm` / `model` / `byok_credential_id`
- `images`
- `turn_input`
- `bohrium_required`
- `remote_workdir`

Worker 调用 `AgentRunService.run_agent(...)` 时也没有 skill invocation 参数。

### 3.3 `AgentRunRequest.active_skills` 当前由 Exp 覆盖

`AgentRunService` 创建 `AgentRunRequest` 时传入:

```python
active_skills=frozenset()
```

`Exp.run_stream()` 在 root run 中调用 `resolve_turn_intent(...)`, 然后用
`resolution.active_skills` 覆盖 `ctx.request.active_skills`。

这意味着即使 service 未来直接把 `/vasp` 解析成 `active_skills={"vasp"}`,
也会被 `resolve_turn_intent()` 的历史扫描结果覆盖。显式 slash invocation 必须作为
单独请求字段传入, 并在 Exp 内与历史 active skills 合并。

### 3.4 `SkillTool` 已经有直接可复用能力

`matmaster/tools/builtin/skill_tool.py` 已经实现:

- `skill_registry.get_skill(skill_name)`
- `skill.get_full_info()`
- `${SKILL_DIR}` 渲染
- skill 自身 `mcp_server` lazy activation
- dependency skill 的 `mcp_server` lazy activation

本设计不复制一套 skill 文档读取逻辑。实现阶段应抽取或复用 `SkillTool` 的公共渲染能力,
保证模型主动调用 `Skill` 和用户显式输入 `/skill-name` 的行为一致。

### 3.5 devshell 当前会吞掉未知 slash command

`matmaster/devshell/repl.py` 当前先解析 `/help`、`/run` 等内置命令。未知 `/xxx`
会被 devshell 当作未知命令处理, 不会进入 agent。若只改 Web API, devshell 用户仍无法
输入 `/vasp`。

## 4. 问题定义

当前系统的问题是缺少确定性 slash skill invocation:

- 用户写 `/vasp` 时, 系统不能保证本轮加载 vasp skill。
- 用户写 `/vasp args` 时, 系统没有结构化保存 args。
- API / Worker 分离下, 即使 API 端解析了 slash, 也必须通过 Redis job payload 传给 Worker。
- 当前 active skill 恢复依赖历史 `skill_hit` 事件, 适合后续轮次, 不适合表达当前轮
  显式 invocation。
- devshell 目前把未知 slash command 截留在本地命令层。

因此需要新增一条 typed、跨进程、request-local 的显式 skill invocation 链路。

## 5. 目标

1. 用户在消息首行输入 `/skill-name` 时, 系统确定性触发对应 skill。
2. 当前轮模型在第一次 LLM 调用前就看到完整 skill 文档, 不需要先调用 `Skill` 工具。
3. 带 `mcp_server` 的 skill 通过 slash 触发时, lazy MCP schema 与工具列表同步可用。
4. 显式 slash invocation 会产出 `SkillHitEvent`, 让后续轮次复用现有 active skill
   恢复机制。
5. 不改变现有自然语言自动 skill trigger 机制。
6. API / Worker 分离下, 不依赖同进程状态或服务端热缓存。
7. DTO 使用明确字段, 不通过 `run_meta`、`dict[str, Any]` 或 runtime port 兜底承载。
8. devshell 与 Web API 使用同一个 parser 规则。

## 6. 非目标

- 不实现多 skill 一次性触发。
- 不实现 skill alias 或模糊匹配。
- 不实现 shell command 执行。
- 不实现用户自定义 slash prompt 文件。
- 不改 MCP server 配置格式。
- 不改变 `SkillTool` 作为模型主动 skill activation 工具的存在。
- 不把 slash invocation 写入 `run_meta`。
- 不恢复 `AgentRunService` 的 active skill 热缓存。
- 不为旧 Redis job payload 写主代码内联迁移或兼容兜底。
- 不在本设计处理 compaction 与 slash 全文的交互(精确去重升级、压缩摘要如何保留 skill)。
  本设计只保证 compaction 之前的行为正确, compaction 相关逻辑在单独 PR 解决。

## 7. 语法

第一版只解析第一段非空文本的第一行。

### 7.1 有效输入

```text
/vasp
```

表示当前轮显式触发 `vasp`, 用户正文为空。

```text
/vasp 帮我为 Si 结构生成 INCAR 和 KPOINTS
```

表示当前轮显式触发 `vasp`, `cleaned_user_text` 为:

```text
帮我为 Si 结构生成 INCAR 和 KPOINTS
```

```text
/vasp
帮我为 Si 结构生成 INCAR 和 KPOINTS
```

表示当前轮显式触发 `vasp`, 首行命令后无 tail, `cleaned_user_text` 为第二行及后续内容。

### 7.2 非命令输入

```text
//vasp
```

按普通文本处理, 用于转义用户确实想输入的 `/vasp`。

```text
/share/work/POSCAR
```

按普通文本处理。首个 token 内出现额外 `/`, 更像路径而不是 command name。

```text
请用 /vasp 帮我做计算
```

按普通自然语言处理。现有模型自动 skill trigger 仍可决定是否调用 `Skill` 工具。

```text
/
```

按普通文本处理。单独一个 `/` 不构成 command name。

```text
/中文
```

按普通文本处理。command name 不符合第一版字符集时, parser 不产生 candidate、不截断、
不改写。

### 7.3 name 字符集

command name 采用保守字符集:

```regex
[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}
```

不允许 `/`、空格、中文、shell metacharacter。当前仓库内 skill name 如 `vasp`、
`quantum_espresso`、`operate-molecular-crystal` 均可覆盖。

边界规则:

- parser 先对整段输入 strip, 首行行首字符必须是 `/`; 前导空白在 strip 阶段消除。
- 正则锚定首行行首; name 长度上限 128 字符, 超长 token 整体判为非 candidate, 不截断。
- name 之后必须紧跟空白或行尾, 否则不构成 candidate。`/vasp!`、`/vasp,`、`/vasp(`
  这类 name 后接非空白非法字符的输入按普通文本处理。
- name 大小写敏感, 按原样与 registry 精确匹配。`/VASP` 不匹配 `vasp`, 走未命中路径。
- cleaned text 定义: 从 raw 去掉首行 `/name` token 及其后至多一个空格, 其余内容(含换行
  与后续行)原样保留, 再 strip 首尾空白。

### 7.4 多 command

第一版只支持首行一个 command。若正文后续行再次出现 `/foo`, 视为用户正文的一部分。

## 8. 核心设计决策

| # | 决策 | 说明 |
|---|---|---|
| D1 | slash parser 位于模型之前 | `/skill-name` 是输入协议, 不依赖模型调用 `Skill` 工具。 |
| D2 | 原始用户消息按原样入库 | UI 与审计仍能看到用户实际输入的 `/vasp ...`。 |
| D3 | API 阶段不因 slash candidate 改写 `TurnInput.user_text` | registry 未命中时必须保留 `/not-exist` 这类原文给模型。 |
| D4 | Exp 确认 candidate 命中后直接作为本轮触发 | 当前轮显式触发不通过历史事件推导, 也不让未命中候选污染当前轮输入。 |
| D5 | Exp 合并历史 active skills 与当前显式 skills | 避免当前 `resolve_turn_intent()` 覆盖 service 传入值。 |
| D6 | 当前轮直接注入完整 skill 文档 | 第一轮 LLM call 前 skill 指令已经可见。 |
| D7 | 通过 synthetic `SkillHitEvent(source="slash")` 记录显式触发 | 后续轮次沿用现有历史扫描机制; source 标 `slash` 以区分用户显式触发与模型激活。 |
| D8 | 复用 `SkillTool` 的 full-info 渲染与 MCP activation | 模型主动调用和用户直接 slash 行为保持一致。 |
| D9 | registry 未命中的 slash candidate 不触发 skill | 没有 slash 层中断分支; 未命中候选按普通用户文本继续进入 LLM。 |
| D10 | devshell 内置命令优先于 skill | `/help` 等本地命令不被 skill registry 抢占。 |
| D11 | 已激活 skill 的重复 slash 触发不重注入全文 | 全文已在历史里; 去重时只清理本轮输入文本, 不再注入、不发 `SkillHitEvent`。 |
| D12 | 注入的 skill 全文随 user_turn_context 每轮重放(粘性) | 与模型 `Skill` 工具激活(tool_result 不截断、每轮重放)一致, 不是一次性。 |

## 9. 新增类型

### 9.1 `SkillCommandCandidate`

建议放在 `matmaster/skills/invocation.py` 或 `matmaster/types/skill_invocation.py`。
它需要跨 Redis job payload 序列化, 是唯一跨进程边界的新 DTO, 因此用 Pydantic model。

```python
from typing import Literal

from pydantic import BaseModel, ConfigDict


class SkillCommandCandidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    raw_command: str
    cleaned_user_text: str = ""
    source: Literal["slash"] = "slash"
```

字段语义:

- `name`: 语法层解析出的 candidate name, 不带前导 `/`。
- `raw_command`: 第一行原始 command 文本, 例如 `/vasp 帮我...`。
- `cleaned_user_text`: 去掉首行 `/name` token 后的用户正文(见 7.3 cleaned text 定义),
  单行场景是命令后的 tail, 多行场景是后续行; 命中后用它替换 `TurnInput.user_text`。
- `source`: 第一版固定为 `slash`, 为后续 UI pick skill 等显式来源预留类型边界。

不再单独保留 `arguments` / `body_after_command`: 两者都只是为了得到 `cleaned_user_text`,
合并成一个字段可避免同一段正文在 `[Arguments]` 段和 current instruction 段重复出现。

candidate 只是语法候选。只有 Exp 基于当前 `SkillRegistry` 确认 `name` 存在时, 才把它
当作本轮已确认的触发直接使用——不再单独建 invocation 类型, confirmed candidate 即 invocation。

### 9.2 `SlashSkillParseResult`

parser 返回值只用于 API / devshell 入口, 不跨进程边界, 因此用 frozen dataclass
(内部仍持有一个 Pydantic `candidate`)。parser 是保守全函数: 无法识别成合法
`/skill-name` 形状的输入一律返回 `candidate=None`, 保留 ordinary text, 不抛异常。

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class SlashSkillParseResult:
    candidate: SkillCommandCandidate | None
    ordinary_user_text: str
    raw_user_text: str
```

字段语义:

- `candidate is None`: 没有解析到 slash skill candidate。
- `ordinary_user_text`: 无 candidate 时使用的普通用户文本。`//vasp` 会变成 `/vasp`, 其他未识别
  输入保持原样。
- `raw_user_text`: 原始 strip 后用户输入。

### 9.3 `InvokedSkillContext`

该 DTO 用于 context rendering, 进程内使用, 用 frozen dataclass。它不负责读取 registry,
只承载 Exp 已经解析好的内容。

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class InvokedSkillContext:
    name: str
    base_dir: str
    full_body: str
```

不再带 `description`(渲染里没用到)和 `arguments`(已并入 `cleaned_user_text`,
通过 current instruction 呈现, 不在 skill section 内重复)。

## 10. 数据流

### 10.1 Web API path

```text
ChatStreamService.prepare_send_message
  -> parse_slash_skill_invocation(req.content)
  -> user_msg.content = raw_user_text
  -> TurnInput.from_values(user_text=raw_user_text_or_ordinary_text, ...)
  -> SendStreamContext.skill_command_candidate
  -> generate_send_stream
  -> Redis job skill_command_candidate payload

agent_worker
  -> SkillCommandCandidate.model_validate(...)
  -> AgentRunService.run_agent(skill_command_candidate=...)

AgentRunService.run_agent
  -> AgentRunRequest(skill_command_candidate=...)

Exp.run_stream
  -> resolve_turn_intent(...)
  -> build runtime
  -> resolve candidate against SkillRegistry
  -> if hit: confirm candidate, replace TurnInput with cleaned_user_text
  -> if miss: keep original TurnInput, no invocation
  -> for confirmed invocations: merge active skills, replace TurnInput with cleaned text
  -> first activation (not already active): render invoked skill context + yield SkillHitEvent(source="slash")
  -> repeat activation (already active): skip render + skip SkillHitEvent (dedup)
  -> assemble root turn with invoked skill sections
  -> AgentKernel.run_stream(...)
```

### 10.2 devshell path

```text
devshell input
  -> parse_builtin_command(line)
  -> if builtin: handle locally
  -> else parse_slash_skill_invocation(line)
  -> if candidate: send candidate to agent runner
  -> else ordinary prompt
```

内置 devshell command 优先, 避免 `/help`、`/quit` 等本地控制命令被 skill name 抢占。
未知 `/xxx` 若符合 command name 规则但不命中 skill registry, devshell 按普通 prompt
继续进入 agent。

## 11. `TurnInput` 与原始消息

设计要求:

- `user_msg["content"]` 保留用户原始输入。
- `user_prompt` 保留用户原始输入, 用于通知、日志摘要和排障。
- API / Worker payload 中的 `TurnInput.instruction.user_text` 默认保留普通用户文本。
- `SkillCommandCandidate.cleaned_user_text` 保存去掉首行命令后的正文(单行的 tail 或
  多行的后续行), 不再拆成 arguments 与 body 两段。
- 只有 Exp 确认 candidate 命中 registry 后, 才把 root run 内部的
  `TurnInput.instruction.user_text` 替换为 cleaned text(confirmed candidate 即 invocation)。

示例:

```text
/vasp 帮我生成 INCAR
```

入库用户消息:

```text
/vasp 帮我生成 INCAR
```

`TurnInput.user_text`:

```text
/vasp 帮我生成 INCAR
```

`SkillCommandCandidate`:

```json
{
  "name": "vasp",
  "raw_command": "/vasp 帮我生成 INCAR",
  "cleaned_user_text": "帮我生成 INCAR",
  "source": "slash"
}
```

若 Exp 确认 `vasp` 存在, 直接用该 confirmed candidate(字段同上)把当前轮
`TurnInput.user_text` 替换为:

```text
帮我生成 INCAR
```

若 Exp 没有找到 `vasp`, 不确认命中, `TurnInput.user_text` 保持:

```text
/vasp 帮我生成 INCAR
```

并作为普通用户输入进入 LLM。

多行示例:

```text
/vasp
帮我生成 INCAR
```

API / Worker payload 中的 `TurnInput.user_text`:

```text
/vasp
帮我生成 INCAR
```

`SkillCommandCandidate`:

```json
{
  "name": "vasp",
  "raw_command": "/vasp",
  "cleaned_user_text": "帮我生成 INCAR",
  "source": "slash"
}
```

若 Exp 确认 `vasp` 存在, root run 内部 `TurnInput.user_text` 替换为:

```text
帮我生成 INCAR
```

单行与多行都只把正文落在 `cleaned_user_text` 一处, 既进 current instruction,
又不在 skill section 内重复。

## 12. Exp 内部行为

### 12.1 历史 active skills 与 candidate 确认顺序

当前逻辑:

```python
ctx = ctx.model_copy(
    update={
        "request": ctx.request.model_copy(
            update={"active_skills": resolution.active_skills}
        )
    }
)
```

改为两段式:

```python
# build_runtime 前只写入历史 active skills, 用于 replay 过去已激活 skill 的 MCP tools。
ctx = ctx.model_copy(
    update={
        "request": ctx.request.model_copy(
            update={"active_skills": resolution.active_skills}
        )
    }
)

# build_runtime 之后, Exp 已有当前 SkillRegistry, 再确认 slash candidate。
confirmed_candidate = resolve_skill_command_candidate(
    ctx.request.skill_command_candidate
)
if confirmed_candidate is not None:
    confirmed_names = frozenset({confirmed_candidate.name})
    ctx = ctx.model_copy(
        update={
            "request": ctx.request.model_copy(
                update={
                    "active_skills": ctx.request.active_skills | confirmed_names,
                    "turn_input": replace_turn_input_text(
                        ctx.request.turn_input,
                        confirmed_candidate.cleaned_user_text,
                    ),
                }
            )
        }
    )
```

注意:

- 历史 `active_skills` 必须在 `build_runtime()` 之前写入, 因为 `_init_skill_tools()`
  会读取它来 replay lazy MCP tools。
- 当前轮 slash candidate 需要当前 `SkillRegistry` 才能确认, 因此确认发生在
  `build_runtime()` 之后。
- 首次确认命中的 skill 不依赖 replay 路径激活 MCP, 而是在确认时直接复用 `SkillTool`
  的 MCP activation helper; 重复激活(已在历史 active skills)的 skill, 其 MCP 已由历史
  active skills 经 `_init_skill_tools()` replay, 不再重复激活。

### 12.2 skill 命中确认

slash candidate 必须在 Exp 中基于当前 `SkillRegistry` 确认:

- skill 不存在: 不确认命中, 不改写 `TurnInput`, 不产出 `SkillHitEvent`, 按普通用户文本继续执行。
- skill 被 disabled: 因 registry 已 remove, 表现等同 skill 不存在, 按普通用户文本继续执行。
- skill 存在且不在历史 active skills 中(首次激活): 确认命中, 替换当前轮
  `TurnInput` 文本, 渲染完整文档, 通过 helper 激活 MCP server, 产出 `SkillHitEvent`。
- skill 存在且已在历史 active skills 中(重复激活): 仍替换当前轮 `TurnInput` 文本
  (去掉 `/vasp`), 但**不重渲全文、不产出 `SkillHitEvent`**。全文已在历史里随
  user_turn_context 重放, MCP 也已由历史 active skills 经 `_init_skill_tools()` replay。

去重判定比较的是**合并前**的历史 active skills(`resolution.active_skills`), 不是合并后的
集合, 否则当前轮显式 skill 永远命中、永远被误判为重复。

> compaction 边界(本 PR 不处理, 见非目标): 本 PR 用 `skill ∈ 历史 active skills` 做去重,
> 等价于 compaction 之前的精确判定。单独的 compaction PR 会把判定升级为
> `skill ∈ live_doc_skills`(最近一次 `history_checkpoint` 之后的 tail 里仍有 `skill_hit`),
> 使 compaction 摘掉全文后再 `/vasp` 能重新注入。

确认不放在 API 端作为唯一来源, 因为 API 进程不一定和 Worker 拥有完全相同的远端
session skill root 状态。API 可以为前端菜单提供列表, 但 Worker / Exp 必须做最终确认。

### 12.3 synthetic `SkillHitEvent`

模型调用 `Skill` 工具时, `agent_tool_dispatch` 会产出 `SkillHitEvent`。slash 触发没有
真实 tool call。只有 slash candidate 确认命中、且本轮**真正注入了全文**时, Exp 才主动 yield:

```python
SkillHitEvent(source="slash", skill_name=candidate.name)
```

`source` 填 `slash`(不是 `agent`), 以在事件日志里区分用户显式触发与模型自动激活。
`scan_skill_hits()` / `resolve_turn_intent()` 不按 source 过滤, 后续轮次 active skill
恢复不受影响。该事件的用途是持久化后续轮次 active skill。SSE 过滤层当前已隐藏内部
`skill_hit` 事件, 用户不需要看到一条额外流式消息。

不变量: **`SkillHitEvent` 与全文注入一一对应**。被 12.2 去重跳过的重复 `/vasp`
(全文已在历史)既不重渲全文, 也**不产出新的 `SkillHitEvent`**——否则 tail 里有 hit
但全文可能已被 compaction 摘走, 会让(单独 PR 的)`live_doc_skills` 判定失真。被跳过那次
的审计来自原样入库的用户消息(D2), 不依赖额外 hit。

## 13. context 注入

### 13.1 新 section

新增 source, 建议文件:

```text
matmaster/context/sources/invoked_skills.py
```

渲染内容:

```text
[Invoked skill: vasp]
Base directory for this skill: /path/to/skill

...full SKILL.md body...
```

不渲染 `[Arguments]` 段: 用户正文已通过 `cleaned_user_text` 进入 current instruction,
不在 skill section 内重复。上面的 `[Invoked skill: ...]` 只是示意, 实际包裹标签沿用
现有 `ContextSection.tag` 渲染风格, 不另造一套括号格式。

### 13.2 section order

新增顺序:

```python
class SectionOrder(IntEnum):
    ...
    SESSION_TOOLS = 400
    INVOKED_SKILLS = 900
    TURN_INSTRUCTION = 1000
```

`invoked_skills` 放在 session skills/tools 之后, 当前用户指令之前。原因:

- session skills/tools 是历史激活能力摘要。
- invoked skill 是当前轮显式选择的完整指令。
- current instruction 是用户本轮目标, 应贴近最终任务文本。

### 13.3 视图

`invoked_skills` 使用 `RUNTIME_ONLY_VIEWS`。

要点(纠正一个常见误解): `RUNTIME_ONLY` 不等于只在当前轮可见。本仓库的 root turn
RUNTIME 渲染会被整条写进 `user_turn_context` 事件(`agent_run_service.py` 持久化的是
`message.model_dump()`, 不是 hash), 后续轮次由 `history_restore` 把它原样重放成一条历史
用户消息。所以注入的完整 `SKILL.md` 会**每轮都在 provider prompt 里**, 直到被 compaction
覆盖——这正是 (a) 粘性语义, 也与模型走 `Skill` 工具激活完全一致(其 tool_result 因
`SkillTool.max_result_chars=0` 不截断, 同样每轮重放)。

`RUNTIME_ONLY` 在这里只做一件事: 把完整文档挡在 `CHECKPOINT` durable base 之外, 避免它被
固化进压缩基线; 它**不**阻止当轮 user_turn_context 的每轮重放。compaction 时 CHECKPOINT
渲染自动排除 RUNTIME-only section, 全文从压缩基线消失——这部分的精确行为(压缩后摘要如何
保留、再触发如何重注入)留给单独的 compaction PR, 本 PR 不处理。

### 13.4 composition

`ContextCompositionInputs` 新增:

```python
invoked_skills: tuple[InvokedSkillContext, ...] = ()
```

`ANCHOR_COMPOSITION` 增加 `_step_invoked_skills`, 放在 `_step_session_sections`
之后、`_step_turn_input` 之前。

`CONTINUATION_COMPOSITION` 不加 invoked skills。第一版 slash command 只属于 root user
turn, 不属于 spawn continuation。

`COMPACTED_COMPOSITION` 不加 invoked skills。compaction 与 slash 全文的完整交互在单独
PR 处理, 本 PR 不在压缩 composition 里重注入当前轮 skill 文档。

## 14. MCP lazy activation

直接 slash invocation 必须复用 `SkillTool` 的 MCP activation 语义:

1. 激活 skill 自身 `meta_info.mcp_server`。
2. 激活 `depends_on` 中每个 dependency skill 的 `mcp_server`。
3. 通过 `_init_skill_tools()` 内部的 `activate_mcp_server()` 注册 overlay tools。
4. schema cache 缺失时按现有逻辑 warning + skip, 不阻塞 run。

实现时不应把 `activate_mcp_server()` 暴露成 runtime port。它是 Exp build runtime 生命周期
内部的局部能力, 不属于 service 注入能力。

推荐做法:

- 将 `SkillTool` 中的 skill full-info 渲染和 dependency MCP hit 抽成小 helper。
- helper 接收 `SkillRegistry`、`session`、`on_skill_hit`。
- `SkillTool.execute()` 和 Exp slash invocation rendering 共用 helper。

## 15. 触发与非触发语义

| 输入 | 行为 |
|---|---|
| `/vasp` 且 skill 存在 | 当前轮加载 vasp skill, 继续执行 agent。 |
| `/not-exist` | parser 可产生 candidate, 但 registry 未命中; 不触发 skill, 原文作为普通文本进入 LLM。 |
| `/` | parser 不解析, 作为普通文本进入 LLM 流程。 |
| `/中文` | parser 不解析, 作为普通文本进入 LLM 流程。 |
| `/share/work/POSCAR` | 普通文本, 不触发 command。 |
| `//vasp` | 普通文本 `/vasp`。 |
| `请用 /vasp ...` | 普通自然语言, 可由模型自动触发 skill。 |

本节没有中断分支。slash 直接触发遵循确认才生效:

- parser 识别不到 candidate: 普通文本。
- parser 识别到 candidate, registry 未命中: 普通文本。
- parser 识别到 candidate, registry 命中: 确认命中, 注入 skill context。

第一版不做模糊匹配, 不自动推荐相近名称, 也不为未命中 candidate 生成系统消息。
前端菜单可以降低输错概率, 但后端语义保持无中断: 没命中就不触发。

## 16. 前端菜单

后端功能不依赖前端菜单。即使前端完全不改, 用户也能直接输入 `/vasp`。

后续可新增一个只读接口:

```text
GET /api/v1/skills/commands?session_id=...
```

返回:

```json
{
  "skills": [
    {
      "name": "vasp",
      "description": "..."
    }
  ],
  "reserved_commands": ["help", "skills", "stop"]
}
```

这个接口只用于 UI 自动补全。最终可用性仍以 Worker / Exp 中的 registry 确认为准。

## 17. 命名冲突

保留一组 reserved slash commands:

```python
RESERVED_SLASH_COMMANDS = frozenset({
    "help",
    "skills",
    "stop",
    "clear",
})
```

第一版 Web API 不一定实现这些内置命令, 但 parser 应知道它们不属于 skill name。

处理规则:

1. devshell 内置命令优先。
2. Web API 若收到 reserved command, 返回当前未支持或交给对应控制命令处理。
3. skill registry 中若存在同名 skill, slash 直接调用仍以 reserved command 为准。

skill 自动触发不受 reserved name 影响。若模型通过自然语言选择某个同名 skill, 仍由
`SkillTool` 和 registry 行为决定。

## 18. 测试策略

### 18.1 parser tests

新增 `tests/matmaster/skills/test_slash_invocation_parser.py`:

- `/vasp` -> candidate name `vasp`, cleaned_user_text 为空。
- `/vasp 帮我生成 INCAR` -> candidate name `vasp`, cleaned_user_text 为该中文正文。
- `/vasp\n帮我生成 INCAR` -> candidate name `vasp`, cleaned_user_text 为第二行正文。
- `/vasp!`、`/vasp,` -> no candidate(name 后非空白非法字符)。
- `/VASP` -> candidate name `VASP`(大小写敏感; 是否命中由 registry 精确匹配决定)。
- `//vasp` -> no candidate, ordinary text 为 `/vasp`。
- `/share/work/POSCAR` -> no candidate。
- `请用 /vasp 帮我生成 INCAR` -> no candidate。
- `/` -> no candidate, ordinary text 为 `/`。
- `/中文` -> no candidate, ordinary text 为 `/中文`。
- `/quantum_espresso`、`/operate-molecular-crystal` -> valid candidate names。

### 18.2 stream service tests

覆盖:

- `prepare_send_message()` 保留 `user_msg["content"]` 原始输入。
- `prepare_send_message()` 保留 raw `TurnInput.user_text`, 不在 API 阶段提前清理 slash command。
- `SendStreamContext.skill_command_candidate` 正确填充。
- `generate_send_stream()` Redis job payload 包含 `skill_command_candidate`。

### 18.3 worker tests

覆盖:

- Redis job 中的 `skill_command_candidate` 反序列化为 typed model。
- `agent_run_service.run_agent(...)` 收到 `skill_command_candidate` 参数。

### 18.4 AgentRunService tests

覆盖:

- `AgentRunRequest.skill_command_candidate` 正确传给 Exp。
- `AgentRunService` 不维护 active skill 热缓存。
- 不把 candidate 写进 `run_meta`。

### 18.5 Exp tests

覆盖:

- 历史 active skills 与显式 skill names 合并。
- 显式 skill 不被 `resolve_turn_intent()` 覆盖。
- registry 未命中的 candidate 不确认命中, 不改写 `TurnInput`, 不产出 `SkillHitEvent`。
- 首次 valid slash skill 产出 `SkillHitEvent`, 且 `source == "slash"`。
- valid slash skill context 出现在 root turn runtime message 中。
- valid slash skill 会把 root run 内部 `TurnInput.user_text` 替换为 cleaned text。
- 重复激活去重: skill 已在历史 active skills 中时, 仍清理 `TurnInput` 文本, 但不重渲
  全文、不产出新的 `SkillHitEvent`。
- 跨轮粘性: 第一轮注入全文后, 第二轮的 provider-facing history 里仍包含该 skill 全文
  (经 user_turn_context 重放)。
- 带 `mcp_server` 的 slash skill 激活 lazy MCP tools。
- dependency skill 的 MCP server 也被激活。

### 18.6 context tests

覆盖:

- `invoked_skills` section order 在 `SESSION_TOOLS` 与 `TURN_INSTRUCTION` 之间。
- section 使用 `RUNTIME_ONLY_VIEWS`。
- invoked_skills section 不渲染 `[Arguments]` 段; 单行正文只出现在 current instruction 一处, 不重复。
- 正文后续行再次出现 `/foo` 时不产生第二个 candidate, 只作为用户正文保留。

### 18.7 devshell tests

覆盖:

- `/help` 仍走内置命令。
- `/vasp` 命中 skill 时进入 agent runner。
- `/not-exist` 未命中 skill 时作为普通 prompt 进入 agent runner。
- 普通文本继续进入 agent runner。

## 19. 实施阶段

### Phase 1: typed parser 与 payload 贯通

- 新增 parser 与 DTO。
- 修改 `SendStreamContext`。
- 修改 Redis job payload。
- 修改 Worker 反序列化。
- 修改 `AgentRunService.run_agent()` 签名与 `AgentRunRequest`。

本阶段不要求模型已经看到 skill 文档, 但测试应证明 candidate 已传到 Exp。

### Phase 2: Exp 合并 active skills 与注入 context

- 修改 `Exp.run_stream()` active skill 合并逻辑。
- 新增 invoked skill rendering helper。
- 新增 context source 与 composition step。
- 注入完整 skill 文档。
- 产生 synthetic `SkillHitEvent`。

本阶段完成 Web API 主功能。

### Phase 3: MCP 复用与 devshell

- 抽取或复用 `SkillTool` helper。
- 验证 slash skill 的 MCP lazy activation 与 `SkillTool.execute()` 一致。
- 修改 devshell slash handling。

### Phase 4: UI 自动补全候选

- 新增只读 skills command list endpoint。
- 前端在用户输入 `/` 时展示 skill name / description。
- 该阶段不是后端语法的前提。

## 20. 风险与缓解

### 20.1 skill 文档过长

风险: 用户显式 slash 后注入完整 `SKILL.md`, 且按 (a) 粘性语义, 该全文会随
user_turn_context **每轮重放**直到 compaction, token 占用持续存在(不是只当轮一次)。

定性: 这与模型主动 `Skill` 工具激活的成本**完全相同**(tool_result 同样不截断、每轮重放),
不是 slash 新引入的成本。当前仓库 skill 文档量级约 3k–8k tokens。

缓解:

- 只有用户显式输入 `/skill-name`(命中 registry)才注入完整文档。
- 已激活 skill 的重复 `/skill-name` 不重注入(12.2 去重), 避免堆叠多份。
- prompt caching 摊薄重放的计费成本; window 占用上限由 compaction 收敛。
- checkpoint durable base 不固化完整文档(RUNTIME_ONLY)。
- compaction 之后的摘要/重注入策略在单独 PR 处理。

### 20.2 非 command slash 文本误伤路径

风险: `/share/work/POSCAR`、`/` 或 `/中文` 被误判为 command。

缓解:

- command name token 禁止包含第二个 `/`。
- 只解析第一行首 token。
- 支持 `//` 转义。

### 20.3 当前轮 skill 与历史 active skills 时序错位

风险: 显式 skill 如果只写 `SkillHitEvent`, 当前轮 assembly 看不到它。

缓解:

- 当前轮只在 candidate 命中后使用 request-local invocation。
- `SkillHitEvent` 只负责后续轮次恢复。

### 20.4 API / Worker registry 不一致

风险: API 端菜单显示某 skill, Worker 执行时 registry 不存在。

缓解:

- API 端列表只做提示。
- Worker / Exp 做最终确认。
- registry 未命中的 candidate 按普通文本进入 LLM。

### 20.5 与模型自动 skill trigger 重复

风险: slash 已注入 skill 后, 模型又调用 `Skill` 工具。

缓解:

- 当前 skill section 明确写已 invoked skill。
- `SkillTool.prompt()` 已要求不要 invoke 已运行 skill。实现阶段可补一句:
  已在 `invoked_skills` 中出现的 skill 不要再次调用 `Skill`。
- 即使重复调用, `SkillHitEvent` 和 MCP registration 均应保持幂等。

## 21. 自检

- 没有把服务能力、callback、factory 或外部 service 对象放进 `run_meta`。
- 没有给 `RuntimePorts` 增加兜底字段。
- 没有引入进程内 active skill 热缓存。
- 没有依赖 API 进程与 Worker 进程是同一进程。
- 没有让未命中的 slash candidate 中断当前轮。
- 没有设计 shell execution。
- 没有给旧 Redis payload 加主代码内联迁移逻辑。
- 当前轮 request-local invocation 与后续轮次 `SkillHitEvent` 职责分离。
- `SkillHitEvent` 与全文注入一一对应; 去重跳过时不产出 hit。
- slash 全文采用 (a) 粘性语义, 与模型 `Skill` 工具激活一致, 不是一次性注入。
- compaction 与全文的交互(精确去重、压缩摘要)不在本 PR, 留给单独 PR。
- 仅跨 Redis 的 `SkillCommandCandidate` 用 Pydantic; 其余新 DTO 用 frozen dataclass。
