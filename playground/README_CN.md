# Playground

Playground 是开发者构建自己科研智能体的工作区。每个 playground 定义了一个完整的实验工作流，通过继承 EvoMaster 的基础组件（`BasePlayground`、`BaseExp`）来实现特定的科学实验自动化。

**本仓库仅内置产品级 playground（`mat_master`）。若要新增其它 agent，可在 `playground/` 下自建目录，或参考 [EvoMaster 上游](https://github.com/sjtu-sai-agents/EvoMaster) 中的 minimal 与各类示例。**

## 内置 Playground

| Playground | 类型 | 说明 | 文档 |
|---|---|---|---|
| `mat_master` | 产品 | MatMaster 前端/技能/Bohrium 与 MCP 集成等 | 见 `playground/mat_master/` 与 `configs/mat_master/` |

命令行示例：`python run.py --agent mat_master --config configs/mat_master/config.yaml --task "你的任务"`。

## 快速开始：创建你的 Playground

### 1. 创建目录结构

```bash
mkdir -p playground/my_agent/core
mkdir -p playground/my_agent/prompts
mkdir -p configs/my_agent
```

### 2. 实现 Playground 类

`playground/my_agent/core/playground.py`:

```python
import logging
from pathlib import Path
from evomaster.core import BasePlayground, register_playground

@register_playground("my_agent")
class MyPlayground(BasePlayground):
    def __init__(self, config_dir=None, config_path=None):
        if config_path is None and config_dir is None:
            config_dir = Path(__file__).parent.parent.parent.parent / "configs" / "my_agent"
        super().__init__(config_dir=config_dir, config_path=config_path)
        self.logger = logging.getLogger(self.__class__.__name__)
```

这是最小实现。如果需要多智能体或自定义实验流程，可以覆盖 `setup()`、`_create_exp()`、`run()` 等方法，可参考本仓库 `playground/mat_master/` 或 EvoMaster 上游的 `playground/` 示例。

### 3. 编写提示词

`playground/my_agent/prompts/system_prompt.txt`:

```
你是一个科研智能体。请根据任务描述进行分析、实验和总结。
```

`playground/my_agent/prompts/user_prompt.txt`:

```
任务 ID：{task_id}
描述：{description}
{input_data}
```

### 4. 配置

`configs/my_agent/config.yaml`:

```yaml
llm:
  openai:
    provider: "openai"
    model: "gpt-4"
    api_key: "your-api-key"
    temperature: 0.7
  default: "openai"

agent:
  llm: "openai"
  max_turns: 50
  enable_tools: true
  system_prompt_file: "prompts/system_prompt.txt"
  user_prompt_file: "prompts/user_prompt.txt"
  context:
    max_tokens: 128000
    truncation_strategy: "latest_half"

session:
  type: "local"
  local:
    working_dir: "./workspace"

logging:
  level: "INFO"
  format: "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
```

### 5. 运行

```bash
python run.py --agent my_agent --task "你的任务描述"
```

## 开发指南

### 三层架构

```
Playground  →  工作流编排、组件初始化、生命周期管理
    │
   Exp       →  单次实验执行逻辑
    │
  Agent      →  LLM + 工具调用 + 上下文管理
```

### 常用扩展模式

**自定义实验流程** — 继承 `BaseExp`，覆盖 `run()`:

```python
from evomaster.core.exp import BaseExp

class MyExp(BaseExp):
    def run(self, task_description, task_id="exp_001"):
        # 自定义执行逻辑
        ...
```

**多智能体** — 覆盖 `setup()` 创建多个 Agent，覆盖 `_create_exp()` 使用自定义 Exp:

```python
def setup(self):
    llm_config_dict = self._setup_llm_config()
    self._setup_session()
    self._setup_tools()
    agents_config = getattr(self.config, 'agents', {})
    self.agent_a = self._create_agent("a", agents_config['a'], llm_config_dict=llm_config_dict)
    self.agent_b = self._create_agent("b", agents_config['b'], llm_config_dict=llm_config_dict)
```

**MCP 工具集成** — 在配置中启用:

```yaml
mcp:
  enabled: true
  config_file: "mcp_config.json"
```

**Docker 环境** — 切换 Session 类型:

```yaml
session:
  type: "docker"
  docker:
    image: "evomaster/base:latest"
    working_dir: "/workspace"
```

### 关键原则

- 尽量复用 `BasePlayground` 的 `_setup_*` 和 `_create_agent()` 方法
- 每个 Agent 使用独立 LLM 实例，共享 Session 和 Tools
- 提示词文件使用相对路径（相对于 playground 目录）
- `run()` 中使用 `try-finally` 确保 `cleanup()` 被调用

更多细节请参考 [开发文档](../docs/zh/architecture.md)。
