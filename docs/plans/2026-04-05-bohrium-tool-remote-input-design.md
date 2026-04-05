# Bohrium Tool Remote Input Design

## Goal

修复内置 `Bohrium` tool 在提交作业时无法处理远端共享目录 `input_dir` 的问题，使其在运行时位于远端服务器时，能够正确读取 `/share/...`、`/personal/...` 目录并继续走现有的 Bohrium 提交流程。

本次设计只覆盖内置 `Bohrium` tool：

- 处理 `Bohrium(action="submit", input_dir=...)`
- 不修改 Bohrium OpenAPI 提交协议
- 不处理即将归档废弃的 `playground-skills/bohrium-job` 脚本链

## Decision Summary

- 保持 Bohrium 主提交流程不变：`job/create -> Tiefblue upload -> job/add`
- 不将 Bohrium tool 改为项目自有 OSS URL 传递模式
- 在 `matmaster/tools/builtin/bohrium_tool.py` 中新增文件级共享预处理层
- `BohriumTool._submit()` 在上传前调用该预处理层，把 `input_dir` 统一转换为本地临时 `input.zip`
- `input_dir` 支持三类输入：
  - 相对目录
  - 本地绝对目录
  - 远端共享目录 `/share/...` 与 `/personal/...`
- 对远端共享目录采用 远端先打包、再下载、再本地统一生成 `input.zip` 的策略
- 无活动 session 时，对远端共享目录快速失败并返回明确错误

## Findings

### 1. Bohrium 输入文件并不走项目自有 OSS URL

仓库内置 `Bohrium` tool 和脚本版提交流程都采用同一模式：

1. 调用 `job/create`
2. 从响应中拿到 `storePath`、`storeHost`、`token`
3. 用 `Tiefblue` 上传输入归档
4. 调用 `job/add` 并通过 `ossPath` 引用刚才的对象

这说明 Bohrium 作业输入走的是 Bohrium 自身对象存储通道，而不是项目 calculation adaptor 使用的阿里云 OSS URL 模式。

### 2. 当前 bug 的根因是路径访问语义错误

当前 `BohriumTool._submit()` 直接执行：

- `Path(input_dir).is_dir()`
- `input_path.rglob('*')`

这隐含假设 `input_dir` 位于当前 Python 进程可见的本地文件系统。对于远端执行上下文中的 `/share/Pd111_submit`，该假设不成立，因此报出：

```text
input_dir not found: /share/Pd111_submit
```

### 3. 现有 `poll(result_dir)` 已经有远端路径语义

`BohriumTool._poll()` 已通过 runtime bridge 的 `resolve_output_path()` 判断：

- 相对路径
- 本地绝对路径
- 远端共享路径 `/share/...`、`/personal/...`

这次提交修复应与该路径语义保持一致，而不是再发明一套不同规则。

## Scope

### In Scope

- 修改 `matmaster/tools/builtin/bohrium_tool.py`
- 为 `submit` 增加远端共享目录处理能力
- 扩展 `tests/matmaster/tools/builtin/test_bohrium_tool.py`
- 必要时新增与 Bohrium tool 强相关的集成回归测试

### Out of Scope

- 修改 Bohrium OpenAPI 协议或 `job/add` 参数格式
- 将提交流程切换为项目自有 OSS URL
- 维护 `playground-skills/bohrium-job` 脚本链兼容
- 为普通脚本引入新的 session 注入机制

## Design Principles

- 单一职责：预处理层只负责把 `input_dir` 转换成可上传的本地归档
- 协议不动：不改 `job/create`、`Tiefblue` 上传、`job/add`
- 显式失败：远端路径缺少 session 时快速报错，不做 silent fallback
- 行为收敛：路径分类规则与 `poll(result_dir)` 保持一致
- 兼容优先：本地目录成功路径与现有实现保持一致

## Target Architecture

### Module Placement

共享预处理逻辑放在：

- `matmaster/tools/builtin/bohrium_tool.py`

但必须是 文件级共享辅助层，而不是 `BohriumTool` 类内部私有逻辑。

推荐结构：

```text
matmaster/tools/builtin/bohrium_tool.py
  - imports
  - shared helper dataclasses / context managers
  - shared helper functions for input_dir preflight + bundling
  - BohriumTool class
```

### Shared Helper Boundary

建议新增一个文件级入口，命名可类似：

```python
def prepare_bohrium_input_bundle(
    *,
    input_dir: str,
    workdir: Path | None,
    session: Any | None,
) -> PreparedBohriumBundle:
    ...
```

建议返回轻量对象，命名可类似：

```python
@dataclass
class PreparedBohriumBundle:
    zip_path: Path
    source_kind: Literal["relative", "local_abs", "remote_share"]
    normalized_input_dir: str
    cleanup: Callable[[], None] | None
```

该辅助层只负责：

- 路径分类
- 目录可访问性校验
- 本地或远端目录打包
- 返回本地 `input.zip`
- 清理临时文件

该辅助层不负责：

- `ToolResult`
- OpenAPI 请求
- Tiefblue 上传
- `job/add` 参数拼装

## Data Flow

### Step 1: Classify `input_dir`

输入为原始 `input_dir` 字符串。

分类规则：

