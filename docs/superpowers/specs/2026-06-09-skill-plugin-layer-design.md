# Skill Plugin 层设计

- 日期: 2026-06-09
- 状态: 已通过 brainstorming，待写实现计划
- 范围: 仅 `matmaster-evo` 后端
- 作者: Kealdoom + Claude

## 1. 背景与动机

matmaster-evo 现有 51 个 skill（目录 + `SKILL.md` + 可选 `references/` `scripts/`），扁平铺在 `matmaster/skills/` 下。两个问题：

1. **强关联 skill 缺一级归属单元**。一个计算软件常需要多个子能力（输入准备、提交、后处理），现在只能拆成并列 skill，相互关系无处表达。
2. **计算软件需要横向拓展**，单个 SKILL.md 表达力受限，但新增子 skill 时没有共享资源/配置的归属点，只能重复。

调研发现仓库里已有两套**休眠**的分组设施：

- `matmaster/skills/builtin_tags.yaml`：把 51 个 skill 编进 `categories → groups → skills` 两级分类，是一个干净的 partition（每个 skill 恰好出现一次，全部 51 个都被覆盖，无孤儿）。但**没有任何运行时代码加载它**——纯文档。
- `skill_type` frontmatter 字段（`operator` / `mcp-loader`）：除测试外**无任何运行时消费**。

唯一真正被运行时消费的 frontmatter 是 `mcp_server`（skill 命中时激活对应 MCP 工具，见 `matmaster/tools/builtin/skill_tool.py:113`）。

当前运行时模型是**按需载入**而非路由选择：`scanner` 检测 skill 命中 → 变成 `ActiveSkill` 进入 `[Loaded skills]` 段（`matmaster/context/sources/skills.py`、`matmaster/context/skill_resolver.py`）→ 带 `mcp_server` 则顺带激活工具。

## 2. 核心判断

plugin 不是运行时路由/联动单元，而是**打包 / 资源归属 / 生命周期单元**。这意味着 skill 的"命中→载入"链路完全不动，plugin 层只引入一处运行时触点：注册过滤（§3.5）。

这把改造的爆炸半径限制在：目录布局、一个瘦清单、一个路径变量、一个注册门控、一个迁移脚本。

## 3. 设计决策

### 3.0 四个支点

| 维度 | 决策 | 理由 |
|------|------|------|
| 语义 | 打包 / 资源 / 生命周期单元 | 用户选了"共享资源与配置" + "打包版本与启停"，未选路由/联动 |
| 粒度 | group 级（约 20 个），每个 skill 恰属一个 plugin（树状包含） | 软件族粒度恰好是版本/启停/资源共享的单元；builtin_tags 已是 partition |
| 方案 | B（瘦清单 + 文件共享），执行配置留在 SKILL.md | 见 §3.0.1 |
| 范围 | 仅 matmaster-evo 后端 | 前端 UX、用户自建 skill 另开 spec |

#### 3.0.1 为何选 B 而非"富清单 A"

曾考虑过把执行配置（Bohrium 镜像 / 机型 / cmd）结构化进 plugin 清单（方案 A）。核对数据后否决，理由：

- **镜像按 skill 走，不按 plugin 走**。同一 plugin 内 operator 多用不同镜像（abacus plugin 里 abacus 用 `abacusp:1.0.3-…`、pyatb 是 query-first 不固定）。把 image 塞进清单几乎去不了重。
- **唯一真正重复的机型规格 `c64_m256_cpu`（出现在 cp2k/quantum_espresso/orca/pyscf/abinit/pyatb 6 处）是跨 plugin 共享的**，该进 plugin 之上的全局机型目录，而非塞进单个 plugin 清单。
- **版本可复现性 B 也能拿到**：整个 plugin 目录（含 SKILL.md 里的镜像 tag prose）一起 git 版本化，plugin 的某个版本 = 该目录的 git 状态，镜像 tag 天然被钉住。只有需要"程序化批量改镜像 / 运行时注入覆盖"时才需结构化，而当前工作流对部分软件是实时 query 镜像，反而不宜钉死。

跨 plugin 的机型去重（把 `c64_m256_cpu` 抽成全局 reference 供相关 skill 指向）是与本设计**解耦的独立小清理**，列入范围外（§7）。

### 3.1 目录布局

`matmaster/skills/` 整体改名为 `matmaster/plugins/`，其直接子目录从 skill 变为 plugin：

