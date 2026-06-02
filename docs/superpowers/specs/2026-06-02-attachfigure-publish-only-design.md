# AttachFigure 设计:publish-only · 批量 · 全成功

> 原 `PlotFigure` 的重设计与改名。`PlotFigure` 在 publish-only 定位下已是误名(不再画图),改名为 `AttachFigure`。

日期:2026-06-02
范围:见 §12。

## 1. 背景与动机

现状 `PlotFigure` 有两种模式(command 跑命令生成 + publish 发布已存在文件),把"生成图"和"发布图"耦合在一个工具里:

- command 模式复用 `run_bash_command` 执行模型给的任意命令,成为继 Bash 之后**第二个任意命令入口**。
- 结果语义自相矛盾:命令 `exit_code != 0` 但图采集成功时,status 被设成 error 却仍 emit figure。
- 创建了一个 flat-view 符号链接 `<workdir>/.matmaster/figures/<figure_id>`,经核查后端前端均无消费方(前端文件访问用 `FigureDescriptor.remote_path`),是目前唯一让本工具还要碰 shell 的死基础设施。

## 2. 用途(定位)

`AttachFigure` 只做一件事:**把工作区里已存在的一张或多张图片上传为托管资产,产出 figure_id**,使模型能在 response 正文里用 `[[fig:<figure_id>]]` 指代图片、前端据此渲染。

- 不负责生成图片 —— 由 Bash 负责。
- 不负责本地扁平文件访问 —— 去掉符号链接。
- 对工具而言只有一种行为(发布已存在文件);模型侧两个场景共用它:(1) 刚生成了新图要发布;(2) 已存在一张图要引用。

标记格式是**双中括号** `[[fig:<figure_id>]]`,与前端 `response-figure-anchors.ts` 的正则 `/\[\[fig:([A-Za-z0-9._-]+)\]\]/g` 对齐。

## 3. schema(批量 + 绝对路径)

```json
{
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "figures": {
      "type": "array",
      "minItems": 1,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "properties": {
          "output_path": {
            "type": "string",
            "minLength": 1,
            "description": "Absolute path to an existing image inside the workspace."
          },
          "caption": {
            "type": "string",
            "minLength": 1,
            "description": "Caption shown with the figure in the response."
          }
        },
        "required": ["output_path", "caption"]
      }
    }
  },
  "required": ["figures"]
}
```

单图即单元素数组。删除 `command`、`timeout` 字段。

## 4. 校验分层

- **StructuralValidation**:仅按 schema 校验结构(`figures` 非空数组;每项含非空 `output_path` + `caption`)。注:`AttachFigure` 绑定 `EXTERNAL_SERVICE` plane,不在结构层的 `_WORKSPACE_PLANES` 内;且 `structural_validation._PATH_KEYS` 只认顶层 `file_path` / `path`,嵌套 `figures[*].output_path` 本就不会被结构层归一化 —— 所有路径语义由 `validate_input` 负责。
- **validate_input**(纯路径,无 session I/O):workdir 已注入;遍历 `figures[*]`,每项 `output_path` 必须 `posixpath.isabs` 且落在工作区内(`resolve_workspace_output_path` 非 None);**同批 `output_path` 不得重复**。任一不满足 → deny 整次调用。
- **执行阶段**:文件存在性、是否常规文件、图像字节合法性、`figure_id` 去重、上传。

## 5. 执行(两阶段 · 全成功)

`execute_with_context` 在事件循环里取出 `figure_upload_config` 与 `tool_call_id`,再 `asyncio.to_thread(_run)`。

- **Phase A — 校验全部,不上传**:对每个 figure 依次 `path_exists` → `is_file` → `download` → 图像字节校验 → 计算 `figure_id`,保留字节。随后校验**同批 `figure_id` 唯一**。任一项失败或出现重复 `figure_id` → 立即返回 error,**零上传**。
- **Phase B — 统一上传**:Phase A 全过后,逐项 `upload_bytes(asset_key)` 并构造 `FigureDescriptor`。某次上传(经 `_upload_with_retry` 仍)失败 → 返回 error;此前已上传字节成孤儿,无害(内容寻址、重试幂等)。

