from __future__ import annotations

from functools import lru_cache
from typing import Any

from matmaster.config.llm import LLMProfileConfig
from matmaster.providers.llm_factory import build_provider_from_profile
from src.services.byok_redaction import sanitize_provider_error


class BYOKVerificationError(RuntimeError):
    pass


class BYOKVerifier:
    async def verify_unsaved(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str,
        supports_vision: bool = False,
    ) -> dict[str, Any]:
        profile = LLMProfileConfig(
            provider="openai",
            model=model,
            api_key=api_key,
            base_url=base_url,
            max_tokens=16,
            timeout=30,
            stream_timeout=30,
            stream_idle_timeout=30,
            max_retries=0,
            retry_delay=0,
            supports_vision=supports_vision,
        )
        try:
            provider = build_provider_from_profile(profile, model)
            async with provider:
                await provider.chat(
                    [{"role": "user", "content": "Reply with OK."}],
                    tools=None,
                )
        except Exception as exc:
            return {
                "status": "failed",
                "supports_streaming": False,
                "supports_tool_calling": False,
                "supports_vision": False,
                "error": sanitize_provider_error(exc),
            }

        return {
            "status": "verified",
            "supports_streaming": True,
            "supports_tool_calling": True,
            "supports_vision": supports_vision,
            "error": None,
        }


@lru_cache(maxsize=1)
def get_byok_verifier() -> BYOKVerifier:
    return BYOKVerifier()
