# Bohrium Runtime 简化重构设计

## 背景

当前 Bohrium 相关实现横跨以下几类模块：

- `matmaster/tools/builtin/bohrium_tool/__init__.py`
- `matmaster/tools/builtin/bohrium_tool/_helpers.py`
- `matmaster/tools/builtin/bohrium_tool/_results.py`
- `matmaster/tools/builtin/bohrium_tool/_api.py`
- `matmaster/integration/runtime_bridge/bridge.py`
- `matmaster/integration/runtime_bridge/path_policy.py`
- `matmaster/integration/runtime_bridge/adapters/bohrium.py`

其中，鉴权、路径解析、远端目录打包、Tiefblue 上传、作业状态查询、结果下载与远端回写等职责相互交织，并夹杂多层中转函数与历史迁移兼容包装。其结果是：

- 调用栈过长，追踪一个字段时需要在多个文件间往返跳转
- 同一概念存在多套中间表示，例如 `OutputPathDecision`、`_DownloadTargetDir`、`tuple[str, Path | str]`
- 失败路径风格不统一，混用 `ToolResult`、`ValueError`、`RuntimeError`
- `runtime_bridge` 中混入了 Bohrium 专属路径语义，边界不清
- 技能脚本 `matmaster/skills/playground-skills/bohrium-job/scripts/submit_job.py` 与主实现重复维护提交逻辑

本设计目标是在一次性重构中压平这些历史包装层，保留已有 Bohrium 凭证解析能力，同时将路径与工件生命周期彻底收回 Bohrium 领域。

## 目标

1. 让 Bohrium 调用栈清晰可追踪，删除无业务语义的中转层与兼容包装。
2. 保留现有 Bohrium 凭证解析逻辑，不重新设计另一套鉴权包装。
3. 将路径解析从 `runtime_bridge` 中迁出，改为 Bohrium 领域内的专属规则。
4. 统一 submit 和 download 两条链路中的路径模型、工件传输模型与错误处理风格。
5. 让 `BohriumTool` 只承担 action 编排职责，不再直接承载路径、传输与 SDK 细节。
6. 让技能脚本复用主实现中的 Bohrium 提交能力，消除平行实现。

## 非目标

- 不重写现有 Bohrium 凭证 precedence 规则
- 不重构与 Bohrium 无关的其他 builtin tool
- 不将所有运行时能力抽象成更大的跨服务平台层
- 不保留过渡期兼容包装，本次为一次性切换

## 当前问题分析

### 1. BohriumTool 顶层承担过多职责

当前 `BohriumTool` 的单文件实现同时负责：

- 参数校验
- Bohrium 凭证获取
- 路径分类
- 输入目录打包
- OpenAPI `create` / `add` / `detail` / `poll`
- Tiefblue 上传
- 结果下载与解压
- 远端回写
- 异常转换为 `ToolResult`

这导致 `__init__.py` 体积过大，阅读时需要同时维护多种执行语义。

### 2. 存在多层无意义中转

当前 download 路径存在如下调用链：

`_download`
-> `bohrium_tool.__init__._resolve_download_target_dir`
-> `bohrium_tool._helpers._resolve_download_target_dir`
-> `runtime_bridge.bridge.resolve_output_path`
-> `runtime_bridge.path_policy.resolve_output_path`

其中多层函数仅做参数透传，不引入新的领域语义。

### 3. runtime_bridge 混入 Bohrium 领域路径语义

`runtime_bridge` 的价值在于凭证解析与环境投影。当前 `resolve_output_path` 与 `path_policy.py` 将 Bohrium 的 `/share`、`/personal`、远端 session 可用性等专属规则塞入通用 bridge，削弱了边界清晰度。

### 4. 中间对象和错误风格不统一

当前存在以下不一致：

- 输入路径解析返回 `tuple[str, Path | str]`
- 下载路径解析返回 `_DownloadTargetDir`
- 通用 bridge 返回 `OutputPathDecision`
- 有的 helper 返回 `ToolResult(error)`
- 有的 helper 抛 `ValueError`
- 顶层再捕获部分异常拼装为 `ToolResult`

这种不一致直接增加认知负担。

### 5. SDK 与主实现存在重复逻辑

技能脚本 `submit_job.py` 中复制维护了一套提交三步：

- `job/create`
- Tiefblue 上传
- `job/add`

如果主实现重构而脚本不收敛，后续仍会出现语义漂移。

## 设计原则

