"""Human simulator for MATTER evaluation."""

from __future__ import annotations

import json
from typing import Any

from evomaster.utils.llm import LLMConfig, create_llm
from evomaster.utils.types import Dialog, SystemMessage, UserMessage

from .schemas import LLMRuntimeConfig, QuestionItem


_SIMULATOR_SYSTEM_PROMPT = """You are a human scientist asking a question to an AI research assistant.
Rewrite the seed prompt to look like natural human wording while preserving:
1) physics goal
2) observable or deliverable
3) required accuracy/constraints
4) method class constraints

Output STRICT JSON:
{"prompt": "<rewritten prompt>"}
Do not add any extra text.
"""


class SingleTurnSimulator:
    """Single-turn simulator (default)."""

    def __init__(self, llm_cfg: LLMRuntimeConfig | None = None, use_seed_prompt: bool = True):
        self._use_seed_prompt = use_seed_prompt
        self._llm = None
        if llm_cfg is not None:
            cfg = LLMConfig(
                provider=llm_cfg.provider,
                model=llm_cfg.model,
                api_key=llm_cfg.api_key,
                base_url=llm_cfg.base_url,
                temperature=llm_cfg.temperature,
                max_tokens=llm_cfg.max_tokens,
                timeout=llm_cfg.timeout,
            )
            # Keep simulator logs off by default in evaluation pipeline.
            self._llm = create_llm(cfg, output_config={"show_in_console": False, "log_to_file": False})

    def render_prompt(self, question: QuestionItem) -> str:
        if self._use_seed_prompt or self._llm is None:
            return question.human_prompt_seed
        return self._rewrite_prompt(question)

    def _rewrite_prompt(self, question: QuestionItem) -> str:
        dialog = Dialog(
            messages=[
                SystemMessage(content=_SIMULATOR_SYSTEM_PROMPT),
                UserMessage(
                    content=(
                        f"Question intent:\n{question.intent}\n\n"
                        f"Seed prompt:\n{question.human_prompt_seed}\n\n"
                        "Return JSON only."
                    )
                ),
            ],
            tools=[],
        )
        try:
            reply = self._llm.query(dialog)
            payload = self._parse_json_payload(reply.content or "")
            prompt = str(payload.get("prompt", "")).strip()
            return prompt or question.human_prompt_seed
        except Exception:
            return question.human_prompt_seed

    @staticmethod
    def _parse_json_payload(text: str) -> dict[str, Any]:
        stripped = text.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            return json.loads(stripped)
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start >= 0 and end > start:
            return json.loads(stripped[start : end + 1])
        raise ValueError("No JSON object found in simulator output")


class MultiTurnSimulator:
    """Reserved extension point for multi-turn simulation."""

    def run(self, *_args: Any, **_kwargs: Any) -> None:
        raise NotImplementedError("MultiTurnSimulator is reserved for future iterations.")


    def __init__(self) -> None:
        self.enabled = False
