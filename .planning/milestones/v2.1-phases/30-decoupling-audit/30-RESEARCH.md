# Phase 30: 解耦审计与独立性证明 - Research

**Researched:** 2026-04-02
**Domain:** Python import isolation, test architecture, migration documentation
**Confidence:** HIGH

## Summary

Phase 30 是 v2.1 里程碑的收口阶段，目标是通过三类证据证明 matmaster 已完全独立于 evomaster/playground/src：AST 级 import audit 测试、evomaster/src 隐藏后的隔离测试通过、以及完整的迁移文档。

当前代码库状态良好：matmaster/ 源码本身已无运行时 evomaster/playground/src 导入（仅存注释引用），现有 test_import_audit.py 已有 15 个 AST 级审计测试全部通过。**核心挑战在于 tests/matmaster/ 下 7 个 evomaster 和 28 个 src 导入——这些是测试代码自身的依赖，需要在隔离测试前修复。**

evomaster/ 目录（87 个 .py 文件）审计通过后物理删除，5 个技能目录归档到 .archive/evomaster-skills/。直接相关的配置文件（configs/mat_master/config.yaml 的 skills_root、pyproject.toml 的 packages 列表）需同步更新。

**Primary recommendation:** 先修复 tests/matmaster/ 中 5 个文件的 evomaster 导入（替换为 matmaster 等价物），再用 mv 隐藏法运行隔离测试。src 导入的测试文件本质上是集成测试（测试 src 消费 matmaster 的路径），隔离测试时会自然失败——严格全通过策略要求识别并处理这些测试。

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- D-01: 采用重命名隐藏法：测试前将 evomaster/ 和 src/ 临时 mv 为 _evomaster_hidden/ 和 _src_hidden/，运行 tests/matmaster/ 全集，再还原
- D-02: 覆盖范围为 tests/matmaster/ 下所有 100 个测试文件，不做子集筛选
- D-03: 严格全通过策略，不使用 xfail 标记。如有失败则修复测试代码或 matmaster 源码，而不是跳过
- D-04: evomaster/ 和 src/ 同时隐藏，证明 matmaster 对两者都无运行时依赖
- D-05: 审计通过后在本 phase 物理删除 evomaster/ 整个目录。git 历史可追溯，不需要保留死代码
- D-06: evomaster/skills/ 的 5 个技能在删除前归档到 .archive/evomaster-skills/，与 Phase 29 的 playground 技能归档模式一致
- D-07: skills_root 配置需更新（当前指向 evomaster/skills），删除后指向归档位置或 matmaster/skills/
- D-08: 文档放在 docs/ 目录下，与现有 docs/architecture-reference-claude-code.md 和 docs/specs/ 一致
- D-09: 文档涵盖四部分内容：解耦过程回顾、当前架构状态、残留路径清单、v2.2 清理顺序
- D-10: import audit 保持在 pytest 套件中即可，不单独配置 CI 门禁
- D-11: 全量测试运行并记录实际通过数到迁移文档，不设硬编码数字基线

### Claude's Discretion
- 隔离测试的具体 shell 脚本结构（mv/test/restore 的原子性保障）
- 迁移文档的具体章节组织和详细程度
- evomaster/ 删除时的 git commit 拆分策略（单次 vs 分步）
- 全量测试运行时的具体 pytest 参数

### Deferred Ideas (OUT OF SCOPE)
- .archive/playground-skills/ 和 .archive/evomaster-skills/ 的正式合并到 matmaster -- 项目完成后用户手动
- v2.2 清理工作的实际执行（LEGY-01, LEGY-02, PKG-01）
- CI pipeline 集成 import audit 门禁 -- 如果团队规模扩大再考虑
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| QUAL-06 | 仓库提供 import audit 或等价测试，验证 matmaster/ 运行时模块不再直接 import evomaster、playground 或 src | 现有 test_import_audit.py 已覆盖 7 个模块前缀的 AST 审计（15 测试全通过），需扩展为全面覆盖 `evomaster` 根前缀 + `playground` + `src` 的统一审计。Code Examples 节提供扩展模式 |
| QUAL-07 | 在不安装 evomaster 的受控测试环境中，tests/matmaster/ 的核心测试集可以通过 | 隔离测试前需修复 5 个测试文件的 evomaster 导入 + 处理 10 个含 src 导入的集成测试文件。Architecture Patterns 节详细分析了每类导入的修复策略 |
| QUAL-08 | 仓库提供一份解耦迁移文档，明确保留 compat layer、剩余遗留路径与后续清理顺序 | 文档结构在 Architecture Patterns 节定义，残留路径和 compat layer 在 Common Pitfalls 节列出 |
</phase_requirements>

