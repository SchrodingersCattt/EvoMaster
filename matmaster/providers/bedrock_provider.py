"""LLMProvider implementation using Amazon Bedrock Converse / ConverseStream (boto3).

Credentials use the default AWS chain (``AWS_ACCESS_KEY_ID`` / ``AWS_SECRET_ACCESS_KEY`` /
``AWS_REGION``, instance profile, etc.). ``api_key`` / ``base_url`` from YAML are ignored.

Maps OpenAI-shaped ``messages`` / ``tools`` (from the kernel) to Bedrock Converse requests
and streams deltas back as :class:`StreamChunk` compatible with :class:`ChatCompletionsProvider`.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import threading
from collections.abc import AsyncIterator
from typing import Any

from matmaster.types.llm_provider import LLMProvider  # noqa: F401
from matmaster.types.messages import (
    LLMResponse,
    StreamChunk,
    ToolCallData,
    parse_tool_arguments,
)

# botocore timeout / connection errors are NOT ClientError subclasses,
# so they need explicit handling to enable the kernel's retry loop.
try:
    from botocore.exceptions import (
        ConnectionClosedError,
        ConnectTimeoutError,
        EndpointConnectionError,
        ReadTimeoutError,
    )

    _BOTOCORE_TRANSIENT_ERRORS: tuple[type[BaseException], ...] = (
        ReadTimeoutError,
        ConnectTimeoutError,
        EndpointConnectionError,
        ConnectionClosedError,
    )
except ImportError:  # botocore not installed; let tuple be empty
    _BOTOCORE_TRANSIENT_ERRORS = ()

logger = logging.getLogger(__name__)


def _openai_tools_to_bedrock(tools: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Map OpenAI ``tools`` list to Bedrock ``toolConfig`` (toolSpec list)."""
    specs: list[dict[str, Any]] = []
    for block in tools:
        if block.get("type") != "function":
            continue
        fn = block.get("function") or {}
        name = str(fn.get("name") or "")
        if not name:
            continue
        desc = str(fn.get("description") or "")
        params = fn.get("parameters")
        if not isinstance(params, dict):
            params = {"type": "object", "properties": {}}
        specs.append(
            {
                "toolSpec": {
                    "name": name,
                    "description": desc,
                    "inputSchema": {"json": params},
                }
            }
        )
    if not specs:
        return None
    return {"tools": specs}


def _parse_tool_arguments_string(raw: str) -> dict[str, Any]:
    raw = raw.strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def openai_messages_to_bedrock_converse(
    messages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]] | None, list[dict[str, Any]]]:
    """Split OpenAI-compatible history into Bedrock ``system`` blocks and ``messages``.

    Returns:
        (system_blocks, messages) where system_blocks is a list of ``{"text": ...}``
        or None if empty; messages is the Bedrock ``messages`` array.
    """
    system_texts: list[str] = []
    bedrock_messages: list[dict[str, Any]] = []
    i = 0
    n = len(messages)

    while i < n:
        m = messages[i]
        role = m.get("role")
        if role == "system":
            system_texts.append(str(m.get("content") or ""))
            i += 1
            continue
        if role == "user":
            text = str(m.get("content") or "")
            merged = [text]
            j = i + 1
            while j < n and messages[j].get("role") == "user":
                merged.append(str(messages[j].get("content") or ""))
                j += 1
            combined = "\n\n".join(t for t in merged if t)
            bedrock_messages.append({"role": "user", "content": [{"text": combined}]})
            i = j
            continue
        if role == "assistant":
            blocks: list[dict[str, Any]] = []
            text = str(m.get("content") or "")
            if text:
                blocks.append({"text": text})
            tool_calls = m.get("tool_calls") or []
            if isinstance(tool_calls, list):
                for tc in tool_calls:
                    if not isinstance(tc, dict):
                        continue
                    fn = tc.get("function") or {}
                    name = str(fn.get("name") or "")
                    tid = str(tc.get("id") or "")
                    args_raw = fn.get("arguments")
                    if isinstance(args_raw, str):
                        inp = _parse_tool_arguments_string(args_raw)
                    elif isinstance(args_raw, dict):
                        inp = args_raw
                    else:
                        inp = {}
                    blocks.append(
                        {
                            "toolUse": {
                                "toolUseId": tid,
                                "name": name,
                                "input": inp,
                            }
                        }
                    )
            if not blocks:
                blocks.append({"text": ""})
            bedrock_messages.append({"role": "assistant", "content": blocks})
            i += 1
            continue
        if role == "tool":
            tool_blocks: list[dict[str, Any]] = []
            while i < n and messages[i].get("role") == "tool":
                tm = messages[i]
                tid = str(tm.get("tool_call_id") or "")
                txt = str(tm.get("content") or "")
                tool_blocks.append(
                    {
                        "toolResult": {
                            "toolUseId": tid,
                            "content": [{"text": txt}],
                            "status": "success",
                        }
                    }
                )
                i += 1
            bedrock_messages.append({"role": "user", "content": tool_blocks})
            continue

        logger.warning("Skipping unsupported message role in Bedrock mapping: %s", role)
        i += 1

    system_blocks: list[dict[str, Any]] | None = None
    if system_texts:
        joined = "\n\n".join(s for s in system_texts if s)
        if joined:
            system_blocks = [{"text": joined}]
    return system_blocks, bedrock_messages


