import logging
from types import SimpleNamespace

from evomaster.utils.llm import LLMConfig, OpenAILLM
from evomaster.utils.types import Dialog, UserMessage


def _make_chunk(
    *,
    reasoning_content: str | None = None,
    content: str | None = None,
    finish_reason: str | None = None,
):
    delta = SimpleNamespace(
        reasoning_content=reasoning_content,
        content=content,
        tool_calls=None,
    )
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice])


class _CreateStub:
    def __init__(self, chunks):
        self._chunks = chunks

    def create(self, **kwargs):
        return self._chunks


def test_openai_stream_accumulates_reasoning_content():
    llm = OpenAILLM.__new__(OpenAILLM)
    llm.config = LLMConfig(
        provider='openai',
        model='claude-sonnet-4-6',
        api_key='dummy',
        base_url='https://proxy.example.com',
        thinking_effort='high',
    )
    llm.logger = logging.getLogger(__name__)
    llm._use_azure_client = False
    llm.client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=_CreateStub(
                [
                    _make_chunk(reasoning_content='r1'),
                    _make_chunk(reasoning_content='r2', content='final '),
                    _make_chunk(content='answer', finish_reason='stop'),
                ]
            )
        )
    )

    deltas: list[str] = []
    msg = llm.query_stream(
        Dialog(messages=[UserMessage(content='hello')]),
        on_token=deltas.append,
    )

    assert deltas == ['r1', 'r2']
    assert msg.content == 'final answer'
    assert msg.meta['reasoning_content'] == 'r1r2'
