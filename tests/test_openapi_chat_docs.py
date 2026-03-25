from app import app


def _get_parameter(operation: dict, name: str, location: str) -> dict | None:
    for parameter in operation.get('parameters', []):
        if parameter.get('name') == name and parameter.get('in') == location:
            return parameter
    return None


def test_chat_session_list_openapi_contains_project_filter_and_user_header():
    schema = app.openapi()
    operation = schema['paths']['/api/v1/chat/sessions/list']['get']

    project_param = _get_parameter(operation, 'project_id', 'query')
    user_header = _get_parameter(operation, 'X-User-Id', 'header')

    assert operation['summary'] == '查询会话列表'
    assert project_param is not None
    assert project_param['required'] is False
    assert user_header is not None
    assert user_header['required'] is True


def test_chat_stream_openapi_contains_org_header():
    schema = app.openapi()
    operation = schema['paths']['/api/v1/chat/sessions/{session_id}/stream']['post']

    org_header = _get_parameter(operation, 'X-Org-Id', 'header')
    session_param = _get_parameter(operation, 'session_id', 'path')

    assert operation['summary'] == '发送消息或订阅会话流'
    assert org_header is not None
    assert org_header['required'] is False
    assert session_param is not None
    assert session_param['required'] is True
