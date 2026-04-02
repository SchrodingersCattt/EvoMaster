# Phase 29: 主执行路径切换 - Research

**Researched:** 2026-04-01
**Domain:** 遗留代码删除 / 残余依赖清理 / 模块迁移
**Confidence:** HIGH

## Summary

Phase 29 的核心任务是三件事：(1) 物理删除 matmaster 不再使用的遗留目录 (playground/, evaluation/, run.py)，(2) 消除 matmaster 包对 evomaster 的最后 2 处 runtime import (bash_tool 的 evomaster LocalSession 分支 + monitor_job/_llm.py 的 evomaster ConfigManager/create_llm)，(3) 将 workspace_resolver 从 playground 迁移到 matmaster 侧。

代码库审计确认：agent_run_service.py 已完全使用 matmaster PlaygroundManager 作为入口，CONS-01 基本满足。src 侧对 playground 的唯一 runtime import 是 agent_run_bohrium.py 的 workspace_resolver，迁移后即可断开。matmaster 包内部对 evomaster 的 runtime import 精确为两处，均在函数级别 lazy import，清理方案明确。

**Primary recommendation:** 按「删除 -> 迁移 -> 清理 -> 配置修正 -> 测试更新」的顺序执行。先删大块死代码减少干扰，再做精确的代码修改。

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** 物理删除 `playground/` 整个目录、`run.py`、`evaluation/` 目录。git 历史可追溯，不需要保留死代码
- **D-02:** 一并删除对应测试：`tests/playground/`、`tests/evaluation/`、以及引用 playground 的 5 个测试文件（test_chat_history_reasoning_state.py、test_streaming_thought_protocol.py、test_ask_human_helpers.py、test_dialog_history_helpers.py、test_chat_event_source.py）
- **D-03:** `tests/test_workspace_resolver.py` 更新为从 matmaster 导入（不删除，因 workspace_resolver 迁移到 matmaster）
- **D-04:** CONS-02（本地 Web 调试后端走 matmaster 入口）标记为不适用（N/A）。本地调试以 DevShell 为准，不维护两套本地后端
- **D-05:** `playground/mat_master/core/workspace_resolver.py` 的 `get_remote_session_workspace_root` 和 `load_workspace_config_dict` 搬入 matmaster 侧。`src/services/agent_run_bohrium.py` 改为从 matmaster 导入
- **D-06:** 清理 `matmaster/tools/builtin/bash_tool.py:135` 的 evomaster LocalSession isinstance 分支，只保留 matmaster LocalSession 检查
- **D-07:** 清理 `matmaster/tools/builtin/monitor_job/_llm.py:67-68` 的 evomaster ConfigManager/create_llm 依赖，改用 matmaster 原生 llm_factory
- **D-08:** evomaster/ 目录不在本 phase 删除，留 Phase 30 审计后处理
- **D-09:** `playground/mat_master/skills/`（19 个技能目录 + _common）移到 `.archive/playground-skills/`，不迁移到 matmaster。项目完成后由用户手动合并或删除
- **D-10:** `evomaster/skills/`（5 个技能）保持不动，`skills_root` 配置保持 `evomaster/skills`（evomaster/ 本 phase 不删）

### Claude's Discretion
- workspace_resolver 在 matmaster 侧的具体模块位置（`matmaster/integration/` 或新建 `matmaster/workspace/` 均可）
- monitor_job/_llm.py 替换为 matmaster llm_factory 的具体适配方式
- `.archive/` 是否加入 .gitignore

### Deferred Ideas (OUT OF SCOPE)
- evomaster/ 目录删除 -- Phase 30 审计后处理
- evomaster/skills/ 的 5 个技能迁移到 matmaster/skills/ -- 随 evomaster/ 删除一起处理
- `.archive/playground-skills/` 技能的正式合并到 matmaster -- 项目完成后用户手动
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| CONS-01 | API/worker 主执行路径通过 matmaster 原生入口初始化 playground/exp/agent，不再使用 evomaster.core.get_playground_class | agent_run_service.py 已使用 matmaster PlaygroundManager；本 phase 清除 matmaster 内部对 evomaster 的最后 2 处 runtime import，使整条路径无 evomaster 依赖 |
| CONS-02 | 本地 Web 调试后端通过 matmaster 原生入口初始化 playground | D-04 标记为 N/A。matmaster/devshell/ 已是纯 matmaster 方案，零 playground 依赖。playground/ 删除后不影响任何当前使用的本地调试路径 |
</phase_requirements>

## Architecture Patterns

### 变更影响图

