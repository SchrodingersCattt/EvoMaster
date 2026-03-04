"""EvoMaster - 科学实验迭代 Agent 系统

EvoMaster 是一个用于迭代完成科学实验任务的 Agent 系统，
主要针对 MLE、Phys、Embody 等科学实验场景。

核心组件（三层架构）：
- agent: 智能体（包含 Session、Tools）
- env: 环境（集群调度、Docker 沙箱）
- skills: 技能（Knowledge、Operator）
"""

__version__ = '0.1.0'

# 从 agent 模块导出常用类（Types 如 Dialog/Message/Trajectory/TaskInstance 从 utils 统一导入，避免 F811）
from evomaster.agent import (
    Agent,
    AgentConfig,
    BaseAgent,
    BaseSession,
    BaseTool,
    DockerSession,
    DockerSessionConfig,
    SessionConfig,
    ToolRegistry,
    create_default_registry,
)

# 从 config 模块导出配置管理
from evomaster.config import (  # 配置基类; Env 配置; Skill 配置; Tool 配置（v0.0.2 per-agent）; 日志配置; 顶层配置; 配置管理器
    BaseConfig,
    ClusterConfig,
    ClusterPoolConfig,
    ConfigManager,
    DockerEnvConfig,
    EnvConfig,
    EvoMasterConfig,
    KnowledgeSkillConfig,
    LoggingConfig,
    OperatorSkillConfig,
    SchedulerConfig,
    SkillConfig,
    ToolConfig,
    get_config,
    get_config_manager,
    load_config,
)

# 从 utils 模块导出工具类和类型
from evomaster.utils import (  # LLM; Types; Multimodal
    AnthropicLLM,
    AssistantMessage,
    BaseLLM,
    Dialog,
    FunctionCall,
    FunctionSpec,
    LLMConfig,
    LLMResponse,
    Message,
    MessageRole,
    OpenAILLM,
    StepRecord,
    SystemMessage,
    TaskInstance,
    ToolCall,
    ToolMessage,
    ToolSpec,
    Trajectory,
    UserMessage,
    build_multimodal_content,
    create_llm,
    encode_image_to_base64,
)

__all__ = [
    # Agent
    'BaseAgent',
    'Agent',
    'AgentConfig',
    # Types (from utils)
    'MessageRole',
    'SystemMessage',
    'UserMessage',
    'AssistantMessage',
    'ToolMessage',
    'Message',
    'FunctionCall',
    'ToolCall',
    'FunctionSpec',
    'ToolSpec',
    'Dialog',
    'StepRecord',
    'Trajectory',
    'TaskInstance',
    # Session
    'BaseSession',
    'SessionConfig',
    'DockerSession',
    'DockerSessionConfig',
    # Tools
    'BaseTool',
    'ToolRegistry',
    'create_default_registry',
    # Utils - Multimodal
    'encode_image_to_base64',
    'build_multimodal_content',
    # Utils - LLM
    'BaseLLM',
    'LLMConfig',
    'LLMResponse',
    'OpenAILLM',
    'AnthropicLLM',
    'create_llm',
    # Config
    'BaseConfig',
    'EnvConfig',
    'ClusterConfig',
    'ClusterPoolConfig',
    'DockerEnvConfig',
    'SchedulerConfig',
    'SkillConfig',
    'KnowledgeSkillConfig',
    'OperatorSkillConfig',
    'ToolConfig',
    'LoggingConfig',
    'EvoMasterConfig',
    'ConfigManager',
    'get_config_manager',
    'load_config',
    'get_config',
]
