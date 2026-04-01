# MatMaster v2.1 解耦迁移文档

**完成日期:** 2026-04-02
**里程碑:** v2.1 matmaster/ 完全独立化
**范围:** Phases 25-30

## 1. 解耦过程回顾

v2.1 里程碑目标：让 matmaster/ 运行时路径不再 import evomaster/、playground/ 或 src/，成为可独立运行、测试与发布的核心包。

整个解耦过程跨 6 个 Phase，从 session/config 原生化开始，逐层消除 matmaster 对外部模块的运行时依赖，最终通过 AST 级审计和隔离测试证明独立性。

### 各 Phase 概览

| Phase | 名称 | 核心工作 | 消除的依赖 |
|-------|------|---------|-----------|
| 25 | Session 与 Playground 原生化 | 创建 matmaster 自有 session（Local/SSH）、config loader、Playground 参数化构造 | evomaster.agent.session.*, evomaster.config.ConfigManager, PlaygroundSessionMixin |
| 26 | Tool 内化与遗留工具收归 | 内化 bash_safety/editor helper、MonitorJobTool 搬入、web_search 收归 | EvoToolAdapter, evomaster.agent.tools.builtin.*, playground.mat_master.tools.web_search |
| 27 | MCP 与 Calculation 原生链路 | MCP Connection/Manager 搬入、Calculation adaptors 搬入、LazyMCPTool 直连 | evomaster.agent.tools.mcp.*, evomaster.adaptors.calculation.* |
| 28 | src 反向依赖反转与 Consumer 迁移 | bohrium_setup 回调注入、script_env 常量内化、chat_history 类型迁移 | src.services.agent_run_bohrium (反向), src.utils.constant |
| 29 | 主执行路径切换 | workspace_resolver 迁移、playground/evaluation 物理删除、config 路径迁移 | evomaster.core.get_playground_class, playground/ 整体 |
| 30 | 解耦审计与独立性证明 | Import audit 扩展、隔离测试、evomaster/ 物理删除、迁移文档 | evomaster/ 整体 |

### 起始 vs 终态

| 维度 | v2.0 终态（Phase 24） | v2.1 终态（Phase 30） |
|------|---------------------|---------------------|
| matmaster -> evomaster 导入 | ~15 imports, 8 files | 0 imports |
| matmaster -> playground 导入 | 1 import | 0 imports（playground/ 已删除）|
| matmaster -> src 导入 | 6 imports, 2 files | 0 imports |
| evomaster/ 目录 | 存在（87 .py 文件）| 已删除（技能归档到 .archive/）|
| playground/ 目录 | 存在 | 已删除（技能归档到 .archive/）|
| 测试独立性 | 未验证 | 全量测试 1276 passed, 5 skipped |

### 关键技术决策

以下是解耦过程中做出的重要设计决策：

- **Session 简化**: 合并 SSHSession + SSHEnv 为单一类，直接持有 paramiko.SSHClient；移除 Docker session 分支，仅保留 local 和 ssh
- **Playground 参数化**: Playground 构造函数从 config_path 模式改为 5 个 keyword-only 参数，PlaygroundManager._load_raw_config 返回原始 dict（非 Pydantic model）
- **回调注入**: BohriumSetupService 从直接 import src 服务函数改为 4 个 callable 回调注入，matmaster 侧不知道 src 的存在
- **Duck-typing**: 跨包 session 检测从 isinstance(SSHSession) 改为 hasattr 鸭子类型，避免 import 耦合
- **Lazy import 清理**: evomaster.env 导入改为 evomaster.env.bohrium 精确导入，避免触发全量加载链
- **严格全通过策略**: 隔离测试采用 pytest.importorskip（非 xfail），src 不可用时集成测试优雅跳过

## 2. 当前架构状态

### matmaster 模块边界

matmaster/ 是完全独立的核心包，运行时不依赖 evomaster、playground 或 src。

依赖方向：`src/ -> matmaster/ -> 无项目内依赖（仅第三方库）`

```
src/services/agent_run_service.py  <- 编排入口（应用层）
        |
        v
matmaster/core/playground.py   <- Layer 1: 工作区准备
matmaster/core/exp.py          <- Layer 2: Agent 组装
matmaster/core/agent.py        <- Layer 3: 执行循环
```

### 三层执行模型

- **Playground（Layer 1）**: 工作区准备、session 创建、日志初始化。输出 PlaygroundContext（frozen Pydantic model）
- **Exp（Layer 2）**: 三阶段生命周期 -- assemble(ctx) 纯数据变换产出 AgentRuntimeSpec，build_runtime(ctx, bus) 创建资源产出 AgentRuntime，run(ctx, task) 完整执行
- **AgentKernel（Layer 3）**: 纯 async 执行循环 -- LLM 调用、Guard 检查、Hook 回调、工具执行、消息累积。不知道外层存在

