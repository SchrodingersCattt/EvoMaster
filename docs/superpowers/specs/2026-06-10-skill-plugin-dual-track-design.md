# Skill 双轨与 Plugin 层设计

- 日期: 2026-06-10
- 状态: 已通过 brainstorming，待写实现计划
- 范围: 仅 `matmaster-evo` 后端
- 作者: Kealdoom + Claude
- 取代: `2026-06-09-skill-plugin-layer-design.md`（该版采用全量收编模型，经代码核查与二轮讨论后由本版双轨模型取代）

## 1. 背景与动机

matmaster-evo 现有 51 个 skill（目录 + `SKILL.md` + 可选 `references/` `scripts/`），分布在 `matmaster/skills/` 顶层及 `lazymcp/`、`planner/`、`playground-skills/` 三个子目录。两个问题：

1. **强关联 skill 缺一级归属单元**。一个计算软件常需要多个子能力（输入准备、提交、后处理），现在只能拆成并列 skill，相互关系无处表达。
2. **计算软件需要横向拓展**，单个 SKILL.md 表达力受限，但新增子 skill 时没有共享资源与配置的归属点，只能重复。

代码核查确认的现状事实（修正上一版 spec 的三处误判）：

- `matmaster/skills/builtin_tags.yaml` 把 51 个 skill 编进 `categories → groups → skills` 两级分类，共 21 个 group，是干净 partition（每个 skill 恰好出现一次）。它**不是死文件**：`app.py` 启动时 `src/services/builtin_skills_sync.py` 读取它，把每个 skill 的 category/tags 同步给 tools-server（`POST /api/v1/skills/sync-builtin`），并按硬编码的 `_SKILLS_ROOT` 打包上传 skill zip。
- frontmatter 中被运行时消费的字段有**两个**：`mcp_server`（skill 命中时激活对应 MCP 工具，`matmaster/tools/builtin/skill_tool.py:113`）和 `depends_on`（命中时递归激活依赖 skill 的 MCP，`skill_tool.py:95-98`，依赖缺失时静默跳过）。`skill_type` 被解析存储但零业务读取，是死字段。
- `matmaster/skills/` 同时是 python 包：`registry.py`、`settings.py` 住在里面，被 skill_tool、skill_registry_cache、exp 等 import。
- 运行时模型是**按需载入**而非路由选择：scanner 检测 skill 命中 → `ActiveSkill` 进入 `[Loaded skills]` 段 → 带 `mcp_server` 则顺带激活工具。skill 发现机制是对各 root `rglob("SKILL.md")`，本身与目录布局无关；registry 已支持多 root（内置 + 本地/远程用户 skill root）。

## 2. 核心判断

plugin 与扁平 skill **双轨并存**：

- plugin 是**打包 / 资源归属 / 生命周期单元**，不是运行时路由或联动单元。只收两类成员：强关联 skill 簇（软件家族、工作流家族、depends_on 强耦合对），以及必定横向拓展的计算软件（单 skill 也升格，预留生长点）。
- 扁平轨继续承载独立 skill。弱主题关联（如表征类）用标签表达，不强行打包。
- 不变量：**每个 skill 物理上恰属一轨**。plugin 成员住在 plugin 目录内，不再出现在扁平序列中。

skill 的命中与载入链路完全不动。改造对运行时只有两处触点：注册门控（§3.5）和可用 skill 列表的分组渲染（§3.6，纯展示）。爆炸半径限制在：一个新目录、一个瘦清单、一个路径变量、一个注册门控、列表渲染、sync 双根改造、一个迁移脚本。

## 3. 设计决策

### 3.0 支点

| 维度 | 决策 | 理由 |
|------|------|------|
| 语义 | 打包 / 资源 / 生命周期单元 | 共享资源与配置 + 打包启停，不做路由联动 |
| 入轨判据 | 强关联簇 或 计算软件 | 强关联才值得打包；计算软件必拓展，单 skill 也预留生长点 |
| 弱主题分组 | 留扁平轨，用标签表达 | characterization 等成员内部互不依赖，打包无收益 |
| 清单形态 | 瘦清单 + 文件共享，执行配置留 SKILL.md | 镜像按 skill 走去不了重；机型去重是独立清理（§7）|
| 范围 | 仅 matmaster-evo 后端 | 前端 UX、用户自建 skill 另开 spec |

保留上版对富清单方案的否决结论：镜像按 skill 不按 plugin 走；`c64_m256_cpu` 机型跨 plugin 共享，该进全局目录而非塞进单个清单；版本可复现性由 git 提供。

### 3.1 目录布局（兄弟根）

`matmaster/skills/` 原位不动，新增兄弟根 `matmaster/plugins/`：

```
matmaster/
├── skills/                        # 扁平轨：11 个独立 skill + 包代码，布局不变
│   ├── __init__.py  registry.py  settings.py
│   ├── builtin_tags.yaml          # 缩减为扁平轨标签目录（§3.7）
│   └── <skill>/SKILL.md
└── plugins/                       # plugin 轨：15 个 plugin
    └── <plugin>/
        ├── plugin.yaml            # 瘦清单（§3.2）
        ├── skills/<skill>/SKILL.md
        ├── references/            # plugin 级共享参考（可选）
        └── scripts/               # plugin 级共享脚本（可选）
```