```
删除:
  playground/               # 整个目录 (含 mat_master/core/, service/, skills/, tools/ 等)
  run.py                    # evomaster 统一 CLI
  evaluation/               # 评估路径
  tests/playground/         # playground 测试
  tests/evaluation/         # evaluation 测试
  tests/test_chat_history_reasoning_state.py
  tests/test_streaming_thought_protocol.py
  tests/test_ask_human_helpers.py
  tests/test_dialog_history_helpers.py
  tests/test_chat_event_source.py

归档 (移动):
  playground/mat_master/skills/ -> .archive/playground-skills/

迁移 (复制到 matmaster 后随 playground/ 一起删除):
  playground/mat_master/core/workspace_resolver.py
    -> matmaster/integration/workspace_resolver.py (推荐位置，见下方分析)

修改:
  matmaster/tools/builtin/bash_tool.py          # 删除 evomaster LocalSession 分支
  matmaster/tools/builtin/monitor_job/_llm.py   # 替换 evomaster ConfigManager/create_llm
  src/services/agent_run_bohrium.py             # import 改为 matmaster 侧
  configs/mat_master/config.yaml                # session.local.working_dir 更新
  matmaster_config/config.yaml                  # session.local.working_dir 更新
  tests/test_workspace_resolver.py              # import 改为 matmaster 侧
  tests/matmaster/test_import_audit.py          # 移除 xfail + 新增 evomaster.config/utils 审计
  pyproject.toml                                # hatch packages 移除 playground, evaluation
```

### workspace_resolver 迁移位置分析

**推荐: `matmaster/integration/workspace_resolver.py`**

理由:
1. `matmaster/integration/` 已有 bohrium_setup.py、bohrium_env.py 等与外部环境交互的模块，workspace_resolver 属于同类（解析远程 workspace root 路径）
2. workspace_resolver 的调用者是 `src/services/agent_run_bohrium.py`（应用层 -> 核心层），符合 integration 层的桥接角色
3. 不需要新建额外目录层级
4. 仅迁移 `get_remote_session_workspace_root` 和 `load_workspace_config_dict` 两个函数 + 必要的内部辅助函数，不迁移 `resolve_workspace_path` 和 `WorkspaceResolution`（后者仅被 playground 内部使用，将随 playground 删除）

迁移范围（从原 workspace_resolver.py）:
- `_DEFAULT_REMOTE_SESSION_WORKSPACE_ROOT` 常量
- `_default_project_root()` -- 需要调整路径计算（原文件在 playground/mat_master/core/ 层级，新位置在 matmaster/integration/）
- `_mat_master_config_path()` -- 需要指向正确的 configs 路径
- `_load_workspace_config_from_file()` -- 原封不动
- `load_workspace_config_dict()` -- 原封不动
- `_mat_master_value()` -- 原封不动
- `_resolve_optional_path()` -- 原封不动
- `get_remote_session_workspace_root()` -- 原封不动

### monitor_job/_llm.py 适配方案

**问题:** `_get_llm_by_alias` 当前使用 evomaster 的同步 LLM (`BaseLLM._call`)，而 matmaster 的 `OpenAIProvider` 是异步的 (`AsyncOpenAI`)。`_call_llm_decision` 同步调用 `llm._call()`。

**推荐方案: 使用 matmaster config + OpenAI 同步 SDK 构建轻量同步客户端**

具体做法:
1. 用 `matmaster.config.loader.load_llm_config` 加载 `matmaster_config/llm_config.yaml`
2. 用 `LLMConfig.get_profile(alias)` 获取 `LLMProfileConfig`
3. 直接用 `openai.OpenAI`（同步客户端）构建请求，不经过 `OpenAIProvider`（它是 async 的）
4. monitor_job 的 LLM 调用场景极为简单（单次 chat completion，无流式、无工具），不需要完整的 Provider 抽象

```python
@lru_cache(maxsize=4)
def _get_llm_by_alias(alias: str | None = None):
    from matmaster.config.loader import load_llm_config
    from matmaster.config.llm import LLMConfig

    llm_config = load_llm_config(REPO_ROOT / 'matmaster_config' / 'llm_config.yaml')
    profile_key = alias or llm_config.default
    profile = llm_config.get_profile(profile_key)

    import openai
    return openai.OpenAI(
        api_key=profile.api_key,
        base_url=profile.base_url,
        timeout=profile.timeout,
    ), profile.model
```

然后在 `_call_llm_decision` 中:
```python
client, model = _get_llm_by_alias(llm_alias)
response = client.chat.completions.create(
    model=model,
    messages=[
        {'role': 'system', 'content': system_prompt},
        {'role': 'user', 'content': user_payload},
    ],
    temperature=0.0,
    timeout=timeout_seconds,
)
return _parse_llm_decision(response.choices[0].message.content)
```