1. 保留有价值的边界，删除无语义中转。
2. Bohrium 的路径规则必须留在 Bohrium 领域，而不是放在通用 bridge。
3. 领域内部统一使用 领域对象 + 领域异常，顶层统一转 `ToolResult`。
4. `BohriumTool` 是 Session、Open SDK 与 OpenAPI 之间的编排桥，但桥的实现必须显式对象化，而不是散落在多个 helper 中。
5. 测试结构必须随实现一起收敛，否则旧 patch 点会继续固化旧结构。

## 保留与删除边界

### 保留

保留现有 Bohrium 凭证解析链，不新增鉴权包装：

- `matmaster/integration/runtime_bridge/resolver.py`
- `matmaster/integration/runtime_bridge/env_projector.py`
- `matmaster/integration/runtime_bridge/adapters/bohrium.py`
- `matmaster/integration/runtime_bridge/models.py` 中与凭证相关的模型

这套链路已经实现了：

- `explicit > session > env > none`
- 从 `session._bohrium_credentials` 读取 Bohrium 凭证
- 从 `.env` 或环境变量回退读取 `BOHRIUM_ACCESS_KEY` 等字段
- 将凭证投影为 `BOHRIUM_*` 环境变量
- 为 MCP executor 注入 Bohrium 鉴权字段

### 删除或迁出

以下内容应在本次重构中删除或迁出：

- `matmaster/integration/runtime_bridge/bridge.py`
- `matmaster/integration/runtime_bridge/path_policy.py`
- `runtime_bridge` 对外暴露的 `resolve_output_path`
- `OutputPathDecision` 作为运行时路径模型的职责
- `bohrium_tool/__init__.py` 中的薄包装 helper
- `bohrium_tool/_helpers.py` 与 `_results.py` 中职责不清的中间实现

## 目标结构

### 1. runtime_bridge 只保留凭证职责

`runtime_bridge` 在重构后的定位是：

- 解析 Bohrium 凭证
- 构造 Bohrium 环境变量
- 为 MCP executor 注入 Bohrium 鉴权参数

它不再承担路径解析与路径判定职责。

### 2. Bohrium 领域内建立清晰模块边界

本次重构中，Bohrium 领域实现重组为以下模块：

- `matmaster/tools/builtin/bohrium_tool/tool.py`
  - 只放 `BohriumTool`
  - 只做 action 编排与 `ToolResult` 组装
- `matmaster/tools/builtin/bohrium_tool/api.py`
  - Bohrium OpenAPI 封装
  - 负责 `create`、`add`、`detail`、`poll`
  - 负责 sandbox 与普通 HPC 差异
  - 负责终态确认与状态映射
- `matmaster/tools/builtin/bohrium_tool/paths.py`
  - Bohrium 专属路径解析
  - 负责 `input_dir` 与 `result_dir` 的领域判定
- `matmaster/tools/builtin/bohrium_tool/transfers.py`
  - 输入打包
  - 结果下载
  - staging 生命周期
  - 远端回写与临时目录清理
- `matmaster/tools/builtin/bohrium_tool/open_sdk.py`
  - 封装 `bohrium_open_sdk` 导入与 Tiefblue 上传
- `matmaster/tools/builtin/bohrium_tool/models.py`
  - 领域数据模型
- `matmaster/tools/builtin/bohrium_tool/errors.py`
  - 领域异常类型
- `matmaster/tools/builtin/bohrium_tool/__init__.py`
  - 仅导出稳定入口，例如 `BohriumTool`

## 领域模型设计

### BohriumContext

用于表示 Bohrium 运行上下文，包含：

- `access_key`
- `project_id`
- `base_url`
- `credential_source`
- 必要时的 `user_id`、`user_no`

该对象由已有 Bohrium 凭证解析链构造，`BohriumTool` 不自己实现任何凭证 precedence 逻辑。

### BohriumInputSource

表示 submit 的输入目录来源，至少包含：

- `kind`
  - `local_dir`
  - `remote_share_dir`
- `raw_path`
- `resolved_path`

构造成功即表示：

- 所需 session 条件已满足
- 路径存在
- 路径是目录

因此不再保留单独的 `requires_remote_session` 等半完成状态字段。

### BohriumDownloadTarget

表示 download 的目标目录，至少包含：

- `kind`
  - `local_dir`
  - `remote_share_dir`
- `raw_path`
- `resolved_path`
- `staging_dir`
- `publish_mode`
  - `local_only`
  - `upload_back`

设计意图：

