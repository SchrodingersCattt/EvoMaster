# Phase 30: 解耦审计与独立性证明 - Context

**Gathered:** 2026-04-02
**Status:** Ready for planning

<domain>
## Phase Boundary

用 import audit、隔离测试和迁移文档证明 matmaster 可脱离 evomaster/playground/src 独立运行。审计通过后物理删除 evomaster/ 目录，技能归档。编写完整迁移文档记录 v2.1 解耦成果与后续清理方向。

不包含新功能开发、evomaster 代码重构、或 v2.2 清理工作的实际执行。

</domain>

<decisions>
## Implementation Decisions

### 隔离测试方案
- **D-01:** 采用重命名隐藏法：测试前将 evomaster/ 和 src/ 临时 mv 为 _evomaster_hidden/ 和 _src_hidden/，运行 tests/matmaster/ 全集，再还原
- **D-02:** 覆盖范围为 tests/matmaster/ 下所有 100 个测试文件，不做子集筛选
- **D-03:** 严格全通过策略，不使用 xfail 标记。如有失败则修复测试代码或 matmaster 源码，而不是跳过
- **D-04:** evomaster/ 和 src/ 同时隐藏，证明 matmaster 对两者都无运行时依赖

### evomaster/ 目录处置
- **D-05:** 审计通过后在本 phase 物理删除 evomaster/ 整个目录。git 历史可追溯，不需要保留死代码
- **D-06:** evomaster/skills/ 的 5 个技能在删除前归档到 .archive/evomaster-skills/，与 Phase 29 的 playground 技能归档模式一致
- **D-07:** skills_root 配置需更新（当前指向 evomaster/skills），删除后指向归档位置或 matmaster/skills/

### 迁移文档
- **D-08:** 文档放在 docs/ 目录下，与现有 docs/architecture-reference-claude-code.md 和 docs/specs/ 一致
- **D-09:** 文档涵盖四部分内容：解耦过程回顾（v2.1 各 phase 做了什么）、当前架构状态（matmaster 模块边界与依赖方向）、残留路径清单（compat layer、遗留路径）、v2.2 清理顺序（优先级与建议）

### 测试门禁
- **D-10:** import audit 保持在 pytest 套件中即可，不单独配置 CI 门禁。团队持续跑 pytest 就能防回归
- **D-11:** 全量测试运行并记录实际通过数到迁移文档，不设硬编码数字基线

### Claude's Discretion
- 隔离测试的具体 shell 脚本结构（mv/test/restore 的原子性保障）
- 迁移文档的具体章节组织和详细程度
- evomaster/ 删除时的 git commit 拆分策略（单次 vs 分步）
- 全量测试运行时的具体 pytest 参数

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Import Audit 基础设施
- `tests/matmaster/test_import_audit.py` -- 现有 AST 级 import audit 测试套件（15 个测试方法，6 个测试类），TYPE_CHECKING 感知
- `tests/matmaster/` -- 完整 matmaster 测试集（100 个文件，26 个子目录）

### 架构与规范
- `docs/architecture-reference-claude-code.md` -- matmaster 架构参考文档
- `docs/specs/2026-04-02-tool-runtime-v2.md` -- Tool Runtime v2 架构设计（untracked）

### 删除目标
- `evomaster/` -- 整个目录（87 个 .py 文件），审计通过后删除
- `evomaster/skills/` -- 5 个技能目录，删除前归档到 .archive/evomaster-skills/

### 配置文件
- `matmaster_config/config.yaml` -- skills_root 配置当前指向 evomaster/skills，需更新
- `configs/mat_master/config.yaml` -- 可能也有 skills_root 相关配置

### 先前 phase 参考
- `.planning/phases/29-main-execution-path/29-CONTEXT.md` -- D-08 evomaster/ 延后到本 phase，D-09/D-10 技能归档决策
- `.planning/phases/28-src-consumer/28-CONTEXT.md` -- xfail 策略参考（本 phase 选择了严格全通过）
- `.planning/REQUIREMENTS.md` -- QUAL-06, QUAL-07, QUAL-08 定义

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `tests/matmaster/test_import_audit.py` -- 成熟的 AST 级审计套件，可扩展或直接复用
- `.archive/playground-skills/` -- Phase 29 建立的技能归档模式，可复用于 evomaster skills

### Established Patterns
- 重命名隐藏：Phase 30 首创，但概念简单（mv + test + restore）
- 技能归档：Phase 29 已建立 `.archive/<source>-skills/` 模式
- 物理删除：Phase 29 已建立 playground/ + evaluation/ + run.py 删除模式

### Integration Points
- `matmaster_config/config.yaml` -- skills_root 配置需在 evomaster/ 删除后更新
- `pyproject.toml` -- 可能有 evomaster 相关的包配置或测试路径
- `.gitignore` -- 可能有 evomaster 相关的规则

</code_context>

<specifics>
## Specific Ideas

- 用户选择严格全通过而非 xfail，体现了对 matmaster 独立性的高信心
- evomaster/ 本 phase 直接删除，与 v2.1 里程碑"完全独立化"目标彻底收口
- 迁移文档作为 v2.1 的交付物，同时为 v2.2 提供清晰的后续路径

</specifics>

<deferred>
## Deferred Ideas

- `.archive/playground-skills/` 和 `.archive/evomaster-skills/` 的正式合并到 matmaster -- 项目完成后用户手动
- v2.2 清理工作的实际执行（LEGY-01, LEGY-02, PKG-01）
- CI pipeline 集成 import audit 门禁 -- 如果团队规模扩大再考虑

</deferred>

---

*Phase: 30-decoupling-audit*
*Context gathered: 2026-04-02*