这比包装 async OpenAIProvider 更简单、更直接。openai SDK 已是项目依赖。

### bash_tool.py 清理方案

当前代码 (L129-132):
```python
from evomaster.agent.session.local import LocalSession as _EvoLocal
from matmaster.sessions.local import LocalSession as _MatLocal

if isinstance(self._session, (_EvoLocal, _MatLocal)):
```

清理后:
```python
from matmaster.sessions.local import LocalSession as _MatLocal

if isinstance(self._session, _MatLocal):
```

逻辑影响: 移除后，只有 matmaster LocalSession 走 async 快速路径。evomaster LocalSession (如果还在某些遗留路径中使用) 会 fallback 到 `super().execute()` 同步路径。由于主执行路径已全面切换到 matmaster，这个 fallback 不影响功能。

### 配置文件修正

两个配置文件都有 `session.local.working_dir: "./playground/mat_master/workspace"`，删除 playground/ 后路径失效。

**修正方案:** 改为 `./workspace`。原因:
- `matmaster_config/config.yaml` 已有 `workspace: "./workspace"` 字段
- 这与 matmaster Playground 的独立配置理念一致
- 需要确保 `./workspace` 目录存在或在首次运行时自动创建

同时，docker.volumes 中的 `{"./playground/mat_master/workspace": "/workspace"}` 也需要更新为 `{"./workspace": "/workspace"}`。

### pyproject.toml 修正

当前 hatch packages 列表:
```toml
packages = ["evomaster", "evaluation", "playground", "matmaster", "utils"]
```

删除 playground 和 evaluation 后:
```toml
packages = ["evomaster", "matmaster", "utils"]
```

### Anti-Patterns to Avoid
- **归档前不 mkdir:** 移动 skills 到 `.archive/playground-skills/` 前必须先创建目标目录
- **先删 playground 再迁移:** workspace_resolver 必须先迁移到 matmaster 侧，验证通过后再删除 playground/（或同一提交中处理）
- **遗漏 lru_cache:** `_get_llm_by_alias` 和 `_load_workspace_config_from_file` 都有 `@lru_cache`。迁移时必须保留缓存装饰器以保持行为一致
- **忽略 _default_project_root 路径层级:** 原文件通过 `parents[3]` 找到项目根（playground/mat_master/core/ -> 3 级）。新位置 matmaster/integration/ 只需要 `parents[2]`

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| 同步 LLM 调用 | 用 asyncio.run 包装 matmaster OpenAIProvider | openai.OpenAI 同步客户端 | monitor_job 本身是同步上下文，直接用同步 SDK 最简单 |
| workspace config 加载 | 新写 YAML 解析逻辑 | 原封搬运 _load_workspace_config_from_file + yaml.safe_load | 已有 lru_cache 优化，行为经过验证 |
| LLM 配置解析 | 手动解析 YAML 到 dict | matmaster.config.loader.load_llm_config | 已有完整的 profiles/routes/default 解析逻辑 |

## Common Pitfalls

### Pitfall 1: 技能归档目录与 git
**What goes wrong:** `.archive/` 目录默认会被 git 跟踪，19 个技能目录的文件量不小
**Why it happens:** `.archive/` 不在 .gitignore 中
**How to avoid:** 决策点 -- 要么加入 .gitignore（归档纯本地保留），要么提交到 git（保留在仓库历史中，之后可批量删除）。推荐加入 .gitignore，因为 git 历史已经有完整记录
**Warning signs:** `git status` 显示大量新增文件

### Pitfall 2: import audit xfail 残留
**What goes wrong:** `TestNoEvomasterSessionImportsInMatmaster.test_no_evomaster_session_imports` 有 `@pytest.mark.xfail` 装饰器，bash_tool 清理后如果不移除 xfail，测试会变成 xpass 警告而非正常通过
**Why it happens:** xfail(strict=False) 在实际通过时只是标记 xpass，不会失败，但语义不清晰
**How to avoid:** bash_tool 清理后同步移除 xfail 装饰器
**Warning signs:** pytest 输出中出现 XPASS

### Pitfall 3: monitor_job _get_llm_by_alias 返回值变更
**What goes wrong:** 原来返回 evomaster BaseLLM 对象（有 `._call` 方法），新方案返回 `(openai.OpenAI, str)` 元组。如果只改了 `_get_llm_by_alias` 不改 `_call_llm_decision`，会在运行时崩溃
**Why it happens:** 接口不匹配
**How to avoid:** `_get_llm_by_alias` 和 `_call_llm_decision` 必须作为原子修改
**Warning signs:** `AttributeError: 'tuple' object has no attribute '_call'`

