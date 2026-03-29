"""Async protocol validation helper.

Bridges the gap left by @runtime_checkable, which does not distinguish
sync from async method implementations.  Use validate_async_protocol()
at assembly time (Exp.assemble) or in tests to catch mismatches early.
"""

from __future__ import annotations

import inspect


def _is_async_callable(method: object) -> bool:
    """Check if a method is async (coroutine function OR async generator function).

    Python distinguishes:
    - async def f(): return ...  -> iscoroutinefunction=True
    - async def f(): yield ...   -> isasyncgenfunction=True (NOT iscoroutinefunction)

    Both are valid async implementations for an async Protocol method.
    For example, LLMProvider.chat_stream() Protocol stub is iscoroutinefunction=True,
    but implementations use async generators (isasyncgenfunction=True).
    """
    return inspect.iscoroutinefunction(method) or inspect.isasyncgenfunction(method)


def validate_async_protocol(
    obj: object,
    protocol_cls: type,
    *,
    async_gen_methods: frozenset[str] = frozenset(),
) -> list[str]:
    """Validate that obj's methods match protocol_cls async/sync signatures.

    Checks every method declared in protocol_cls.__protocol_attrs__.
    Skips properties (e.g. Tool.name/description/json_schema).
    Skips non-callable attributes on the implementation side.

    For methods expected to be async (Protocol stub is iscoroutinefunction),
    the implementation can be either a coroutine function or an async
    generator function -- both count as async.

    Args:
        obj: The implementation instance to validate.
        protocol_cls: The Protocol class to validate against.
        async_gen_methods: Optional set of method names where the implementation
            is expected to be an async generator (async def + yield).
            When specified, these methods are validated with isasyncgenfunction
            instead of iscoroutinefunction. If not specified, both are accepted.

    Returns:
        List of mismatch error messages. Empty list means all checks passed.
    """
    errors: list[str] = []
    proto_attrs = getattr(protocol_cls, "__protocol_attrs__", set())

    for attr_name in sorted(proto_attrs):  # sorted for deterministic output
        # Skip properties defined on the Protocol
        proto_static = inspect.getattr_static(protocol_cls, attr_name)
        if isinstance(proto_static, property):
            continue

        proto_method = getattr(protocol_cls, attr_name, None)
        impl_method = getattr(obj, attr_name, None)

        if impl_method is None:
            errors.append(
                f"{type(obj).__name__} missing method '{attr_name}' "
                f"required by {protocol_cls.__name__}"
            )
            continue

        if not callable(impl_method):
            continue  # property on impl side, skip async check

        proto_is_async = _is_async_callable(proto_method)
        impl_is_async = _is_async_callable(impl_method)

        if proto_is_async != impl_is_async:
            expected = "async def" if proto_is_async else "def"
            actual = "async def" if impl_is_async else "def"
            errors.append(
                f"{type(obj).__name__}.{attr_name}() is {actual}, "
                f"expected {expected} per {protocol_cls.__name__}"
            )

    return errors
