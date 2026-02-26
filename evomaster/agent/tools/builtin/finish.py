"""EvoMaster Finish 工具

用于标记任务完成。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Literal

from pydantic import Field

from ..base import BaseTool, BaseToolParams

if TYPE_CHECKING:
    from evomaster.agent.session import BaseSession


class FinishToolParams(BaseToolParams):
    """Signals the completion of the current task or conversation.

    Use this tool when:
    - You have successfully completed the user's requested task
    - You cannot proceed further due to technical limitations or missing information

    The message should include:
    - A clear summary of actions taken and their results
    - Any next steps for the user
    - Explanation if you're unable to complete the task
    - Any follow-up questions if more information is needed
    - A "## Execution Details" section with one "###" subsection per step/tool call.
      Under each subsection: numerical results, job IDs, warnings, and output files
      as "- [filename](url)" list items (never bare URLs). Each OSS link must appear
      exactly once, under the step that produced it. Do NOT add a separate
      "Output Files" or "All OSS links" section.

    The task_completed field should be set to True if you believed you have completed the task, and False otherwise.
    """

    name: ClassVar[str] = 'finish'

    message: str = Field(description='Final message to send to the user')
    task_completed: Literal['true', 'false', 'partial'] = Field(
        description='Whether you have completed the task.'
    )


class FinishTool(BaseTool):
    """完成工具"""

    name: ClassVar[str] = 'finish'
    params_class: ClassVar[type[BaseToolParams]] = FinishToolParams

    def execute(
        self, session: BaseSession, args_json: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """标记任务完成。返回 (result_dict, info)，result_dict 供前端/SSE 直接使用。"""
        try:
            params = self.parse_params(args_json)
        except Exception as e:
            err = {
                'status': 'error',
                'error': str(e),
                'message': f"Parameter validation error: {str(e)}",
            }
            return err, {'error': str(e)}

        assert isinstance(params, FinishToolParams)

        # 记录完成信息
        self.logger.info(f"Task finished. Completed: {params.task_completed}")
        self.logger.info(f"Final message: {params.message[:200]}...")

        result: dict[str, Any] = {
            'status': 'success',
            'message': params.message,
            'task_completed': params.task_completed,
        }
        info: dict[str, Any] = {
            'task_completed': params.task_completed,
            'message': params.message,
        }
        return result, info
