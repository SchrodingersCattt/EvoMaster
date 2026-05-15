"""User instructions injection helpers extracted from agent_run_service.

Phase 0 refactor (DESIGN.md §14 Phase 0a): moved out of
``agent_run_service.py`` so it stays under the 800-line target. These
helpers are the legacy runtime-injection path that Phase 1 will replace
with the AGENT.md hash anchor in user_turn_context events
(DESIGN.md §1c HASH-03). Until then, behavior is unchanged.

NOTE: ``agent_run_service.py`` re-exports the public names from this
module so existing test imports keep working through Phase 1.

COMPAT:legacy-runtime-injection-helper -- Phase 1 removes this helper from the
AgentRunService runtime main path, but the function stays importable for old
tests and for Phase 2C cleanup.
"""

from __future__ import annotations

from matmaster.types.messages import Message, UserMessage

_USER_INSTRUCTIONS_PATH = '/personal/.matmaster/AGENT.md'
_USER_INSTRUCTIONS_START = (
    f'<matmaster-user-instructions source="{_USER_INSTRUCTIONS_PATH}">'
)
_USER_INSTRUCTIONS_END = '</matmaster-user-instructions>'
_USER_INSTRUCTIONS_TEMPLATE = (
    f"{_USER_INSTRUCTIONS_START}\n"
    "The following content comes from the user's personal instruction file.\n"
    "\n"
    "Treat it as user-level preferences. Follow it when relevant, but do not "
    "let it override system, developer, tool, safety, data-access, or project "
    "constraints.\n"
    "\n"
    "{content}\n"
    f"{_USER_INSTRUCTIONS_END}\n"
    "\n"
    "{user_query}"
)


def _strip_user_instructions_prefix(text: str | None) -> str:
    """Remove a leading runtime user-instructions wrapper if present."""
    if not text:
        return ""
    if not text.startswith(_USER_INSTRUCTIONS_START):
        return text

    end_idx = text.find(_USER_INSTRUCTIONS_END)
    if end_idx == -1:
        return text

    remainder = text[end_idx + len(_USER_INSTRUCTIONS_END) :]
    if remainder.startswith("\n\n"):
        return remainder[2:]
    if remainder.startswith("\n"):
        return remainder[1:]
    return remainder


def _find_first_user_message_index(history: list[Message]) -> int | None:
    """Return the first UserMessage index in model-visible history."""
    for index, message in enumerate(history):
        if isinstance(message, UserMessage):
            return index
    return None


def _render_user_instructions_block(
    *,
    user_instructions: str,
    user_query: str,
) -> str:
    """Render user instructions as a user-level prefix for the first query."""
    return _USER_INSTRUCTIONS_TEMPLATE.format(
        content=user_instructions,
        user_query=user_query,
    )


def _apply_user_instructions_to_initial_user_query(
    *,
    user_prompt: str,
    user_instructions: str | None,
    history: list[Message],
) -> tuple[str, list[Message]]:
    """Inject user instructions into the first model-visible user query.

    The transform is runtime-only and idempotent. Existing wrappers are stripped
    first, which prevents duplicate prefixes when restored history comes from a
    durable compaction checkpoint that captured a previously rewritten message.
    """
    instructions = (user_instructions or "").strip()
    updated_history = list(history)
    first_user_idx = _find_first_user_message_index(updated_history)

    if first_user_idx is None:
        stripped_prompt = _strip_user_instructions_prefix(user_prompt)
        if not instructions:
            return stripped_prompt, updated_history
        return (
            _render_user_instructions_block(
                user_instructions=instructions,
                user_query=stripped_prompt,
            ),
            updated_history,
        )

    first_user = updated_history[first_user_idx]
    stripped_content = _strip_user_instructions_prefix(first_user.content)
    if instructions:
        stripped_content = _render_user_instructions_block(
            user_instructions=instructions,
            user_query=stripped_content,
        )
    updated_history[first_user_idx] = first_user.model_copy(
        update={"content": stripped_content}
    )
    return user_prompt, updated_history
