"""轨迹 JSON 文件追加写入（BaseAgent mixin）。

与 BaseAgent 共享类属性 _trajectory_file_path / _trajectory_file_lock。
"""

from __future__ import annotations

import json

from evomaster.utils.types import Dialog, StepRecord


class TrajectoryPersistenceMixin:
    """将每步 prompt/response/tool 结果追加到共享轨迹文件。"""

    def _append_trajectory_entry(
        self, dialog_for_query: Dialog, step_record: StepRecord
    ) -> None:
        """追加轨迹条目到轨迹文件

        每次step完成后，将prompt、response和tool_responses追加保存到轨迹文件。
        使用文件锁确保多个agent写入同一文件时的线程安全。

        保存格式与现有轨迹格式保持一致：
        [
            {
                "task_id": "...",
                "status": "...",
                "steps": ...,
                "trajectory": {...}
            }
        ]

        每次step会追加一个新的条目，包含本次调用的prompt、response和tool_responses。

        Args:
            dialog_for_query: 发送给LLM的对话（prompt）
            step_record: 步骤记录（包含assistant_message和tool_responses）
        """
        if self._trajectory_file_path is None:
            return

        try:
            with self._trajectory_file_lock:
                # 读取现有数据
                existing_data = []
                if self._trajectory_file_path.exists():
                    try:
                        with open(self._trajectory_file_path, encoding='utf-8') as f:
                            existing_data = json.load(f)
                    except (json.JSONDecodeError, FileNotFoundError):
                        # 如果文件损坏或不存在，从空列表开始
                        existing_data = []

                # 构建新的轨迹条目
                # 格式与现有轨迹格式保持一致，但保存的是每次LLM调用的信息
                task_id = self.trajectory.task_id if self.trajectory else 'unknown'
                status = self.trajectory.status if self.trajectory else 'running'

                # 将dialog_for_query转换为字典格式
                prompt_dict = (
                    dialog_for_query.model_dump()
                    if hasattr(dialog_for_query, 'model_dump')
                    else {
                        'messages': [
                            {
                                'role': (
                                    msg.role.value
                                    if hasattr(msg.role, 'value')
                                    else str(msg.role)
                                ),
                                'content': (
                                    msg.content if hasattr(msg, 'content') else str(msg)
                                ),
                            }
                            for msg in dialog_for_query.messages
                        ],
                        'tools': (
                            dialog_for_query.tools
                            if hasattr(dialog_for_query, 'tools')
                            else []
                        ),
                    }
                )

                # 从step_record中获取assistant_message
                assistant_message = step_record.assistant_message

                # 将assistant_message转换为字典格式
                response_dict = (
                    assistant_message.model_dump()
                    if hasattr(assistant_message, 'model_dump')
                    else {
                        'role': (
                            assistant_message.role.value
                            if hasattr(assistant_message.role, 'value')
                            else str(assistant_message.role)
                        ),
                        'content': (
                            assistant_message.content
                            if hasattr(assistant_message, 'content')
                            else ''
                        ),
                        'tool_calls': (
                            [
                                {
                                    'id': tc.id if hasattr(tc, 'id') else '',
                                    'function': {
                                        'name': (
                                            tc.function.name
                                            if hasattr(tc.function, 'name')
                                            else ''
                                        ),
                                        'arguments': (
                                            tc.function.arguments
                                            if hasattr(tc.function, 'arguments')
                                            else ''
                                        ),
                                    },
                                }
                                for tc in (assistant_message.tool_calls or [])
                            ]
                            if hasattr(assistant_message, 'tool_calls')
                            and assistant_message.tool_calls
                            else []
                        ),
                    }
                )

                # 将tool_responses转换为字典格式
                tool_responses_list = []
                for tr in step_record.tool_responses:
                    tr_dict = (
                        tr.model_dump()
                        if hasattr(tr, 'model_dump')
                        else {
                            'role': 'tool',
                            'content': tr.content if hasattr(tr, 'content') else '',
                            'tool_call_id': (
                                tr.tool_call_id if hasattr(tr, 'tool_call_id') else ''
                            ),
                            'name': tr.name if hasattr(tr, 'name') else '',
                        }
                    )
                    tool_responses_list.append(tr_dict)

                # 构建轨迹条目，格式与现有轨迹格式保持一致
                entry = {
                    'task_id': f"{task_id}_{self._agent_name or 'agent'}_step_{self._step_count}",
                    'exp_name': self._current_exp_name,  # exp阶段名称
                    'exp_index': self._current_exp_index,  # exp迭代序号
                    'status': status,
                    'steps': self._step_count,
                    'trajectory': {
                        'task_id': task_id,
                        'agent_name': self._agent_name or 'unknown',
                        'step': self._step_count,
                        'dialogs': [prompt_dict],  # 保存本次调用的prompt
                        'steps': [
                            {
                                'step_id': self._step_count,
                                'assistant_message': response_dict,  # 保存本次调用的response
                                'tool_responses': tool_responses_list,  # 保存工具响应
                                'meta': {},
                            }
                        ],
                        'start_time': None,
                        'end_time': None,
                        'status': status,
                        'result': {'prompt': prompt_dict, 'response': response_dict},
                        'meta': {
                            'agent_version': self.VERSION,
                            'agent_name': self._agent_name or 'unknown',
                            'step': self._step_count,
                            # model_name is extracted here so EvidenceExtractor can
                            # read it from trajectory.meta without scanning all steps.
                            'model_name': (
                                (assistant_message.meta or {}).get('model')
                                if hasattr(assistant_message, 'meta')
                                and assistant_message.meta
                                else None
                            ),
                        },
                    },
                }

                # 追加新条目
                existing_data.append(entry)

                # 写回文件
                with open(self._trajectory_file_path, 'w', encoding='utf-8') as f:
                    json.dump(
                        existing_data, f, indent=2, default=str, ensure_ascii=False
                    )

        except Exception as e:
            # 如果保存失败，只记录日志，不中断执行
            self.logger.warning(
                f"Failed to append trajectory entry: {e}", exc_info=True
            )
