"""DevShell 评测外层编排：基于 Claude Agent SDK 驱动「跑题 → 判分 → 改仓库」迭代。

安装依赖::

    uv sync --extra eval-agent

入口脚本：``evaluation/scripts/devshell/run_devshell_agent_loop.py``。

主要类型：:class:`evaluation.devshell_agent.loop.DevshellAgentLoop`、
:class:`evaluation.devshell_agent.sdk_tools.MatmasterEvalMcpToolkit`、
:class:`evaluation.devshell_agent.subprocess_runner.DevshellEvalSubprocess`。
"""
