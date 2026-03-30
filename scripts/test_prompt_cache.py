"""Quick diagnostic: does the LLM gateway return prompt-cache tokens?

Usage (from repo root):
    uv run python scripts/test_prompt_cache.py

Reads LITELLM_PROXY_API_BASE / LITELLM_PROXY_API_KEY from .env (via dotenv).
Sends two requests with the same long system prompt; the second should show
non-zero cached_tokens if prompt caching is enabled.
"""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

BASE = os.environ.get("LITELLM_PROXY_API_BASE", "")
KEY = os.environ.get("LITELLM_PROXY_API_KEY", "")
MODEL = sys.argv[1] if len(sys.argv) > 1 else "claude-sonnet-4-6"

if not BASE or not KEY:
    sys.exit("ERROR: set LITELLM_PROXY_API_BASE and LITELLM_PROXY_API_KEY in .env")

client = OpenAI(api_key=KEY, base_url=BASE)

# ~2400 tokens system prompt — exceeds Anthropic's 1024-token cache minimum
LONG_SYSTEM = "You are a helpful assistant. " * 400


def _cache_info(usage) -> dict:
    ptd = getattr(usage, "prompt_tokens_details", None)
    extra = getattr(usage, "model_extra", {}) or {}
    return {
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "total_tokens": usage.total_tokens,
        # OpenAI format
        "cached_tokens": getattr(ptd, "cached_tokens", None) if ptd else None,
        "cache_creation_tokens": (
            getattr(ptd, "cache_creation_tokens", None) if ptd else None
        ),
        # Anthropic format (in model_extra)
        "cache_read_input_tokens": extra.get("cache_read_input_tokens"),
        "cache_creation_input_tokens": extra.get("cache_creation_input_tokens"),
    }


print(f"model:    {MODEL}")
print(f"base_url: {BASE}")
print()

# Call 1 — should create cache
r1 = client.chat.completions.create(
    model=MODEL,
    messages=[
        {"role": "system", "content": LONG_SYSTEM},
        {"role": "user", "content": "Say hi."},
    ],
    max_tokens=10,
)
info1 = _cache_info(r1.usage)
print(f"Call 1 (cache create): {info1}")

# Call 2 — same system prefix, should hit cache
r2 = client.chat.completions.create(
    model=MODEL,
    messages=[
        {"role": "system", "content": LONG_SYSTEM},
        {"role": "user", "content": "Say bye."},
    ],
    max_tokens=10,
)
info2 = _cache_info(r2.usage)
print(f"Call 2 (cache read):   {info2}")

# Verdict
cached = (info2.get("cached_tokens") or 0) + (info2.get("cache_read_input_tokens") or 0)
print()
if cached > 0:
    print(f"✅ Prompt caching is ACTIVE — {cached} tokens read from cache on call 2")
else:
    print("❌ Prompt caching NOT active — cached_tokens = 0 on both calls")
    print("   Ask gateway admin to enable: anthropic-beta: prompt-caching-2024-07-31")