def _bedrock_stop_to_openai_finish(reason: str | None) -> str | None:
    if not reason:
        return None
    r = reason.lower()
    if r in ("tool_use",):
        return "tool_calls"
    if r in ("end_turn",):
        return "stop"
    if r in ("max_tokens",):
        return "length"
    return reason


def _get_positive_int(usage: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = usage.get(key)
        if isinstance(value, int) and value > 0:
            return value
    return 0


def _usage_from_metadata(meta: dict[str, Any]) -> tuple[dict[str, int], dict[str, Any]]:
    usage = meta.get("usage") or {}
    inp = int(usage.get("inputTokens") or usage.get("input_tokens") or 0)
    out = int(usage.get("outputTokens") or usage.get("output_tokens") or 0)
    tot = int(usage.get("totalTokens") or usage.get("total_tokens") or (inp + out))
    flat = {
        "prompt_tokens": inp,
        "completion_tokens": out,
        "total_tokens": tot,
    }
    cache_read = _get_positive_int(
        usage,
        "cacheReadInputTokens",
        "cache_read_input_tokens",
        "cache_read_tokens",
    )
    if cache_read:
        flat["cache_read_tokens"] = cache_read
    cache_write = _get_positive_int(
        usage,
        "cacheWriteInputTokens",
        "cacheCreationInputTokens",
        "cache_write_input_tokens",
        "cache_creation_input_tokens",
        "cache_write_tokens",
    )
    if cache_write:
        flat["cache_write_tokens"] = cache_write
    reasoning = _get_positive_int(
        usage,
        "reasoningTokens",
        "reasoning_tokens",
    )
    if reasoning:
        flat["reasoning_tokens"] = reasoning
    return flat, dict(usage)


class BedrockProvider:
    """Bedrock Converse / ConverseStream behind :class:`LLMProvider`."""

    def __init__(
        self,
        *,
        model_id: str,
        region: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        timeout: float = 300.0,
        stream_timeout: float | None = None,
        stream_idle_timeout: float | None = None,
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ) -> None:
        self._model_id = model_id
        self._region = region
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout = int(round(timeout))
        self._stream_timeout = stream_timeout
        self._stream_idle_timeout = stream_idle_timeout
        self._max_retries = max_retries
        self._retry_delay = retry_delay
        self._client: Any = None
        self._enter_count: int = 0

    async def __aenter__(self) -> BedrockProvider:
        self._enter_count += 1
        if self._client is not None:
            return self
        import boto3
        from botocore.config import Config

        self._client = boto3.client(
            "bedrock-runtime",
            region_name=self._region,
            config=Config(
                read_timeout=self._timeout + 30,
                connect_timeout=15,
            ),
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:  # type: ignore[no-untyped-def]
        self._enter_count -= 1
        if self._enter_count > 0:
            return
        self._client = None

    def _ensure_client(self) -> Any:
        if self._client is None:
            raise RuntimeError(
                "BedrockProvider must be used as async context manager: "
                "'async with provider:'"
            )
        return self._client

    @property
    def stream_timeout(self) -> float | None:
        return self._stream_timeout

    @property
    def stream_idle_timeout(self) -> float | None:
        return self._stream_idle_timeout

    @property
    def max_retries(self) -> int:
        return self._max_retries

    @property
    def retry_delay(self) -> float:
        return self._retry_delay

    def _inference_config(self) -> dict[str, Any]:
        cfg: dict[str, Any] = {
            "temperature": self._temperature,
        }
        if self._max_tokens is not None:
            cfg["maxTokens"] = self._max_tokens
        return cfg

    def _converse_kwargs(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        system, bedrock_messages = openai_messages_to_bedrock_converse(messages)
        kwargs: dict[str, Any] = {
            "modelId": self._model_id,
            "messages": bedrock_messages,
            "inferenceConfig": self._inference_config(),
        }
        if system:
            kwargs["system"] = system
        tc = _openai_tools_to_bedrock(tools or [])
        if tc is not None:
            kwargs["toolConfig"] = tc
        return kwargs

    def _parse_output_message(self, msg: dict[str, Any]) -> LLMResponse:
        content = msg.get("content") or []
        text_parts: list[str] = []
        tool_calls: list[ToolCallData] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if "text" in block:
                text_parts.append(str(block.get("text") or ""))
            tu = block.get("toolUse")
            if isinstance(tu, dict):
                tid = str(tu.get("toolUseId") or "")
                name = str(tu.get("name") or "")
                raw_in = tu.get("input")
                if isinstance(raw_in, dict):
                    args = raw_in
                elif isinstance(raw_in, str):
                    args = parse_tool_arguments(raw_in)
                else:
                    args = parse_tool_arguments(json.dumps(raw_in))
                tool_calls.append(
                    ToolCallData(id=tid, name=name, arguments=args),
                )
        joined = "".join(text_parts) if text_parts else None
        return LLMResponse(
            content=joined,
            tool_calls=tool_calls if tool_calls else None,
            finish_reason=None,
            usage={},
            usage_vendor=None,
        )

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        tool_choice: str | dict | None = None,
    ) -> LLMResponse:
        from botocore.exceptions import ClientError

        from matmaster.types.errors import LLMError

        if tool_choice is not None and tool_choice != "none":
            raise NotImplementedError(
                f"bedrock_provider does not yet support tool_choice={tool_choice!r}"
            )

        client = self._ensure_client()
        kwargs = self._converse_kwargs(messages, tools)

        def _call() -> dict[str, Any]:
            return client.converse(**kwargs)

        try:
            raw = await asyncio.to_thread(_call)
        except ClientError as exc:
            raise self._map_client_error(exc) from exc
        except _BOTOCORE_TRANSIENT_ERRORS as exc:
            raise LLMError(str(exc), retryable=True, error_category="server") from exc

        out = raw.get("output") or raw.get("Output") or {}
        msg = out.get("message") or out.get("Message") or {}
        llm = self._parse_output_message(msg)
        stop = raw.get("stopReason") or raw.get("stop_reason")
        usage: dict[str, int] = {}
        usage_vendor: dict[str, Any] | None = None
        u = raw.get("usage") or {}
        if u:
            usage, usage_vendor = _usage_from_metadata({"usage": u})
        return LLMResponse(
            content=llm.content,
            tool_calls=llm.tool_calls,
            finish_reason=_bedrock_stop_to_openai_finish(str(stop) if stop else None),
            usage=usage,
            usage_vendor=usage_vendor,
        )

    def _map_client_error(self, exc: Any) -> Any:
        from botocore.exceptions import ClientError

        from matmaster.types.errors import LLMError

        if not isinstance(exc, ClientError):
            return LLMError(str(exc), retryable=False, error_category="unknown")

        code = (exc.response.get("Error") or {}).get("Code") or ""
        msg = str(exc)
        code_l = str(code).lower()
        if code_l in ("throttlingexception", "toomanyrequestsexception"):
            return LLMError(msg, retryable=True, error_category="rate_limit")
        if code_l in (
            "serviceunavailable",
            "internalserverexception",
            "modeltimeoutexception",
        ):
            return LLMError(msg, retryable=True, error_category="server")
        if code_l in ("validationexception",):
            low = msg.lower()
            if "not authorized" in low or "unauthorized to invoke" in low:
                return LLMError(msg, retryable=False, error_category="auth")
            if "context" in low and ("length" in low or "token" in low):
                return LLMError(msg, retryable=False, error_category="context_overflow")
            return LLMError(msg, retryable=False, error_category="bad_request")
        if code_l in (
            "accessdeniedexception",
            "unauthorizedexception",
            "invalidsignatureexception",
        ):
            return LLMError(msg, retryable=False, error_category="auth")
        return LLMError(msg, retryable=True, error_category="unknown")

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        timeout: float | None = None,
    ) -> AsyncIterator[StreamChunk]:
        from botocore.exceptions import ClientError

        from matmaster.types.errors import LLMError

        client = self._ensure_client()
        kwargs = self._converse_kwargs(messages, tools)

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Any] = asyncio.Queue()
        sentinel = object()

        def worker() -> None:
            def put_item(obj: object) -> None:
                fut = asyncio.run_coroutine_threadsafe(queue.put(obj), loop)
                try:
                    fut.result(timeout=300)
                except concurrent.futures.CancelledError:
                    # Consumer stopped (e.g. after LLMError); avoid thread traceback noise.
                    pass

            try:
                resp = client.converse_stream(**kwargs)
                stream = resp.get("stream")
                if stream is None:
                    put_item(
                        LLMError(
                            "bedrock converse_stream returned no stream",
                            retryable=True,
                            error_category="server",
                        )
                    )
                    put_item(sentinel)
                    return
                for event in stream:
                    put_item(event)
                put_item(sentinel)
            except ClientError as exc:
                put_item(exc)
                put_item(sentinel)
            except _BOTOCORE_TRANSIENT_ERRORS as exc:
                # ReadTimeoutError / ConnectTimeoutError / etc. are NOT ClientError;
                # convert to retryable LLMError so the kernel retry loop can handle them.
                put_item(LLMError(str(exc), retryable=True, error_category="server"))
                put_item(sentinel)
            except Exception as exc:
                put_item(exc)
                put_item(sentinel)

        threading.Thread(target=worker, daemon=True).start()

        last_usage: dict[str, int] | None = None
        last_usage_vendor: dict[str, Any] | None = None

        try:
            while True:
                item = await queue.get()
                if item is sentinel:
                    break
                if isinstance(item, ClientError):
                    raise self._map_client_error(item) from item
                if isinstance(item, LLMError):
                    raise item
                if isinstance(item, BaseException):
                    raise LLMError(
                        str(item), retryable=False, error_category="unknown"
                    ) from item

                event = item
                if not isinstance(event, dict):
                    continue

                if "contentBlockDelta" in event:
                    cbd = event["contentBlockDelta"] or {}
                    delta = cbd.get("delta") or {}
                    idx = int(cbd.get("contentBlockIndex", 0))
                    if "text" in delta and delta["text"]:
                        yield StreamChunk(content=str(delta["text"]))
                    rc = delta.get("reasoningContent")
                    if rc:
                        text = ""
                        if isinstance(rc, dict) and "text" in rc:
                            text = str(rc.get("text") or "")
                        elif isinstance(rc, str):
                            text = rc
                        if text:
                            yield StreamChunk(reasoning_content=text)
                    tu = delta.get("toolUse")
                    if isinstance(tu, dict):
                        d: dict[str, Any] = {"index": idx}
                        if tu.get("toolUseId"):
                            d["id"] = str(tu["toolUseId"])
                        if tu.get("name"):
                            d["name"] = str(tu["name"])
                        inp = tu.get("input")
                        if isinstance(inp, str) and inp:
                            d["arguments"] = inp
                        if len(d) > 1:
                            yield StreamChunk(tool_call_deltas=[d])

                if "contentBlockStart" in event:
                    cbs = event["contentBlockStart"] or {}
                    start = cbs.get("start") or {}
                    idx = int(cbs.get("contentBlockIndex", 0))
                    tu = start.get("toolUse")
                    if isinstance(tu, dict):
                        d = {"index": idx}
                        if tu.get("toolUseId"):
                            d["id"] = str(tu["toolUseId"])
                        if tu.get("name"):
                            d["name"] = str(tu["name"])
                        yield StreamChunk(tool_call_deltas=[d])

                if "messageStop" in event:
                    ms = event["messageStop"] or {}
                    sr = ms.get("stopReason")
                    fr = _bedrock_stop_to_openai_finish(str(sr) if sr else None)
                    if fr:
                        yield StreamChunk(finish_reason=fr)

                if "metadata" in event:
                    md = event["metadata"] or {}
                    usage = md.get("usage") or {}
                    if usage:
                        u_flat, u_vendor = _usage_from_metadata({"usage": usage})
                        last_usage = u_flat
                        last_usage_vendor = u_vendor
        finally:
            pass

        if last_usage is not None:
            yield StreamChunk(
                usage=last_usage,
                usage_vendor=last_usage_vendor,
            )