`figure_artifacts.py` 把现有 `collect_declared_figure` 拆成:

- `prepare_declared_figure(...)`:resolve → exists → is_file → download → validate → figure_id,返回(figure_id、字节、resolved_path、caption)或失败。
- `publish_prepared_figure(...)`:upload(asset_key) → 构造 descriptor。

同时删除 `_link_figure_flat`、`flat_dir` 及符号链接相关常量。绝对路径强制只在 `validate_input` 一处;越界路径仍由 `resolve_workspace_output_path` 归为 `outside_workspace`。

## 6. 结果语义(全成功)

- **全部成功** → `status="success"`;`payload.figures` = 全部 descriptor;content 逐项回显 `[[fig:id]]` + path + caption。
- **任一失败**(校验 / 重复 / 上传)→ `status="error"`;**`payload` 不含 figures**;content 逐项列出失败 path + reason + guidance,供模型修正后重发整批。

**关键不变量**:下游 `ResponseFiguresAccumulator` 只读 `payload.figures`、**不看 `ToolResult.status`**(`src/services/response_figures_service.py`)。所以"失败即零图"必须由"失败时 payload 为空"来保证;两阶段天然满足(全过才构造 descriptor)。此不变量必须有硬测试(见 §13),防止实现时边上传边 append 导致 error 仍泄出半套 payload。

## 7. 工具元数据(外部发布工具,对齐 Bohrium)

| 字段 | 现 PlotFigure | AttachFigure |
| --- | --- | --- |
| `name` | `"PlotFigure"` | `"AttachFigure"` |
| `capabilities` | `{"shell.execute"}` | `{"workspace.read"}` |
| `resource_claims` | workspace+session 双 exclusive | `(workspace, shared_read)` |
| `effect_level` | `local_mutation` | `external_effect` |
| `plane` | `SESSION_SHELL` | `EXTERNAL_SERVICE` |

并把 `"AttachFigure"` 加入 `exp.py` 的 `_EXTERNAL_EFFECT_TOOL_NAMES`,使配置它时激活 `EXTERNAL_SERVICE` plane,capability policy 才会对其 `external_effect` 真正把门(与 Bohrium 同范式:`matmaster/tools/builtin/bohrium_tool/tool.py:249-251`,`exp.py:68-75`、`capability_policy.py:109-112`)。

**不采用**"改 `DefaultCapabilityPolicy` 让所有 `external_effect` 无视 plane 都要门"的支路:那会改动全局 policy 语义、波及所有工具,风险更大,而 Bohrium 已确立 plane + 名单 的既有做法。

## 8. 绝对路径与工作区根

- `validate_input` 递归对 `figures[*].output_path` 做 `isabs` + 工作区边界,拒相对路径(与 `WriteTool` 一致)。
- `prompt()` 像 `BashTool` 那样通过 `ctx.workspace_root`(回退 `self._workdir`)把当前 session workspace 的绝对根写进工具描述,让模型知道往哪写绝对路径——否则模型会沿用 Bash/Read/Write 的相对路径习惯。

## 9. prompt / _base.toml

- `prompt()`:改为发布语气;说明两个场景(刚生成的图 / 已存在要引用的图);提示可在一次调用里发布多张;给出工作区绝对根;成功后用 `[[fig:id]]`。去掉两模式与 command 叙述。
- `_base.toml`:`[[fig:id]]` 的答案书写规则只留这一处(prompt 不再重复);把 `AttachFigure` 从 `Use dedicated tools over Bash: Read (not cat)...` 这串里拿出来单列(它不替代某个 bash 命令,而是"最终答案里的图必须用 AttachFigure 发布")。

## 10. 接受的取舍

- 模型需自行记得"先生成、再发布"(不做自动检测)。兜底:figure_id 内容哈希自动去重、图像校验挡明显错图、prompt 明确指引。
- 全成功:一个坏路径让整批失败,模型修正后重发。两阶段保证常见失败零上传。
- 上传中途失败可能留下孤儿字节,无害(内容/键寻址、幂等)。
- 同一次调用里**声明同一张图两次**按错误处理(整批失败)。注意这与模型在正文里**多处复用**同一个 `[[fig:id]]` 是两回事——后者允许且正常,前端逐处解析。

