# Phase 26: Tool 内化与遗留工具收归 - Research

**Researched:** 2026-04-01
**Domain:** matmaster tool system internalization -- eliminating evomaster/playground runtime imports from matmaster/tools/
**Confidence:** HIGH

## Summary

Phase 26 将 matmaster 工具系统中残余的 evomaster/playground 运行时依赖全部消除。涉及四个维度：(1) bash_safety helper 内联到 bash_tool.py；(2) editor helper 内联到 edit_tool.py；(3) MonitorJobTool 从 evomaster 搬入 matmaster/tools/builtin/monitor_job/ 并改继承 BuiltinTool ABC；(4) exp.py 切换到 matmaster 原生 WebSearchTool 并删除 playground 导入；最终删除 EvoToolAdapter 文件和 exp.py 中的 evo adapter 注册段。

核心技术难点集中在 MonitorJobTool 的移植：该 tool 有 5 个子模块（_tool.py, _constants.py, _lifecycle.py, _download.py, _llm.py, _logs.py），深度依赖 `evomaster.adaptors.calculation.job_service`（6 个函数）、`evomaster.agent.session.ssh.SSHSession`（isinstance 判断）、以及 `evomaster.config.ConfigManager` + `evomaster.utils.create_llm`（LLM 决策功能）。其中 `job_service` 和 LLM 相关依赖是跨 Phase 的（Phase 27 CALC-01/CALC-02 会迁移 calculation adaptors），因此 Phase 26 应保留对 `evomaster.adaptors.calculation` 的 import 不变，仅将 `_tool.py` 改继承 BuiltinTool 并调整参数接口。

**Primary recommendation:** 按依赖反转方向分层实施 -- 先内联 helper（无外部依赖），再切换 web_search（已有原生实现），再搬入 monitor_job（最复杂），最后删除 EvoToolAdapter。

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** MonitorJobTool 移植为 BuiltinTool 子类，放入 `matmaster/tools/builtin/monitor_job/` 目录
- **D-02:** session 依赖通过 self.session 接口获取（workspace、credentials、stop_event），使用 getattr 取属性，与 bash_tool 双路径模式一致
- **D-03:** `_tool.py`、`_constants.py`、`_lifecycle.py` 三文件整体搬入，改继承 BuiltinTool ABC，参数从 BaseToolParams 改为 json_schema dict + arguments dict 模式
- **D-04:** exp.py 直接切换到 matmaster 原生 WebSearchTool（`matmaster/tools/builtin/web_search_tool.py`），不再 import `playground.mat_master.tools.web_search`
- **D-05:** 不补齐 playground 旧版的 page/location 参数，名称统一为 `web_search`（与 TOML 配置一致）
- **D-06:** 最小成本断依赖。`is_dangerous_bash_command` 及相关常量/正则直接内联到 `bash_tool.py` 中；`MAX_OUTPUT_SIZE`、`SNIPPET_LINES`、`maybe_truncate` 直接内联到 `edit_tool.py` 中
- **D-07:** 不创建独立 helper 模块，解耦后会重新更新 tool，届时一并重构
- **D-08:** 完全删除 `matmaster/tools/evomaster_tool_adapter.py` 文件
- **D-09:** 删除 `matmaster/tools/__init__.py` 中 EvoToolAdapter 的导出
- **D-10:** exp.py 中 `# 2. Evo adapter tools` 整段替换为原生注册（MonitorJobTool + WebSearchTool 作为 builtin 直接注册，source='builtin'）

### Claude's Discretion
- MonitorJobTool 内部 `_lifecycle.py` 中对 evomaster session 类型判断（isinstance SSHSession）的具体替代方式
- 内联 helper 时的代码组织细节（放文件顶部 vs 使用处附近）

