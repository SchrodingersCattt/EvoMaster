"""服务端默认 agent 模型 profile 配置 + 解析链路测试。"""

from pathlib import Path

from matmaster.config.loader import load_agents_general_llm, load_llm_config

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "config.yaml"
_LLM_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "llm_config.yaml"
)


def test_default_agent_llm_is_deepseek_v4_pro():
    assert load_agents_general_llm(_CONFIG_PATH) == "matmaster/DeepSeek-v4-Pro"


def test_default_agent_llm_resolves_without_keyerror():
    # 真链路：默认 profile key 必须能被 llm_config 解析，否则 model=None
    # 落默认链路时 LLMConfig.resolve(default_key=...) 抛 KeyError。
    llm_config = load_llm_config(_LLM_CONFIG_PATH)
    default_llm = load_agents_general_llm(_CONFIG_PATH)
    resolved = llm_config.resolve(default_key=default_llm)
    assert resolved.profile_key == "matmaster/DeepSeek-v4-Pro"
