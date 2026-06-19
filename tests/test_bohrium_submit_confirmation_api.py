from src.apis import chat_api


def test_session_bohrium_submit_confirmation_data_reads_session_value():
    data = chat_api._session_bohrium_submit_confirmation_data_from_row(
        "s1",
        {"bohrium_submit_confirmation_required": 0},
    )

    assert data.session_id == "s1"
    assert data.required is False


def test_session_bohrium_submit_confirmation_data_keeps_unset():
    data = chat_api._session_bohrium_submit_confirmation_data_from_row(
        "s1",
        {"bohrium_submit_confirmation_required": None},
    )

    assert data.session_id == "s1"
    assert data.required is None
