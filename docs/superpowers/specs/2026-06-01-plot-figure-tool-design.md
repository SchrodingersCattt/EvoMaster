# PlotFigure 绘图工具:从 BashTool 拆分 figure 链路

日期: 2026-06-01
分支: refactor/context
状态: 设计已确认, 已按 review 修订为单一 PlotFigure 双模式方案, 待实现

> 说明:本仓库 figure 聚合层已重构进
> `src/services/figure_coordinator.py`(`FigureCoordinator`)。本 spec 的 prose
> 描述以当前 checkout 的架构边界为准;实现时仍需复核实时行号。
> 本设计不包含 shell safety policy 调整。

## 1. 目标

把当前内嵌在 `BashTool` 里的 figure 收集链路拆成一个绘图/发图专用的
builtin tool: `PlotFigure`。

`PlotFigure` 是唯一新增的模型可见工具, 负责把一张 workspace 内图片发布到最终回答。
它支持两种调用形态:

- **生成并发布**:提供 `command`、`output_path`、`caption`。工具先执行命令,
  再按 `output_path` 收图、上传、产出 figure 元数据。
- **发布已有图片**:只提供 `output_path`、`caption`, 不提供 `command`。工具不执行
  shell, 直接把已有图片收图、上传、产出 figure 元数据。

具体目标:

- 新增 `PlotFigure`:一个工具覆盖"执行绘图命令并发布图片"与"发布已有图片"。
- 用 tool 参数 `output_path` 替代脆弱的"存到 `$ARTIFACT_DIR` + 写
  `$MANIFEST_PATH` 清单文件"隐式契约。
- `output_path` 放宽到整个 workspace 内任意路径, 支持绝对路径和相对 workspace
  路径, 并在越界时明确拒绝。
- `BashTool` 卸掉全部 figure 逻辑, 回归纯命令执行;`BashTool` 与
  `PlotFigure` 共用抽出的 bash 执行核心。
- 下游回答级聚合(`FigureCoordinator` -> `ResponseFiguresAccumulator` ->
  `ResponseFiguresEvent` -> fanout)零改动。
- 按项目"禁止兼容、偏好迁移"原则, 旧 manifest 链路直接删除, 不留内联兜底。

## 2. 当前事实与背景

figure 链路当前由两条互锁子链构成, 接合点是 `ToolResultEvent.payload["figures"]`。

### 2.1 上传子链(写入侧, 当前内嵌在 BashTool)

- `FigureUploadConfig` 由
  `src/services/agent_run_bohrium_stage.py:_build_figure_upload_config` 构造。
  当前由 `src/services/figure_coordinator.py:FigureCoordinator.__init__` 调用。
- `src/services/agent_run_service.py` 构造 `FigureCoordinator`, 取
  `figure_upload_config = figure_coordinator.upload_config`, 再通过
  `AgentRunPorts.figure_upload` 注入运行请求。
- `matmaster/core/exp.py` 把 `request.ports.figure_upload.config` 放进
  `runner_state["figure_upload_config"]`。
- `matmaster/tools/builtin/bash_tool.py` 当前承担了额外 figure 职责:
  - 执行前通过 `build_figure_env(workdir, tool_call_id)` 算出按 tool call
    隔离的 `$ARTIFACT_DIR` / `$MANIFEST_PATH`。
  - prompt 要求模型把图存进 `$ARTIFACT_DIR`, 再写 `$MANIFEST_PATH` JSON 清单。
  - 执行后 `collect_figures_from_session(...)` 读取 manifest、校验、上传,
    并写入 `ToolResult.payload["figures"]`。
- `matmaster/tools/figure_artifacts.py` 当前提供 manifest 收割流水线:
  `_load_manifest`、`_resolve_artifact_path`、`_download_with_retry`、
  `_validate_image_bytes` / `_sniff_image_format`、`_build_asset_key`、
  `_upload_with_retry`、`_link_figure_into_flat_view`, 产出 `FigureDescriptor`。

### 2.2 聚合子链(读出侧, 封装在 FigureCoordinator)