- 所有下载动作都统一写入 `staging_dir`
- 本地目标场景下，`staging_dir` 就是目标目录
- 远端共享目标场景下，`staging_dir` 是本地临时目录，完成后再回写到 `resolved_path`

## 路径解析规则

### 输入路径

`resolve_input_source` 负责以下规则：

- 相对路径按 `workdir` 解析为本地路径
- 本地绝对路径直接使用
- `/share` 与 `/personal` 视为 Bohrium 远端共享目录
- 远端共享目录必须有可用且打开的 session
- 远端共享目录必须存在且必须是目录

### 下载路径

`resolve_download_target` 负责以下规则：

- 相对路径按 `workdir` 解析为本地目标目录
- 本地绝对路径直接作为目标目录
- `/share` 与 `/personal` 视为远端回写目标
- 远端目标要求存在可用且打开的 session
- 远端目标下载时总是先创建本地 staging 目录

说明：

远端 download 天然是两阶段过程：

1. 下载结果到本地 staging
2. 使用 session 回写到远端共享目录

该两阶段语义必须显式体现在模型中，而不是通过模糊的中间字段隐式表达。

## 工件生命周期设计

### 输入工件

submit 过程中统一采用以下流程：

1. 使用 `BohriumInputSource` 解析 `input_dir`
2. 通过 `transfers.py` 生成本地 `input.zip`
3. 若输入源是远端共享目录，则通过 session 在远端打包，再下载 zip 到本地
4. 将本地 zip 交给 `open_sdk.py` 通过 Tiefblue 上传

### 输出工件

download 过程中统一采用以下流程：

1. 使用 `BohriumDownloadTarget` 解析 `result_dir`
2. 根据 job detail 下载结果 zip 或对象列表到 `staging_dir`
3. 在 `staging_dir` 内解压、读取日志、构造结果清单
4. 若目标是远端共享目录，则通过 session 回写到远端
5. 回写成功返回远端目录，失败返回本地 staging 目录

## Open SDK 与 Session 的桥接边界

`BohriumTool` 的核心角色是桥接以下三类能力：

- Bohrium OpenAPI
- Bohrium Open SDK
- Session 远端文件系统能力

重构后的边界如下：

- `api.py`
  - 只处理 OpenAPI
- `open_sdk.py`
  - 只处理 Tiefblue 上传
- `transfers.py`
  - 只处理本地与远端工件搬运
- `tool.py`
  - 负责编排 submit、poll、download 工作流

这样可以保留桥梁角色，同时避免让 `BohriumTool` 单文件直接了解所有底层细节。

## 错误处理统一方案

### 原则

- 领域层与集成层只抛异常，不返回 `ToolResult`
- `BohriumTool` 顶层 action 方法在最外层统一捕获并转换为 `ToolResult`

### 异常类型

本次重构采用以下异常层级：

- `BohriumError`
- `BohriumCredentialError`
- `BohriumPathError`
- `BohriumTransferError`
- `BohriumAPIError`
- `BohriumJobStateError`

约束如下：

- `paths.py` 只抛 `BohriumPathError`
- `open_sdk.py` 只抛 `BohriumTransferError`
- `transfers.py` 抛 `BohriumTransferError` 或 `BohriumPathError`
- `api.py` 抛 `BohriumAPIError` 或 `BohriumJobStateError`
- `tool.py` 统一映射为 `ToolResult`

这消除当前 `ToolResult(error)`、`ValueError`、`RuntimeError` 混杂的问题。

## BohriumTool 顶层目标形态

重构后，`BohriumTool` 的 action 方法应呈现为短小的编排函数。

### submit

逻辑顺序应为：

1. 校验参数
2. 构建 `BohriumContext`
3. 解析 `BohriumInputSource`
4. 准备本地输入 zip
5. 调用 OpenAPI create
6. 通过 Open SDK 上传 zip
7. 调用 OpenAPI add
8. 组装成功结果

### download

逻辑顺序应为：

1. 校验参数
2. 构建 `BohriumContext`
3. 解析 `BohriumDownloadTarget`
4. 获取作业 detail 并确认终态
5. 下载结果到 `staging_dir`
6. 根据目标类型决定是否回写远端
7. 组装成功或失败结果

### poll

逻辑顺序应为：

1. 校验参数
2. 构建 `BohriumContext`
3. 调用 `api.py` 查询 detail
4. 根据 `wait` 与 job 状态做轮询或立即返回
5. 组装成功结果

## 文件迁移方案

### 新增文件