### Deferred Ideas (OUT OF SCOPE)
None -- discussion stayed within phase scope
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| TOOL-07 | 开发者可以在 matmaster.tools 中注册并执行遗留 builtin 能力，而不需要 EvoToolAdapter | 删除 EvoToolAdapter 文件和 __init__.py 导出；exp.py 改为直接 register BuiltinTool 实例，source='builtin' |
| TOOL-08 | matmaster.tools.builtin 中的 bash safety 与 edit helper 由 matmaster 原生实现提供，不再导入 evomaster.agent.tools.builtin.* | bash_safety 全量内联到 bash_tool.py；MAX_OUTPUT_SIZE/SNIPPET_LINES/maybe_truncate 内联到 edit_tool.py |
| TOOL-09 | MonitorJobTool 通过 matmaster 原生注册，exp.py 不再 lazy import evomaster.agent.tools.builtin.monitor_job | MonitorJobTool 搬入 matmaster/tools/builtin/monitor_job/，改继承 BuiltinTool，保留 _lifecycle/_download/_llm/_logs 子模块及其 evomaster.adaptors.calculation 依赖（Phase 27 迁移） |
| TOOL-10 | web_search_tool 通过 matmaster 原生实现提供，exp.py 不再 import playground.mat_master.tools.web_search | matmaster 原生 WebSearchTool 已存在（web_search_tool.py），exp.py 直接注册即可 |
</phase_requirements>

## Architecture Patterns

### MonitorJobTool 移植结构

```
matmaster/tools/builtin/monitor_job/
├── __init__.py          # 导出 MonitorJobTool（+ run_monitor_decision_once 保持兼容）
├── _tool.py             # MonitorJobTool(BuiltinTool) -- json_schema + _execute
├── _constants.py        # 常量（TERMINAL_SUCCESS, LOG_PATTERNS, LLM prompt 等）
├── _lifecycle.py        # _run_lifecycle 核心轮询循环
├── _download.py         # 结果下载（_download_results_to_local_dir, _sftp_push_directory）
├── _llm.py              # LLM 决策（_call_llm_decision, _terminate_job_if_needed）
└── _logs.py             # 日志发现与读取
```

### 当前依赖图（需要修改的 import 链）

```
matmaster/tools/builtin/bash_tool.py
  └─ from evomaster.agent.tools.builtin.bash_safety import is_dangerous_bash_command  [TOOL-08: 内联]
  └─ from evomaster.agent.session.local import LocalSession  [NOT in scope: Phase 25 PLAY-02]

matmaster/tools/builtin/edit_tool.py
  └─ from evomaster.agent.tools.builtin.editor import MAX_OUTPUT_SIZE, SNIPPET_LINES, maybe_truncate  [TOOL-08: 内联]

matmaster/core/exp.py §393-414
  └─ from evomaster.agent.tools.builtin.monitor_job import MonitorJobTool  [TOOL-09: 改为 from matmaster.tools.builtin.monitor_job]
  └─ from playground.mat_master.tools.web_search import get_web_search_tool  [TOOL-10: 删除]
  └─ EvoToolAdapter(tool, ctx.session)  [TOOL-07: 删除整段]

matmaster/tools/evomaster_tool_adapter.py  [TOOL-07: 删除文件]
matmaster/tools/__init__.py  [TOOL-07: 删除 EvoToolAdapter 导出]
```

### MonitorJobTool 内部依赖（搬入后保留的外部 import）

Phase 26 只消除 `evomaster.agent.tools.builtin.*` 和 `evomaster.agent.session.*` 的依赖。以下 import 在 Phase 27 (MCP-01/CALC-01/CALC-02) 处理：

```
_lifecycle.py:
  from evomaster.adaptors.calculation.job_service import download_job_file, get_job_results, query_job_status
  from evomaster.agent.session.ssh import SSHSession  [isinstance 判断]

_download.py:
  from evomaster.adaptors.calculation.job_service import download_job_directory, download_job_file, get_file_token, iterate_job_files
  from evomaster.agent.session.ssh import SSHSession  [isinstance 判断]

_llm.py:
  from evomaster.adaptors.calculation.job_service import terminate_job
  from evomaster.config import ConfigManager
  from evomaster.utils import LLMConfig, create_llm

_logs.py:
  from evomaster.adaptors.calculation.job_service import download_job_file, iterate_job_files, query_job_status
  from evomaster.agent.session import BaseSession
```

