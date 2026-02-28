import asyncio
import sys
import types

_pymysql = types.ModuleType('pymysql')
_pymysql.cursors = types.SimpleNamespace(DictCursor=object)
_pymysql.Error = Exception
sys.modules.setdefault('pymysql', _pymysql)

from src.services.context_injection_service import ContextInjectionService


class _FakeEventsService:
    def __init__(self, events):
        self._events = events

    def get_session_events(self, session_id: str):
        return self._events


class _BrokenEventsService:
    def get_session_events(self, session_id: str):
        raise RuntimeError('db unavailable')


def test_injects_history_block(monkeypatch):
    events = [
        {'source': 'User', 'type': 'query', 'content': '第一轮需求'},
        {'source': 'System', 'type': 'status', 'content': 'Initializing'},
        {
            'source': 'ToolExecutor',
            'type': 'tool_result',
            'content': {'name': 'run_abacus', 'result': {'status': 'success'}},
        },
    ]
    svc = ContextInjectionService(events_service=_FakeEventsService(events))
    monkeypatch.setattr('src.services.context_injection_service.CTX_INJECTION_ENABLED', True)
    monkeypatch.setattr(
        'src.services.context_injection_service.CTX_TOTAL_PROMPT_MAX_CHARS', 12000
    )

    prompt, meta = asyncio.run(svc.build_augmented_prompt('s1', '第二轮问题'))

    assert '[Session history]' in prompt
    assert 'User(query): 第一轮需求' in prompt
    assert meta['history_lines_count'] >= 1


def test_skips_current_query_duplicate(monkeypatch):
    events = [
        {'source': 'User', 'type': 'query', 'content': '重复问题'},
        {'source': 'User', 'type': 'query', 'content': '更早的其他问题'},
    ]
    svc = ContextInjectionService(events_service=_FakeEventsService(events))
    monkeypatch.setattr('src.services.context_injection_service.CTX_INJECTION_ENABLED', True)

    prompt, _ = asyncio.run(svc.build_augmented_prompt('s1', '重复问题'))

    assert 'User(query): 重复问题' not in prompt
    assert 'User(query): 更早的其他问题' in prompt


def test_respects_prompt_length_limit(monkeypatch):
    events = [
        {'source': 'User', 'type': 'query', 'content': '很长历史' * 50},
        {'source': 'User', 'type': 'query', 'content': '另外一条很长历史' * 50},
    ]
    svc = ContextInjectionService(events_service=_FakeEventsService(events))
    monkeypatch.setattr('src.services.context_injection_service.CTX_INJECTION_ENABLED', True)
    monkeypatch.setattr(
        'src.services.context_injection_service.CTX_TOTAL_PROMPT_MAX_CHARS', 220
    )
    monkeypatch.setattr(
        'src.services.context_injection_service.CTX_MAX_TOKENS_LIMIT', 100000
    )

    prompt, meta = asyncio.run(svc.build_augmented_prompt('s1', '短问题'))

    assert prompt.startswith('短问题')
    assert ('[Session history]' in prompt) or ('[Session intent]' in prompt)
    assert meta.get('fallback') is not True
    assert meta.get('context_truncated') is True


def test_fallback_when_history_read_fails(monkeypatch):
    svc = ContextInjectionService(events_service=_BrokenEventsService())
    monkeypatch.setattr('src.services.context_injection_service.CTX_INJECTION_ENABLED', True)

    prompt, meta = asyncio.run(svc.build_augmented_prompt('s1', '本轮输入'))

    assert prompt == '本轮输入'
    assert meta.get('fallback') is True


def test_injects_session_state_and_file_memory(monkeypatch):
    events = [
        {
            'source': 'User',
            'type': 'query',
            'content': {'content': '先分析上传文件', 'files': ['https://oss/a.cif']},
        },
        {
            'source': 'ToolExecutor',
            'type': 'tool_result',
            'content': {
                'name': 'mat_doc_parse',
                'result': {'output': 'see https://oss/report.md'},
            },
        },
    ]
    svc = ContextInjectionService(events_service=_FakeEventsService(events))
    svc._session_store().clear()
    monkeypatch.setattr('src.services.context_injection_service.CTX_INJECTION_ENABLED', True)

    prompt, meta = asyncio.run(
        svc.build_augmented_prompt(
            'sid_state',
            '第二轮继续',
            mode='direct',
            attached_files=['https://oss/new.cif'],
        )
    )

    assert '[Session intent]' in prompt
    assert '[Session files]' in prompt
    assert 'https://oss/new.cif' in prompt
    assert meta.get('state_injected') is True
    assert meta.get('file_refs_count', 0) >= 1