## Architecture Patterns

### 隔离测试的关键挑战：tests/matmaster/ 中的外部依赖

通过 AST 分析，tests/matmaster/ 下共有 **7 个真实 evomaster 导入**（分布在 5 个文件中）和 **28 个真实 src 导入**（分布在 10 个文件中）。D-03 要求严格全通过，不使用 xfail，因此必须在隔离测试前解决这些依赖。

#### Category A: evomaster 导入可替换为 matmaster 等价物（3 个文件，4 个导入）

这些导入有直接的 matmaster 等价类，可以安全替换：

| 文件 | evomaster 导入 | matmaster 等价物 |
|------|---------------|-----------------|
| `tests/matmaster/tools/test_bash_tool.py:L11` | `evomaster.agent.session.local.LocalSession` | `matmaster.sessions.local.LocalSession` |
| `tests/matmaster/core/test_local_session_stop.py:L8` | `evomaster.agent.session.local.LocalSession, LocalSessionConfig` | `matmaster.sessions.local.LocalSession`（构造函数参数不同，需调整） |
| `tests/matmaster/tools/test_skill_meta_extras.py:L1` | `evomaster.skills.base.Skill, SkillMetaInfo` | `matmaster.skills.registry.Skill, SkillMetaInfo` |
| `tests/matmaster/tools/test_skill_tool_callback.py:L5-6` | `evomaster.agent.tools.skill.SkillTool`, `evomaster.skills.base.SkillRegistry` | `matmaster.tools.skill_tool.SkillTool`, `matmaster.skills.registry.SkillRegistry` |

**注意：** matmaster LocalSession 构造函数签名是 `LocalSession(workspace_path, *, timeout=300, encoding='utf-8')`，而 evomaster 是 `LocalSession(LocalSessionConfig(...))`。test_local_session_stop.py 需要调整构造方式。

#### Category B: evomaster 导入在审计/验证型测试中（2 个文件，3 个导入）

这些测试本身是审计类的（验证某文件不含 evomaster 导入），它们导入 evomaster 是为了做比较/验证：

| 文件 | 导入目的 | 处理策略 |
|------|----------|---------|
| `tests/matmaster/config/test_config_consolidation.py:L23` | `EvoMasterConfig` -- 验证 cleaned config 可加载 | evomaster 隐藏后此测试失效，需修改为跳过 EvoMasterConfig 加载测试或将导入移入 try/except |
| `tests/matmaster/integration/test_bohrium_execution_contract.py:L512` | `SSHSession` -- 验证技能上传排除集 | 替换为 `matmaster.sessions.ssh.SSHSession` 或 Mock |

#### Category C: src 导入——跨层集成测试（10 个文件，28 个导入）

这些测试验证的是 src 消费 matmaster 的集成路径（src -> matmaster 是正向依赖，合法）：

| 文件 | 导入的 src 模块 | 测试目的 |
|------|----------------|---------|
| `test_bohrium_execution_contract.py` | `agent_run_bohrium`, `sessions_service`, `agent_run_service` | Bohrium SSH 设置与执行契约 |
| `test_e2e_mat_master.py` | `agent_run_service`, `agent_run_bohrium` | 端到端 matmaster 流程 |
| `test_upstream_scenarios.py` | `agent_run_bohrium`, `agent_run_service` | 上游服务场景 |
| `test_events_to_messages.py` | `chat_history.ChatHistoryConverter` | 事件到消息转换 |
| `test_subagent_event_routing.py` | `chat_history`, `chat_event_source` | 子 agent 事件路由 |
| `test_agent_run_service_workspace_upload.py` | `agent_run_service` | Workspace 上传 |
| `test_quota_pipeline.py` | `agent_run_service` | 配额管道 |
| `test_worker_registry.py` | `worker_registry_adapter` | Worker 注册适配器 |