回答级聚合已集中在 `src/services/figure_coordinator.py:FigureCoordinator`:

- 内部持有 `ResponseFiguresAccumulator` 与 `asyncio.Lock`。
- `record_tool_result(event, include_spawned, reason)` 在锁内调用
  `accumulator.add_tool_result(event, include_spawned=...)` 后 flush。
- `child_event_sink(event)` 对子 agent 的 `ToolResultEvent` 调
  `record_tool_result(include_spawned=True)`。
- `flush_if_dirty(reason)` / `_flush_if_dirty_unlocked(...)`:
  `build_snapshot_event_if_dirty` -> `flush_persistence_barrier` ->
  `dispatch_and_wait_persistence` -> `mark_snapshot_emitted`。
- `ResponseFiguresAccumulator` 按 `figure_id` 去重(first-writer-wins)、
  保到达顺序, 构造全量快照 `ResponseFiguresEvent`。
- `ResponseFiguresAccumulator.add_tool_result` 只从
  `event.payload["figures"]` 读图。

关键边界:聚合子链只认 `ToolResultEvent.payload["figures"]`, 不关心图片由哪个
tool 产生。本设计保持该边界不变。`PlotFigure` 两种模式产出的
`payload["figures"]` 都会被现有聚合链路原样吸收。

同时必须明确:下游不会扫描 workspace。`Bash`、`Bohrium`、skill 或任何脚本生成的
图片文件, 如果没有经过 `PlotFigure(output_path=..., caption=...)` 形成
`payload["figures"]`, 就不会进入 `ResponseFiguresEvent`。

## 3. 问题定义

现行契约要求模型做两件反直觉、易遗漏的事:

1. 把图存到一个陌生的、按 tool call 隔离的 `$ARTIFACT_DIR`。
2. 额外写一个 `$MANIFEST_PATH` JSON 清单。

这会造成两类常见失败:

- **图存到约定目录外**:图落在 `$ARTIFACT_DIR` 之外, manifest 里的 `path`
  经 `_resolve_artifact_path` 判为 `unsafe_path`。更糟的是 `_load_manifest`
  是 all-or-nothing:一条 entry 越界 / id 非法 / id 重复, 整份清单作废。
- **manifest 漏写或格式错**:忘写 `$MANIFEST_PATH`, 或 JSON 字段/格式不对。

任一失败都会导致 `payload` 无 figures -> accumulator 不脏 ->
不发 `ResponseFiguresEvent`, 图在最后一刻丢失, 且模型当场不知道如何纠正。

根因是契约设计, 不是下游聚合设计:问题出在"隐式写文件 + 强制特定目录 +
无即时反馈"。

## 4. 方案总览

新增唯一模型可见 builtin tool `PlotFigure`。它不是第二个 Bash, 而是回答级图片
发布入口:

- 如果需要现在生成图片, 调:

  ```text
  PlotFigure(command="python plot_band.py", output_path="band.png",
             caption="Band structure")
  ```

- 如果图片已经由 Bash / Bohrium / skill / 先前命令生成, 调:

  ```text
  PlotFigure(output_path="results/xrd_fit.png",
             caption="PXRD fit after refinement")
  ```

多图场景保持一次一图的发布语义, 但不要求重复执行昂贵绘图脚本:

```text
Bash(command="python make_all_figures.py")
PlotFigure(output_path="band.png", caption="Band structure")
PlotFigure(output_path="dos.png", caption="Density of states")
PlotFigure(output_path="pdos.png", caption="Projected density of states")
```

治理对应:

- 治"目录外":`output_path` 放宽到整个 workspace, 仅做 workspace containment
  校验, 不再强制 `$ARTIFACT_DIR`。
- 治"漏写/格式错":不再写 manifest 文件。图片由 `output_path` 声明,
  `caption` 由 tool 参数声明。
- 补"无反馈":tool 同步返回单图收集结果;失败当场给分类和修法。
- 保持"一次一图":每次 `PlotFigure` 只发布一张图, 多图多次调用, 便于精确反馈和
  first-writer-wins 去重。

## 5. 接口设计

