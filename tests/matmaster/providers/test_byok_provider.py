"""BYOK：合成 profile + 固定 transport=chat_completions，extra_body 黑盒透传。"""

from __future__ import annotations

from matmaster.providers.llm_factory import BYOK_PROFILE_KEY, build_byok_provider_bundle
from matmaster.providers.transports.chat_completions import ChatCompletionsTransport

_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def test_basic_identity_and_passthrough() -> None:
    b = build_byok_provider_bundle(
        model="qwen-max",
        api_key="sk-user",
        base_url=_BASE_URL,
        credential_id="cred-1",
        extra_body={"enable_thinking": True, "thinking_budget": 1024},
    )
    assert b.model == "qwen-max"
    assert b.model_profile == BYOK_PROFILE_KEY
    assert b.model_route == "byok:cred-1"
    assert b.provider_name == "byok"
    assert isinstance(b.provider, ChatCompletionsTransport)
    assert b.provider._model == "qwen-max"
    assert b.provider._api_key == "sk-user"
    assert b.provider._base_url == _BASE_URL
    assert b.provider._extra_body == {"enable_thinking": True, "thinking_budget": 1024}


def test_no_extra_body_default_context_limit() -> None:
    b = build_byok_provider_bundle(
        model="qwen-max",
        api_key="sk-user",
        base_url=_BASE_URL,
        credential_id="cred-2",
    )
    assert b.provider._extra_body is None
    assert b.context_limit == 200_000
    assert b.context_limit_source == "byok_default"


def test_explicit_context_limit_from_credential() -> None:
    b = build_byok_provider_bundle(
        model="qwen-max",
        api_key="sk-user",
        base_url=_BASE_URL,
        credential_id="cred-3",
        context_limit=1_000_000,
    )
    assert b.context_limit == 1_000_000
    assert b.context_limit_source == "byok_credential"


def test_route_falls_back_to_profile_key_without_credential_id() -> None:
    b = build_byok_provider_bundle(model="qwen-max", api_key="sk", base_url=_BASE_URL)
    assert b.model_route == BYOK_PROFILE_KEY


def test_extra_body_passthrough_in_build_kwargs() -> None:
    b = build_byok_provider_bundle(
        model="qwen-max",
        api_key="sk",
        base_url=_BASE_URL,
        extra_body={"enable_thinking": True},
    )
    kw = b.provider.build_kwargs([], None)
    assert kw["extra_body"] == {"enable_thinking": True}