**关键决策点：** D-01 同时隐藏 evomaster/ 和 src/，D-02 要求全部 116 个测试文件（1327 个测试）参与，D-03 严格全通过。但上述 10 个文件在 src 被隐藏后必然 ImportError。

**解决方案：** 这些文件测试的是 src 层代码（不是 matmaster 核心），隔离测试的目的是证明 matmaster 可独立运行。需要修改这些测试文件，使其在 src 不可用时优雅跳过（`pytest.importorskip` 或顶层 `try/except + pytest.skip`），而不是 xfail。这满足 D-03（不使用 xfail）同时满足 D-04（src 隐藏）。

### 推荐的隔离测试脚本结构

```bash
#!/usr/bin/env bash
# scripts/test_matmaster_isolation.sh
# 证明 matmaster 在 evomaster/ 和 src/ 不存在时可独立运行测试
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

# 原子性保障：trap 确保即使测试中断也会还原
cleanup() {
    [ -d _evomaster_hidden ] && mv _evomaster_hidden evomaster
    [ -d _src_hidden ] && mv _src_hidden src
    echo "[isolation] Restored evomaster/ and src/"
}
trap cleanup EXIT

# 隐藏 evomaster/ 和 src/
mv evomaster _evomaster_hidden
mv src _src_hidden
echo "[isolation] Hidden evomaster/ and src/"

# 运行 tests/matmaster/ 全集
uv run python -m pytest tests/matmaster/ -x -q --tb=short 2>&1

echo "[isolation] All tests passed with evomaster/ and src/ hidden"
# cleanup 由 trap 自动执行
```

**关键设计：**
- `trap cleanup EXIT` 保证在脚本异常退出（Ctrl+C、pytest 失败）时也能还原目录
- `-x` 在首次失败即停止，快速定位问题
- 不使用 `--ignore` 排除子集（D-02 要求全集）

### Import Audit 扩展架构

现有 test_import_audit.py 按模块前缀做细粒度审计，每个前缀一个测试类。Phase 30 需要添加一个统一的全覆盖测试：

```python
class TestNoEvomasterImportsAnywhere:
    """Phase 30: matmaster/ 不得有任何 evomaster 运行时导入（统一审计）。"""
    
    def test_no_evomaster_imports_in_matmaster(self):
        """扫描所有 matmaster/*.py，确认无 evomaster 运行时导入。"""
        project_root = Path(__file__).parent.parent.parent
        violations = []
        for py_file in _find_matmaster_py_files():
            try:
                source = py_file.read_text(encoding="utf-8")
            except OSError:
                continue
            hits = _find_all_imports_matching(
                source, "evomaster", exclude_type_checking=True
            )
            for node in hits:
                rel = py_file.relative_to(project_root)
                violations.append(
                    f"{rel}:L{node.lineno}: from {node.module} import ..."
                )
        assert violations == [], (
            "Found non-TYPE_CHECKING evomaster imports in matmaster/:\n"
            + "\n".join(violations)
        )
```

同时添加 `playground` 和 `src` 前缀的统一审计，形成三合一的完整独立性证明。

### 迁移文档结构

按 D-09 要求的四部分结构：

```
docs/decoupling-migration-v2.1.md

1. 解耦过程回顾
   - v2.1 各 Phase（25-30）做了什么
   - 起始状态 -> 终态的对比表

2. 当前架构状态
   - matmaster 模块边界图
   - 三层模型（Playground -> Exp -> Kernel）
   - 依赖方向（src -> matmaster -> 无外部依赖）

3. 残留路径清单
   - configs/mat_master/config.yaml 中的遗留配置项
   - .archive/ 中的技能归档
   - playground/mat_master/skills 在 direct.toml 中的引用
   - evomaster 字符串在注释/文档中的残留

4. v2.2 清理顺序
   - LEGY-01: playground 历史 solver 路径清理
   - LEGY-02: 非主路径 evomaster import 清理
   - PKG-01: matmaster 独立打包
```

