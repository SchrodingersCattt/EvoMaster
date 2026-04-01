# Phase 30: 解耦审计与独立性证明 - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md -- this log preserves the alternatives considered.

**Date:** 2026-04-02
**Phase:** 30-解耦审计与独立性证明
**Areas discussed:** 隔离测试方案, evomaster/ 目录去留, 迁移文档范围, 测试门禁持久化

---

## 隔离测试方案

### Q1: 如何在不安装 evomaster 的环境中证明 matmaster 可独立运行？

| Option | Description | Selected |
|--------|-------------|----------|
| 重命名隐藏法 | 测试前 mv evomaster/ 为 _evomaster_hidden/，运行测试后还原。简单直接 | ✓ |
| 独立 virtualenv | 创建干净 venv 只装 matmaster 依赖。更严格但配置复杂 | |
| sys.path 操控 | fixture 中从 sys.path 移除路径。轻量但有缓存问题 | |
| 组合方案 | 静态 AST + 重命名隐藏。两层保证 | |

**User's choice:** 重命名隐藏法
**Notes:** 无额外说明

### Q2: 隔离测试的覆盖范围？

| Option | Description | Selected |
|--------|-------------|----------|
| tests/matmaster/ 全集 | 100 个测试文件全部运行。最强证明力 | ✓ |
| 核心路径子集 | 只跑 core/tools/types/config。更稳定但证明力弱 | |
| 全量测试 | 仓库所有 1195+ 测试。包括 src 的测试 | |

**User's choice:** tests/matmaster/ 全集
**Notes:** 无额外说明

### Q3: 隔离测试失败时的处理策略？

| Option | Description | Selected |
|--------|-------------|----------|
| xfail 标记 | 已知环境依赖测试用 xfail 标记。Phase 28 已有模式 | |
| 严格全通过 | 所有测试必须通过，失败则修复 | ✓ |
| 分级报告 | 核心 MUST-PASS + 边缘 SHOULD-PASS | |

**User's choice:** 严格全通过
**Notes:** 无额外说明

### Q4: 是否也隐藏 src/？

| Option | Description | Selected |
|--------|-------------|----------|
| 两个都隐藏 | 同时隐藏 evomaster/ 和 src/，证明对两者都无依赖 | ✓ |
| 只隐藏 evomaster/ | 严格按 QUAL-07 原文，src 独立性由 AST audit 覆盖 | |

**User's choice:** 两个都隐藏
**Notes:** 无额外说明

---

## evomaster/ 目录去留

### Q1: 审计通过后是否在本 phase 物理删除 evomaster/？

| Option | Description | Selected |
|--------|-------------|----------|
| 本 phase 删除 | 审计通过后立即删除。彻底收口 v2.1 | ✓ |
| 保留到 v2.2 | 保守策略，留 87 个无用 .py 文件 | |
| 归档后删除 | 先 mv 到 .archive/ 再删除原目录 | |

**User's choice:** 本 phase 删除
**Notes:** 无额外说明

### Q2: evomaster/skills/ 的 5 个技能如何处置？

| Option | Description | Selected |
|--------|-------------|----------|
| 迁移到 matmaster/skills/ | 完全原生化 | |
| 归档到 .archive/ | 与 playground 技能归档一致 | ✓ |
| 随 evomaster/ 一起删除 | git 历史可恢复 | |

**User's choice:** 归档到 .archive/
**Notes:** 无额外说明

---

## 迁移文档范围

### Q1: 迁移文档放在哪里？

| Option | Description | Selected |
|--------|-------------|----------|
| docs/ 目录 | 与现有 docs/ 一致，可见性高 | ✓ |
| .planning/ 目录 | 与 GSD 工作流一致 | |

**User's choice:** docs/ 目录
**Notes:** 用户标注"不需要"（不需要更多讨论）

### Q2: 文档涵盖哪些内容？（多选）

| Option | Description | Selected |
|--------|-------------|----------|
| 解耦过程回顾 | v2.1 各 phase 做了什么、关键决策 | ✓ |
| 当前架构状态 | matmaster 模块边界与依赖方向 | ✓ |
| 残留路径清单 | compat layer、遗留路径 | ✓ |
| v2.2 清理顺序 | 优先级与建议 | ✓ |

**User's choice:** 全部选中
**Notes:** 无额外说明

---

## 测试门禁持久化

### Q1: import audit 是否需要集成到 CI？

| Option | Description | Selected |
|--------|-------------|----------|
| 保持 pytest 即可 | 已在 pytest 套件中，团队跑 pytest 就能防回归 | ✓ |
| 加 CI 门禁 | 单独 CI step 防止跳过测试 | |
| pre-commit hook | 本地拦截但可被跳过 | |

**User's choice:** 保持 pytest 即可
**Notes:** 无额外说明

### Q2: 全量测试基线确认方式？

| Option | Description | Selected |
|--------|-------------|----------|
| 运行并记录 | 运行全量测试，记录实际通过数到迁移文档 | ✓ |
| 严格匹配 1195+ | 必须 >= 1195 才算通过 | |

**User's choice:** 运行并记录
**Notes:** 无额外说明

---

## Claude's Discretion

- 隔离测试的具体 shell 脚本结构
- 迁移文档的章节组织和详细程度
- evomaster/ 删除的 git commit 拆分策略
- 全量测试运行时的 pytest 参数

## Deferred Ideas

- .archive/ 技能的正式合并到 matmaster -- 项目完成后用户手动
- CI pipeline 集成 -- 团队规模扩大再考虑
