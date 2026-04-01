# Phase 29: 主执行路径切换 - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-04-01
**Phase:** 29-main-execution-path
**Areas discussed:** 丢弃方式, CONS-02 处置, CONS-01 验证, 技能归档

---

## 前置澄清

用户在 gray area 选择前明确指示："丢弃所有目前没有在 matmaster 中使用的功能。包括本地后端，cli等。" 这显著简化了 Phase 29 范围。

---

## 丢弃方式

| Option | Description | Selected |
|--------|-------------|----------|
| 物理删除 | 直接删文件，干净彻底，git 历史可追溯 | ✓ |
| 标记废弃不删除 | 文件留着加 deprecated 标记 | |
| 只删入口点 | 删 run.py 和 playground_init/run_agent，保留 helpers | |

**User's choice:** 物理删除
**Notes:** 无

---

## 删除范围

| Option | Description | Selected |
|--------|-------------|----------|
| playground/mat_master/service/server/ | 本地 Web 调试后端全部 | ✓ |
| run.py | evomaster 统一 CLI 入口 | ✓ |
| evaluation/ | 评估路径 | ✓ |
| playground/ 整个目录 | 更彻底——删除整个 playground/ | ✓ |

**User's choice:** 全选
**Notes:** 无

---

## CONS-02 处置

| Option | Description | Selected |
|--------|-------------|----------|
| 标记为不适用 | 不再需要——本地调试已有 DevShell | ✓ |
| 重新定义为 DevShell | 将 CONS-02 改为 DevShell 替代方案 | |
| 延后到 v2.2 | 本地 Web 调试能力延后重建 | |

**User's choice:** 标记为不适用
**Notes:** 无

---

## workspace_resolver 处理

| Option | Description | Selected |
|--------|-------------|----------|
| 搬入 matmaster | 纯工具函数搬入 matmaster 侧 | ✓ |
| 内联到 agent_run_bohrium | 直接复制两个函数 | |
| 放入 src/ 工具层 | 放入 src/utils/ | |

**User's choice:** 搬入 matmaster
**Notes:** src/services/agent_run_bohrium.py 对 playground 的唯一依赖

---

## matmaster → evomaster 残余清理

| Option | Description | Selected |
|--------|-------------|----------|
| 本 phase 一并清理 | bash_tool + monitor_job 一起处理 | ✓ |
| 留给 Phase 30 | 工具层依赖让审计阶段处理 | |

**User's choice:** 本 phase 一并清理
**Notes:** 无

---

## evomaster/ 目录删除

| Option | Description | Selected |
|--------|-------------|----------|
| 一起删 | matmaster 零依赖后直接删 | |
| 不删，留给 Phase 30 | 本 phase 只确保零依赖 | ✓ |

**User's choice:** 不删，留给 Phase 30
**Notes:** 无

---

## 测试文件处理

| Option | Description | Selected |
|--------|-------------|----------|
| 一并删除 | 删除 tests/playground/、tests/evaluation/ 及引用 playground 的测试 | ✓ |
| 保留测试标记 skip | 不删测试，加 pytest.mark.skip | |

**User's choice:** 一并删除
**Notes:** workspace_resolver 测试更新为从 matmaster 导入

---

## 技能迁移

| Option | Description | Selected |
|--------|-------------|----------|
| 整体搬入 matmaster | 19 + 5 个技能全部迁移 | |
| 只搬活跃技能 | 确认哪些在用再搬 | |
| 直接丢弃 | 全部丢弃 | |
| 延后处理 | 移到临时位置 | |

**User's choice:** 初选"整体搬入 matmaster"，后修改为归档到临时位置
**Notes:** 用户澄清——playground 下的技能不迁移，存放到临时归档位置（.archive/playground-skills/），项目完成后由用户手动合并或删除

---

## 归档位置

| Option | Description | Selected |
|--------|-------------|----------|
| .archive/playground-skills/ | 项目根目录下 .archive/ 子目录 | ✓ |
| /tmp 或项目外 | 移到项目目录外 | |
| 单独 git 分支 | 在 archive 分支上保留 | |

**User's choice:** .archive/playground-skills/
**Notes:** 无

---

## Claude's Discretion

- workspace_resolver 在 matmaster 侧的具体模块位置
- monitor_job/_llm.py 替换为 matmaster llm_factory 的具体适配方式
- .archive/ 是否加入 .gitignore

## Deferred Ideas

- evomaster/ 目录删除 — Phase 30
- evomaster/skills/ 迁移 — 随 evomaster/ 删除处理
- .archive/playground-skills/ 正式合并 — 项目完成后
