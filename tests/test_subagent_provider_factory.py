"""service 层 subagent provider factory：换 profile + 共享 billing state。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from clients.matmaster_platform.billing.client import BillingRunContext
from matmaster.config.loader import load_llm_config
from src.services.agent_run_service import make_subagent_provider_factory
from src.services.billing_llm_provider import BillingLLMProvider, BillingRunState

_LLM_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "llm_config.yaml"


def _factory(state):
    return make_subagent_provider_factory(
        llm_config=load_llm_config(_LLM_CONFIG_PATH),
        run_context=BillingRunContext(session_id="s", task_id="t", invocation_id="i"),
        billing_service=MagicMock(),
        billing_state=state,
    )


def test_factory_returns_billing_wrapped_bundle_for_profile():
    state = BillingRunState(session_id="s")
    fac = _factory(state)
    bundle = fac(profile_key="matmaster/DeepSeek-v4-Pro")
    assert bundle.model_profile == "matmaster/DeepSeek-v4-Pro"
    assert isinstance(bundle.provider, BillingLLMProvider)


def test_factory_shares_billing_run_state():
    state = BillingRunState(session_id="s")
    fac = _factory(state)
    bundle = fac(profile_key="matmaster/DeepSeek-v4-Pro")
    assert bundle.provider._run_state is state


def test_factory_returns_fresh_bundle_each_call():
    state = BillingRunState(session_id="s")
    fac = _factory(state)
    b1 = fac(profile_key="matmaster/DeepSeek-v4-Pro")
    b2 = fac(profile_key="matmaster/DeepSeek-v4-Pro")

    assert b1.provider is not b2.provider
    assert b1.provider._inner is not b2.provider._inner
    assert b1.provider._run_state is b2.provider._run_state is state


def test_factory_raises_keyerror_on_unknown_profile():
    fac = _factory(BillingRunState(session_id="s"))
    with pytest.raises(KeyError):
        fac(profile_key="matmaster/does-not-exist")