**关键决策：Phase 26 scope 内的 session isinstance 处理**

`_lifecycle.py` 和 `_download.py` 中有 `isinstance(session, SSHSession)` 判断。这是 Claude's Discretion 区域。推荐方案：

**方案 A（推荐）：使用 getattr 鸭子类型**
```python
# 替代 isinstance(session, SSHSession)
is_ssh = hasattr(session, '_env') and hasattr(getattr(session, '_env', None), 'upload_file')
```
这与 bash_tool 的双路径模式一致（检查能力而非类型），不需要 import SSHSession。

**方案 B：保留 TYPE_CHECKING import**
```python
if TYPE_CHECKING:
    from evomaster.agent.session.ssh import SSHSession
# 运行时用 getattr 或 str 类型名判断
is_ssh = type(session).__name__ == 'SSHSession'
```
可行但脆弱，类重命名会破坏。

**推荐方案 A**，理由：与 D-02 决策一致（session 依赖通过 self.session 接口获取，使用 getattr），不依赖任何 evomaster 运行时 import。

### 已修改文件影响域

| 文件 | 修改类型 | 风险 |
|------|---------|------|
| `bash_tool.py` | 删除 import + 内联 ~100 行代码 | LOW -- 纯复制，逻辑不变 |
| `edit_tool.py` | 删除 import + 内联 ~15 行代码 | LOW -- 3 个常量 + 1 个函数 |
| `exp.py` §393-414 | 删除 evo adapter 段 + 原生注册 | MEDIUM -- 注册逻辑改变 |
| `evomaster_tool_adapter.py` | 删除文件 | LOW -- 无其他引用 |
| `tools/__init__.py` | 删除 EvoToolAdapter 导出 | LOW |
| `monitor_job/` 6 个文件 | 搬入 + 改继承 | MEDIUM -- 参数接口变化 |
| `eval_tooling_snapshot.py` | `web-search` 改为 `web_search` | LOW -- 仅名称修正 |

### _tool.py 接口变化（MonitorJobTool 核心改造）

**Before（evomaster BaseTool 风格）：**
```python
class MonitorJobTool(BaseTool):
    name = 'monitor_job'
    params_class = MonitorJobParams  # Pydantic BaseToolParams

    def execute(self, session: BaseSession, args_json: str) -> tuple[str, dict]:
        params = self.parse_params(args_json)
        workspace = params.workspace
        ...
```

**After（matmaster BuiltinTool 风格）：**
```python
class MonitorJobTool(BuiltinTool):
    name: ClassVar[str] = 'monitor_job'
    description: ClassVar[str] = '...'
    json_schema: ClassVar[dict[str, Any]] = {
        'type': 'object',
        'properties': {
            'job_id': {'type': 'string', 'description': '...'},
            'software': {'type': 'string', 'description': '...'},
            # ... 从 MonitorJobParams 的 Field 定义转换
        },
        'required': ['job_id', 'software'],
    }

    def _execute(self, arguments: dict[str, Any]) -> ToolResult:
        session = self._require_session()
        job_id = arguments.get('job_id', '')
        software = arguments.get('software', '')
        workspace = arguments.get('workspace', '.')
        ...
        # session 属性通过 getattr 获取
        is_ssh = hasattr(session, '_env') and hasattr(getattr(session, '_env', None), 'upload_file')
        ...
```

关键变化：
1. 继承 `BuiltinTool` 而非 `BaseTool`
2. `params_class` (Pydantic) 转为 `json_schema` (ClassVar dict) + `description` (ClassVar str)
3. `execute(session, args_json)` 转为 `_execute(arguments: dict)` -- session 从 `self._session` 获取
4. 返回值从 `tuple[str, dict]` 转为 `ToolResult`
5. `MonitorJobParams` 类完全删除，参数校验由 json_schema 在 LLM 侧完成

### exp.py 注册段替换

