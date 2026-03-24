"""默认 Agent 实现（可配置提示词文件）。"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from evomaster.utils.types import TaskInstance

from .agent_config import AgentConfig
from .base_agent import BaseAgent

if TYPE_CHECKING:
    from evomaster.skills import SkillRegistry
    from evomaster.utils import BaseLLM

    from .session import BaseSession
    from .tools import ToolRegistry


class Agent(BaseAgent):
    """标准 Agent 实现

    使用可配置的提示词模板。
    支持从配置文件加载提示词。
    """

    def __init__(
        self,
        llm: BaseLLM,
        session: BaseSession,
        tools: ToolRegistry,
        system_prompt_file: str | Path | None = None,
        user_prompt_file: str | Path | None = None,
        prompt_format_kwargs: dict[str, Any] | None = None,
        config: AgentConfig | None = None,
        skill_registry: SkillRegistry | None = None,
        output_config: dict[str, Any] | None = None,
        config_dir: Path | str | None = None,
        enable_tools: bool = True,
        enabled_tool_names: list[str] | None = None,
    ):
        """初始化 Agent

        Args:
            llm: LLM 实例
            session: 环境会话
            tools: 工具注册中心
            system_prompt_file: 系统提示词文件路径（相对于config_dir或绝对路径）
            user_prompt_file: 用户提示词文件路径（相对于config_dir或绝对路径）
            prompt_format_kwargs: 用于格式化提示词的参数字典（{}占位符）
            config: Agent 配置
            skill_registry: Skills 注册中心（可选）
            output_config: 输出显示配置
            config_dir: 配置目录路径，用于加载提示词文件
            enable_tools: 是否在提示词中包含工具信息（默认 True）。如果为 False，工具仍然注册但不会出现在提示词中，Agent 将不会调用工具
            enabled_tool_names: 仅暴露给 LLM 的工具名列表；None 表示全部
        """
        super().__init__(
            llm,
            session,
            tools,
            config,
            skill_registry,
            output_config,
            config_dir=config_dir,
            enable_tools=enable_tools,
            enabled_tool_names=enabled_tool_names,
        )

        # 存储提示词
        self._system_prompt: str | None = None
        self._user_prompt: str | None = None
        self._prompt_format_kwargs = prompt_format_kwargs or {}

        # 加载系统提示词（优先级：system_prompt_file > 默认）
        if system_prompt_file:
            self._system_prompt = self.load_prompt_from_file(
                system_prompt_file, format_kwargs=self._prompt_format_kwargs
            )
        else:
            self._system_prompt = self._default_system_prompt()

        # 加载用户提示词（可选）
        if user_prompt_file:
            self._user_prompt = self.load_prompt_from_file(
                user_prompt_file, format_kwargs=self._prompt_format_kwargs
            )

    def _default_system_prompt(self) -> str:
        """默认系统提示词"""
        prompt = """You are a helpful AI assistant that can execute tasks using tools.

You have access to the following tools:
- execute_bash: Execute bash commands in a terminal
- str_replace_editor: View, create, and edit files
- finish: Signal that you have completed the task
"""

        # 如果有 skill_registry，添加 skills 信息
        if self.skill_registry is not None:
            skills_info = self.skill_registry.get_meta_info_context()
            if skills_info:
                prompt += f"\n{skills_info}\n"
                prompt += """
You can use the 'use_skill' tool to:
1. Get detailed information about a skill: action='get_info'
2. Get reference documentation: action='get_reference'
3. Run scripts from Operator skills: action='run_script'
"""

        prompt += """
When you need to complete a task:
1. First understand what needs to be done
2. Check if any available skills can help you
3. Use the available tools to accomplish the task
4. When finished, use the finish tool to signal completion

Always be careful with file operations and bash commands.
"""
        return prompt

    def _get_system_prompt(self) -> str:
        """获取系统提示词，动态添加工作目录信息；若有 skill_registry 则自动注入 skills 信息"""
        working_dir = self.session.config.workspace_path
        # 将相对路径转换为绝对路径
        working_dir_abs = str(Path(working_dir).absolute())
        working_dir_info = f"\n\n重要提示：当前工作目录是 {working_dir_abs}。你必须在这个目录下进行所有操作，不能切换工作目录。所有文件操作、命令执行都必须在工作目录 {working_dir_abs} 下进行。"
        prompt = self._system_prompt + working_dir_info
        # 若有 skill_registry，自动注入 skills 信息（与 _default_system_prompt 一致）
        if self.skill_registry is not None:
            skills_info = self.skill_registry.get_meta_info_context()
            if skills_info:
                prompt += f"\n{skills_info}\n"
                prompt += """
You can use the 'use_skill' tool to:
1. Get detailed information about a skill: action='get_info'
2. Get reference documentation: action='get_reference'
3. Run scripts from Operator skills: action='run_script'
"""
        return prompt

    def _get_user_prompt(self, task: TaskInstance) -> str:
        """获取用户提示词"""
        # 如果设置了用户提示词，使用它（可以包含{}占位符）
        if self._user_prompt:
            try:
                return self._user_prompt.format(
                    task_id=task.task_id,
                    task_type=task.task_type,
                    description=task.description,
                    input_data=task.input_data,
                    **self._prompt_format_kwargs,
                )
            except KeyError:
                # 如果格式化失败，直接返回（可能没有占位符）
                return self._user_prompt

        # 默认用户提示词
        return f"""Please complete the following task:

Task ID: {task.task_id}
Task Type: {task.task_type}
Description: {task.description}

Additional Information:
{task.input_data}
"""

    def _get_tool_specs(self) -> list:
        """获取工具规格列表

        覆盖基类方法，但逻辑与基类相同（已移至基类）。
        保留此方法以保持向后兼容性。
        """
        return super()._get_tool_specs()