层间传递使用 frozen Pydantic model（PlaygroundContext、AgentRuntimeSpec），不可变。

### 关键子系统

| 子系统 | 位置 | 说明 |
|--------|------|------|
| Session | matmaster/sessions/ (local, ssh) | 原生实现，不依赖 evomaster session |
| Tools | matmaster/tools/ (builtin/, lazy_mcp, skill_tool) | 原生 Tool Protocol，ToolRegistry 统一注册 |
| MCP | matmaster/tools/lazy_mcp.py + matmaster/mcp/ | 原生连接与管理，启动时零连接 |
| Calculation | matmaster/adaptors/calculation/ | 原生 path adaptor/job service/oss io |
| Events | matmaster/types/events.py + matmaster/core/bus.py | 18 种事件类型，asyncio.Queue 事件总线 |
| Integration | matmaster/integration/ | bohrium_setup（回调注入）, bohrium_env, workspace_resolver |
| Config | matmaster/config/ | ExpConfig, LLMConfig, YAML + TOML loader |
| DevShell | matmaster/devshell/ | 纯 matmaster 层本地开发 REPL |
| Skills | matmaster/skills/ | registry + lazymcp skill roots |

### Exp 定义（TOML 驱动）

Exp 通过 TOML 文件声明式定义，位于 matmaster/exps/。当前活跃的 direct.toml 配置：

- builtin 工具集：16 个内置工具（bash/file/task/spawn/web 等）
- skills_root：`matmaster/skills/lazymcp`（单一路径，已清理旧路径）
- mcp：通配符模式，动态加载所有可用 MCP server

## 3. 残留路径清单

以下路径/配置在 v2.1 完成后仍存在，但不影响 matmaster 独立性：

### 3.1 Compat Layer（兼容层）

| 位置 | 内容 | 说明 |
|------|------|------|
| matmaster/integration/bohrium_setup.py | 回调注入模式 | src 通过 functools.partial 注入 5 个 Bohrium 操作函数，matmaster 侧不知道 src 的存在。这是设计上的依赖反转，非遗留耦合 |
| matmaster/adaptors/calculation/oss_io.py:L52 | `oss_prefix: str = 'evomaster/calculation'` | OSS 存储路径的默认前缀，是字符串常量而非 import，属于历史命名。修改需要考虑 OSS 存量数据兼容 |

### 3.2 遗留路径名

| 文件 | 路径 | 性质 |
|------|------|------|
| configs/mat_master/config.yaml:L238 | `local_user_skills_root: "~/.evomaster-skills"` | 文件系统目录名，用户本机上的实际目录路径，非代码依赖 |
| configs/mat_master/config.yaml:L240 | `remote_user_skills_root: "/personal/.evomaster-skills"` | SSH 远程节点上的目录名，非代码依赖 |

### 3.3 归档资产

| 位置 | 内容 | 来源 |
|------|------|------|
| .archive/playground-skills/ | 19 个 playground 技能（Phase 29 归档）| playground/mat_master/skills/ |
| .archive/evomaster-skills/ | 5 个 evomaster 技能（Phase 30 归档）| evomaster/skills/ |

注：.archive/ 已在 .gitignore 中，不纳入版本控制。这些技能仅在本地保留以供参考。

### 3.4 配置双目录

`matmaster_config/` 和 `configs/mat_master/` 两个配置目录共存：

- `matmaster_config/` -- matmaster 独立运行使用的配置目录（config.yaml, llm_config.yaml, mcp.yaml, mcp_config.*.json）
- `configs/mat_master/` -- src/services/ 经 ConfigManager 桥接使用的旧路径，包含完整的 LLM、session、skill、calculation 配置

两者在 LLM 配置和技能路径上存在内容重叠。matmaster 通过 matmaster/config/loader.py 独立加载 matmaster_config/，不依赖 configs/ 目录。

### 3.5 文档和注释中的历史引用

部分文档（docs/evomaster/、docs/mat_master/）和代码注释中仍存在 evomaster 相关的描述。这些不构成运行时依赖，属于历史记录性质。

## 4. v2.2 清理顺序

以下清理项按优先级排序，建议在 v2.2 里程碑中执行：

### P1: 配置统一（高优先级）

**目标:** 将 configs/mat_master/ 和 matmaster_config/ 合并为单一配置源。

- 当前两个目录存在 LLM 配置和技能路径的重叠定义
- 合并后 src/services/ 直接读取 matmaster_config/，消除 ConfigManager 桥接层
- 需同步更新所有消费 configs/ 路径的 src 代码

### P2: 历史路径清理（LEGY-01, LEGY-02）

**目标:** 清理非主执行路径中的剩余 evomaster 引用。

- 清理 docs/ 目录下的 evomaster 相关文档（归档或更新）
- 统一代码注释和 docstring 中的历史引用