`PlotFigure` 继承 `BuiltinTool`, 字段与方法对齐现有 builtin tool 风格:
扁平参数、克制字段、一句话 `description`、`prompt()` 承担使用指引。

```python
class PlotFigure(BuiltinTool):
    name: ClassVar[str] = "PlotFigure"
    description: ClassVar[str] = (
        "Generate or publish one figure and attach it to the response."
    )
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "command": {
                "type": "string",
                "minLength": 1,
                "description": (
                    "Optional shell command to generate the figure. "
                    "Omit this when output_path already exists."
                ),
            },
            "output_path": {
                "type": "string",
                "minLength": 1,
                "description": (
                    "Path to the image to attach. Absolute, or relative to "
                    "the session workspace."
                ),
            },
            "caption": {
                "type": "string",
                "minLength": 1,
                "description": "Caption shown with the figure in the response.",
            },
            "timeout": {
                "type": "integer",
                "minimum": 1,
                "maximum": 600000,
                "description": (
                    "Optional timeout in milliseconds for command execution. "
                    "Used only when command is provided. "
                    "Default 120000 (2 min), max 600000 (10 min)."
                ),
            },
        },
        "required": ["output_path", "caption"],
    }
    resource_claims: ClassVar[tuple[ResourceClaim, ...]] = (
        ResourceClaim(resource="workspace", mode="exclusive"),
        ResourceClaim(resource="session", mode="exclusive"),
    )
    capabilities: ClassVar[frozenset[str]] = frozenset({"shell.execute"})
    effect_level: ClassVar[str] = "local_mutation"
    max_result_chars: ClassVar[int] = 30_000
    plane: ClassVar[ToolPlane] = ToolPlane.SESSION_SHELL
```

`command` 可选, 不新增 `mode` / `action` 字段。是否提供 `command` 已足够表达
两种形态:

- 有 `command`:生成并发布。
- 无 `command`:发布已有图片。

`timeout` 仅在有 `command` 时生效。`PlotFigure` 不继承 `Bash` 的纯 `sleep`
1h 特例, 上限固定 10min。

建议 `prompt()`:

```text
Use PlotFigure for any figure that should appear in the final answer.

If you need to create the image now, provide command, output_path, and caption.
If the image already exists from Bash, Bohrium, a skill, or a previous command,
omit command and provide output_path and caption.

Bash output is never shown as an answer image by itself. To show an existing
image, publish it with PlotFigure. Write one figure per PlotFigure call; call it
again for additional figures.
```

## 6. output_path 校验

`PlotFigure.output_path` 允许相对 workspace 路径, 因此不能直接套用
`WriteTool.validate_input` 的绝对路径语义。

需要新增或内置一个 helper:

```python
def resolve_workspace_output_path(
    *,
    raw_path: str,
    workdir: str | PurePosixPath,
) -> str | None:
    root = PurePosixPath(posixpath.normpath(str(workdir)))
    candidate = (
        raw_path
        if posixpath.isabs(raw_path)
        else posixpath.join(str(root), raw_path)
    )
    resolved = PurePosixPath(posixpath.normpath(candidate))
    if not resolved.is_relative_to(root):
        return None
    return str(resolved)
```

`validate_input(arguments, runner_state)` 只做结构性输入检查:

- `output_path` 非空。
- `caption` 非空。
- `command` 如果出现, strip 后必须非空。
- `output_path` 必须位于 workspace 内。

不要在 `validate_input` 检查文件存在:

- 有 `command` 时, 文件在执行前尚未生成。
- 无 `command` 时, 存在性检查放在 execute 阶段能返回更完整的 `ToolResult`
  和可操作 guidance。

不复用 `grep_tool` 的 `resolve_safe_path`:它对越界输入会静默回退到 workdir,
而 `PlotFigure` 需要越界即 deny。

## 7. figure_id 生成与模型可见反馈

`figure_id` 不进入 schema, 由工具内部稳定生成:

```text
figure_id = sanitized_stem + "-" + sha256(image_bytes)[:12]
```

规则:

- `sanitized_stem` 来自 `output_path` basename 去后缀。
- 只保留 `A-Za-z0-9._-`。
- 其他字符折叠成 `-`。
- 连续 `-` 合并。
- 去掉首尾 `-`。
- 空 stem 用 `figure`。
- stem 最多保留 48 字符。
- 总长度不超过 64 字符。
- 不包含 `/`、NUL、控制字符、空白换行。

示例:

```text
plots/band structure.png -> band-structure-a1b2c3d4e5f6
结果图.png -> figure-a1b2c3d4e5f6
```

成功时 `ToolResult.content` 必须包含 `figure_id`, 因为模型下一轮可见的是
`ToolMessage.content`, 不是 `ToolResult.payload`:

```text
Figure attached:
- figure_id: band-structure-a1b2c3d4e5f6
- path: plots/band structure.png
- caption: Band structure
Use [[fig:band-structure-a1b2c3d4e5f6]] when referring to this figure.
```

这保证最终回答能正确使用 `[[fig:<figure_id>]]`。

## 8. 数据流

### 8.1 有 command:生成并发布

1. `figure_upload_config` 仍由 `FigureCoordinator` 经
   `_build_figure_upload_config` 构造, 并由 `exp.py` 注入 `runner_state`。
2. 模型调用:

   ```text
   PlotFigure(command="python plot.py", output_path="xrd.png",
              caption="XRD pattern")
   ```

3. `validate_input` 检查 `output_path` / `caption` / `command` 与 workspace 边界。
4. `execute_with_context`:
   - 从 `exec_ctx.runner_state` 取 `figure_upload_config`。
   - 取 `exec_ctx.tool_call_id`。
   - 调 `run_bash_command(...)` 执行 `command`。
   - 无论 exit code 是否为 0, 都调用 `collect_declared_figure(...)` 收
     `output_path`。
   - 组装 `ToolResult`;成功收到的图进入 `payload["figures"]`。
5. `ToolResult` -> `ToolResultEvent(payload.figures)` ->
   `FigureCoordinator.record_tool_result(...)` ->
   `ResponseFiguresAccumulator.add_tool_result(...)` ->
   `ResponseFiguresEvent`。

### 8.2 无 command:发布已有图片

1. 模型调用:

   ```text
   PlotFigure(output_path="results/xrd_fit.png",
              caption="PXRD fit after refinement")
   ```

2. `validate_input` 检查 `output_path` / `caption` 与 workspace 边界。
3. `execute_with_context`:
   - 不调用 `session.exec_bash`。
   - 从 `runner_state` 取 `figure_upload_config`。
   - 取 `tool_call_id`。
   - 直接调用 `collect_declared_figure(...)`。
   - 成功则返回 `payload["figures"]`;失败则返回 error content。

该形态用于 Bash、Bohrium、skill 或先前命令已经生成图片的场景。

## 9. 错误处理与即时反馈

### 9.1 命令错误

有 `command` 时, command observation 与 Bash 保持一致:

- 包含 stdout / stderr。
- 包含 `[Session working directory: ...]`。
- 包含 `[Command finished with exit code N]`。

命令失败仍尝试收图。科研脚本可能已经写出图片, 但在后处理步骤报错;这种情况不应
丢图。

status 判定:

- 命令 exit code == 0 且图成功收集 -> `success`。
- 命令 exit code != 0 但图成功收集 -> `error`, 但 payload 仍包含 figures。
- 命令成功但图收集失败 -> `error`, payload 不含 figures。
- 命令失败且图收集失败 -> `error`, content 同时说明命令失败与收图失败。

### 9.2 收图错误分类

`collect_declared_figure` 必须返回稳定分类, 不依赖 transport 异常文本:

- `outside_workspace`
- `file_not_found`
- `not_a_file`
- `unsupported_format`
- `image_header_mismatch`
- `figure_too_large`
- `download_failed`
- `upload_failed`

每类失败都要在 content 中给出可操作 guidance, 例如:

```text
Figure attachment failed: file_not_found
Expected image: plots/band.png
The command did not create this file. Re-run PlotFigure with the correct
output_path, or publish an existing image by omitting command.
```