### Pitfall 4: 两个 config.yaml 都需要更新
**What goes wrong:** 只更新 `matmaster_config/config.yaml` 忘记 `configs/mat_master/config.yaml`，或反过来
**Why it happens:** 双配置目录共存，容易遗漏
**How to avoid:** 两处同步修改，或在修改时 grep 搜索 `playground/mat_master/workspace` 确认全部替换
**Warning signs:** session 初始化报路径不存在

### Pitfall 5: evaluation 目录下有非空测试
**What goes wrong:** evaluation/ 目录包含 4 个测试文件，tests/evaluation/ 也有测试，删除后 pytest 收集可能报 import error
**Why it happens:** 其他测试文件可能间接引用 evaluation 模块
**How to avoid:** 删除后运行 `pytest --collect-only` 验证无收集错误
**Warning signs:** `ModuleNotFoundError: No module named 'evaluation'`

## Code Examples

### workspace_resolver 迁移后的新位置示例

```python
# matmaster/integration/workspace_resolver.py
"""Workspace resolver for remote SSH session roots.

Migrated from playground.mat_master.core.workspace_resolver (Phase 29).
Only the functions used by src/services/agent_run_bohrium.py are retained.
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_DEFAULT_REMOTE_SESSION_WORKSPACE_ROOT = '/share'


def _default_project_root() -> Path:
    """Return repository root.
    matmaster/integration/workspace_resolver.py -> parents[2] = repo root
    """
    return Path(__file__).resolve().parents[2]


def _mat_master_config_path(project_root: Path | None = None) -> Path:
    root = project_root or _default_project_root()
    return root / 'configs' / 'mat_master' / 'config.yaml'


@lru_cache
def _load_workspace_config_from_file(config_path: str) -> dict[str, Any]:
    path = Path(config_path)
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    except Exception as exc:
        logger.debug('workspace resolver: load config failed path=%s err=%s', path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def load_workspace_config_dict(project_root: Path | None = None) -> dict[str, Any]:
    return _load_workspace_config_from_file(str(_mat_master_config_path(project_root)))


def _mat_master_value(config_dict: dict[str, Any] | None, key: str) -> Any:
    if not isinstance(config_dict, dict):
        return None
    section = config_dict.get('mat_master')
    if not isinstance(section, dict):
        return None
    return section.get(key)


def _resolve_optional_path(
    raw: str | os.PathLike[str] | None,
    *,
    project_root: Path | None = None,
) -> Path | None:
    if raw is None:
        return None
    value = str(raw).strip()
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = ((project_root or _default_project_root()) / path).resolve()
    return path


def get_remote_session_workspace_root(
    config_dict: dict[str, Any] | None = None,
    *,
    project_root: Path | None = None,
) -> Path:
    raw = (
        _mat_master_value(config_dict, 'remote_session_workspace_root')
        or _DEFAULT_REMOTE_SESSION_WORKSPACE_ROOT
    )
    resolved = _resolve_optional_path(raw, project_root=project_root)
    assert resolved is not None
    return resolved
```

### monitor_job/_llm.py 重写 _get_llm_by_alias

```python
@lru_cache(maxsize=4)
def _get_llm_client(alias: str | None = None):
    """Build a sync OpenAI client from matmaster LLM config.

    Returns (client, model_name) tuple.
    """
    from matmaster.config.loader import load_llm_config

    llm_config = load_llm_config(REPO_ROOT / 'matmaster_config' / 'llm_config.yaml')
    profile_key = alias or llm_config.default
    profile = llm_config.get_profile(profile_key)

    import openai
    client = openai.OpenAI(
        api_key=profile.api_key,
        base_url=profile.base_url,
        timeout=profile.timeout,
    )
    return client, profile.model
```

### bash_tool.py 清理后的 execute 方法

```python
async def execute(self, arguments: dict[str, Any]) -> str:
    from matmaster.sessions.local import LocalSession as _MatLocal

    if isinstance(self._session, _MatLocal):
        try:
            return await self._execute_async(arguments)
        except Exception as e:
            self.logger.error("Tool %s failed: %s", self.name, e, exc_info=True)
            return f"Error: {e}"

    return await super().execute(arguments)
```

### agent_run_bohrium.py import 修正

