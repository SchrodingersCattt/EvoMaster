"""MatMasterAgent: finish only when task_completed=true.

Base Agent treats any finish tool call as task end. For async/long-running
tasks we only want to finish when the agent signals task_completed=true
(partial/false means continue).
"""

from __future__ import annotations

import json
from typing import Any

from evomaster.agent.agent import Agent
from evomaster.utils.types import StepRecord, ToolMessage


class MatMasterAgent(Agent):
    """Agent that only ends the run when the finish tool is called with task_completed=true.

    If the agent calls finish with task_completed=false or partial, we add the
    tool response and continue (do not set should_finish).
    """

    def _step(self) -> bool:
        """Override: for finish tool, execute it and only set should_finish when task_completed==true."""
        self._step_count += 1

        dialog_for_query = self.context_manager.prepare_for_query(self.current_dialog)
        assistant_message = self.llm.query(dialog_for_query)
        self.current_dialog.add_message(assistant_message)
        step_record = StepRecord(
            step_id=self._step_count,
            assistant_message=assistant_message,
        )

        if not assistant_message.tool_calls:
            if hasattr(self, "enable_tools") and not self.enable_tools:
                self.trajectory.add_step(step_record)
                self._append_trajectory_entry(dialog_for_query, step_record)
                return True
            self._handle_no_tool_call()
            self.trajectory.add_step(step_record)
            self._append_trajectory_entry(dialog_for_query, step_record)
            return False

        should_finish = False
        for tool_call in assistant_message.tool_calls:
            self.logger.debug("Processing tool call: %s", tool_call.function.name)

            if tool_call.function.name == "finish":
                try:
                    finish_args = json.loads(tool_call.function.arguments)
                    self.logger.info("=" * 80)
                    self.logger.info("Finish Tool Arguments: task_completed=%s", finish_args.get("task_completed"))
                    self.logger.info("=" * 80)
                except Exception:
                    pass

            observation, info = self._execute_tool(tool_call)

            MAX_TOOL_OUTPUT = 30000
            if len(observation) > MAX_TOOL_OUTPUT:
                observation = (
                    observation[: MAX_TOOL_OUTPUT // 2]
                    + "\n\n... [output truncated due to length] ...\n\n"
                    + observation[-MAX_TOOL_OUTPUT // 2 :]
                )

            if tool_call.function.name == "finish":
                task_completed = info.get("task_completed", "false")
                if task_completed == "true":
                    should_finish = True

            tool_message = ToolMessage(
                content=observation,
                tool_call_id=tool_call.id,
                name=tool_call.function.name,
                meta={"info": info},
            )
            self.current_dialog.add_message(tool_message)
            step_record.tool_responses.append(tool_message)

        self.trajectory.add_step(step_record)
        self._append_trajectory_entry(dialog_for_query, step_record)
        return should_finish