缺 `figure_upload_config` 时返回 error:

```text
Figure attachment failed: figure upload is not configured for this run.
```

缺 `tool_call_id` 时返回 error, 因为 asset key 与 `source_tool_call_id` 依赖它。

## 10. 架构与代码组织

### 10.1 抽出共享 bash 执行核心

从 `BashTool._execute_with_figure_support` 中抽出纯执行部分, 建议新建
`matmaster/tools/bash_runner.py`:

```python
@dataclass(slots=True)
class BashRunResult:
    output: str
    exit_code: int
    working_dir: str
    observation: str

def run_bash_command(
    *,
    session: Any,
    command: str,
    timeout_s: float,
    cancel_token: CancellationToken | None,
    extra_env: dict[str, str] | None = None,
) -> BashRunResult:
    ...
```

职责:

- `plan_shell_command`
- `prepare_script_command` / `prepare_inline_command`
- runtime env 注入: `get_runtime(session).build_env()`
- 合并 `extra_env`
- `session.exec_bash(command, timeout, cancel_token)`
- 组装 observation

timeout 上限策略留在各 tool:

- `BashTool` 保留纯 `sleep` 1h 特例。
- `PlotFigure` 固定 10min cap。

`run_bash_command` 不负责 figure、不负责 upload、不负责 path validation。

### 10.2 figure_artifacts 复用与新增

复用:

- `_validate_image_bytes`
- `_sniff_image_format`
- `_build_asset_key`
- `_sanitize_key_segment`
- `_upload_with_retry`
- `_download_with_retry`
- `FigureDescriptor`
- `FigureUploadConfig`
- 各常量

新增:

```python
@dataclass(slots=True)
class DeclaredFigureResult:
    figure: FigureDescriptor | None
    failure_reason: str | None
    guidance: str | None = None
    resolved_path: str | None = None
    figure_id: str | None = None

def collect_declared_figure(
    *,
    session: Session,
    workdir: str,
    output_path: str,
    caption: str,
    tool_call_id: str,
    upload_config: FigureUploadConfig,
) -> DeclaredFigureResult:
    ...
```

执行顺序:

1. `resolve_workspace_output_path(...)`。
2. `session.path_exists(resolved_path)`。
3. `session.is_file(resolved_path)`。
4. `_download_with_retry(session=session, path=resolved_path)`。
5. `_validate_image_bytes(payload=payload, path=resolved_path)`。
6. 自动生成 `figure_id`。
7. `_build_asset_key(...)`。
8. `_upload_with_retry(...)`。
9. `_link_figure_into_flat_view(...)`。
10. 返回 `FigureDescriptor`。

### 10.3 flat symlink view 修正

旧 `_link_figure_into_flat_view` 通过 `artifact_dir` 反推 flat dir, 与新设计的
workspace 任意路径不匹配。改为基于 `workdir`:

```python
def _link_figure_into_flat_view(
    *,
    session: Session,
    workdir: str,
    resolved_path: str,
    figure_id: str,
) -> None:
    flat_dir = posixpath.join(posixpath.normpath(workdir), ".matmaster", "figures")
    suffix = posixpath.splitext(resolved_path)[1].lower()
    link_path = posixpath.join(flat_dir, f"{figure_id}{suffix}")
    rel_target = posixpath.relpath(resolved_path, start=flat_dir)
    ...
```

保留旧行为:

- 创建 `<workdir>/.matmaster/figures`。
- link path 为 `<workdir>/.matmaster/figures/<figure_id><suffix>`。
- target 使用相对路径。
- 已存在时只记录 warning, 不影响 figure 成功。
- symlink 失败只记录 warning, 不影响 figure 成功。

### 10.4 BashTool 卸载

`BashTool` 删除所有 figure 逻辑:

- 删除 import:`build_figure_env`、`collect_figures_from_session`、`FigureUploadConfig`。
- 删除 prompt 中 `$ARTIFACT_DIR` / `$MANIFEST_PATH` 说明。
- 删除 `execute_with_context` 中读取 `figure_upload_config` 的逻辑。
- 删除 artifact dir 创建、env 注入和 manifest 收割。
- 改为调用 `run_bash_command(...)`。