```
matmaster/plugins/<plugin>/
├── plugin.yaml                  # 瘦清单，见 §3.2
├── skills/<skill>/SKILL.md      # 成员 skill，各自带 references/ scripts/
├── references/                  # plugin 级共享参考（可选）
└── scripts/                     # plugin 级共享脚本（可选）
```

单 skill 的 plugin（cp2k、orca 等）走相同结构：`plugins/cp2k/skills/cp2k/SKILL.md`。多一层但保持统一，且正是横向拓展的生长点——以后 `plugins/cp2k/skills/cp2k-postprocess/` 直接进来，共享 `plugins/cp2k/references/`。

plugin 目录名取自 builtin_tags 的 group key（连字符形式，如 `quantum-espresso`）；skill 目录名保持原样（如 `quantum_espresso`），二者在不同命名空间，不冲突。

### 3.2 plugin.yaml（瘦清单）

成员**靠扫描 `skills/` 子目录得到**（目录即真相，不在清单里重列，避免漂移）。清单只放元数据：

```yaml
name: abacus
version: 0.1.0
category: simulation          # 来自 builtin_tags 顶层 category
description: "ABACUS DFT 软件族：输入准备 / 提交 / 解析 + PyATB 紧束缚后处理"
```

- `mcp_server` 保持在 skill 的 frontmatter 不动（运行时唯一被消费的字段；lazymcp 也是一 skill 一 server，无上移必要）。
- 执行配置（镜像 / 机型 / cmd）按方案 B 留在各 `SKILL.md`，本设计不动。
- 迁移时 `description` 由脚本生成占位串（如 `"<plugin> plugin（待人工补充）"`），后续人工补全；这是产物占位，不是本 spec 的未决项。

### 3.3 plugin 边界 = builtin_tags 的 group

直接采用 `builtin_tags.yaml` 的 group 划分作为 plugin 边界，category 落成 plugin 的 `category` 字段。这会**解散现有三个物理目录**（按功能重组）：

- `lazymcp/` 散掉：6 个 `mcp-mat-*` 按功能并入 characterization / structure-search / data-mining / literature。**plugin 因此可含混合类型**（operator + mcp-loader 同处一个 plugin），这是预期且更有用的结果。
- `planner/` + 顶层 `plan-executor` → 合成 `task-planning` plugin。
- `playground-skills/` 散掉（见 §4 剪枝）：幸存 skill 按功能并入对应 plugin，不保留独立暂存 plugin。

### 3.4 `${PLUGIN_DIR}` 资源解析

复用现有 `${SKILL_DIR}` 注入机制（当前 SKILL.md 已用 `${SKILL_DIR}/references/...`），新增 `${PLUGIN_DIR}`：loader 给每个 skill 计算两个变量——`${SKILL_DIR}`（skill 自身目录，语义不变）和 `${PLUGIN_DIR}`（所属 plugin 根目录）。

SKILL.md 中：
- `${SKILL_DIR}/references/x.md` → skill 私有资源
- `${PLUGIN_DIR}/references/y.md` → 同 plugin 共享资源

无优先级魔法，由 skill 作者显式选择路径。实现计划需定位现有 `${SKILL_DIR}` 替换点并平行加入 `${PLUGIN_DIR}`。

### 3.5 注册门控（后端配置级启停）

`config/plugins.yaml` 新增启停配置，默认全开、列禁用名单：

```yaml
disabled_plugins: []          # 留空即全部启用；如 [gpumd] 则禁用该 plugin
```

注册流程改为：扫描 `plugins/*` → 读各 `plugin.yaml` → 跳过 `disabled_plugins` 中的 plugin → 仅把启用 plugin 的成员 skill 注册进 registry。

**这是整个改造对运行时的唯一触点**：禁用 plugin 的 skill 进不了 registry → scanner 命不中 → 不进上下文。其余"命中→载入→激活 mcp"链路一行不动。

## 4. playground 剪枝

`playground-skills/` 共 13 个，保留 3、删 10。

**保留（解散后并入功能 plugin）：**

| skill | 去向 plugin |
|-------|------------|
| pxrd-refinement | characterization |
| checkcif-validator | characterization |
| composition-optimization | data-mining |

**删除（10）：** compliance-guardian、deep-survey、lit-data-organizer、manuscript-scribe、md-analysis、poly-forcefield、poly-generator、result-analysis、tasker-polar-surface、vaspkit-postprocess。

**对 plugin 边界的连锁影响：**