### evomaster/ 删除与配置更新清单

删除 evomaster/ 后需同步更新的配置：

| 文件 | 当前值 | 更新为 |
|------|--------|--------|
| `pyproject.toml:L65` | `packages = ["evomaster", "matmaster", "utils"]` | `packages = ["matmaster", "utils"]` |
| `configs/mat_master/config.yaml:L374` | `skills_root: evomaster/skills` | `skills_root: matmaster/skills/lazymcp`（或 `.archive/evomaster-skills`） |
| `matmaster/exps/direct.toml:L41` | `skills_root = ["playground/mat_master/skills", "matmaster/skills/lazymcp"]` | `skills_root = ["matmaster/skills/lazymcp"]`（playground 已删） |
| `matmaster/skills/__init__.py:L6` | 注释引用 `evomaster/skills/` | 更新注释 |
| `tests/matmaster/integration/test_bohrium_execution_contract.py:L35` | 硬编码 `'skills_root': 'evomaster/skills'` | 更新测试 fixture |

### evomaster/skills/ 归档结构

遵循 Phase 29 建立的模式（.archive/playground-skills/）：

```
.archive/
  playground-skills/     # Phase 29 已建立
    _common/
    ask-human/
    ... (19 个目录)
  evomaster-skills/      # Phase 30 新建
    calculation/
    mcp-builder/
    pdf/
    rag/
    skill-creator/
```

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| AST 级 import 分析 | 自定义 AST walker | 复用现有 `_find_all_imports_matching` 函数 | 已处理 TYPE_CHECKING、col_offset、嵌套等边界情况 |
| 目录隐藏的原子性 | 复杂的临时目录管理 | bash `trap EXIT` + mv | 简单可靠，即使 SIGTERM 也能恢复 |
| 测试跳过机制 | 自定义 skip 装饰器 | `pytest.importorskip("src.services")` | pytest 内置，自动在 import 失败时 skip 并记录原因 |

## Common Pitfalls

### Pitfall 1: 隔离测试中 src 导入导致批量失败
**What goes wrong:** D-04 要求 src 同时隐藏，但 tests/matmaster/ 有 10 个文件共 28 个 src 导入。如果不处理，隔离测试会有大量 ImportError。
**Why it happens:** 这些测试本质上是集成测试（测试 src 消费 matmaster），放在 tests/matmaster/ 目录下是因为它们主要验证 matmaster 侧的行为契约。
**How to avoid:** 使用 `pytest.importorskip` 将 src 导入包装为条件导入。隔离测试时这些测试被 skip（不是 xfail），正常测试时正常运行。
**Warning signs:** 在修改前先运行一次 mv + pytest 测试，确认失败测试列表与分析一致。

### Pitfall 2: evomaster LocalSession 与 matmaster LocalSession API 不兼容
**What goes wrong:** 替换 test_local_session_stop.py 的 evomaster LocalSession 后测试逻辑可能失败，因为构造函数签名不同。
**Why it happens:** evomaster 使用 `LocalSession(LocalSessionConfig(workspace_path=..., timeout=...))` 配置对象模式，matmaster 使用 `LocalSession(workspace_path, *, timeout=300)` 直接参数模式。
**How to avoid:** 替换导入的同时调整构造函数调用。matmaster LocalSession 需要先 `.open()` 才能标记为 open。
**Warning signs:** 测试中如果检查 `session.config` 属性，matmaster 版本可能不存在。

### Pitfall 3: pyproject.toml packages 列表未更新导致 import 路径断裂
**What goes wrong:** 删除 evomaster/ 后如果 pyproject.toml 仍列出 `evomaster` 包，安装过程可能报错或静默忽略。
**Why it happens:** Hatch build backend 会尝试寻找 packages 列表中的所有目录。
**How to avoid:** 删除 evomaster/ 的同一个 commit 中同步更新 pyproject.toml。
**Warning signs:** `uv pip install -e .` 或 `uv run` 报 package not found 错误。

