"""devshell 非计费 subagent provider factory。"""

from __future__ import annotations

from pathlib import Path

import pytest

from matmaster.config.loader import load_llm_config
from matmaster.devshell.runner import make_dev_subagent_provider_factory

_LLM_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "llm_config.yaml"


def test_dev_factory_returns_bare_bundle_for_profile():
    fac = make_dev_subagent_provider_factory(load_llm_config(_LLM_CONFIG_PATH))
    bundle = fac(profile_key="matmaster/DeepSeek-v4-Pro")
    assert bundle.model_profile == "matmaster/DeepSeek-v4-Pro"
    # devshell 不包计费：provider 不是 BillingLLMProvider
    from src.services.billing_llm_provider import BillingLLMProvider

    assert not isinstance(bundle.provider, BillingLLMProvider)


def test_dev_factory_raises_keyerror_on_unknown():
    fac = make_dev_subagent_provider_factory(load_llm_config(_LLM_CONFIG_PATH))
    with pytest.raises(KeyError):
        fac(profile_key="matmaster/nope")


def test_build_run_context_installs_factory_when_llm_config_present(tmp_path):
    from matmaster.devshell.config import DevConfig
    from matmaster.devshell.runner import DevRunner

    def sink(event):
        return None

    runner = DevRunner(
        config=DevConfig(),
        workdir=tmp_path,
        llm_provider=object(),
        llm_config=load_llm_config(_LLM_CONFIG_PATH),
    )

    request = runner.build_run_context(child_event_sink=sink).request
    assert request.ports.child_event_forward_sink is sink
    assert request.ports.subagent_provider_factory is not None