迁移后, `Bash` 可以生成图片文件, 但不会发布图片。发布图片统一由
`PlotFigure(output_path=..., caption=...)` 完成。

### 10.5 删除旧 manifest 链路

直接删除, 不保留兼容:

- `figure_artifacts.build_figure_env`
- `figure_artifacts.collect_figures_from_session`
- `figure_artifacts._load_manifest`
- `figure_artifacts._ManifestLoadResult`
- `figure_artifacts._resolve_artifact_path`
- `figures.FigureManifestEntry`

同步删除或迁移引用:

- `matmaster/tools/builtin/bash_tool.py`
- `matmaster/types/__init__.py`
- `tests/matmaster/types/test_figures.py`
- `tests/matmaster/tools/test_figure_artifacts*.py`
- `tests/matmaster/tools/builtin/test_bash_tool.py` 中旧 figure 相关用例

### 10.6 注册与提示

代码注册:

- `matmaster/tools/builtin/__init__.py`:导入并加入 `__all__`。
- `matmaster/core/exp.py`:
  - import `PlotFigure`。
  - `session_tools` 列表加入 `PlotFigure(session=env.session, workdir=exec_wd)`。
  - `_SESSION_REQUIRING_TOOL_NAMES` 加 `"PlotFigure"`。
  - docstring 工具清单更新。

提示迁移:

- `matmaster/exps/_base.toml` 的工具使用说明需要补充:
  - 最终回答要展示的图必须用 `PlotFigure` 发布。
  - `Bash` 生成图片不会自动展示为回答图片。
  - 已存在图片也通过无 `command` 的 `PlotFigure` 发布。

显式 builtin allowlist 的 toml 是否加入 `PlotFigure` 属于配置迁移决策, 不作为本
spec 的验收标准。

## 11. 测试策略

### 11.1 collect_declared_figure

- 相对路径成功。
- 绝对 workspace 内路径成功。
- `../escape.png` 越界失败。
- 文件不存在 -> `file_not_found`。
- 路径是目录 -> `not_a_file`。
- 非图片 -> `image_header_mismatch` 或 `unsupported_format`。
- 后缀和魔数不一致 -> `image_header_mismatch`。
- 过大 -> `figure_too_large`。
- download 重试耗尽 -> `download_failed`。
- upload 重试耗尽 -> `upload_failed`。
- 自动 `figure_id` 稳定、sanitize、长度受控。
- flat symlink 写入 `<workdir>/.matmaster/figures/<figure_id>.<ext>`。

### 11.2 PlotFigure 有 command

- 命令成功 + 收图成功:`payload["figures"]` 含一张。
- content 包含命令输出、`figure_id`、`[[fig:<figure_id>]]` guidance。
- 命令失败但图已生成:返回 `status="error"`, payload 仍含 figures。
- 命令成功但图不存在:返回 error, payload 不含 figures。
- `output_path` 越界:`validate_input` deny。
- 合法相对路径不被误拒。
- 缺 `figure_upload_config`:返回 error。
- 缺 `tool_call_id`:返回 error。

### 11.3 PlotFigure 无 command

- 已存在图片成功发布。
- 不调用 `session.exec_bash`。
- content 包含 `figure_id` 与 `[[fig:<figure_id>]]` guidance。
- 文件不存在 / 非图片 / 越界路径返回明确 error。
- `timeout` 参数在无 command 模式下不产生执行行为。

### 11.4 共享执行核心

- `run_bash_command` 提取后, `BashTool` 现有命令执行行为不变。
- `BashTool` 保留纯 `sleep` 1h 特例。
- `PlotFigure` 使用同一执行核心, 但使用固定 10min cap。
- cancel token 仍传给 `session.exec_bash`。

### 11.5 BashTool 回归

