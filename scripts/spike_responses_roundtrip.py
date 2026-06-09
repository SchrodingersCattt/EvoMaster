"""3c spike: 验证 litellm Responses 网关的 reasoning 回放（方案 A）。

手动运行（需 live gateway + .env 已加载 LITELLM_PROXY_API_KEY / LITELLM_PROXY_RESPONSES_BASE）：

    uv run python scripts/spike_responses_roundtrip.py

PASS 条件：
  1) 纯文本 round 的 reasoning item 带非空 encrypted_content，并且
     [reasoning, easy assistant message, new user] 回放不报 400。
  2) 工具 round 的 reasoning item 带非空 encrypted_content，并且
     [reasoning, easy assistant message, function_call, function_call_output,
     new user] 回放不报 400（§7.4 顺序约束被满足）。
"""

from __future__ import annotations

import asyncio
import os

import openai


def _dump(item: object) -> dict:
    return item.model_dump(mode="json", exclude_none=True)  # type: ignore[attr-defined]


def _input_text(text: str) -> dict:
    return {"role": "user", "content": [{"type": "input_text", "text": text}]}


def _output_text(response: object) -> str:
    texts: list[str] = []
    for item in getattr(response, "output", None) or []:
        if getattr(item, "type", None) != "message":
            continue
        for part in getattr(item, "content", None) or []:
            if getattr(part, "type", None) == "output_text":
                texts.append(getattr(part, "text", "") or "")
    return "".join(texts)


def _reasoning_items(response: object) -> list[dict]:
    return [
        _dump(item)
        for item in getattr(response, "output", None) or []
        if getattr(item, "type", None) == "reasoning"
    ]


def _assert_encrypted(items: list[dict], label: str) -> None:
    assert items, f"FAIL: {label} 未返回 reasoning item"
    assert all(item.get("encrypted_content") for item in items), (
        f"FAIL: {label} reasoning item 缺 encrypted_content"
        "（网关未尊重 include/store=false，§13.3）"
    )


async def main() -> None:
    base_url = os.environ["LITELLM_PROXY_RESPONSES_BASE"]
    api_key = os.environ["LITELLM_PROXY_API_KEY"]
    model = "matmaster/gpt-5.5"
    tools = [
        {
            "type": "function",
            "name": "get_weather",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
            "strict": False,
        }
    ]
    client = openai.AsyncOpenAI(api_key=api_key, base_url=base_url, max_retries=0)

    # --- text round 1: 触发 reasoning + easy assistant message ---
    text_prompt = "用一句话说出北京是中国的首都。"
    async with client.responses.stream(
        model=model,
        input=[_input_text(text_prompt)],
        instructions="You are a helpful assistant.",
        reasoning={"effort": "xhigh", "summary": "detailed"},
        include=["reasoning.encrypted_content"],
        store=False,
    ) as stream:
        final_text1 = await stream.get_final_response()

    text_reasoning_items = _reasoning_items(final_text1)
    _assert_encrypted(text_reasoning_items, "text round 1")
    assistant_text = _output_text(final_text1).strip()
    assert assistant_text, "FAIL: text round 1 未返回可回放的 assistant message"

    # --- text round 2: 验证 [reasoning, easy assistant message, new user] ---
    text_replay_input: list[dict] = [
        _input_text(text_prompt),
        *text_reasoning_items,
        {"role": "assistant", "content": assistant_text},
        _input_text("再用一句话继续。"),
    ]
    try:
        async with client.responses.stream(
            model=model,
            input=text_replay_input,
            instructions="You are a helpful assistant.",
            reasoning={"effort": "xhigh", "summary": "detailed"},
            include=["reasoning.encrypted_content"],
            store=False,
        ) as stream:
            final_text2 = await stream.get_final_response()
        print(f"OK text replay: 方案 A 纯文本回放被接受, status={final_text2.status}")
    except openai.BadRequestError as exc:
        print(f"SPIKE FAIL text replay (BadRequest): {exc}")
        print("-> 按 spec §7.4 降级方案 B（payload 存原始 output item 数组），先停下与设计者确认")
        raise

    # --- tool round 1: 强制 function_call，避免模型选择行为污染 spike ---
    tool_prompt = "先简短说明你要调用工具，再调用 get_weather 查北京天气。"
    async with client.responses.stream(
        model=model,
        input=[_input_text(tool_prompt)],
        instructions="You are a helpful assistant.",
        reasoning={"effort": "xhigh", "summary": "detailed"},
        include=["reasoning.encrypted_content"],
        store=False,
        tools=tools,
        tool_choice={"type": "function", "name": "get_weather"},
    ) as stream:
        final_tool1 = await stream.get_final_response()

    tool_reasoning_items = _reasoning_items(final_tool1)
    _assert_encrypted(tool_reasoning_items, "tool round 1")
    function_calls = [
        item
        for item in final_tool1.output
        if getattr(item, "type", None) == "function_call"
    ]
    assert function_calls, "FAIL: tool round 1 未产生 function_call"
    fc = function_calls[0]
    assistant_probe = _output_text(final_tool1).strip() or (
        "我将调用 get_weather 查询北京天气。"
    )
    print(
        "OK tool round1: "
        f"{len(tool_reasoning_items)} reasoning item(s) w/ encrypted_content; "
        f"call_id={fc.call_id}"
    )

    # --- tool round 2: 方案 A 顺序回放，显式覆盖 easy message + function_call ---
    tool_replay_input: list[dict] = [
        _input_text(tool_prompt),
        *tool_reasoning_items,
        {"role": "assistant", "content": assistant_probe},
        {
            "type": "function_call",
            "call_id": fc.call_id,
            "name": fc.name,
            "arguments": fc.arguments,
        },
        {
            "type": "function_call_output",
            "call_id": fc.call_id,
            "output": "晴，25°C",
        },
        _input_text("谢谢"),
    ]
    try:
        async with client.responses.stream(
            model=model,
            input=tool_replay_input,
            instructions="You are a helpful assistant.",
            reasoning={"effort": "xhigh", "summary": "detailed"},
            include=["reasoning.encrypted_content"],
            store=False,
            tools=tools,
            tool_choice="auto",
        ) as stream:
            final_tool2 = await stream.get_final_response()
        print(f"OK tool replay: 方案 A 工具回放被接受, status={final_tool2.status}")
        print("SPIKE PASS -> 维持方案 A")
    except openai.BadRequestError as exc:
        print(f"SPIKE FAIL tool replay (BadRequest): {exc}")
        print("-> 按 spec §7.4 降级方案 B（payload 存原始 output item 数组），先停下与设计者确认")
        raise
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