**Before：**
```python
# 2. Evo adapter tools (source="builtin_evo")
from evomaster.agent.tools.builtin.monitor_job import MonitorJobTool
from playground.mat_master.tools.web_search import get_web_search_tool

evo_tools = [MonitorJobTool(), get_web_search_tool()]
registered_evo = []
for tool in evo_tools:
    adapted = EvoToolAdapter(tool, ctx.session)
    if _want(adapted.name):
        registry.register(adapted, source='builtin_evo')
        registered_evo.append(adapted)
```

**After：**
```python
# 2. Additional builtin tools (science-specific)
from matmaster.tools.builtin.monitor_job import MonitorJobTool

additional_tools = [
    MonitorJobTool(session=ctx.session, workdir=exec_wd),
]
for tool in additional_tools:
    if _want(tool.name):
        registry.register(tool, source='builtin')
```

注意：WebSearchTool 已在 native_tools 列表中注册（exp.py 第 379 行），不需要额外注册。只需删除 playground web_search 的导入和 evo adapter 段。

### Anti-Patterns to Avoid

- **不要创建新的 adapter 层**：MonitorJobTool 应直接继承 BuiltinTool，不要为了减少改动量而引入新的 wrapper
- **不要在 Phase 26 迁移 job_service**：`evomaster.adaptors.calculation.job_service` 属于 Phase 27 (CALC-01/CALC-02) 范围，Phase 26 的 monitor_job 子模块应保留这些 import
- **不要在 Phase 26 处理 bash_tool 的 LocalSession import**：`from evomaster.agent.session.local import LocalSession` 属于 Phase 25 (PLAY-02) 范围

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| bash 安全检查 | 自创新规则集 | 直接内联 evomaster bash_safety.py 全部内容 | 已有完善的规则集，经过生产验证 |
| web_search | 重新实现 | matmaster 原生 WebSearchTool（已存在） | 已实现且经过测试 |
| ToolResult 状态判断 | 自定义返回值 | ToolResult(status, content, info) | matmaster 统一结果模型 |
| 参数校验 | 自建 Pydantic model | json_schema dict（BuiltinTool 约定） | 与现有 14 个 builtin tool 一致 |

## Common Pitfalls

### Pitfall 1: web_search 名称不匹配
**What goes wrong:** playground 旧版 WebSearchTool.name = `web-search`（hyphen），matmaster 原生版 name = `web_search`（underscore）。如果 TOML whitelist 用 `web_search`，旧版永远不会被 `_want()` 匹配到。
**Why it happens:** 命名风格不统一的历史遗留。
**How to avoid:** 使用 matmaster 原生 WebSearchTool（name='web_search'），与 TOML 配置匹配。同时更新 `eval_tooling_snapshot.py` 的 `_BUILTIN_WHEN_STAR` 列表，将 `web-search` 改为 `web_search`。
**Warning signs:** `_want('web-search')` 在非 wildcard 模式下返回 False。

### Pitfall 2: MonitorJobTool _lifecycle.py 中的 SSHSession isinstance 残留
**What goes wrong:** 搬入后如果保留 `isinstance(session, SSHSession)`，运行时需要 import evomaster.agent.session.ssh，违反 TOOL-09 要求。
**Why it happens:** _lifecycle.py 中有 `is_ssh = isinstance(session, SSHSession)` 用于决定下载路径（本地 vs SFTP push）。
**How to avoid:** 替换为 getattr 鸭子类型检测：`is_ssh = hasattr(session, '_env') and hasattr(getattr(session, '_env', None), 'upload_file')`
**Warning signs:** import error 当 evomaster 不可用时。

### Pitfall 3: MonitorJobTool _constants.py 中的 REPO_ROOT 路径
**What goes wrong:** `REPO_ROOT = Path(__file__).resolve().parents[5]` 硬编码了文件在 evomaster 中的深度（5 级 parent）。搬到 matmaster 后层级不同。
**Why it happens:** 该常量用于 _llm.py 中定位 configs/ 目录和 logs/ 目录。
**How to avoid:** 搬入后调整 parents 数字（matmaster/tools/builtin/monitor_job/_constants.py 距 repo root 是 4 级 parent），或改用更稳健的方式定位 repo root。
**Warning signs:** 测试注入日志路径找不到；LLM 配置加载失败。