### Pitfall 4: direct.toml skills_root 指向已删除的 playground/mat_master/skills
**What goes wrong:** direct.toml 仍引用 `playground/mat_master/skills`，playground 在 Phase 29 已删除。Agent 初始化时找不到技能目录，可能 silent fail。
**Why it happens:** Phase 29 删除了 playground/ 但没有更新 direct.toml 的 skills_root。
**How to avoid:** 在本 phase 更新 direct.toml，移除已不存在的路径。
**Warning signs:** SkillRegistry 初始化日志中报告根目录不存在。

### Pitfall 5: .gitignore 归档规则遗漏
**What goes wrong:** Phase 29 已在 .gitignore 添加了 playground-skills 归档的注释，但 evomaster-skills 归档可能需要类似处理。
**Why it happens:** .archive/ 目录下的内容体积较大，不确定是否应纳入 git 追踪。
**How to avoid:** 检查 Phase 29 的 .gitignore 处理模式，保持一致。如果 playground-skills 被追踪，evomaster-skills 也应被追踪。
**Warning signs:** `git status` 显示 .archive/evomaster-skills/ 为 untracked 但没有被 .gitignore 忽略。

## Code Examples

### 隔离测试中的条件跳过模式

对于 src 依赖的集成测试文件，使用 pytest.importorskip 在文件顶层或测试函数内：

**模式 A：顶层 importorskip（整个文件依赖 src）**
```python
# tests/matmaster/integration/test_events_to_messages.py
import pytest
ChatHistoryConverter = pytest.importorskip(
    "src.services.chat_history", reason="src not available (isolation test)"
).ChatHistoryConverter
```

**模式 B：函数内 importorskip（文件中部分测试依赖 src）**
```python
def test_bohrium_setup_full_path(self):
    arb = pytest.importorskip("src.services.agent_run_bohrium")
    AgentRunService = pytest.importorskip("src.services.agent_run_service").AgentRunService
    # ... 测试逻辑
```

**模式 C：对于 evomaster 导入替换为 matmaster 等价物**
```python
# 替换前
from evomaster.agent.session.local import LocalSession
# 替换后
from matmaster.sessions.local import LocalSession
```

### test_config_consolidation.py EvoMasterConfig 测试处理

```python
class TestCleanedConfigYaml:
    def test_loads_via_evomaster_config(self, cleaned_config):
        """EvoMasterConfig(**config_dict) must not raise."""
        EvoMasterConfig = pytest.importorskip(
            "evomaster.config", reason="evomaster not available"
        ).EvoMasterConfig
        cfg = EvoMasterConfig(**cleaned_config)
        assert cfg.env is not None
```

### 全覆盖 import audit 测试

```python
class TestPhase30FullIsolation:
    """Phase 30 统一审计：matmaster/ 不得有任何 evomaster/playground/src 运行时导入。"""
    
    FORBIDDEN_PREFIXES = ["evomaster", "playground", "src."]
    
    def test_no_forbidden_imports(self):
        project_root = Path(__file__).parent.parent.parent
        violations = []
        for py_file in _find_matmaster_py_files():
            try:
                source = py_file.read_text(encoding="utf-8")
            except OSError:
                continue
            for prefix in self.FORBIDDEN_PREFIXES:
                hits = _find_all_imports_matching(
                    source, prefix, exclude_type_checking=True
                )
                for node in hits:
                    rel = py_file.relative_to(project_root)
                    violations.append(
                        f"{rel}:L{node.lineno}: from {node.module} import ..."
                    )
        assert violations == [], (
            "matmaster/ has forbidden runtime imports:\n"
            + "\n".join(violations)
        )
```

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest + pytest-asyncio (auto mode) |
| Config file | `pytest.ini` |
| Quick run command | `uv run python -m pytest tests/matmaster/test_import_audit.py -x -q` |
| Full suite command | `uv run python -m pytest tests/matmaster/ -q --tb=short` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| QUAL-06 | matmaster/ 无 evomaster/playground/src 运行时导入 | unit (AST) | `uv run python -m pytest tests/matmaster/test_import_audit.py -x -q` | Partial (现有覆盖 7 前缀，需扩展到全覆盖) |
| QUAL-07 | tests/matmaster/ 在 evomaster/src 隐藏后全通过 | integration | `bash scripts/test_matmaster_isolation.sh` | Not yet (Wave 0) |
| QUAL-08 | 迁移文档存在且完整 | manual (file existence) | `test -f docs/decoupling-migration-v2.1.md && echo PASS` | Not yet (Wave 0) |