- `BashTool` 不再注入 `ARTIFACT_DIR` / `MANIFEST_PATH`。
- `BashTool` 不再读取 `figure_upload_config`。
- `BashTool` 不再产出 `payload["figures"]`。
- 旧 manifest / bash figure 相关用例迁移到 `PlotFigure` 或删除。

### 11.6 端到端聚合

- `PlotFigure(command=...)` -> `ToolResultEvent(payload.figures)` ->
  `ResponseFiguresEvent`。
- `PlotFigure(output_path=...)` 无 command 也触发 `ResponseFiguresEvent`。
- 多次 `PlotFigure(output_path=...)` 形成完整快照 `[fig1]`、`[fig1, fig2]`。
- child agent 的 `PlotFigure` payload 仍经 `child_event_sink` promote 到 parent
  response figures。
- duplicate `figure_id` 仍 first-writer-wins。

## 12. 改动清单

新增:

- `matmaster/tools/builtin/plot_figure_tool.py`
- `matmaster/tools/bash_runner.py`
- `matmaster/tools/figure_artifacts.py`:
  - `DeclaredFigureResult`
  - `collect_declared_figure`
  - `resolve_workspace_output_path`
  - 自动 `figure_id` 生成 helper

修改:

- `matmaster/tools/builtin/bash_tool.py`
- `matmaster/tools/builtin/__init__.py`
- `matmaster/core/exp.py`
- `matmaster/tools/figure_artifacts.py`
- `matmaster/types/__init__.py`
- `matmaster/types/figures.py`
- `matmaster/exps/_base.toml`

删除:

- `figure_artifacts.build_figure_env`
- `figure_artifacts.collect_figures_from_session`
- `figure_artifacts._load_manifest`
- `figure_artifacts._ManifestLoadResult`
- `figure_artifacts._resolve_artifact_path`
- `figures.FigureManifestEntry`
- 旧 manifest / bash figure 测试

不动:

- `src/services/figure_coordinator.py`
- `src/services/response_figures_service.py`
- `src/services/agent_run_service.py` 的 figure 聚合接线
- `matmaster/types/events.py:ResponseFiguresEvent`
- `FigureDescriptor`
- `FigureUploadConfig`
- `agent_run_bohrium_stage.py:_build_figure_upload_config`

## 13. 非目标

- 不新增第二个图片发布 tool。
- 不保留旧 manifest 链路的任何兼容/兜底。
- 不扫描 workspace 自动发现图片。
- 不让 `Bash` / `Bohrium` / skill 自动发布图片;这些路径产出的图片必须通过
  `PlotFigure(output_path=..., caption=...)` 显式发布。
- 不支持一次 `PlotFigure` 发布多图;多图多次调用。
- 不把 `alt` / `importance` / `placement_hint` 暴露到 schema。
- 不改 figure 上传后端和 `FigureUploadConfig` 契约。
- 不在本设计内做绘图模板化、自动发现或受控目录扫描。
- 不在本设计内调整 shell safety policy。

## 14. 验收标准

- 只新增一个模型可见 builtin tool:`PlotFigure`。
- `PlotFigure(command, output_path, caption)` 能执行命令并发布一张图。
- `PlotFigure(output_path, caption)` 能发布 workspace 内已有图片, 且不执行 shell。
- `output_path` 支持绝对路径和相对 workspace 路径;越界在输入校验阶段被拒。
- 命令 exit code 非 0 时仍尝试收图;如果图已生成, payload 仍包含 figures。
- 成功时 `ToolResult.content` 必须包含 `figure_id` 和 `[[fig:<figure_id>]]`
  引用提示。
- `ToolResult.payload["figures"]` 使用现有 `FigureDescriptor` 格式。
- `figure_id` 自动生成、稳定、sanitize、长度受控。
- `BashTool` 不再包含任何 figure manifest / artifact env / upload 逻辑。
- 旧 manifest 链路已删除, 无兼容残留与 import break。
- flat symlink view 继续存在, 但基于 `workdir`, 不再依赖旧 `artifact_dir`。
- 下游聚合层不改, 端到端测试证明 `PlotFigure` 两种模式都能产出
  `ResponseFiguresEvent`。
