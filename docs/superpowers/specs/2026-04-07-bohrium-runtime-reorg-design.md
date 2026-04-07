# Bohrium Runtime 重组设计

## 1. 背景

当前项目中，Bohrium 相关能力虽然在业务上已经是一套一体化运行时，但代码实现仍然分散在多个历史层次中：

- `matmaster/adaptors/calculation/`
- `matmaster/integration/runtime_bridge/`
- `matmaster/integration/bohrium_env.py`
- `src/services/agent_run_bohrium.py`

这些模块在命名上分别属于 adaptor、integration、service，但在真实职责上都直接参与 Bohrium 运行时的关键行为，包括：

- 凭证解析
- 环境变量投影
- executor 注入
- storage 构造
- 路径规则
- OSS 上传下载
- OpenAPI job 查询
- SSH session 附着与远端执行准备

结果是：

1. 同一份 Bohrium 语义被多个目录重复表达。
2. 主执行链最早在 `agent_run_bohrium.py` 中产生 Bohrium 运行时状态，但后续调用方仍然通过零散 helper 间接消费。
3. `runtime_bridge` 名义上是通用运行时桥，实际上承载的主实现仍然高度 Bohrium 化。
4. `CalculationPathAdaptor` 既做协议前处理，又承担部分 Bohrium 领域注入，边界不够清晰。
5. 当前已经存在 Bohrium 鉴权与 executor/storage 注入的双实现。

本次重构的目标不是机械合并目录，而是让代码结构准确表达项目当前真实架构：

- 短中期只有一个一等计算运行时：Bohrium
- 后续可能开放用户自定义计算资源或额外 MCP 计算后端
- 因此今天应该承认 Bohrium 中心现实，同时保留少量稳定扩展缝

## 2. 设计结论

本次采用 收敛版路线 B：

- `matmaster/bohrium/` 成为当前唯一的一等计算运行时实现
- `src/services/agent_run_bohrium.py` 成为 Bohrium runtime 的唯一注册与初始化入口
- `matmaster/mcp/calculation/` 保留为 calculation MCP 的协议前处理层
- 不再保留 `matmaster/integration/runtime_bridge/` 作为独立主结构
- 不再保留 `matmaster/integration/bohrium_env.py` 作为兼容主入口
- 不再保留 `matmaster/adaptors/calculation/` 这一命名与聚合方式
- 为未来扩展保留一个很薄的 `matmaster/calculation_runtimes/` 契约层，但它不承载 Bohrium 主实现

该设计的核心原则是：

1. 承认单后端现实，不做提前泛化。
2. 把 Bohrium 专属语义集中到 Bohrium 领域包中。
3. 把真正稳定的扩展点保留为窄接口，而不是保留一个过大的通用 bridge。
4. 所有后续调用都围绕 runtime object 展开，而不是围绕 helper 函数展开。

## 3. 目标结构

### 3.1 顶层目录

```text
matmaster/
  bohrium/
    __init__.py
    credentials.py
    env.py
    executor.py
    storage.py
    paths.py
    oss.py
    jobs.py
    runtime.py
    types.py
    errors.py
    session.py

  calculation_runtimes/
    __init__.py
    base.py
    types.py
    registry.py

  mcp/
    calculation/
      __init__.py
      preflight.py
      selectors.py
      config_env.py
      errors.py
```

### 3.2 各层职责

#### `matmaster/bohrium/`

这是当前唯一的一等计算运行时实现，集中承载所有 Bohrium 专属语义，包括：

- 凭证和值对象定义
- session runtime attach / get / detach
- shell/script 环境投影
- executor 注入
- storage 构造
- 路径物化
- OSS 上传下载
- OpenAPI job 相关客户端逻辑

凡是显式依赖以下 Bohrium 语义的内容，都属于这一层：

- `BOHRIUM_*`
- `/share`
- Tiefblue / OSS 上传
- `dispatcher` / `local`
- Bohrium OpenAPI
- `project_id` / `user_no`

#### `matmaster/calculation_runtimes/`

这是未来扩展缝，但保持极薄。

它只定义 calculation 运行时对外需要的最小能力接口，例如：

- 获取 execution context
- 构造 env
- 构造 submission spec
- 物化输入路径

当前注册表只注册 Bohrium runtime。

该层不允许承载 Bohrium 主实现，不允许重新长成新的通用 bridge。

#### `matmaster/mcp/calculation/`

这是 calculation MCP 的协议前处理层。

它负责理解这次 MCP 调用本身，而不是负责实现 Bohrium 领域行为。职责包括：

- 识别远程工具名
- 解析 sync / async 约束
- 读取 schema / docstring / config 中的 path selector
- 区分输入路径和输出路径
- 遍历参数树并执行改写
- 组装最终提交参数

它通过 runtime object 消费 Bohrium 能力，但不拥有 Bohrium 主实现。

## 4. `agent_run_bohrium.py` 的新角色

### 4.1 作为 Bohrium runtime 的 composition root

