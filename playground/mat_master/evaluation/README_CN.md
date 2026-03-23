# MATTER Evaluation v4 — 三维评测系统

> **版本**: v4 (2026-03)  
> **模块路径**: `playground/mat_master/evaluation/`  
> **设计理念**: 准确性 × 溯源性 × 效率性 三维独立评估，解耦评测与运行时

---

## 目录

- [概述](#概述)
- [架构总览](#架构总览)
- [目录结构](#目录结构)
- [集成接触点（与仓库其他代码的关系）](#集成接触点与仓库其他代码的关系)
- [三维评分体系](#三维评分体系)
  - [维度定义](#维度定义)
  - [双总分公式](#双总分公式)
  - [权重设计](#权重设计)
- [Evidence 层（证据抽取）](#evidence-层证据抽取)
- [题库规范](#题库规范)
  - [Checklist Item 维度标注](#checklist-item-维度标注)
  - [Rubric 权重字段](#rubric-权重字段)
  - [题库统计](#题库统计)
- [Evaluator（评分器）](#evaluator评分器)
  - [确定性检查方法](#确定性检查方法)
  - [LLM Judge](#llm-judge)
  - [JudgeAdapter](#judgeadapter)
- [Runner（执行器）](#runner执行器)
- [Aggregator（聚合器）](#aggregator聚合器)
- [Reporter（报告器）](#reporter报告器)
- [运行方式](#运行方式)
- [CLI 参数说明](#cli-参数说明)
- [配置文件](#配置文件)
- [输出结果](#输出结果)
- [监控脚本](#监控脚本)
- [统计方法说明](#统计方法说明)
- [插拔与扩展指南](#插拔与扩展指南)
- [向后兼容](#向后兼容)

---

## 概述

MATTER (MATerial Testing & Evaluation Runner) 是 MatMaster 材料科学智能体的独立评测框架。v4 版本引入 **三维评分** 和 **证据解耦** 两大设计变更，使评测系统从「只看答案对不对」升级为「答案对不对 + 依据可不可靠 + 过程高不高效」的全面评估。

核心设计原则：

1. **评测与运行时完全解耦** — Evaluator 只依赖 `EvidenceBundle`，不依赖 agent 运行时、工具名称或 trajectory 结构
2. **三维独立评估** — accuracy / grounding / efficiency 各有独立分数
3. **双总分** — strict_final（乘法惩罚）+ analysis_final（加权求和），适配不同场景
4. **配置驱动** — 所有工具→事件的映射通过 `evidence_mapping.yaml` 配置，无硬编码


### 新增文件

| 文件 | 说明 |
|------|------|
| `evidence.py` | 证据层：`EventType`/`SourceType`/`CallStatus` 枚举，`EvidenceBundle`，`EvidenceExtractor` |
| `evidence_mapping.yaml` | 28 条工具→事件类型映射规则 |

### 修改文件

| 文件 | 主要变更 |
|------|----------|
| `schemas.py` | 新增 `DimensionLiteral`、`TokenUsageRecord`；`Rubric` 增 3 权重字段；`ScoringCheckItem` 增 `dimension`；`EvalRunRecord` 增 7 字段；`EvaluationSummary` 增 `by_model` |
| `evaluator.py` | 三维分数计算、`JudgeAdapter` 类、6 个新确定性 check 方法 |
| `runner.py` | 集成 `EvidenceExtractor`，运行前自动提取证据 |
| `aggregator.py` | 三维聚合 + `by_model` 分组 + token 开销统计 |
| `reporter.py` | 三维 Markdown 表 + 模型对比 + `scores_by_model.json` |
| `question_bank/*.yaml` | 全部 38 题 212 项 checklist 加 `dimension` 标注 + rubric 权重 |
| `evomaster/agent/agent.py` | `_append_trajectory_entry()` 写入 `model_name` 到 `trajectory.meta` |

---

## 架构总览

```
                     ┌──────────────┐
                     │  CLI (cli.py)│
                     └──────┬───────┘
                            │
                    ┌───────▼────────┐
                    │ Runner         │ ← config.yaml + question_bank/*.yaml
                    │ (runner.py)    │
                    └───────┬────────┘
                            │ 对每道题 × mode × k:
              ┌─────────────┼──────────────┐
              │             │              │
     ┌────────▼──────┐ ┌───▼──────┐ ┌─────▼──────────┐
     │ Simulator      │ │MatRunner │ │ EvidenceExtractor│
     │ (simulator.py) │ │(mat_     │ │ (evidence.py)    │
     │ 生成提问       │ │runner.py)│ │ trajectory→Bundle│
     └────────────────┘ │ 执行agent│ └─────┬──────────┘
                        └───┬──────┘       │
                            │              │
                    ┌───────▼──────────────▼──┐
                    │ RubricEvaluator          │
                    │ (evaluator.py)           │
                    │ ├─ 确定性 check (14种)    │
                    │ ├─ LLM judge (accuracy)  │
                    │ └─ JudgeAdapter          │
                    │    ├─ grounding judge    │
                    │    └─ efficiency judge   │
                    └──────────┬───────────────┘
                               │ EvalRunRecord (含三维分数)
                    ┌──────────▼──────────┐
                    │ Aggregator          │
                    │ (aggregator.py)     │
                    │ by_level/mode/model │
                    └──────────┬──────────┘
                               │ EvaluationSummary
                    ┌──────────▼──────────┐
                    │ Reporter            │
                    │ (reporter.py)       │
                    │ JSON + Markdown     │
                    └─────────────────────┘
```

---

## 目录结构

```
playground/mat_master/evaluation/
├── __init__.py
├── __main__.py
├── cli.py                      # CLI 入口
├── config.yaml                 # 评测配置
├── evidence.py                 # ★ 证据解耦层 (v4 新增)
├── evidence_mapping.yaml       # ★ 工具→事件映射 (v4 新增)
├── evaluator.py                # Rubric 评分器 + JudgeAdapter
├── mat_runner.py               # Mat Master agent 执行与答案抽取
├── runner.py                   # 批量运行编排
├── aggregator.py               # 统计聚合
├── reporter.py                 # 报告生成
├── schemas.py                  # 全部数据模型 (Pydantic)
├── simulator.py                # Human Simulator
├── README_CN.md                # ← 本文档
├── question_bank/
│   ├── level1.yaml             # L1: 批量计算与结构操作 (18题)
│   ├── level2.yaml             # L2: 多步工作流 (8题)
│   ├── level3.yaml             # L3: 诊断与多源整合 (6题)
│   ├── level4.yaml             # L4: 前瞻性研究与综合报告 (2题)
│   ├── safety_refusal.yaml     # Safety: 安全拒绝 (4题)
│   └── data/                   # 题目关联的输入数据文件
│       ├── L1_Q01/             # 每题一个目录
│       ├── ...
│       └── S_Q01/
└── literature_benchmark/       # 文献复现基准 (预留)
    └── scripts/
```

---

## 集成接触点（与仓库其他代码的关系）

evaluation 模块设计为**高度自治**的子系统，与仓库主代码的接触面极小。以下是全部接触点：

### 接触点 1：`evomaster/agent/agent.py` — model_name 写入 trajectory

evaluation 需要知道 agent 使用了哪个 LLM 模型。v4 在 `BaseAgent._append_trajectory_entry()` 中新增了一行，将 `model_name` 写入 trajectory JSON 的 `trajectory.meta.model_name`：

```
位置: evomaster/agent/agent.py:900-911
```

```python
'meta': {
    'agent_version': self.VERSION,
    'agent_name': self._agent_name or 'unknown',
    'step': self._step_count,
    'model_name': (
        (assistant_message.meta or {}).get('model')
        if hasattr(assistant_message, 'meta')
           and assistant_message.meta
        else None
    ),
},
```

**数据流完整链路**：

```
LLM Provider (_call / query_stream)
  ├─ OpenAILLM:     LLMResponse(meta={'model': response.model})
  ├─ DeepSeekLLM:   LLMResponse(meta={'model': response.model})
  └─ AnthropicLLM:  LLMResponse(meta={'model': response.model})
        │
        ▼
LLMResponse.to_assistant_message()
  → AssistantMessage(meta={**self.meta, 'finish_reason': ..., 'usage': ...})
  → assistant_message.meta['model'] = 'gemini-2.5-flash' (实际模型名)
        │
        ▼
agent._append_trajectory_entry()
  → trajectory JSON: entry.trajectory.meta.model_name = 'gemini-2.5-flash'
        │
        ▼
EvidenceExtractor.extract(trajectory_path)
  → EvidenceBundle.model_name = 'gemini-2.5-flash'
        │
        ▼
runner.py → EvalRunRecord.model_name → aggregator by_model 分组
```

> ⚠️ **注意**：非流式 (`_call()`) 和流式 (`query_stream()`) 两条路径都已确认能正确写入 `meta.model`。流式路径从 stream chunk 的首帧 `chunk.model` 捕获（OpenAI/DeepSeek），或从 `get_final_message().model` 获取（Anthropic）。

### 接触点 2：`mat_runner.py` — 调用 MatMaster agent 执行任务

`mat_runner.run_mat_task()` 是 evaluation 与 MatMaster agent 的唯一连接点。它：

1. 导入 `playground.mat_master.core` 中的 playground 和配置
2. 调用 agent 执行任务
3. 返回 `answer_text` + `trajectory_path`

```
位置: playground/mat_master/evaluation/mat_runner.py:31-85
```

如果要把 evaluation 移植到其他 agent（如 x_master），只需替换 `mat_runner.py` 中的 agent 调用逻辑。其余模块（evaluator、aggregator、reporter）完全不感知 agent 实现。

### 接触点 3：`evidence_mapping.yaml` — 工具名称映射

这是 evaluation 对 MatMaster 工具集的唯一认知。包含 28 条规则，将 MatMaster 的 MCP 工具名（如 `mat_struct_db_get_by_mpid`）映射为抽象事件类型（如 `STRUCTURE_QUERY`）。

```
位置: playground/mat_master/evaluation/evidence_mapping.yaml
```

添加或删除 MatMaster 工具时，只需更新此文件。

### 无其他接触点

- evaluation 不依赖 `src/` 目录下的任何服务代码（API、Worker、Redis）
- evaluation 不依赖 `evomaster/` 下除 agent.py 外的任何代码
- evaluation 不写入数据库、不调用 OSS、不发送 SSE 事件
- evaluation 的全部外部依赖是：`pydantic`、`pyyaml`、标准库

---

## 三维评分体系

### 维度定义

| 维度 | 英文 | 含义 | 评估方式 |
|------|------|------|----------|
| **准确性** (Accuracy) | `accuracy` | 答案本身是否正确 | 确定性 check + LLM 评判 |
| **溯源性** (Grounding) | `grounding` | 结论是否有可靠的工具调用/数据支撑 | 证据规则 check + LLM judge |
| **效率性** (Efficiency) | `efficiency` | 过程是否高效（无冗余重试、token 合理） | 证据规则 check + LLM judge |

### 双总分公式

v4 提供两种总分计算方式，适配不同决策场景：

#### strict_final — 乘法惩罚模式

```
strict_final = accuracy × (α × grounding + β × efficiency)
```

- α = `grounding_weight`，β = `efficiency_weight`（α + β 无需为 1）
- **特点**：accuracy 为 0 时总分直接归零，答案错误不可能靠溯源和效率挽回
- **适用**：生产部署评估、合规验收

#### analysis_final — 加权求和模式

```
analysis_final = wₐ × accuracy + w_g × grounding + w_e × efficiency
```

- 权重来自 rubric 的 `analysis_weights`（wₐ + w_g + w_e 通常 = 1.0）
- **特点**：各维度独立贡献，即使答案错误也能看到溯源/效率表现
- **适用**：开发调试、能力诊断、回归分析

### 权重设计

每个难度级别有不同的权重配比，反映题目特性：

| 级别 | grounding_weight | efficiency_weight | 设计理由 |
|------|-----------------|-------------------|----------|
| L1 (批量计算) | 0.4 | 0.6 | 批量操作更关注效率 |
| L2 (多步工作流) | 0.5 | 0.5 | 溯源与效率同等重要 |
| L3 (诊断整合) | 0.6 | 0.4 | 诊断依赖可靠证据 |
| L4 (前瞻研究) | 0.7 | 0.3 | 前瞻性任务高度依赖溯源 |
| Safety (安全拒绝) | 0.8 | 0.2 | 安全以准确性为主 |

---

## Evidence 层（证据抽取）

`evidence.py` 是 v4 的核心解耦层，将 agent 原始 trajectory 转换为标准化 `EvidenceBundle`，使 evaluator 完全不依赖运行时细节。

### 核心数据类型

```python
# 枚举
class EventType(str, Enum):      # STRUCTURE_QUERY, DFT_SUBMIT, DB_SEARCH, ...
class SourceType(str, Enum):     # TOOL_CALL, LLM_INFERENCE, USER_INPUT, ...
class CallStatus(str, Enum):     # SUCCESS, FAILURE, TIMEOUT, UNKNOWN

# 记录
class EventRecord(BaseModel):    # 抽象事件（event_type + source_type + status）
class ToolCallRecord(BaseModel): # 原始工具调用（name + args + response 摘要）
class ArtifactRecord(BaseModel): # 产出物（path + mime_type）
class TokenUsage(BaseModel):     # token 开销（prompt + completion + total + model）

# 总线
class EvidenceBundle(BaseModel): # ← evaluator 唯一输入
    events:      list[EventRecord]
    tool_calls:  list[ToolCallRecord]
    artifacts:   list[ArtifactRecord]
    token_usage: TokenUsage
    model_name:  str | None
    raw_meta:    dict[str, Any]
```

### 映射配置 `evidence_mapping.yaml`

定义了 28 条 tool_name → EventType + SourceType 的映射规则：

```yaml
# 示例
- tool_name: "mat_struct_db_get_by_mpid"
  event_type: "STRUCTURE_QUERY"
  source_type: "TOOL_CALL"

- tool_name: "mat_dft_*"        # 支持通配符
  event_type: "DFT_SUBMIT"
  source_type: "TOOL_CALL"
```

添加新工具时只需在此文件追加映射，无需修改 Python 代码。

### EvidenceExtractor

```python
extractor = EvidenceExtractor()
bundle: EvidenceBundle = extractor.extract(trajectory_path)
```

提取流程：
1. 读取 trajectory JSON
2. 遍历每条 step 中的 tool_call，匹配 mapping 生成 `EventRecord` + `ToolCallRecord`
3. 汇总 token 用量 → `TokenUsage`
4. 读取 `trajectory.meta.model_name` → `model_name`
5. 打包为 `EvidenceBundle` 返回

---

## 题库规范

### 题库概况

| 级别 | 文件 | 题数 | Checklist 项数 | 说明 |
|------|------|------|---------------|------|
| L1 | `level1.yaml` | 18 | 99 | 批量计算与结构操作 |
| L2 | `level2.yaml` | 8 | 52 | 多步工作流 |
| L3 | `level3.yaml` | 6 | 33 | 诊断与多源整合 |
| L4 | `level4.yaml` | 2 | 12 | 前瞻性研究与综合报告 |
| Safety | `safety_refusal.yaml` | 4 | 16 | 安全拒绝 |
| **合计** | | **38** | **212** | |

### Checklist Item 维度标注

v4 中每个 checklist item 都标注了 `dimension` 字段：

```yaml
scoring_checklist:
  # accuracy 维度：验证答案正确性
  - verify: "numerical_range"
    dimension: "accuracy"         # ← 维度标注
    weight: 0.30
    reference_answer:
      value: 5.43
      tolerance: 0.1

  # grounding 维度：验证证据支撑
  - verify: "source_type_used"
    dimension: "grounding"
    weight: 0.15
    expected: "TOOL_CALL"

  # efficiency 维度：验证执行效率
  - verify: "no_retries"
    dimension: "efficiency"
    weight: 0.10
    expected: "mat_dft_*"
```

212 项 checklist 的维度分布：

| 维度 | 数量 | 占比 |
|------|------|------|
| accuracy | 88 | 41.5% |
| grounding | 72 | 34.0% |
| efficiency | 52 | 24.5% |

### Rubric 权重字段

v4 的 `rubric` 新增三个字段：

```yaml
rubric:
  touchpoint_bands: [1, 2, 3, 4]
  score_bands: [0.0, 0.25, 0.5, 0.75, 1.0]

  # ★ v4 新增
  grounding_weight: 0.5           # strict_final 中溯源权重
  efficiency_weight: 0.5          # strict_final 中效率权重
  analysis_weights:               # analysis_final 中各维度权重
    accuracy: 0.5
    grounding: 0.3
    efficiency: 0.2
```

---

## Evaluator（评分器）

`evaluator.py` 包含 `RubricEvaluator` 和 `JudgeAdapter` 两个核心类。

### 确定性检查方法

v4 的 `verify` 字段共支持 14 种值（v3 为 8 种，v4 新增 6 种）：

| verify 值 | 维度 | 说明 | v4 新增 |
|-----------|------|------|---------|
| `exact_match` | accuracy | 精确匹配 | |
| `numerical_range` | accuracy | 数值±容差 | |
| `contains_all` | accuracy | 包含全部关键词 | |
| `llm_judge` | accuracy | LLM 评判 | |
| `tool_called` | accuracy | 工具是否被调用 | |
| `tool_args_match` | accuracy | 工具参数匹配 | |
| `safety_refusal` | accuracy | 安全拒绝检测 | |
| `llm_judge_grounding` | grounding | LLM 溯源评判 | |
| `llm_judge_efficiency` | efficiency | LLM 效率评判 | |
| `event_type_called` | grounding | 事件类型是否触发 | ✅ |
| `source_type_used` | grounding | 证据来源类型检查 | ✅ |
| `call_count_range` | efficiency | 调用次数范围 | ✅ |
| `no_retries` | efficiency | 无重试检查 | ✅ |
| `artifact_exists` | efficiency | 产出物存在性 | ✅ |
| `token_budget` | efficiency | Token 预算检查 | ✅ |

### LLM Judge

用于 `llm_judge` / `llm_judge_grounding` / `llm_judge_efficiency` 三个 verify 类型：

- 使用 `evaluator_llm` 配置的模型（推荐 `gemini-2.5-flash` 或同级）
- 结构化 JSON 输出：`{"score": 0.0~1.0, "reasoning": "..."}`
- 当 LLM 不可用时回退到 `0.0`（确定性兜底）

### JudgeAdapter

`JudgeAdapter` 封装了 grounding 和 efficiency 的 LLM 评判：

```python
adapter = JudgeAdapter(llm_client)

# 溯源评判：基于 EvidenceBundle 的工具调用摘要
grounding_score = adapter.judge_grounding(question, answer, evidence_bundle)

# 效率评判：基于工具调用数量、重试情况、token 用量
efficiency_score = adapter.judge_efficiency(question, answer, evidence_bundle)
```

输入信息：
- 题目描述 + 答案文本
- `EvidenceBundle` 中的工具调用摘要（自动格式化为人类可读文本）
- token 用量统计

---

## Runner（执行器）

`runner.py` 编排整个评测流程：

```
对每道题 × mode × k:
  1. simulator.formulate(question) → prompt
  2. mat_runner.run_mat_task(prompt) → answer + trajectory_path
  3. EvidenceExtractor.extract(trajectory_path) → EvidenceBundle   ← v4 新增
  4. evaluator.evaluate(question, answer, evidence) → 三维分数      ← v4 扩展
  5. 构建 EvalRunRecord（含 accuracy/grounding/efficiency/strict_final/
     analysis_final/model_name/token_usage）                        ← v4 扩展
  6. reporter.append_raw_run(record)
```

v4 新增的 `EvalRunRecord` 字段：

```python
accuracy_score:   float | None   # accuracy 维度得分
grounding_score:  float | None   # grounding 维度得分
efficiency_score: float | None   # efficiency 维度得分
strict_final:     float | None   # 乘法总分
analysis_final:   float | None   # 加权总分
model_name:       str | None     # 使用的 LLM 模型名
token_usage:      TokenUsageRecord  # token 开销
```

---

## Aggregator（聚合器）

`aggregator.py` 将 `EvalRunRecord` 列表聚合为 `EvaluationSummary`。

### 分组维度

| 分组 | 说明 | 每组统计内容 |
|------|------|-------------|
| `by_question` | `(question_id, mode)` → k 次重复 | mean/std/min/max × 5 维分数 |
| `by_level` | L1-L4 / Safety | mean/std/ci95 × 5 维分数 |
| `by_mode` | direct / planner | mean/std/ci95 × 5 维分数 |
| `by_model` | 按 `model_name` 分组 | band_score + 双总分 + token 开销 |
| `overall` | 全部记录 | mean/std/ci95 × 5 维分数 + safety |

### by_model 聚合

v4 新增的模型对比功能：

```json
{
  "by_model": {
    "gemini-2.5-flash": {
      "count": 24,
      "band_score": { "mean": 0.72, "std": 0.18, "ci95_half_width": 0.08 },
      "strict_final": { "mean": 0.58, ... },
      "analysis_final": { "mean": 0.65, ... },
      "token_cost": {
        "total_prompt": 125000,
        "total_completion": 38000,
        "total_tokens": 163000,
        "mean_per_run": 6791
      }
    }
  }
}
```

---

## Reporter（报告器）

`reporter.py` 生成以下输出文件：

| 文件 | 格式 | 说明 |
|------|------|------|
| `raw_runs.jsonl` | JSONL | 每条 EvalRunRecord 一行 |
| `scores_by_question.json` | JSON | 按题 × mode 分组统计 |
| `scores_by_level.json` | JSON | 按 level / mode / overall 分组统计 |
| `scores_by_model.json` | JSON | ★ 按模型分组统计 (v4 新增) |
| `final_report.md` | Markdown | 人类可读报告 |

### Markdown 报告结构

`final_report.md` 包含：

1. **By-Level 三维评分表** — 每个 level 的 accuracy / grounding / efficiency / strict_final / analysis_final
2. **By-Mode 三维评分表** — direct vs planner 模式对比
3. **Overall 总体评分** — 全局统计 + safety 状态
4. **Model Comparison 模型对比表** — ★ v4 新增，各模型的 band_score / strict_final / analysis_final / token 开销

示例输出片段：

```markdown
## Scores by Level

| Level | Count | Accuracy | Grounding | Efficiency | Strict Final | Analysis Final |
|-------|-------|----------|-----------|------------|-------------|----------------|
| L1    | 36    | 0.7500   | 0.6200    | 0.8100     | 0.5350      | 0.7120         |
| L2    | 16    | 0.6800   | 0.7100    | 0.7500     | 0.4960      | 0.7030         |

## Model Comparison

| Model | Runs | Band Score | Strict Final | Analysis Final | Tokens/Run |
|-------|------|-----------|-------------|----------------|------------|
| gemini-2.5-flash | 24 | 0.7200 | 0.5800 | 0.6500 | 6791 |
| deepseek-v3      | 28 | 0.6900 | 0.5500 | 0.6300 | 8234 |
```

---

## 运行方式

### 最简运行

```bash
python -m playground.mat_master.evaluation.cli
```

### 推荐运行（显式指定配置）

```bash
python -m playground.mat_master.evaluation.cli \
  --eval-config playground/mat_master/evaluation/config.yaml \
  --k 2 \
  --modes direct planner
```

### 覆盖 Mat Master 主配置

```bash
python -m playground.mat_master.evaluation.cli \
  --eval-config playground/mat_master/evaluation/config.yaml \
  --mat-config configs/mat_master/config.yaml
```

### 中途独立打分（对已完成的记录生成 interim 报告）

```bash
python -m playground.mat_master.evaluation.cli \
  --rate-only \
  --run-dir runs/mat_master_eval/<run_label>_<timestamp>
```

### 后台运行 + 监控（PowerShell）

```powershell
# 启动后台评测
.\scripts\run_matmaster_evaluation_bg.ps1

# 监控进度（另一终端）
.\scripts\monitor_matmaster_evaluation.ps1
```

---

## CLI 参数说明

| 参数 | 含义 | 默认值 |
|------|------|--------|
| `--eval-config` | MATTER 评测配置文件路径 | `playground/mat_master/evaluation/config.yaml` |
| `--mat-config` | 覆盖 Mat Master 主配置路径 | `None`（使用 eval-config 中的值） |
| `--k` | 每道题重复评测次数 | 配置文件的 `k`，缺省 `1` |
| `--modes` | 评测模式列表 | 配置文件的 `modes`，缺省 `["direct", "planner"]` |
| `--output-dir` | 输出根目录 | `runs/mat_master_eval` |
| `--run-label` | 运行标签 | `matter_eval` |
| `--question-bank-dir` | 题库目录路径 | 配置文件的值 |
| `--use-seed-prompt` | 强制使用 `human_prompt_seed`，不走 simulator 改写 | 配置文件的值 |
| `--rate-only` | 独立打分模式 | `False` |
| `--run-dir` | 已有运行目录（配合 `--rate-only`） | `None` |
| `--raw-runs` | 直接指定 `raw_runs.jsonl` 路径 | `None` |
| `--rating-prefix` | 独立打分输出文件前缀 | `interim_` |

---

## 配置文件

`config.yaml` 示例：

```yaml
modes: ["direct", "planner"]
k: 1
question_bank_dir: "playground/mat_master/evaluation/question_bank"
output_dir: "runs/mat_master_eval"
run_label: "matter_eval"
use_seed_prompt: true
max_workers: 1
random_seed: 7

mat_config_path: "configs/mat_master/config.yaml"

# 评分器 LLM 配置（用于 LLM judge + JudgeAdapter）
evaluator_llm:
  provider: "openai"
  model: "gemini-2.5-flash"
  api_key: "${LITELLM_PROXY_API_KEY}"
  base_url: "${LITELLM_PROXY_API_BASE}"
  temperature: 0.0
  max_tokens: 2048
  timeout: 180

# 可选：simulator LLM（用于改写提问）
# simulator_llm:
#   provider: "openai"
#   model: "gpt-4o-mini"
#   ...
```

环境变量占位符（`${VAR_NAME}`）会在加载时自动替换。

---

## 输出结果

默认输出到 `runs/mat_master_eval/<run_label>_<timestamp>/`：

```
runs/mat_master_eval/matter_eval_20260323_090000/
├── raw_runs.jsonl            # 原始评测记录（实时追加）
├── scores_by_question.json   # 按题分组统计
├── scores_by_level.json      # 按 level/mode/overall 统计
├── scores_by_model.json      # ★ 按模型分组统计 (v4 新增)
└── final_report.md           # 可读 Markdown 报告
```

---

## 监控脚本

| 脚本 | 作用 |
|------|------|
| `scripts/run_matmaster_evaluation_bg.ps1` | 在 PowerShell 后台启动评测进程 |
| `scripts/monitor_matmaster_evaluation.ps1` | 检查最新 run 目录的进度、文件状态、三维分数摘要 |

`monitor_matmaster_evaluation.ps1` 会自动检测：
- `raw_runs.jsonl` 的行数（已完成题数）
- `scores_by_model.json` 的存在性 (v4)
- `final_report.md` 中的三维评分摘要 (v4)

---

## 统计方法说明

### 均值 (mean)

算术平均 `mean = (x₁ + x₂ + ... + xₙ) / n`，空样本返回 0.0。

### 标准差 (std) — 样本标准差

使用 `statistics.stdev`（除以 n-1），n=1 时返回 0.0。

### 95% 置信区间半宽 (ci95_half_width)

```
ci = t(α/2, df) × s / √n
```

使用 t 分布查表法（覆盖 df=1~30+），不依赖 scipy。k 越小 t 值越大，置信区间越宽。

| df (=k-1) | t 临界值 | 说明 |
|-----------|---------|------|
| 1 (k=2) | 12.706 | 非常宽 |
| 4 (k=5) | 2.776 | 较宽 |
| 9 (k=10) | 2.262 | 接近正态 |
| ≥30 | 1.960 | 退化为 z |

### Safety 统计

Safety veto 具有一票否决语义：

| 指标 | 含义 |
|------|------|
| `triggered_count` | 触发 safety veto 的记录数 |
| `triggered_rate` | `triggered_count / total_runs` |
| `overall.passed` | 当且仅当 `triggered_count == 0` |

---

## 插拔与扩展指南

### 移植到其他 Agent

evaluation 框架不绑定 MatMaster。移植到其他 agent（如 x_master、kaggle agent）只需：

| 步骤 | 操作 | 涉及文件 |
|------|------|----------|
| 1 | 编写新的 `xxx_runner.py` | 新文件，替代 `mat_runner.py` |
| 2 | 更新 `evidence_mapping.yaml` | 替换工具名→事件类型映射 |
| 3 | 在 `runner.py` 中导入新 runner | `runner.py` 1 行 import |
| 4 | 编写题库 YAML | `question_bank/` 目录 |

不需要修改的文件：`evaluator.py`、`aggregator.py`、`reporter.py`、`schemas.py`、`evidence.py`、`cli.py`。

### 添加新工具

当 MatMaster 接入新 MCP 工具时：

1. 在 `evidence_mapping.yaml` 追加映射规则
2. 如果工具代表全新的事件类型，在 `evidence.py` 的 `EventType` 枚举中新增成员
3. 题库中使用新的 `event_type_called` / `source_type_used` checklist item

### 添加新 verify 方法

在 `evaluator.py` 中：

1. 在 `RubricEvaluator._evaluate_check_item()` 的分发逻辑中添加 case
2. 实现对应的 `_check_xxx()` 静态方法
3. 在 `schemas.py` 的 `VerifyLiteral` 类型中添加新值
4. 在题库 YAML 中使用

### 添加新评分维度

如果未来需要第四个维度（如 `safety_score`）：

1. 在 `schemas.py` 的 `DimensionLiteral` 添加新值
2. 在 `evaluator.py` 的维度分组逻辑中新增分桶
3. 在 `aggregator.py` 的累加器中新增字段
4. 在 `reporter.py` 的表格中新增列
5. 更新 rubric 权重字段和总分公式

### 拔掉 evaluation 模块

如果需要移除 evaluation（如裁剪部署镜像）：

1. 删除 `playground/mat_master/evaluation/` 整个目录
2. 回退 `evomaster/agent/agent.py` 中 `model_name` 相关的 3 行（可选，不回退也无影响）
3. 完毕。不影响 agent 正常运行

### 不运行 agent 直接评分

如果已有 `raw_runs.jsonl`（比如从生产日志中提取），可以跳过 agent 执行直接生成报告：

```bash
python -m playground.mat_master.evaluation.cli \
  --rate-only \
  --raw-runs /path/to/raw_runs.jsonl
```

---

## 向后兼容

v4 对 v3 完全向后兼容：

- **题库**：未标注 `dimension` 的 checklist item 自动视为 `accuracy`
- **Rubric**：未配置 `grounding_weight` / `efficiency_weight` 时使用默认值
- **输出**：旧版字段（`score`、`band_score`）保持不变，v4 字段为新增
- **CLI**：参数不变，无需修改调用脚本
- **配置**：`config.yaml` 无需修改即可运行 v4

如果 `evaluator_llm` 未配置或不可用，grounding/efficiency 评分会回退为 `None`（不影响 accuracy 维度正常工作）。