### Pitfall 4: _llm.py 中 ConfigManager 和 create_llm 依赖
**What goes wrong:** _llm.py 的 `_get_llm_by_alias()` 直接 import ConfigManager 和 create_llm。
**Why it happens:** monitor_job 的 LLM 决策功能需要加载 LLM 配置。
**How to avoid:** Phase 26 保留这些 import（属于 Phase 27 CALC-01 范围）。但要确认：这些 import 来自 `evomaster.config` 和 `evomaster.utils`，不属于 `evomaster.agent.tools.builtin.*`，因此不违反 TOOL-08 的 scope。但它们确实是 evomaster 依赖，success criteria 5 要求"不触发任何 evomaster 运行时导入"。需要仔细界定 scope。
**Warning signs:** 如果严格解读 SC-5，Phase 26 可能需要处理 _llm.py 的 evomaster 依赖。

### Pitfall 5: 删除 EvoToolAdapter 后遗留引用
**What goes wrong:** 删除 evomaster_tool_adapter.py 后其他文件仍然 import 它。
**Why it happens:** 测试文件 `tests/matmaster/tools/test_evomaster_tool_adapter.py` 直接 import EvoToolAdapter。
**How to avoid:** 搜索所有引用并处理。具体需要删除或跳过的测试：`test_evomaster_tool_adapter.py`。
**Warning signs:** ImportError in test collection。

### Pitfall 6: Success Criteria 5 的 scope 界定
**What goes wrong:** SC-5 要求"在仅安装 matmaster 的环境中加载全部 builtin tools 和 exp 注册的 tools 时，不会触发任何 evomaster 或 playground 运行时导入"。但 MonitorJobTool 的 _lifecycle/_download/_llm/_logs 仍然 import evomaster.adaptors.calculation。
**Why it happens:** Phase 26 的明确 boundary 是消除 `evomaster.agent.tools.builtin.*` 依赖和 `playground.mat_master.tools.*` 依赖。`evomaster.adaptors.calculation` 和 `evomaster.config` 属于 Phase 27。
**How to avoid:** 以下两种策略之一：
  - (a) 将 MonitorJobTool 子模块中的 evomaster.adaptors/config/utils import 改为 lazy import（try/except ImportError），使加载时不直接触发
  - (b) 在 PLAN 中明确说明 SC-5 对 monitor_job 子模块中 calculation adaptor 依赖的例外，在 Phase 27 完全消除
  推荐 (a)：将 _lifecycle/_download/_llm/_logs 中的 evomaster import 改为 lazy import，在函数内部延迟导入。这样 `from matmaster.tools.builtin.monitor_job import MonitorJobTool` 不会触发 evomaster 模块加载，满足 SC-5 的"加载时不触发"要求。
**Warning signs:** 在 import matmaster.tools.builtin.monitor_job 时出现 evomaster ImportError。

## Code Examples

### bash_safety 内联到 bash_tool.py