def test_planner_mode_filters_thought_noise(monkeypatch):
    events = [
        {'source': 'MatMaster', 'type': 'thought', 'content': '这是一段思维链'},
        {'source': 'User', 'type': 'query', 'content': '请输出计划'},
    ]
    svc = ContextInjectionService(events_service=_FakeEventsService(events))
    svc._session_store().clear()
    monkeypatch.setattr('src.services.context_injection_service.CTX_INJECTION_ENABLED', True)

    planner_prompt, _ = asyncio.run(
        svc.build_augmented_prompt('sid_planner', '下一步', mode='planner')
    )
    direct_prompt, _ = asyncio.run(
        svc.build_augmented_prompt('sid_direct', '下一步', mode='direct')
    )

    assert '思维链' not in planner_prompt
    assert '思维链' not in direct_prompt  # 当前策略两种模式都不过 thought，防污染


def test_tiny_budget_falls_back(monkeypatch):
    events = [{'source': 'User', 'type': 'query', 'content': '历史信息'}]
    svc = ContextInjectionService(events_service=_FakeEventsService(events))
    svc._session_store().clear()
    monkeypatch.setattr('src.services.context_injection_service.CTX_INJECTION_ENABLED', True)
    monkeypatch.setattr('src.services.context_injection_service.CTX_TOTAL_PROMPT_MAX_CHARS', 8)
    monkeypatch.setattr('src.services.context_injection_service.CTX_MAX_TOKENS_LIMIT', 2)

    prompt, meta = asyncio.run(svc.build_augmented_prompt('sid_tiny', '本轮输入'))

    assert prompt == '本轮输入'
    assert meta.get('fallback') is True


def test_history_keeps_chronological_order(monkeypatch):
    events = [
        {'source': 'User', 'type': 'query', 'content': '很早的关键需求'},
        {'source': 'System', 'type': 'finish', 'content': '较新的普通结束信息'},
    ]
    svc = ContextInjectionService(events_service=_FakeEventsService(events))
    svc._session_store().clear()
    monkeypatch.setattr('src.services.context_injection_service.CTX_INJECTION_ENABLED', True)
    monkeypatch.setattr('src.services.context_injection_service.CTX_HISTORY_MAX_LINES', 2)
    monkeypatch.setattr(
        'src.services.context_injection_service.CTX_TOTAL_PROMPT_MAX_CHARS', 2000
    )
    monkeypatch.setattr('src.services.context_injection_service.CTX_MAX_TOKENS_LIMIT', 100000)

    prompt, _ = asyncio.run(svc.build_augmented_prompt('sid_priority', '新问题'))
    pos_query = prompt.find('User(query): 很早的关键需求')
    pos_finish = prompt.find('System(finish): 较新的普通结束信息')
    assert pos_query >= 0 and pos_finish >= 0
    assert pos_query < pos_finish


def test_filters_non_file_urls(monkeypatch):
    events = [
        {
            'source': 'ToolExecutor',
            'type': 'tool_result',
            'content': {
                'name': 'web_search',
                'result': {
                    'links': 'https://example.com/blog/no-file https://oss/x/result.cif'
                },
            },
        }
    ]
    svc = ContextInjectionService(events_service=_FakeEventsService(events))
    svc._session_store().clear()
    monkeypatch.setattr('src.services.context_injection_service.CTX_INJECTION_ENABLED', True)

    prompt, meta = asyncio.run(svc.build_augmented_prompt('sid_file', '继续'))
    assert 'https://oss/x/result.cif' in prompt
    assert 'https://example.com/blog/no-file' not in prompt
    assert (meta.get('file_refs_count') or 0) >= 1


def test_filters_finish_and_think_tool_result_noise(monkeypatch):
    events = [
        {
            'source': 'ToolExecutor',
            'type': 'tool_result',
            'content': {'name': 'think', 'result': {'status': 'success'}},
        },
        {
            'source': 'ToolExecutor',
            'type': 'tool_result',
            'content': {'name': 'finish', 'result': {'status': 'error'}},
        },
        {
            'source': 'ToolExecutor',
            'type': 'tool_result',
            'content': {'name': 'run_abacus', 'result': {'status': 'success'}},
        },
    ]
    svc = ContextInjectionService(events_service=_FakeEventsService(events))
    svc._session_store().clear()
    monkeypatch.setattr('src.services.context_injection_service.CTX_INJECTION_ENABLED', True)

    prompt, _ = asyncio.run(svc.build_augmented_prompt('sid_noise', '继续'))
    assert 'ToolSuccess(think)' not in prompt
    assert 'ToolSuccess(finish)' not in prompt
    assert 'ToolSuccess(run_abacus)' in prompt


def test_file_block_uses_labels(monkeypatch):
    events = [
        {
            'source': 'ToolExecutor',
            'type': 'tool_result',
            'content': {
                'name': 'builder',
                'result': {
                    'output': (
                        'https://oss/x/graphene_4x4x1.cif '
                        'https://oss/x/finish_report.md'
                    )
                },
            },
        }
    ]
    svc = ContextInjectionService(events_service=_FakeEventsService(events))
    svc._session_store().clear()
    monkeypatch.setattr('src.services.context_injection_service.CTX_INJECTION_ENABLED', True)

    prompt, _ = asyncio.run(svc.build_augmented_prompt('sid_labels', '继续'))
    assert 'graphene_4x4x1.cif: https://oss/x/graphene_4x4x1.cif' in prompt
    assert 'finish_report.md: https://oss/x/finish_report.md' in prompt