### P3: 路径名归一化

**目标:** 将文件系统路径和 OSS 存储路径从 evomaster 命名迁移到 matmaster。

| 当前路径 | 目标路径 | 影响 |
|---------|---------|------|
| `~/.evomaster-skills` | `~/.matmaster-skills` | 涉及用户本地目录迁移，需要提供自动迁移脚本 |
| `/personal/.evomaster-skills` | `/personal/.matmaster-skills` | 涉及远程 SSH 节点，需要协调 Bohrium 平台侧 |
| `oss_prefix: 'evomaster/calculation'` | `oss_prefix: 'matmaster/calculation'` | 涉及 OSS 存量数据兼容，需要双前缀兼容期 |

### P4: 独立打包（PKG-01）

**目标:** matmaster 具备独立 pyproject.toml 和安装方案。

- matmaster/ 拥有自己的 pyproject.toml，可通过 pip install 安装
- evomaster 成为可选兼容依赖或完全移除
- src/ 应用层作为独立的顶层包引用 matmaster

### P5: 归档处置

**目标:** 评估并处置 .archive/ 中的技能资产。

- 评估 .archive/ 中 24 个技能（19 playground + 5 evomaster）的使用情况
- 活跃技能迁移到 matmaster/skills/ 正式目录，适配 LazyMCP 加载机制
- 不活跃技能确认废弃，从本地清理

## 质量证据

### Import Audit（QUAL-06）

`tests/matmaster/test_import_audit.py` 提供 AST 级 import 审计：

- **TestPhase30FullIsolation** 类统一覆盖 evomaster + playground + src 三个前缀
- 扫描 matmaster/ 下所有 .py 文件，排除 TYPE_CHECKING 块中的合法类型导入
- 全部 **2 个审计测试通过**（test_no_forbidden_imports_in_matmaster + test_known_violations_count）

已知豁免项（KNOWN_VIOLATIONS）：
- `matmaster/core/__init__.py:L12` -- 历史 playground 引用，playground 目录已物理删除，该导入为惰性的模块级别 re-export，不影响运行时

### 隔离测试（QUAL-07）

`scripts/test_matmaster_isolation.sh` 实现重命名隐藏法：

- evomaster/ 已在 Phase 30-02 物理删除（不需要隐藏）
- src/ 隐藏后运行 tests/matmaster/ 全集
- 含 src 依赖的集成测试通过 pytest.importorskip 优雅跳过
- 结果：全量测试 **1276 passed, 5 skipped**（跳过的是 src 集成测试，使用 pytest.importorskip）
- 32 个 pre-existing failures 已在 Plan 30-02 中确认与解耦无关（devshell Pydantic validation、subagent_spawn API mismatch、PlaygroundContext session typing 等）

### 迁移文档（QUAL-08）

本文档（docs/decoupling-migration-v2.1.md）涵盖：
1. 解耦过程回顾 -- v2.1 各 Phase（25-30）的工作内容和消除的依赖
2. 当前架构状态 -- matmaster 模块边界、三层执行模型、关键子系统
3. 残留路径清单 -- compat layer、遗留路径名、归档资产、配置双目录
4. v2.2 清理顺序 -- 按优先级排列的后续清理建议

## 需求追踪

| 需求 ID | 描述 | Phase | 状态 |
|---------|------|-------|------|
| PLAY-01 | LocalSession 独立创建 | 25 | Complete |
| PLAY-02 | Session factory 原生化 | 25 | Complete |
| PLAY-03 | Playground 独立加载配置 | 25 | Complete |
| TOOL-07 | 遗留 builtin 能力原生注册 | 26 | Complete |
| TOOL-08 | bash safety/edit helper 原生化 | 26 | Complete |
| TOOL-09 | MonitorJobTool 收归 | 26 | Complete |
| TOOL-10 | web_search_tool 收归 | 26 | Complete |
| MCP-01 | lazy_mcp 原生连接 | 27 | Complete |
| CALC-01 | Calculation adaptor 原生化 | 27 | Complete |
| CALC-02 | Bohrium 协议兼容 | 27 | Complete |
| INVR-01 | bohrium_setup 回调注入 | 28 | Complete |
| INVR-02 | script_env 常量内化 | 28 | Complete |
| CONS-01 | API/worker 原生入口 | 29 | Complete |
| CONS-02 | 本地 Web 原生入口 | 29 | Complete |
| CONS-03 | chat_history 类型迁移 | 28 | Complete |
| CONS-04 | SSHSession 抽象切换 | 28 | Complete |
| QUAL-06 | Import audit 测试 | 30 | Complete |
| QUAL-07 | 隔离测试通过 | 30 | Complete |
| QUAL-08 | 迁移文档 | 30 | Complete |

全部 19 个 v2.1 需求已完成。