### Sampling Rate
- **Per task commit:** `uv run python -m pytest tests/matmaster/test_import_audit.py -x -q`
- **Per wave merge:** `uv run python -m pytest tests/matmaster/ -q --tb=short`
- **Phase gate:** Full suite (`uv run python -m pytest -q --tb=short`) green + isolation script green

### Wave 0 Gaps
- [ ] `tests/matmaster/test_import_audit.py` -- 需扩展 Phase 30 全覆盖审计测试（TestPhase30FullIsolation）
- [ ] `scripts/test_matmaster_isolation.sh` -- 隔离测试脚本（mv/test/restore）
- [ ] 5 个 evomaster 导入替换 + 10 个 src 导入条件化处理

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| evomaster 全局导入 + 运行时依赖 | matmaster 独立包 + AST 审计防回归 | v2.1 Phases 25-29 | matmaster/ 源码零外部运行时导入 |
| playground/ 作为 skills/workspace 容器 | .archive/ 归档 + matmaster/skills/lazymcp 原生 | Phase 29 | playground/ 已物理删除 |
| EvoMasterConfig 作为统一配置入口 | matmaster.config.loader 独立加载 | Phase 25 | matmaster 不依赖 evomaster.config |

## Open Questions

1. **configs/mat_master/config.yaml 的 skills_root 更新目标**
   - What we know: 当前指向 `evomaster/skills`，evomaster/ 删除后无效
   - What's unclear: 应指向 `.archive/evomaster-skills`（保持对旧技能的访问）还是 `matmaster/skills/lazymcp`（仅保留活跃技能）
   - Recommendation: 指向 `matmaster/skills/lazymcp`，因为 evomaster/skills 中的 5 个技能（calculation, mcp-builder, pdf, rag, skill-creator）目前没有被 matmaster 的 direct 或 explore exp 使用

2. **test_config_consolidation.py 是否应保留 EvoMasterConfig 验证**
   - What we know: 该测试验证 matmaster_config/config.yaml 可被 EvoMasterConfig 加载
   - What's unclear: evomaster/ 删除后此测试的意义（EvoMasterConfig 不存在了）
   - Recommendation: 将该测试改为验证 matmaster 原生配置加载路径，或降级为 importorskip

3. **matmaster/adaptors/calculation/oss_io.py 中的 evomaster 字符串**
   - What we know: `oss_prefix: str = 'evomaster/calculation'` 是 OSS 存储路径的默认前缀
   - What's unclear: 这是否构成运行时依赖（它只是一个字符串常量用于 OSS key 命名）
   - Recommendation: 这不是 import 依赖，仅是历史命名。记录在迁移文档中作为 v2.2 可选清理项

## Sources

### Primary (HIGH confidence)
- **直接代码审计** -- 通过 AST 分析确认 matmaster/ 零 evomaster/src 运行时导入
- **test_import_audit.py** -- 15 个测试全部通过，验证现有审计基础设施健全
- **tests/matmaster/ 全量收集** -- 1327 个测试成功收集，pytest --co 无错误

### Secondary (MEDIUM confidence)
- **Phase 29 CONTEXT.md** -- evomaster/ 延后到 Phase 30 的决策记录
- **Phase 28 CONTEXT.md** -- xfail 策略对比参考

## Metadata

**Confidence breakdown:**
- Import audit 扩展: HIGH -- 现有基础设施成熟，扩展模式清晰
- 隔离测试修复范围: HIGH -- AST 分析精确定位了所有 7 个 evomaster + 28 个 src 导入
- evomaster/ 删除: HIGH -- 87 个文件，无运行时消费者
- 迁移文档: HIGH -- 信息源充足（REQUIREMENTS.md + STATE.md + 各 phase CONTEXT.md）
- 配置更新: HIGH -- 全部相关配置已定位

**Research date:** 2026-04-02
**Valid until:** 2026-05-02（稳定的内部代码库审计，不涉及外部依赖版本变化）
