"""System/trigger 事件历史还原：落库可区分，喂 LLM 时为普通 UserMessage。"""

from src.services.chat_history import ChatHistoryConverter


def test_system_trigger_event_restored_as_user_message():
    events = [
        {
            'source': 'User',
            'type': 'query',
            'content': '第一轮问题',
            'session_id': 's1',
            'task_id': 'task-0',
        },
        {
            'source': 'MatMaster',
            'type': 'run_result',
            'content': '第一轮回答',
            'session_id': 's1',
            'task_id': 'task-0',
        },
        {
            'source': 'System',
            'type': 'trigger',
            'content': {'text': '作业123已完成，请分析', 'origin': 'hpc_job'},
            'session_id': 's1',
            'task_id': 'trig_1',
        },
    ]
    msgs = ChatHistoryConverter.events_to_dialog_messages(events)
    assert msgs[-1]['role'] == 'user'
    assert msgs[-1]['content'] == '作业123已完成，请分析'


def test_system_trigger_resets_turn_boundary_like_user_query():
    """System/trigger 前若有未 flush 的 reasoning，应在该轮边界被 flush（与 User/query 一致）。"""
    events = [
        {
            'source': 'MatMaster',
            'type': 'thought',
            'content': '思考中…',
            'session_id': 's1',
            'task_id': 'task-0',
        },
        {
            'source': 'System',
            'type': 'trigger',
            'content': {'text': '继续', 'origin': 'loop'},
            'session_id': 's1',
            'task_id': 'trig_1',
        },
    ]
    msgs = ChatHistoryConverter.events_to_dialog_messages(events)
    roles = [m['role'] for m in msgs]
    assert roles[-1] == 'user'
    assert msgs[-1]['content'] == '继续'
    assert 'assistant' in roles  # thought 被 flush