- `matmaster/tools/builtin/bohrium_tool/tool.py`
- `matmaster/tools/builtin/bohrium_tool/api.py`
- `matmaster/tools/builtin/bohrium_tool/paths.py`
- `matmaster/tools/builtin/bohrium_tool/transfers.py`
- `matmaster/tools/builtin/bohrium_tool/open_sdk.py`
- `matmaster/tools/builtin/bohrium_tool/models.py`
- `matmaster/tools/builtin/bohrium_tool/errors.py`

### 删除或瘦身

- 删除 `matmaster/integration/runtime_bridge/bridge.py`
- 删除 `matmaster/integration/runtime_bridge/path_policy.py`
- 删除 `matmaster/tools/builtin/bohrium_tool/_helpers.py`
- 删除 `matmaster/tools/builtin/bohrium_tool/_results.py`
- 将 `matmaster/tools/builtin/bohrium_tool/__init__.py` 改为稳定导出入口

### 迁移顺序

本次重构按以下顺序施工：

1. 新建 Bohrium 领域模型与异常
2. 实现 `paths.py`
3. 实现 `open_sdk.py`
4. 实现 `transfers.py`
5. 整理 `api.py`
6. 重写 `tool.py`
7. 更新 `__init__.py` 导出
8. 删除旧 helper 与旧 bridge
9. 收敛技能脚本到共享实现
10. 收敛测试

## 测试策略

测试必须与实现一起重构，避免旧 patch 点继续固化旧结构。

### 路径测试

新增或重写 `paths.py` 单元测试，覆盖：

- 相对本地路径
- 本地绝对路径
- 远端共享目录
- session 缺失
- session 未打开
- 远端路径不存在
- 远端路径是文件而非目录

### 传输与 SDK 测试

新增或重写 `transfers.py` 与 `open_sdk.py` 单元测试，覆盖：

- 本地目录打包
- 远端目录打包后下载
- Tiefblue 上传成功
- Tiefblue 上传失败
- 结果下载成功
- 远端回写成功
- 远端回写失败时回退为本地 staging 目录

### 编排测试

保留 `BohriumTool` 的 action 编排测试，但 patch 点应移动到新的职责边界：

- patch `paths.py`
- patch `api.py`
- patch `open_sdk.py`
- patch `transfers.py`

不再 patch 旧顶层兼容包装函数，也不再 patch `runtime_bridge.resolve_output_path`。

## 技能脚本收敛

`matmaster/skills/playground-skills/bohrium-job/scripts/submit_job.py` 不应继续维护一套独立的 Bohrium 提交流程。

重构后应改为：

- 只保留脚本层参数解析与 CLI 输出
- 复用 Bohrium 领域内的共享 submit 能力

这样可以避免主实现与技能脚本在 OpenAPI 路径、Tiefblue 上传、sandbox 差异等方面发生漂移。

## 风险与控制

### 风险 1：一次性切换导致 patch 点大范围变化

控制方式：

- 先定义新职责边界，再整体改测试
- 明确禁止保留旧顶层包装 patch 点

### 风险 2：路径重构误伤鉴权解析链

控制方式：

- 明确保留 `resolver.py + adapters/bohrium.py`
- 不在 Bohrium runtime 内新增鉴权包装

### 风险 3：submit 与 download 重构后行为不一致

控制方式：

- 将 input 与 output 都建模为 Bohrium 领域路径对象
- 将 staging 生命周期放在统一传输模块中

## 成功标准

完成后应满足以下标准：

1. 追踪 `input_dir` 或 `result_dir` 的解析链路时，不再经过 `runtime_bridge` 的路径逻辑。
2. `BohriumTool` 顶层 action 方法不直接处理路径细节、SDK 导入细节或文件传输细节。
3. Bohrium 内部统一使用领域对象和领域异常。
4. 现有 Bohrium 凭证解析仍然基于 session 与 `.env` 的既有逻辑。
5. 技能脚本不再维护独立的 Bohrium submit 三步实现。
6. 测试 patch 点对齐新的模块边界，不再依赖兼容包装。

## 决策摘要

本次重构不是简单删除几层函数，而是一次性完成以下边界收敛：

- 保留 Bohrium 凭证解析链
- 删除 generic bridge 与通用路径桥接
- 将路径、工件与 Open SDK 逻辑收回 Bohrium 领域
- 统一领域对象与异常语义
- 让 `BohriumTool` 成为清晰的编排入口

该设计以 Bohrium 的真实运行语义为中心，而不是继续沿用多阶段迁移时期留下的兼容结构。
