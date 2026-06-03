"""Tests for BYOK provider construction: build_byok_provider_bundle + _merge_byok_extra_kwargs.

Sync, no network. Verifies extra_body passthrough/merge and bundle identity.
"""

from __future__ import annotations

from matmaster.providers.llm_factory import (
    BYOK_PROFILE_KEY,
    _merge_byok_extra_kwargs,
    build_byok_provider_bundle,
)

_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


class TestMergeByokExtraKwargs:
    def test_none_extra_body_returns_base_unchanged(self):
        base = {"reasoning_effort": "high"}
        assert _merge_byok_extra_kwargs(base, None) is base
        assert _merge_byok_extra_kwargs(None, None) is None
        assert _merge_byok_extra_kwargs(None, {}) is None

    def test_extra_body_merged_into_empty_base(self):
        out = _merge_byok_extra_kwargs(None, {"enable_thinking": True})
        assert out == {"extra_body": {"enable_thinking": True}}

    def test_user_keys_override_family_extra_body(self):
        base = {"extra_body": {"a": 1, "reasoning_effort": "low"}}
        out = _merge_byok_extra_kwargs(base, {"reasoning_effort": "max"})
        assert out["extra_body"] == {"a": 1, "reasoning_effort": "max"}

    def test_does_not_mutate_input_base(self):
        base = {"extra_body": {"a": 1}}
        _merge_byok_extra_kwargs(base, {"b": 2})
        assert base == {"extra_body": {"a": 1}}


class TestBuildByokProviderBundle:
    def test_basic_identity_and_passthrough(self):
        bundle = build_byok_provider_bundle(
            model="qwen-max",
            api_key="sk-user",
            base_url=_BASE_URL,
            credential_id="cred-1",
            extra_body={"enable_thinking": True, "thinking_budget": 1024},
        )
        assert bundle.model == "qwen-max"
        assert bundle.model_profile == BYOK_PROFILE_KEY
        assert bundle.model_route == "byok:cred-1"
        assert bundle.provider_name == "openai"
        assert bundle.provider._model == "qwen-max"
        assert bundle.provider._api_key == "sk-user"
        assert bundle.provider._base_url == _BASE_URL
        assert bundle.provider._extra_kwargs == {
            "extra_body": {"enable_thinking": True, "thinking_budget": 1024}
        }

    def test_no_extra_body_unknown_family_has_no_extra_kwargs(self):
        # qwen 不在族默认表，build_extra_kwargs 返回 None；无 extra_body 时不下发任何额外参数。
        bundle = build_byok_provider_bundle(
            model="qwen-max",
            api_key="sk-user",
            base_url=_BASE_URL,
            credential_id="cred-2",
        )
        assert bundle.provider._extra_kwargs == {}

    def test_route_falls_back_to_profile_key_without_credential_id(self):
        bundle = build_byok_provider_bundle(
            model="qwen-max", api_key="sk", base_url=_BASE_URL
        )
        assert bundle.model_route == BYOK_PROFILE_KEY

    def test_extra_body_passthrough_independent_of_model_family(self):
        # 已知族（gpt-5）下用户 extra_body 同样原样透传（BYOK profile 不设 effort，
        # 故族默认 build_extra_kwargs 为 None，最终就是用户这份）。
        bundle = build_byok_provider_bundle(
            model="gpt-5",
            api_key="sk",
            base_url=_BASE_URL,
            credential_id="c3",
            extra_body={"enable_thinking": True},
        )
        assert bundle.provider._extra_kwargs == {
            "extra_body": {"enable_thinking": True}
        }
        assert bundle.model_family == "gpt-5"
