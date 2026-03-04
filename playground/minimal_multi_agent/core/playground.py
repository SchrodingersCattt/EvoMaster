"""多智能体 Playground 实现

展示如何使用多个Agent协作完成任务。
包含Planning Agent和Coding Agent的工作流。
"""

import logging
import sys
from pathlib import Path

# 确保可以导入evomaster模块
project_root = Path(__file__).parent.parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from typing import TYPE_CHECKING

from evomaster.core import BasePlayground, register_playground

if TYPE_CHECKING:
    pass

from .exp import MultiAgentExp


@register_playground('minimal_multi_agent')
class MultiAgentPlayground(BasePlayground):
    """多智能体 Playground

    实现Planning Agent和Coding Agent的协作工作流：
    1. Planning Agent分析任务并制定计划
    2. Coding Agent根据计划执行代码任务

    使用方式：
        # 通过统一入口
        python run.py --agent minimal_multi_agent --task "任务描述"

        # 或使用独立入口
        python playground/minimal_multi_agent/main.py
    """

    def __init__(self, config_dir: Path = None, config_path: Path = None):
        """初始化多智能体 Playground

        Args:
            config_dir: 配置目录路径，默认为 configs/minimal_multi_agent/
            config_path: 配置文件完整路径（如果提供，会覆盖 config_dir）
        """
        if config_path is None and config_dir is None:
            # 默认配置目录
            config_dir = (
                Path(__file__).parent.parent.parent.parent
                / 'configs'
                / 'agent'
                / 'minimal_multi_agent'
            )

        super().__init__(config_dir=config_dir, config_path=config_path)
        self.logger = logging.getLogger(self.__class__.__name__)

        # 存储多个Agent
        self.planning_agent = None
        self.coding_agent = None

        # 初始化mcp_manager（BasePlayground.cleanup需要）
        self.mcp_manager = None

    def setup(self) -> None:
        """初始化所有组件

        覆盖基类方法，复用基类的公共方法来创建多个Agent。
        每个Agent使用独立的LLM实例，确保日志记录独立。
        """
        self.logger.info('Setting up multi-agent playground...')

        # 1. 准备 LLM 配置（每个Agent会创建独立的LLM实例）
        llm_config_dict = self._setup_llm_config()
        self._llm_config_dict = llm_config_dict  # 保存配置供后续使用

        # 2. 创建 Session（所有Agent共享）
        self._setup_session()

        # 3. 加载 Skills（如果启用）
        skill_registry = None
        config_dict = self.config.model_dump()
        skills_config = config_dict.get('skills', {})
        if skills_config.get('enabled', False):
            self.logger.info('Skills enabled, loading skill registry...')
            from pathlib import Path

            from evomaster.skills import SkillRegistry

            skills_root = Path(skills_config.get('skills_root', 'evomaster/skills'))
            skill_registry = SkillRegistry(skills_root)
            self.logger.info(f"Loaded {len(skill_registry.get_all_skills())} skills")

        # 4. 创建工具注册表并初始化 MCP 工具（传入 skill_registry）
        self._setup_tools(skill_registry)

        # 5. 创建多个 Agent（复用 base._setup_agents，再赋回子类属性供 Exp 使用）
        agents_config = getattr(self.config, 'agents', None)
        if not agents_config or not isinstance(agents_config, dict):
            raise ValueError(
                'No agents configuration found. '
                "Please add 'agents' section to config.yaml"
            )
        self._setup_agents(skill_registry=skill_registry)
        self.planning_agent = self.agents.get('planning_agent')
        self.coding_agent = self.agents.get('coding_agent')
        self.logger.info('Multi-agent playground setup complete')

    def _create_exp(self):
        """创建多智能体实验实例

        覆盖基类方法，创建 MultiAgentExp 实例。

        Returns:
            MultiAgentExp 实例
        """
        exp = MultiAgentExp(
            planning_agent=self.planning_agent,
            coding_agent=self.coding_agent,
            config=self.config,
        )
        # 传递 run_dir 给 Exp
        if self.run_dir:
            exp.set_run_dir(self.run_dir)
        return exp

    def run(self, task_description: str, output_file: str | None = None) -> dict:
        """运行工作流（覆盖基类方法）

        Args:
            task_description: 任务描述
            output_file: 结果保存文件（可选，如果设置了 run_dir 则自动保存到 trajectories/）

        Returns:
            运行结果
        """
        try:
            self.setup()

            # 设置轨迹文件路径
            self._setup_trajectory_file(output_file)

            # 创建并运行实验
            exp = self._create_exp()

            self.logger.info('Running experiment...')
            # 如果有 task_id，传递给 exp.run()
            task_id = getattr(self, 'task_id', None)
            if task_id:
                result = exp.run(task_description, task_id=task_id)
            else:
                result = exp.run(task_description)

            return result

        finally:
            self.cleanup()