`src/services/agent_run_bohrium.py` 不再只是零散地：

- 加载凭证
- 创建节点
- 挂 SSH session
- 把 `_bohrium_credentials` 塞进 session

而是升级为：

- 构造 Bohrium runtime object
- 将 runtime attach 到当前 session
- 向 playground / context 写入运行时摘要
- 让后续所有模块围绕 runtime object 工作

换句话说，它是唯一允许从原始 run 数据装配 Bohrium runtime 的入口。

### 4.2 保留在 `src/services/` 的职责

以下职责继续留在 `src/services/agent_run_bohrium.py`：

- session store 读取
- `UserService` 取 access key / user_no
- 节点创建、复用、销毁
- reuse table 维护
- SSH session 打开与关闭
- Skills 同步
- 事件总线发 status
- 对 `Playground` 做 session swap

这些都属于应用服务编排，不迁入 `matmaster/bohrium/`。

### 4.3 迁入 `matmaster/bohrium/` 的职责

以下职责迁入 Bohrium 领域核心：

- BohriumCredentials 规范化
- BohriumRuntimeHandle 构造
- env 构造
- executor 注入
- storage 构造
- 运行时 attach / get / detach
- 路径物化
- job client 与 OSS client

## 5. 运行时对象模型

### 5.1 核心值对象

#### `BohriumCredentials`

不可变值对象，字段建议包括：

- `access_key`
- `project_id`
- `user_id`
- `user_no`
- `base_url`

它只表达这个 run 的 Bohrium 身份，不包含节点、SSH 和 workspace 信息。

#### `BohriumExecutionContext`

表示当前 run 的执行面状态，字段建议包括：

- `session_type`
- `execution_session`
- `execution_workdir`
- `remote_workspace_root`
- `remote_project_root`
- `node_id`
- `node_ip`
- `ssh_attached`

#### `BohriumSubmissionSpec`

表示一次 calculation 提交所需的最终 Bohrium 载荷：

- `executor`
- `storage`
- 可能的 submission mode 或调度元数据

### 5.2 统一入口对象

#### `BohriumRuntimeHandle`

后续模块统一围绕该对象工作。

它不暴露大量细碎 helper，而是提供少量高层能力：

- `credentials() -> BohriumCredentials`
- `execution() -> BohriumExecutionContext`
- `build_env() -> dict[str, str]`
- `build_submission(request) -> BohriumSubmissionSpec`
- `materialize_input_path(...) -> str`

## 6. Session 与 Context 挂载规则

### 6.1 Session 挂载

新规则：

- 使用 `session._bohrium_runtime` 保存 `BohriumRuntimeHandle`
- 停止把 `session._bohrium_credentials` 作为公共合同

只有 `matmaster/bohrium/runtime.py` 内部允许读写该挂载。

### 6.2 Context 挂载

`PlaygroundContext` 中只保留可序列化摘要，例如：

- `node_id`
- `session_type`
- `execution_workdir`
- `ssh_attached`

即：

- session 上挂完整 runtime object
- context 上挂运行时摘要

## 7. Preflight 与 Runtime 的边界

### 7.1 `preflight.py` 负责什么

`matmaster/mcp/calculation/preflight.py` 负责理解 MCP 工具合同：

- 解析 `tool_name`、`server_name`、`remote_tool_name`
- 读取 calculation 配置
- 判断 sync / async 约束
- 识别 path selectors
- 解析 model alias
- 遍历参数树并定位要改写的 leaf
- 组装最终 args

### 7.2 `runtime.py` 负责什么

`matmaster/bohrium/runtime.py` 负责把已确定的提交意图物化为 Bohrium 载荷：

- 构造 env
- 构造 submission spec
- 物化单个输入路径

### 7.3 推荐调用链

1. preflight 解析调用意图
2. preflight 构造 `SubmissionRequest`
3. runtime 生成 `BohriumSubmissionSpec`
4. preflight 遍历参数树
5. 对每个输入 leaf 调用 `runtime.materialize_input_path(...)`
6. preflight 组装最终 MCP args

### 7.4 为什么不让 runtime 递归改整棵参数树

递归改写参数树属于协议前处理，不属于 Bohrium 领域物化。

如果 runtime 直接接管 selector 解析和整棵参数树遍历，那么：

- schema 导航逻辑会重新混入 Bohrium 领域层
- calculation preflight 会再次失去边界
- 未来新增 runtime 时会把协议层耦合到具体领域实现里

因此 runtime 只负责 leaf 级别物化，preflight 负责结构遍历与意图解释。

## 8. 扩展缝设计

### 8.1 为什么还保留 `calculation_runtimes/`

虽然当前 Bohrium 已经是一等中心实现，但未来存在以下扩展可能：

- 用户自定义计算资源
- 附加 MCP 计算后端

为了不把今天的 Bohrium 结构彻底封死，保留 `calculation_runtimes/` 作为窄接口层。

### 8.2 该层的最小能力接口

`calculation_runtimes/base.py` 中的契约只定义运行时能力，不按工具类型分接口。