```python
# Before:
from playground.mat_master.core.workspace_resolver import (
    get_remote_session_workspace_root,
    load_workspace_config_dict,
)

# After:
from matmaster.integration.workspace_resolver import (
    get_remote_session_workspace_root,
    load_workspace_config_dict,
)
```

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (pyproject.toml asyncio_mode=auto) |
| Config file | pytest.ini + pyproject.toml [tool.pytest.ini_options] |
| Quick run command | `uv run pytest tests/matmaster/test_import_audit.py -x` |
| Full suite command | `uv run pytest tests/ -x --ignore=tests/playground --ignore=tests/evaluation` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| CONS-01 | matmaster 包内无 evomaster runtime import | unit (import audit) | `uv run pytest tests/matmaster/test_import_audit.py -x` | Exists (需增强: 覆盖 evomaster.config + evomaster.utils) |
| CONS-01 | bash_tool 无 evomaster session import | unit (import audit) | `uv run pytest tests/matmaster/test_import_audit.py::TestNoEvomasterSessionImportsInMatmaster -x` | Exists (需移除 xfail) |
| CONS-01 | workspace_resolver 从 matmaster 导入可用 | unit | `uv run pytest tests/test_workspace_resolver.py -x` | Exists (需改 import) |
| CONS-01 | pytest --collect-only 无 playground/evaluation import 错误 | smoke | `uv run pytest --collect-only 2>&1 | grep -i error` | Manual |

### Sampling Rate
- **Per task commit:** `uv run pytest tests/matmaster/test_import_audit.py tests/test_workspace_resolver.py -x`
- **Per wave merge:** `uv run pytest tests/ -x`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/matmaster/test_import_audit.py` -- 需新增 evomaster.config + evomaster.utils 审计规则（当前只检查 mcp/calculation/session/env.bohrium/src）
- [ ] import audit xfail 移除（bash_tool 清理后）

## Open Questions

1. **`.archive/` 是否加入 .gitignore**
   - What we know: D-09 要求将 19 个技能归档到 `.archive/playground-skills/`。git 历史已完整保留这些文件
   - What's unclear: 用户是否希望归档文件出现在 git 仓库中
   - Recommendation: 加入 .gitignore。理由是归档仅为用户手动参考，不需要进入版本控制。如果之后需要恢复，git log 可追溯

2. **evaluation/ 下的测试是否有其他文件引用**
   - What we know: evaluation/ 包含 test_eval_ingest_client.py 等 4 个测试文件，tests/evaluation/ 目录不存在（评估测试直接在 evaluation/ 下）
   - What's unclear: 是否有其他非删除范围的测试引用 evaluation 模块
   - Recommendation: 删除后运行 `pytest --collect-only` 验证，如有报错再修复

3. **configs/mat_master/ 目录长期定位**
   - What we know: CLAUDE.md 提到双配置目录迁移中，目标是合并为 matmaster_config/ 单一源。本 phase 需要同时修改两处 config.yaml 的 working_dir
   - What's unclear: 是否应在本 phase 顺便统一
   - Recommendation: 不在本 phase 统一，仅修改必要的 working_dir 字段。配置统一是独立关注点

## Sources

### Primary (HIGH confidence)
- 直接代码审计: matmaster/tools/builtin/bash_tool.py L129-132 -- evomaster LocalSession 分支
- 直接代码审计: matmaster/tools/builtin/monitor_job/_llm.py L64-81 -- evomaster ConfigManager/create_llm
- 直接代码审计: src/services/agent_run_bohrium.py L12-15 -- playground workspace_resolver import
- 直接代码审计: playground/mat_master/core/workspace_resolver.py -- 完整迁移源
- 直接代码审计: matmaster/config/loader.py + matmaster/config/llm.py -- matmaster 原生 LLM 配置
- 直接代码审计: matmaster/providers/llm_factory.py + openai_provider.py -- async provider (不适用于 monitor_job 同步场景)
- 直接代码审计: evomaster/utils/llm/factory.py + base.py -- 同步 _call 接口 (被替换)
- grep 全仓库扫描: `from evomaster` 在 matmaster/ 下精确 2 处 runtime import
- grep 全仓库扫描: `from playground` 在非 playground 代码中仅 1 处 (agent_run_bohrium.py)

### Secondary (MEDIUM confidence)
- configs/mat_master/config.yaml + matmaster_config/config.yaml -- 双配置文件审计，确认两处都有 playground 路径引用

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- 无新库引入，全部是现有 matmaster 基础设施
- Architecture: HIGH -- 修改点精确已知，每处都有代码行号
- Pitfalls: HIGH -- 基于实际代码审计的具体问题

**Research date:** 2026-04-01
**Valid until:** 2026-05-01 (stable -- 内部重构，无外部 API 变更)