- `matmaster.skills` 包代码（registry.py、settings.py）不挪不改名，无 import 变更。
- plugin 目录名取连字符形式（`quantum-espresso`），skill 目录名保持原样（`quantum_espresso`），二者在不同命名空间，不冲突。
- 单 skill 软件 plugin 同构：`plugins/cp2k/skills/cp2k/SKILL.md`，以后 `plugins/cp2k/skills/cp2k-postprocess/` 直接进来，共享 `plugins/cp2k/references/`。

### 3.2 plugin.yaml（瘦清单）

成员**靠扫描 `skills/` 子目录得到**（目录即真相，不在清单里重列，避免漂移）。清单只放：

```yaml
name: abacus
category: simulation
description: "ABACUS DFT 软件族：输入准备 / 提交 / 解析 + PyATB 紧束缚后处理"
```

- **无 version 字段**：plugin 的某个版本 = 该目录的 git 状态，不造第二个真相源。
- `mcp_server`、`depends_on` 与执行配置（镜像 / 机型 / cmd）都留在各 SKILL.md 不动。
- 清单从落地第一天就有真实消费者：builtin_skills_sync 用它取 plugin 成员的 category/tags（§3.7）。
- 迁移时 `description` 由脚本生成占位串，后续人工补全；这是产物占位，不是本 spec 的未决项。

### 3.3 双轨边界

plugin 轨 15 个（30 skill）：

| 类型 | plugin | category | 成员 skill |
|------|--------|----------|-----------|
| 簇 | atomic-structure-ops | structure-modeling | atomic-structure, inspect-atomic-structure, build-crystal-from-params, transform-atomic-structure, assemble-atomic-structure, operate-molecular-crystal, sample-atomic-structures |
| 簇 | structure-search | structure-modeling | mcp-mat-struct-db, retrieve-structure（后者 depends_on 前者，依赖内化）|
| 簇 | abacus | simulation | abacus, pyatb |
| 簇 | mlips | simulation | mlips, aissq-explorer |
| 簇 | data-mining | analysis | mcp-mat-compdart, composition-optimization（后者 depends_on 前者，依赖内化）|
| 簇 | task-planning | workflow-system | plan-writer, plan-checker, plan-executor, spec-writer, acceptance-writer, stack-checker |
| 软件 | vasp / cp2k / quantum-espresso / abinit / pyscf / orca / lammps / gromacs / gpumd | simulation | 各含同名 skill 1 个 |

扁平轨 11 个：pxrd-refinement, checkcif-validator, mcp-mat-xrd, mcp-mat-nmr, mcp-mat-electron-microscope, data-analysis, mcp-mat-doc, proposal-review, skill-manager, image-manager, session-analyzer。

三个物理子目录解散：

- `lazymcp/`：struct-db → structure-search，compdart → data-mining，doc / xrd / nmr / electron-microscope → 扁平根顶层。**plugin 可含混合类型**（operator 与 mcp-loader 同处一个 plugin），预期且更有用。
- `planner/` + 顶层 `plan-executor` → task-planning plugin。
- `playground-skills/`：剪枝后（§4）pxrd-refinement、checkcif-validator 上移扁平根，composition-optimization 进 data-mining。

### 3.4 `${PLUGIN_DIR}` 资源解析

复用现有 `${SKILL_DIR}` 注入机制（替换点在 skill_tool.py 单点），平行加入 `${PLUGIN_DIR}`（所属 plugin 根目录）：

- `${SKILL_DIR}/references/x.md` → skill 私有资源
- `${PLUGIN_DIR}/references/y.md` → 同 plugin 共享资源

无优先级魔法，由 skill 作者显式选择路径。扁平 skill 无 plugin 归属，正文出现 `${PLUGIN_DIR}` 属作者错误，保持原样不替换，错误醒目可见。

### 3.5 注册门控（后端配置级启停）

`config/plugins.yaml`：

```yaml
disabled_plugins: []          # 留空即全部启用；如 [gpumd] 则禁用该 plugin
```

- 实现方式：把 `disabled_plugins` 展开成成员 skill 名，灌进现有 disabled 通道——registry 缓存键已含禁用名单三元组，缓存失效自动正确，注册侧几乎无新逻辑。文件加载沿用现有 config 读取模式（与 `config/mcp.yaml` 同级同模式），不另造加载器。
- 扁平 skill 沿用现有 skill 级禁用机制（config + 各 root `.settings.json`），两轨各一套，互不纠缠。
- **depends_on 条款**：门控时检测启用 skill 的 `depends_on` 是否指向被禁 plugin 的成员，命中则打 warning 日志，不阻断（运行时缺失本就静默跳过，此处把静默变可见）。本轮清单已内化两条主依赖边；剩余跨界边：composition-optimization → mcp-mat-struct-db（跨 plugin），composition-optimization 与 retrieve-structure → mcp-mat-doc（plugin → 扁平）。

### 3.6 列表分组渲染