建议最小接口为：

- `build_env()`
- `execution()`
- `build_submission(request)`
- `materialize_input_path(...)`

shell/script 和 mcp calculation 都消费同一个 runtime root object，只是在最后一步分别投影为：

- shell/script：env
- mcp：submission spec + path materialization

## 9. 旧入口删除策略

### 9.1 需要删除的旧公共入口

以下模块不再作为重构后的公共入口：

- `matmaster.integration.runtime_bridge.__init__`
- `matmaster.integration.runtime_bridge.adapters.bohrium`
- `matmaster.integration.bohrium_env`
- `matmaster.adaptors.calculation`

### 9.2 新的唯一合法入口

#### 启动与注册入口

仅允许 `src/services/agent_run_bohrium.py` 使用：

- `create_credentials(...)`
- `create_runtime_handle(...)`
- `attach_runtime(session, runtime)`
- `snapshot_runtime(runtime)`

#### 消费入口

其他模块统一使用：

- `get_runtime(session)`
- `require_runtime(session)`

然后再从 runtime handle 上调用：

- `build_env()`
- `build_submission(...)`
- `materialize_input_path(...)`
- `execution()`

### 9.3 新纪律

除 runtime 模块内部外：

- 任何代码不得直接读取 `session._bohrium_credentials`
- 任何代码不得自行拼装 Bohrium env / executor / storage
- 任何代码不得继续 import `runtime_bridge` 或 `bohrium_env`

## 10. 异常边界

### 10.1 Bohrium 领域异常

建议在 `matmaster/bohrium/errors.py` 中定义：

- `BohriumCredentialError`
- `BohriumRuntimeNotInitialized`
- `BohriumSubmissionBuildError`
- `BohriumPathMaterializationError`

### 10.2 Calculation preflight 异常

`matmaster/mcp/calculation/errors.py` 保留：

- `CalculationPreflightError`

### 10.3 规则

- Bohrium 领域层只抛 Bohrium typed exception
- preflight 只抛 preflight exception
- 顶层 service / tool 层按现有项目约定向上抛，由统一错误处理收口

## 11. 测试策略

### 11.1 测试边界重构

重构后，测试边界同步调整：

- startup 相关测试围绕 `agent_run_bohrium` 是否成功注册 runtime
- shell/script/builtin tool 测试 patch `get_runtime()` 或 fake runtime handle
- calculation preflight 测试 patch `build_submission()` 与 `materialize_input_path()`
- Bohrium 领域核心单测分别覆盖 credentials、env、submission、paths、jobs、oss

### 11.2 明确废弃的 patch 点

不再使用以下 patch 点作为主测试手段：

- `build_service_env`
- `resolve_bohrium_credentials`
- `inject_bohrium_executor`
- `get_bohrium_storage_config`
- 直接伪造 `session._bohrium_credentials`

## 12. 迁移顺序

### 12.1 为什么要从 startup path 开始

重构起点必须前移到 `agent_run_bohrium.py`，因为它是 Bohrium 运行时状态的最早来源。

如果先改 adaptor / bridge，再改 startup path，就会出现：

- 新结构已经出现
- run 启动仍然生产旧状态
- 下游不得不继续兼容旧字段形状

这会让兼容层重新长回来。

### 12.2 推荐迁移顺序

1. 定义 Bohrium 核心类型与 runtime attach/get API
2. 改造 `agent_run_bohrium.py`，最先切到新 runtime object
3. 改造 shell/script/builtin tool，统一从 runtime handle 取 env 与 execution
4. 改造 calculation preflight，切到 `build_submission()` 与 `materialize_input_path()`
5. 迁移 job / oss / path 模块与测试目录
6. 删除旧入口与历史目录

## 13. 非目标

本次重构不追求：

- 立即支持第二个完整计算 runtime
- 立即抽象一套与 Bohrium 等价的通用多后端平台
- 保留旧 import 路径的长期兼容层
- 在 runtime 层复用 LLM provider 目录或命名体系

## 14. 成功标准

重构完成后，应满足以下条件：

1. Bohrium 主实现只存在于 `matmaster/bohrium/`
2. `agent_run_bohrium.py` 成为唯一 runtime 注册入口
3. 下游模块只通过 runtime handle 消费 Bohrium 能力
4. `runtime_bridge`、`bohrium_env`、`adaptors/calculation` 被删除或彻底退场
5. calculation preflight 与 Bohrium runtime 的边界清晰
6. 测试不再依赖旧 helper 和 `_bohrium_credentials`

## 15. 最终判断

本设计并不否认未来多后端的可能性，但它拒绝为了未来可能性而把今天的 Bohrium 核心继续拆散。

当前最重要的不是提前做一个大的通用 runtime 平台，而是先建立：

- 一个准确表达现实的 Bohrium 领域核心
- 一个单一可信的 runtime object
- 一个足够薄、不会反噬当前结构的 calculation runtime 扩展缝

这是让当前架构变简单、同时又不封死未来扩展的最优平衡点。
