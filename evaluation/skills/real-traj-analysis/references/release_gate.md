# Release Gate Cases

`evaluation/release_gate/cases.yaml` 维持固定 **15 个 case** 的回归集。每个 case 测试不同的能力点，用于上线前端到端回归。

## 总量控制

新增一个必须同时下掉一个。下掉的标准：
- 该能力点已被新 case 覆盖（合并）
- 该 case 已经连续多次通过且不再有区分度
- 该 case 的 friction 已被 skill/prompt 修复，不再是痛点

## Inclusion Criteria

- Task is a standard materials science computation workflow (structure build, DPA/ABACUS calc, property analysis)
- Moderate complexity (would take 5-30 tool calls if done smoothly)
- Not dependent on user-specific custom skills or private data
- Each case tests a **distinct capability point** — no two cases should test the same thing
- If user files are needed, an OSS-accessible copy must exist or be created

## Format

```yaml
  - id: rg_NN
    title: "short title"
    prompt: "user prompt as they would naturally ask"
    source_session: "session_id"
    notes: "what capability this tests; what went wrong on prod (if any)"
```
