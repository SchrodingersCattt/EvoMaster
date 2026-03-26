# System Prompt Guardrails Design

## Problem

MatMaster agent 缺乏提前终止能力。面对超出能力范围的任务（无权限、资源不足、反复失败等），agent 会持续执行到 max_turn 才停止，浪费 token 和时间。

## Approach

纯提示词层面。在 `matmaster/exps/_base.toml` 的 system_prompt 中新增两组指令，引导 LLM 在识别到无望情况时走 natural finish 路径（LLM 生成纯文本回复而不调用任何工具，kernel 自动识别为 natural finish 并结束循环）主动停止。

不新增代码（Guard/Hook），因为核心问题是 LLM 不知道何时该停，而不是想停但停不了。

## Relationship to Existing Prompt Content

当前 system_prompt 中已有若干相关指令，新增内容与其关系如下：

| 已有内容 | 位置 | 新增内容的关系 |
|---------|------|--------------|
| `### Restricted Software` — 受限软件替代建议 | 第 65-68 行 | Pre-flight 的软件可用性维度**引用**此段落作为具体规则，自身仅补充「替代方案也无法满足时应停止」的退出判断 |
| `# Domain & Boundaries` — 领域边界 | 第 70-83 行 | Pre-flight 的领域边界维度**不重复**，在 Pre-flight 中用一句话交叉引用 |
| `# General Task Execution` — blocked 时换方案 | 第 88 行 | Runtime Guardrails 是这条原则的**具体化**：定义什么算 genuinely stuck、stuck 后具体怎么做 |
| `# Output & Communication` — 无法完成时的报告 | 第 113 行 | Behavior Pattern 与其一致，不矛盾，Runtime Guardrails 补充的是触发条件 |
| `# Safety & Constraints` — 长时间计算监控 | 第 120 行 | Runtime Guardrails 新增计算超时/不收敛场景，**强化**此处的弱指令 |

处理策略：保持旧内容不变，新段落作为更具体的操作规程。旧内容是原则，新内容是操作细则。

## Design

### Part 1: Pre-flight Check（事前可行性评估）

**位置**：`# Scientific Methodology` 中，`## Propose Before Executing` 之前，作为 `## Pre-flight Check`。

**触发时机**：收到非平凡计算任务时。文件检查、格式转换等简单操作不触发。

**评估维度**：

| 维度 | 不通过的典型信号 | 行为 |
|------|-----------------|------|
| 软件可用性 | 任务本质上需要受限软件，且 Restricted Software 中列出的替代方案无法满足精度要求 | 说明替代方案的局限，建议用户在有许可证的环境中执行，或调整精度预期后使用替代方案 |
| 资源可行性 | 体系规模远超当前环境能力（万原子 DFT、需数百 GPU 的 AIMD） | 给出规模估算，建议降低规模、使用 ML 势函数、或分阶段验证 |
| 领域边界 | 任务与材料科学研究无关（遵循 Domain & Boundaries 中的判断标准） | 简要说明不在能力范围内，停止 |

**关键原则**：评估不是二元的。部分可行时，明确告知哪些能做、哪些不能做，而不是整体拒绝。

### Part 2: Runtime Guardrails（运行时退出规则）

**位置**：`# Safety & Constraints` 中，作为 `## Runtime Guardrails` 子段。与已有的 Workspace Discipline、Destructive Actions、Computational Resource Awareness 风格统一。

**触发场景**：

| 场景 | 判断标准 | 行为 |
|------|---------|------|
| 连续计算失败 | 同一计算在调整参数后仍反复失败，且失败原因指向相同根因 | 汇总每次尝试的参数和失败原因，分析失败模式，建议排查方向，停止 |
| 工具反复报错 | 同一工具反复调用失败且错误原因相同（包括被系统拦截的调用） | 报告错误信息，判断是环境还是用法问题，建议替代方案，停止 |
| 计算超时/不收敛 | 提交的计算长时间无进展，或日志显示 SCF/几何优化振荡不收敛 | 报告收敛趋势或超时状态，分析可能原因（初始结构、参数设置），建议调整后重试或降级方法，停止 |
| 信息缺失 | 缺少关键输入且已询问用户但未获得 | 列出缺少什么、为什么无法继续、用户需要提供什么，停止 |
| 权限/环境不足 | 执行中发现所需能力不可用 | 报告具体限制，建议绕过方案或等环境就绪后重试 |

**关键原则**：

- 退出前必须提供诊断信息：尝试了什么、为什么失败、建议下一步
- 用根因判断而非次数计数：如果失败原因明确指向无法解决的问题，一次就足以触发退出；如果每次失败原因不同且有调整空间，可以多试几次
- 退出是暂停而非放弃：措辞传达「当前条件下无法继续」而非「不可能」
- 系统拦截信号：如果工具调用被系统拦截（如 loop detection），应将其视为需要更换策略的强信号

## Behavior Pattern

Agent 触发护栏后的统一行为模式（终止 + 替代建议）：

1. 说明为什么当前条件下无法继续（具体原因，不是泛泛而谈）
2. 提供可行的替代方案或降级路径
3. 停止执行（不再调用工具，走 natural finish）

**正例**：

> 用户要求对含 5000 个原子的 MOF 体系进行 DFT 全松弛。
>
> Agent 回复：这个体系包含 5000 个原子，使用 DFT 进行全松弛所需的计算资源（预估需要数千核时和数百 GB 内存）远超当前环境能力。建议：(1) 先用 DPA 机器学习势函数进行预松弛，筛选出低能构型；(2) 对筛选后的候选构型截取关键局部结构（如活性位点周围 100-200 原子），再用 DFT 精修。是否按这个方案推进？

**反例**（应避免）：

> 同样的 5000 原子 MOF 体系。
>
> Agent 开始设置 ABACUS 输入文件 → 提交计算 → 计算 OOM 失败 → 调整参数重试 → 再次失败 → 换并行策略 → 又失败 → ... 直到 max_turns。

## Scope

- 修改文件：`matmaster/exps/_base.toml`（system_prompt 字段）
- 不涉及代码变更
- 不影响现有终止机制（max_turns、Guard、Hook 等）
- 主要针对顶层 direct agent 的行为。子 agent（spawn/explore）继承 `_base.toml` 中的 guardrail 指令，其触发护栏时会通过 tool result 将失败信息返回给父 agent，由父 agent 决定后续行动。这是现有 spawn 机制的自然行为，不需要特殊处理。
