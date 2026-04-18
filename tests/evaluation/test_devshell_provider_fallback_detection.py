"""Unit tests for Bedrock transport heuristics used by run_devshell_eval --fallback-model."""

from __future__ import annotations

from evaluation.scripts.devshell.run_devshell_eval_helpers import (
    devshell_console_indicates_provider_fallback,
    text_indicates_devshell_provider_transport_failure,
)


def test_read_timeout_bedrock_url() -> None:
    s = (
        'Error: Read timeout on endpoint URL: "https://bedrock-runtime.us-east-1.amazonaws.com/'
        'model/xxx/converse-stream"'
    )
    assert text_indicates_devshell_provider_transport_failure(s) is True


def test_readtimeouterror_compact() -> None:
    assert (
        text_indicates_devshell_provider_transport_failure(
            "botocore.exceptions.ReadTimeoutError: Read timeout on endpoint"
        )
        is True
    )


def test_llm_stream_failed_with_timeout() -> None:
    s = (
        "LLM stream failed after 3 attempts: Read timeout on endpoint URL: "
        "https://bedrock-runtime.us-east-1.amazonaws.com/..."
    )
    assert text_indicates_devshell_provider_transport_failure(s) is True


def test_connect_timeout_with_bedrock() -> None:
    s = "ConnectTimeoutError: ... bedrock.amazonaws.com"
    assert text_indicates_devshell_provider_transport_failure(s) is True


def test_plain_tool_error_not_fallback() -> None:
    s = "ValueError: bad tool arguments"
    assert text_indicates_devshell_provider_transport_failure(s) is False


def test_devshell_console_file(tmp_path) -> None:
    p = tmp_path / "devshell_console.log"
    p.write_text("ReadTimeoutError while calling converse\n", encoding="utf-8")
    assert devshell_console_indicates_provider_fallback(p) is True