可用 skill 列表（系统提示中的枚举）改为：扁平 skill 逐个列出，plugin 成员归组在所属 plugin 名下展示。纯展示变更，直接回应动机 1 的相互关系无处表达；scanner 命中、载入、激活逻辑零变化。

### 3.7 builtin_tags 缩减与 sync 双根改造

- `builtin_tags.yaml` 缩减为**扁平轨标签目录**：只留 11 个扁平 skill，保持 `categories → groups → skills` 结构（characterization、system-tools 等弱主题分组以标签形式存活）。
- `builtin_skills_sync.py` 改造：扫描双根；plugin 成员的 category 取自 plugin.yaml、group 即 plugin 名；扁平 skill 取自缩减后的 builtin_tags；`_SKILLS_ROOT` 单根常量改双根。同步 payload 结构不变，tools-server 无感。

## 4. playground 剪枝

13 个保 3 删 10，与上版一致。

**保留去向：** pxrd-refinement → 扁平，checkcif-validator → 扁平，composition-optimization → data-mining plugin。

**删除（10）：** compliance-guardian、deep-survey、lit-data-organizer、manuscript-scribe、md-analysis、poly-forcefield、poly-generator、result-analysis、tasker-polar-surface、vaspkit-postprocess。

**连锁影响：** polymer-modeling 分组随 poly-generator 消亡；vasp、gromacs、general-data-analysis、literature、academic-writing 各缩回单 skill——前两者作为计算软件仍升格 plugin，后三者的幸存 skill（data-analysis、mcp-mat-doc、proposal-review）留扁平。

## 5. 注册总账

51 现存 − 10 删除 = 41 注册：30 个经 15 个 plugin 入轨，11 个走扁平轨。

## 6. 迁移与死代码清理

遵循迁移而非兼容、外部脚本、主代码不留兼容兜底。

### 6.1 迁移脚本 `scripts/migrate_to_plugins.py`（一次性，跑完即弃）

1. **剪枝**：删除 §4 列出的 10 个 skill 目录。
2. **入轨**：按 §3.3 清单移动 30 个 skill 到 `plugins/<plugin>/skills/<skill>/`；lazymcp 4 个幸存者与 playground 2 个幸存者上移到 `skills/` 顶层；其余 5 个扁平 skill 原位不动。skill 名全仓唯一，定位无歧义。
3. **生成清单**：写 15 个 `plugin.yaml`（name、category、description 占位）。
4. **缩减 builtin_tags.yaml** 至 11 个扁平 skill。
5. **剥除 skill_type**：从全部 SKILL.md frontmatter 移除该字段。
6. **残留清扫**：被删 skill 名与旧路径字面量的全仓 sweep。已知硬引用三处：gromacs/SKILL.md 引 md-analysis、atomic-structure/SKILL.md 引 tasker-polar-surface、tests/evaluation/test_devshell_agent_sdk_tools.py 引 result-analysis 路径。

### 6.2 loader / registry / sync 改造

- registry：增加 plugins 根扫描；plugin 归属判定为 skill 目录祖先中存在 plugin.yaml。`rglob` 发现机制保持布局无关，用户自建 skill root（本地/远程，扁平布局）天然兼容，不存在双模式问题。
- 门控：§3.5 展开式实现。
- skill_tool：`${PLUGIN_DIR}` 替换。
- 列表渲染：§3.6 分组。
- builtin_skills_sync：§3.7 双根。
- 涉及文件实现计划阶段精确定位（已知：`matmaster/skills/registry.py`、`matmaster/core/skill_registry_cache.py`、`matmaster/tools/builtin/skill_tool.py`、`src/services/skill_registry_factory.py`、`src/services/builtin_skills_sync.py`、可用列表渲染处）。

### 6.3 死代码删除

skill_type 全链删除：`SkillTypeLiteral`、`_parse_skill_type`、`SkillMetaInfo.skill_type` 字段、known-fields 表项（均在 `matmaster/skills/registry.py`）、相关测试断言，加上 §6.1 的 frontmatter 剥除。`mcp_server` 已足以标识 loader 性质。

## 7. 范围外 / 后续

- **前端 plugin 浏览/启停 UX**（scimaster-bohr-chat）：另开 spec。
- **用户自建 skill 如何归属 plugin**（NAS 同步、每用户启停）：另开 spec。
- **跨 plugin 机型去重**：`c64_m256_cpu` 实际出现在 11+ 处（不止上版统计的 6 处，另含 abacus、vasp、gromacs、lammps、pxrd-refinement 及 bohrium_tool.py 默认值、config.yaml、测试），抽全局 reference 是解耦的独立小清理。
- **两级路由选择 / 兄弟联动激活**：明确不做（运行时选择逻辑保持不变）。

## 8. 测试与代码量约束

- **不新增测试文件**；受影响测试随布局变更更新，实现计划阶段全量定位（已知至少 17 个文件引用 skill 路径/名单/frontmatter，不止上版列出的 5 处）。
- 净代码量预期持平或下降：删除（10 个 skill、skill_type 全链、builtin_tags 约 3/4 条目）抵消新增（plugin 扫描与归属、门控展开、`${PLUGIN_DIR}`、列表分组、sync 双根）。