- 相对路径：按 `workdir` 解析
- 本地绝对路径：按本地目录处理
- `/share/...`、`/personal/...`：识别为远端共享目录

推荐复用或镜像 `resolve_output_path()` 的路径语义，而不是另写一套完全独立规则。

### Step 2: Validate accessibility

#### 本地目录

- 校验路径存在
- 校验其为目录

#### 远端共享目录

- 要求存在活动 session
- 用 `session.exec_bash()` 做目录级校验，例如 `test -d`
- 不使用 `session.is_file()` 代替目录校验

### Step 3: Produce a local bundle

#### 本地目录

- 遍历目录
- 本地打包为临时 `input.zip`

#### 远端共享目录

- 在远端会话中创建临时归档，推荐 `tar.gz`
- 使用 `session.download()` 将该归档下载到本地临时目录
- 本地解包后重新统一打包为 `input.zip`

### Step 4: Continue existing submit flow

`BohriumTool._submit()` 获取 `PreparedBohriumBundle.zip_path` 后，继续执行现有逻辑：

1. `job/create`
2. 上传 `input.zip` 到 Bohrium `storePath`
3. `job/add`

## Remote Directory Strategy

### Recommended approach

采用 远端先打包、再下载单个归档 的方式，而不是逐文件拉取。

原因：

- 减少远端文件系统往返调用
- 自动保留目录层级
- 中间状态更少，失败恢复更清晰
- 对包含子目录的科学计算输入目录更稳妥

### Remote packaging details

推荐流程：

1. 通过 `session.exec_bash()` 在远端创建临时归档
2. 归档文件名放在 `/tmp/` 下，带随机后缀
3. 下载该归档到本地临时目录
4. 本地解包并重新生成标准 `input.zip`
5. 使用 `finally` 尝试删除远端临时归档

### Why normalize back to `input.zip`

虽然远端中间格式可用 `tar.gz`，但 Bohrium tool 当前后续逻辑与测试都围绕 `input.zip` 展开。统一回 `input.zip` 可以最大程度降低改动面并保持上传阶段一致性。

## Error Handling

### Required error categories

需要把以下错误类型区分开：

1. 本地目录不存在
2. 本地路径不是目录
3. 远端共享目录缺少活动 session
4. 远端目录不存在
5. 远端打包失败
6. 远端归档下载失败

### Error message guidance

推荐错误信息要能直接说明问题类型。

例如：

```text
input_dir '/share/Pd111_submit' requires an active remote session, but none is available
```

```text
Remote input_dir not found: /share/Pd111_submit
```

```text
Failed to package remote input_dir '/share/Pd111_submit': <stderr>
```

不要再把所有问题都折叠成：

```text
input_dir not found: /share/Pd111_submit
```

## Compatibility

### Preserved behavior

以下行为必须保持不变：

- 本地绝对目录提交成功
- 相对目录提交成功
- `cmd` 自动补 `> log 2>&1`
- `job/create -> upload -> job/add` 的提交流程不变
- 现有 `poll` 行为不变

### Deliberate non-compatibility

本次设计明确不处理：

- `playground-skills/bohrium-job`
- `matmaster/skills/bohrium/scripts/*.py`
- `matmaster/skills/playground-skills/bohrium-job/scripts/*.py`

这些内容已处于即将归档废弃路径，不属于本次修复兼容面。

## Testing Strategy

### Local regression tests

扩展现有 `tests/matmaster/tools/builtin/test_bohrium_tool.py`，覆盖：

- 本地绝对目录提交成功
- 相对目录提交成功
- 现有 `cmd` 自动补齐逻辑保持不变

### New remote-input tests

新增或扩展测试覆盖：

- 有活动 session 时，`/share/...` 目录提交成功
- 有活动 session 时，`/personal/...` 目录提交成功
- 无 session 时，远端共享目录报 remote session required
- 远端目录不存在时报明确错误
- 远端打包失败时报明确错误

### Cleanup tests

覆盖：

- 远端临时归档会被清理
- 本地中间文件会被清理
- 空目录提交行为明确
- 文件路径误传为目录时报错明确

## Success Criteria

本次修复完成后，应满足：

1. `Bohrium(action="submit", input_dir="relative/path", ...)` 正常工作
2. `Bohrium(action="submit", input_dir="/abs/local/path", ...)` 正常工作
3. `Bohrium(action="submit", input_dir="/share/...", ...)` 在有活动 session 时正常工作
4. `Bohrium(action="submit", input_dir="/share/...", ...)` 在无活动 session 时快速失败且报错明确
5. Bohrium OpenAPI 提交协议与 Tiefblue 上传模式保持不变
6. 不再把远端共享目录误报成本地目录不存在

## Risks

- 远端打包命令依赖目标运行环境具备 `tar`
- 远端目录很大时，本地临时归档与解包会带来额外磁盘占用
- 如果错误处理不严谨，容易残留远端 `/tmp` 临时文件

这些风险都属于可控范围，且明显低于逐文件远端拉取的复杂度与脆弱性。

## Final Recommendation

按本设计执行：

- 在 `bohrium_tool.py` 中新增文件级输入预处理层
- 让 `BohriumTool._submit()` 在上传前统一调用该层
- 保持 Bohrium 提交主链路不变
- 只修内置 `Bohrium` tool，不处理即将归档的脚本链