- **`polymer-modeling` plugin 整个消失**（原仅含 poly-generator）→ plugin 总数 21 → 20。
- 缩回单 skill 的 plugin：vasp、gromacs、general-data-analysis、literature（仅剩 mcp-mat-doc）、academic-writing（仅剩 proposal-review）。

## 5. 最终 plugin 清单（20 个 / 41 个 skill）

| category | plugin | 成员 skill |
|----------|--------|-----------|
| structure-modeling | atomic-structure-ops | atomic-structure, inspect-atomic-structure, build-crystal-from-params, transform-atomic-structure, assemble-atomic-structure, operate-molecular-crystal, sample-atomic-structures |
| structure-modeling | structure-search | mcp-mat-struct-db, retrieve-structure |
| simulation | abacus | abacus, pyatb |
| simulation | vasp | vasp |
| simulation | cp2k | cp2k |
| simulation | quantum-espresso | quantum_espresso |
| simulation | abinit | abinit |
| simulation | pyscf | pyscf |
| simulation | orca | orca |
| simulation | lammps | lammps |
| simulation | gromacs | gromacs |
| simulation | gpumd | gpumd |
| simulation | mlips | mlips, aissq-explorer |
| analysis | general-data-analysis | data-analysis |
| analysis | characterization | pxrd-refinement, checkcif-validator, mcp-mat-xrd, mcp-mat-nmr, mcp-mat-electron-microscope |
| analysis | data-mining | mcp-mat-compdart, composition-optimization |
| research-writing | literature | mcp-mat-doc |
| research-writing | academic-writing | proposal-review |
| workflow-system | task-planning | plan-writer, plan-checker, plan-executor, spec-writer, acceptance-writer, stack-checker |
| workflow-system | system-tools | skill-manager, image-manager, session-analyzer |

12 个为单 skill plugin（横向拓展的生长点），8 个为多 skill plugin。

## 6. 迁移与死代码清理

遵循"迁移而非兼容、外部脚本、主代码不留兼容兜底"原则。

### 6.1 迁移脚本 `scripts/migrate_to_plugins.py`（一次性，跑完即弃）

1. **剪枝**：删除 §4 列出的 10 个 skill 目录。
2. **重组**：以 builtin_tags 的 group→skills 为目标、以 skill 名为 key 定位每个 skill 的当前目录（无论它现在在顶层、`lazymcp/`、`planner/` 还是 `playground-skills/`），移动到 `plugins/<group>/skills/<skill>/`。
3. **生成清单**：为每个 group 写 `plugins/<group>/plugin.yaml`（name=group、category=父 category、version=`0.1.0`、description=占位）。
4. **引用清扫**：sweep 这 10 个被删 skill 名在其它配置/测试/系统提示里的残留引用并清理。

skill 名在全仓唯一，定位无歧义；builtin_tags 覆盖全部 51 个 skill 无孤儿，迁移无"未分配"边界情况。

### 6.2 loader / registry 重写

直接重写为读 plugin 布局，删除旧扁平扫描路径（涉及 `matmaster/core/skill_registry_cache.py`、`src/services/skill_registry_factory.py`、`matmaster/context/scanner.py` 等，实现计划阶段精确定位）。不保留"同时支持旧扁平 + 新 plugin"的双模式。

### 6.3 死代码删除

- 删 `matmaster/skills/builtin_tags.yaml`（信息已迁入各 plugin.yaml）。
- 删 `skill_type` frontmatter 字段（全仓无运行时消费；`mcp_server` 已足以标识 loader 性质）——迁移脚本顺手从 frontmatter 剥除。

## 7. 范围外 / 后续

- **前端 plugin 浏览/启停 UX**（scimaster-bohr-chat）：另开 spec。
- **用户自建 skill 如何归属 plugin**（NAS 同步、每用户启停）：另开 spec。
- **跨 plugin 机型去重**（`c64_m256_cpu` 抽全局 reference）：解耦的独立小清理。
- **两级路由选择 / 兄弟联动激活**：本轮明确不做（运行时选择逻辑保持不变）。

## 8. 测试与代码量约束

- **不新增测试文件**（遵用户规则）；现有 skill/registry 相关测试（`tests/test_skill_registry.py`、`test_skill_docs.py`、`test_skill_tool.py`、`test_stream_replay_skill_hit.py`、`tests/skills/`）随布局变更**更新**到 plugin 布局，属迁移非新增。
- 本设计净代码量预期持平或下降：删除（扁平扫描路径 + builtin_tags.yaml + skill_type 字段 + 10 个 skill）抵消新增（瘦清单 loader + `${PLUGIN_DIR}` + 注册门控）。