## 11. 不在范围内

- 新图自动检测(工作区快照 diff)—— 明确不做。
- flat-view 符号链接 / 扁平文件访问 —— 删除,除非出现真实消费方再议。

## 12. 范围(生产 + 测试/策略迁移)

**生产代码:**

- `matmaster/tools/builtin/plot_figure_tool.py` → 重命名为 `attach_figure_tool.py` 并重写(类名、`name`、schema、`prompt()`、文档串、`_execute` 占位串)。
- `matmaster/tools/figure_artifacts.py`:拆 `prepare`/`publish`,删 symlink;`:245/:267/:271` 的 guidance 文案中 `PlotFigure` → `AttachFigure`。
- `matmaster/tools/builtin/__init__.py`:`:16` import、`:36` `__all__` 改名。
- `matmaster/core/exp.py`:`:81` `_SESSION_REQUIRING_TOOL_NAMES`、`:659` import、`:680` 构造、`:636` 文档串 全部改名;并在 `_EXTERNAL_EFFECT_TOOL_NAMES`(`:68-75`)新增 `"AttachFigure"`。
- `matmaster/exps/_base.toml`:`:44` prompt 措辞(改名 + 拿出单列)。
- `matmaster/exps/direct.toml:73`、`matmaster/exps/planner.toml:463`:builtin 白名单 `"PlotFigure"` → `"AttachFigure"`。(explore.toml、verification.toml 未列该工具,无需改。)

**测试 / 策略迁移:**

- `tests/matmaster/tools/builtin/test_plot_figure_tool.py` → 重命名 `test_attach_figure_tool.py` 并重写:plane 断言 `SESSION_SHELL` → `EXTERNAL_SERVICE`;删 command 相关(`TestPlotFigureWithCommand`、empty_command、with_command);`test_valid_relative_allowed` 翻成 relative → deny;`_SESSION_REQUIRING_TOOL_NAMES` 断言(`:213`)改名;补批量与重复用例。
- `tests/matmaster/tools/test_figure_artifacts.py`:删 `_link_figure_flat` 相关(`:21` import、`:156-196`)。
- `tests/matmaster/tools/test_figure_artifacts_real_fs.py`:**整文件删除**(全部围绕 symlink)。
- `tests/matmaster/tools/test_collect_declared_figure.py`:按 `prepare`/`publish` 拆分重写。
- `tests/matmaster/services/test_plot_figure_aggregation.py`:改名引用;删 `test_command_mode_reaches_snapshot`、`test_failed_command_with_figure_still_aggregates`;新增"失败即零图"不变量与批量 N 用例。
- `tests/matmaster/core/test_exp.py`:`:538/:570/:571` 中 `"PlotFigure"` → `"AttachFigure"`(含对 direct/planner 配置的断言)。

## 13. 测试要点

- **单元(AttachFigure)**:
  - schema:空 `figures`、缺字段 → 拒。
  - validate_input:相对路径、越界、同批重复 `output_path` → deny 整次调用;合法绝对批量 → 放行。
  - Phase A 失败(file_not_found、not_a_file、unsupported_format、image_header_mismatch、figure_too_large、download_failed、重复 figure_id)→ error + 零上传。
  - Phase B 失败:批量 3 张、第三张上传失败 → `status=="error"`、`payload` 无 `figures`、喂给 `ResponseFiguresAccumulator` 后 `build_snapshot_event_if_dirty()` 为 `None`。
  - happy:单图、批量 N → success、N 个 descriptor、N 个 `[[fig:id]]`。
  - 重复三例:同路径同 bytes、不同路径同 basename 同 bytes、重复 id 不同 caption → 整批失败。
  - plane / 名单:`AttachFigure.plane == EXTERNAL_SERVICE`;`"AttachFigure"` 在 `_EXTERNAL_EFFECT_TOOL_NAMES`、`_SESSION_REQUIRING_TOOL_NAMES`。
- **后端集成**:批量发布 → accumulator 吸收 N → `response_figures` 快照含 N。
- **前端(另仓 `scimaster-bohr-chat`,不在本 spec 实施范围)**:`[[fig:id]]` anchor 解析,已有 `history-response-figures-render.test.ts`。