```python
# matmaster/tools/builtin/bash_tool.py (内联后)
import re

# ---- Bash Safety (inlined from evomaster) ----
_BLOCKED_FIRST_TOKENS = frozenset({'env', 'set', 'printenv'})

_DANGEROUS_COMMAND_PATTERNS = [
    r'rm\s+-rf\s+/',
    r'rm\s+-rf\s+/\*',
    r'rm\s+-rf\s+\.',
    r'rm\s+-rf\s+\.\.',
    r':\s*\(\s*\)\s*\{\s*[^}]*\|\s*:.*\}',
    r'mkfs\.?\s',
    r'dd\s+if=.*of=/dev',
    r'\bchmod\s+[0-7]{3,4}\s+/',
    r'>\s*/dev/sd',
    r'ssh\s+.*\s+root@',
]
_COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in _DANGEROUS_COMMAND_PATTERNS]

# Python content patterns for ToolGuard (also inlined for completeness)
_DANGEROUS_PYTHON_CONTENT_PATTERNS = [
    (r'\bos\.environ\b', 'reads environment variables (os.environ)'),
    (r'\bos\.getenv\b', 'reads environment variables (os.getenv)'),
    (r'/proc/self/environ', 'reads /proc/self/environ directly'),
    (r'subprocess[^#\n]*\benv\b', "runs 'env' via subprocess"),
    (r'glob\s*\(.*?\.env', 'scans for .env files'),
    (r"open\s*\(\s*['\"]\.env", 'reads .env file directly'),
    (r'(AK|SK|KEY|TOKEN|SECRET|CREDENTIAL|BEARER|ACCESS).{0,40}environ',
     'searches environment for credential-like keys'),
    (r'environ.{0,40}(AK|SK|KEY|TOKEN|SECRET|CREDENTIAL|BEARER|ACCESS)',
     'filters environment variables for credentials'),
]
_PYTHON_CONTENT_COMPILED = [
    (re.compile(p, re.IGNORECASE), msg) for p, msg in _DANGEROUS_PYTHON_CONTENT_PATTERNS
]


def is_dangerous_python_content(content: str) -> tuple[bool, str]:
    if not content or not isinstance(content, str):
        return False, ''
    for pat, msg in _PYTHON_CONTENT_COMPILED:
        if pat.search(content):
            return True, msg
    return False, ''


def is_dangerous_bash_command(command: str) -> tuple[bool, str]:
    if not command or not isinstance(command, str):
        return False, ''
    raw = command.strip()
    if not raw:
        return False, ''
    first_token = raw.split(None, 1)[0].lower() if raw else ''
    if first_token in _BLOCKED_FIRST_TOKENS:
        return True, f"'{first_token}' is not allowed (blocked for security)."
    for pat in _COMPILED_PATTERNS:
        if pat.search(command):
            return True, 'The command contains potentially destructive or unsafe operations.'
    return False, ''
```

### editor helper 内联到 edit_tool.py

```python
# matmaster/tools/builtin/edit_tool.py (内联后顶部)

# ---- Editor helpers (inlined from evomaster) ----
SNIPPET_LINES = 4
MAX_OUTPUT_SIZE = 16000

_TEXT_FILE_TRUNCATED_NOTICE = (
    '<response clipped><NOTE>To save on context only part of this file has been shown to you. '
    'You should retry this tool after you have searched inside the file with `grep -n` in '
    'order to find the line numbers of what you are looking for.</NOTE>'
)

def maybe_truncate(
    content: str,
    max_size: int = MAX_OUTPUT_SIZE,
    notice: str = _TEXT_FILE_TRUNCATED_NOTICE,
) -> str:
    if len(content) <= max_size:
        return content
    half = max_size // 2
    return content[:half] + '\n' + notice + '\n' + content[-half:]
```

### MonitorJobTool json_schema 转换示例

```python
# matmaster/tools/builtin/monitor_job/_tool.py
json_schema: ClassVar[dict[str, Any]] = {
    'type': 'object',
    'properties': {
        'job_id': {
            'type': 'string',
            'description': 'Job ID returned by the MCP submit tool.',
        },
        'software': {
            'type': 'string',
            'description': (
                'Software name (case-insensitive): dpa, abacus, lammps, cp2k, qe, '
                'abinit, orca, gaussian, or any registered async software.'
            ),
        },
        'workspace': {
            'type': 'string',
            'description': 'Workspace directory for result downloads. Defaults to session workspace.',
            'default': '.',
        },
        'bohr_job_id': {
            'type': ['string', 'null'],
            'description': 'Explicit Bohrium OpenAPI job ID. Required for dpdispatcher-based jobs.',
            'default': None,
        },
        'poll_interval': {
            'type': 'integer',
            'description': 'Seconds between status checks.',
            'default': 30,
        },
        # ... 其余参数同理从 MonitorJobParams Field 定义转换
    },
    'required': ['job_id', 'software'],
}
```

### exp.py 注册段最终形态

