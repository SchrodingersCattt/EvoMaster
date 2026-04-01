# Phase 25: Session 与 Playground 原生化 - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-04-01
**Phase:** 25-session-playground
**Areas discussed:** Session 抽象设计, Config 加载替代, Docker/SSH 范围, Mixin 消除策略

---

## Session 抽象设计

### Q1: Session 抽象方式

| Option | Description | Selected |
|--------|-------------|----------|
| @runtime_checkable Protocol | 与 matmaster 其他抽象一致（Tool/Hook/Guard/LLMProvider 全部是 Protocol）。结构性子类型，不强制继承。 | ✓ |
| ABC 抽象基类 | 与 evomaster BaseSession 保持一致的风格，强制子类实现所有抽象方法。 | |
| Claude 决定 | Claude 根据代码库情况自行判断最佳方案 | |

**User's choice:** @runtime_checkable Protocol
**Notes:** 与 matmaster 现有所有抽象保持一致

### Q2: Protocol 方法范围

| Option | Description | Selected |
|--------|-------------|----------|
| 核心 5 方法 + 生命周期 | exec_bash/read_file/write_file/path_exists/is_file + open/close/is_open。这是 builtin tools 实际使用的全集。 | ✓ |
| 完整复制 evomaster BaseSession | 包括 upload_file/download_file/list_dir 等全部方法 | |
| Claude 决定 | 根据实际 import 使用情况确定最小接口 | |

**User's choice:** 核心 5 方法 + 生命周期
**Notes:** 不包含未使用的方法

### Q3: Protocol 位置

| Option | Description | Selected |
|--------|-------------|----------|
| matmaster/types/session.py | 与其他类型定义一致 | ✓ |
| matmaster/sessions/base.py | 按功能分组 | |
| Claude 决定 | 根据代码组织惯例决定 | |

**User's choice:** matmaster/types/session.py

### Q4: SessionConfig 模型

| Option | Description | Selected |
|--------|-------------|----------|
| 精简版 Pydantic model | 只保留 timeout + workspace_path + working_dir | ✓ |
| 完整复制 evomaster SessionConfig | 保留全部字段确保兼容 | |
| Claude 决定 | 根据实际使用情况确定字段范围 | |

**User's choice:** 精简版 Pydantic model

---

## Config 加载替代

### Q5: Config 必要性分析

用户提出关键质疑：在只有一个 playground type 的情况下，是否还需要独立 config 文件？

经分析，Playground 实际从 config.yaml 读取的字段只有 session 参数和 archival，而 agents.general.llm 和 config_path 被 service 层借用但不属于 Playground 职责。

### Q6: 配置来源方式

| Option | Description | Selected |
|--------|-------------|----------|
| 参数化构造，无配置文件 | Playground 不读 config.yaml，改为接受参数 | ✓ |
| 轻量 PlaygroundConfig model | 定义 Pydantic model 但不自己读文件 | |
| 保留配置文件读取 | 用 matmaster 原生加载器替代 ConfigManager | |

**User's choice:** 参数化构造，无配置文件

### Q7: YAML 解析位置

| Option | Description | Selected |
|--------|-------------|----------|
| PlaygroundManager 内部 | get_or_create() 内做 yaml.safe_load + 拆分参数 | ✓ |
| 独立加载函数 | matmaster/config/ 下写 load_playground_config() | |
| Claude 决定 | | |

**User's choice:** PlaygroundManager 内部

---

## Docker/SSH 范围

用户明确指示：
- 只迁移 LocalSession 和 SSHSession
- 废弃 DockerSession
- 希望在 v2.1 里程碑内完成解耦和迁移

### Q8: SSHSession 迁移时机

| Option | Description | Selected |
|--------|-------------|----------|
| Phase 25 一起做 | 同时原生化 Local + SSH，一步到位 | ✓ |
| 分开，SSH 放 Phase 28 | Phase 25 只做 local + Protocol | |

**User's choice:** Phase 25 一起做

---

## Mixin 消除策略

### Q9: PlaygroundSessionMixin 处理

| Option | Description | Selected |
|--------|-------------|----------|
| 内联到 Playground | 把 attach_session/attach_ssh_session 直接写在 Playground 类上 | ✓ |
| 提取为独立 Protocol | 定义 SessionAttachable Protocol | |
| 延到 Phase 28 | 先保留 Mixin 继承 | |

**User's choice:** 内联到 Playground

---

## Claude's Discretion

- SSHSession 原生实现的内部结构（连接池、重连策略等）
- PlaygroundManager 内部 YAML 解析的具体字段提取方式

## Deferred Ideas

None