def test_accepts_escaped_file_urls(monkeypatch):
    events = [
        {
            'source': 'ToolExecutor',
            'type': 'tool_result',
            'content': {
                'name': 'builder',
                'result': {
                    'output': 'https://oss/x/graphene.cif\\\\n https://oss/x/a.md\\\\n'
                },
            },
        }
    ]
    svc = ContextInjectionService(events_service=_FakeEventsService(events))
    svc._session_store().clear()
    monkeypatch.setattr('src.services.context_injection_service.CTX_INJECTION_ENABLED', True)

    prompt, meta = asyncio.run(svc.build_augmented_prompt('sid_escaped', '继续'))
    assert 'graphene.cif: https://oss/x/graphene.cif' in prompt
    assert 'a.md: https://oss/x/a.md' in prompt
    assert (meta.get('file_refs_count') or 0) >= 2


def test_structure_files_rank_ahead_of_reports(monkeypatch):
    events = [
        {
            'source': 'ToolExecutor',
            'type': 'tool_result',
            'content': {
                'name': 'finish',
                'result': {
                    'report_url': 'https://oss/x/finish_report.md',
                    'structure': 'https://oss/x/graphene_4x4x1.cif',
                },
            },
        }
    ]
    svc = ContextInjectionService(events_service=_FakeEventsService(events))
    svc._session_store().clear()
    monkeypatch.setattr('src.services.context_injection_service.CTX_INJECTION_ENABLED', True)

    prompt, _ = asyncio.run(svc.build_augmented_prompt('sid_rank', '继续'))
    pos_structure = prompt.find('graphene_4x4x1.cif: https://oss/x/graphene_4x4x1.cif')
    pos_report = prompt.find('finish_report.md: https://oss/x/finish_report.md')
    assert pos_structure >= 0 and pos_report >= 0
    assert pos_structure < pos_report


def test_history_omits_tool_error_json_and_keeps_success(monkeypatch):
    events = [
        {
            'source': 'ToolExecutor',
            'type': 'tool_result',
            'content': {
                'name': 'mat_sn_web_search',
                'result': {'message': 'Unknown tool: mat_sn_web_search'},
            },
        },
        {
            'source': 'ToolExecutor',
            'type': 'tool_result',
            'content': {
                'name': 'mat_sg_make_supercell_structure',
                'result': {'status': 'success'},
            },
        }
    ]
    svc = ContextInjectionService(events_service=_FakeEventsService(events))
    svc._session_store().clear()
    monkeypatch.setattr('src.services.context_injection_service.CTX_INJECTION_ENABLED', True)

    prompt, _ = asyncio.run(svc.build_augmented_prompt('sid_err_reason', '继续'))
    assert 'Unknown tool: mat_sn_web_search' not in prompt
    assert 'ToolSuccess(mat_sg_make_supercell_structure)' in prompt


def test_direct_mode_three_rounds_keeps_continuity(monkeypatch):
    events = [
        {'source': 'User', 'type': 'query', 'content': '第一轮：分析 graphene.cif'},
        {
            'source': 'ToolExecutor',
            'type': 'tool_result',
            'content': {'name': 'run_abacus', 'result': {'status': 'success'}},
        },
    ]
    svc = ContextInjectionService(events_service=_FakeEventsService(events))
    svc._session_store().clear()
    monkeypatch.setattr('src.services.context_injection_service.CTX_INJECTION_ENABLED', True)

    prompt1, _ = asyncio.run(
        svc.build_augmented_prompt(
            'sid_direct_3_rounds',
            '第二轮：继续收敛',
            mode='direct',
            attached_files=['https://oss/x/graphene.cif'],
        )
    )
    assert 'User(query): 第一轮：分析 graphene.cif' in prompt1
    assert 'ToolSuccess(run_abacus)' in prompt1
    assert 'https://oss/x/graphene.cif' in prompt1

    events.append({'source': 'User', 'type': 'query', 'content': '第二轮：继续收敛'})
    events.append(
        {
            'source': 'ToolExecutor',
            'type': 'tool_result',
            'content': {'name': 'mat_doc_parse', 'result': {'status': 'success'}},
        }
    )
    prompt2, _ = asyncio.run(
        svc.build_augmented_prompt(
            'sid_direct_3_rounds',
            '第三轮：输出结论',
            mode='direct',
            attached_files=['https://oss/x/report.md'],
        )
    )
    assert 'ToolSuccess(mat_doc_parse)' in prompt2
    assert 'https://oss/x/report.md' in prompt2
    assert 'https://oss/x/graphene.cif' in prompt2