```python
# matmaster/core/exp.py -- _init_builtin_tools 末尾
# 2. Science-specific builtin tools
from matmaster.tools.builtin.monitor_job import MonitorJobTool

science_tools = [
    MonitorJobTool(session=ctx.session, workdir=exec_wd),
]
for tool in science_tools:
    if _want(tool.name):
        registry.register(tool, source='builtin')

self.logger.debug(
    'Registered %d native + %d science builtin tools (cfg=%s)',
    len(registered_native),
    len(science_tools),
    builtin_cfg,
)
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| EvoToolAdapter 桥接 evomaster BaseTool | BuiltinTool ABC 原生实现 | Phase 26 | 消除运行时 evomaster 依赖 |
| Pydantic BaseToolParams 参数 | ClassVar json_schema dict | matmaster v2.0 | 与 14 个 builtin tool 一致 |
| `playground.mat_master.tools.web_search` | `matmaster.tools.builtin.web_search_tool` | Phase 26 | 消除 playground 依赖 |

## Open Questions

1. **SC-5 与 monitor_job 子模块 evomaster.adaptors.calculation 依赖**
   - What we know: Phase 26 CONTEXT.md 明确排除了 lazy_mcp.py 和 cache_mcp_schemas.py 中的 evomaster MCP 依赖（属 Phase 27）。但 SC-5 要求"不触发任何 evomaster 运行时导入"。
   - What's unclear: SC-5 是否覆盖 monitor_job 子模块中的 `evomaster.adaptors.calculation` 导入，还是仅限于 Phase 26 明确范围内的 `evomaster.agent.tools.builtin.*` 和 `playground.*`？
   - Recommendation: 将 _lifecycle/_download/_llm/_logs 中的 `evomaster.adaptors.calculation` import 改为函数内 lazy import。这样模块加载时不触发（满足 SC-5 字面意义），运行时 monitor_job 被调用时才延迟加载。总代码改动量小（每处只是把顶层 import 移入函数体），且为 Phase 27 的正式迁移做好准备。

2. **`is_dangerous_python_content` 是否也需要内联**
   - What we know: bash_safety.py 包含两个函数：`is_dangerous_bash_command`（bash_tool.py 使用）和 `is_dangerous_python_content`（被 ToolGuard 使用）。
   - What's unclear: ToolGuard 的 `is_dangerous_python_content` 导入路径是否在 Phase 26 scope 内。
   - Recommendation: 同时内联 `is_dangerous_python_content`，因为它与 `is_dangerous_bash_command` 在同一文件中，整体内联成本几乎为零，且可能有其他 matmaster 代码引用。如果 ToolGuard 在 `playground/` 而非 `matmaster/` 中，则不影响 Phase 26。

3. **eval_tooling_snapshot.py 的 `web-search` 名称更新**
   - What we know: `_BUILTIN_WHEN_STAR` 列表中 `web-search` 用 hyphen，与 matmaster native WebSearchTool 的 `web_search` 不一致。
   - Recommendation: 在切换到原生注册后，应将 `web-search` 更新为 `web_search`，保持 snapshot 与实际注册名一致。

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 9.0.2 + pytest-asyncio (auto mode) |
| Config file | pytest.ini |
| Quick run command | `uv run pytest tests/matmaster/tools/ -x -q` |
| Full suite command | `uv run pytest tests/matmaster/ -x -q` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| TOOL-07 | EvoToolAdapter 删除后 tool 注册仍正常 | unit | `uv run pytest tests/matmaster/tools/test_tool_registry.py -x` | Exists (registry) |
| TOOL-07 | EvoToolAdapter import 不再存在 | unit | `uv run python -c "from matmaster.tools import __all__; assert 'EvoToolAdapter' not in __all__"` | Wave 0 |
| TOOL-08 | bash_tool 不 import evomaster.agent.tools.builtin | unit | `uv run pytest tests/matmaster/tools/test_bash_tool.py -x` | Exists |
| TOOL-08 | edit_tool 不 import evomaster.agent.tools.builtin | unit | `uv run pytest tests/matmaster/tools/test_edit_tool.py -x` | Exists |
| TOOL-08 | is_dangerous_bash_command 内联后行为不变 | unit | `uv run pytest tests/matmaster/tools/test_bash_tool.py -x -k dangerous` | Wave 0 |
| TOOL-09 | MonitorJobTool 作为 BuiltinTool 满足 Tool Protocol | unit | `uv run pytest tests/matmaster/tools/test_monitor_job.py -x` | Wave 0 |
| TOOL-09 | MonitorJobTool json_schema 有效 | unit | `uv run pytest tests/matmaster/tools/test_monitor_job.py -x -k schema` | Wave 0 |
| TOOL-10 | WebSearchTool 原生注册，name='web_search' | unit | `uv run pytest tests/matmaster/tools/test_web_search_tool.py -x` | Exists |
| ALL | matmaster 加载不触发 evomaster.agent.tools.builtin import | smoke | `uv run python -c "import matmaster.tools.builtin"` | Wave 0 |
| ALL | exp.py 不含 evomaster/playground import（grep 校验） | smoke | `grep -r 'from evomaster.agent.tools.builtin\|from playground' matmaster/core/exp.py` | Wave 0 |

### Sampling Rate
- **Per task commit:** `uv run pytest tests/matmaster/tools/ -x -q`
- **Per wave merge:** `uv run pytest tests/matmaster/ -x -q`
- **Phase gate:** Full suite green before /gsd:verify-work

### Wave 0 Gaps
- [ ] `tests/matmaster/tools/test_monitor_job.py` -- MonitorJobTool 作为 BuiltinTool 的 Protocol 满足、json_schema 有效性、_execute 基本流程（mock job_service）
- [ ] `tests/matmaster/tools/test_bash_tool.py` -- 增加 `is_dangerous_bash_command` 内联后的行为测试（现有测试可能已覆盖，需验证）
- [ ] 删除 `tests/matmaster/tools/test_evomaster_tool_adapter.py`（或标记 skip -- 测试对象已删除）
- [ ] smoke test: `import matmaster.tools.builtin` 不触发 evomaster.agent.tools.builtin 模块加载

## Project Constraints (from CLAUDE.md)

- 始终使用 `uv run` 或 `.venv`，不用系统 Python
- Import 按 标准库 -> 第三方 -> 本地 分组
- 单文件超过 1000 行必须重构
- 新增工具必须实现 Tool Protocol 并返回 ToolResult
- DAO 层不吞异常；service 层按需降级

## Sources

### Primary (HIGH confidence)
- `matmaster/tools/builtin/base.py` -- BuiltinTool ABC 定义，直接阅读源码
- `matmaster/tools/builtin/bash_tool.py` -- 当前 evomaster import 位置，直接阅读
- `matmaster/tools/builtin/edit_tool.py` -- 当前 evomaster import 位置，直接阅读
- `matmaster/core/exp.py` 第 310-414 行 -- _init_builtin_tools 完整逻辑，直接阅读
- `evomaster/agent/tools/builtin/bash_safety.py` -- 内联源码（101 行），直接阅读
- `evomaster/agent/tools/builtin/editor.py` 第 32-49 行 -- 内联源码，直接阅读
- `evomaster/agent/tools/builtin/monitor_job/` 全部 6 文件 -- 移植源码（792 行），直接阅读
- `matmaster/tools/builtin/web_search_tool.py` -- 已有原生实现，直接阅读
- `matmaster/tools/evomaster_tool_adapter.py` -- 待删除文件，直接阅读
- `matmaster/tools/__init__.py` -- EvoToolAdapter 导出，直接阅读

### Secondary (MEDIUM confidence)
- `playground/mat_master/tools/web_search.py` -- playground 旧版 web_search，名称为 `web-search`（hyphen），grep 确认
- `matmaster/eval_tooling_snapshot.py` -- `_BUILTIN_WHEN_STAR` 列表中 `web-search` 需更新
- `matmaster/exps/direct.toml`, `matmaster/exps/explore.toml` -- builtin 工具白名单配置

### Tertiary (LOW confidence)
- None

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- 纯代码迁移，无第三方库引入
- Architecture: HIGH -- BuiltinTool ABC 模式成熟，14 个现有工具已验证
- Pitfalls: HIGH -- 通过源码审查直接定位所有依赖点

**Research date:** 2026-04-01
**Valid until:** 2026-05-01 (stable codebase, internal refactoring)
