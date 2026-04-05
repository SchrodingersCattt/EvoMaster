from __future__ import annotations

from typing import Any, Callable

_PATH_FORMATS: frozenset[str] = frozenset(
    {
        "path",
        "file-path",
        "directory-path",
    }
)

_OUTPUT_LIKE_TOKENS: tuple[str, ...] = (
    "output",
    "save",
    "saved",
    "export",
    "plot",
    "write",
)


def resolve_local_ref(
    spec: dict[str, Any], root_schema: dict[str, Any]
) -> dict[str, Any]:
    """Resolve a local ``#/$defs/...`` reference to its concrete schema node."""
    current = spec
    seen_refs: set[str] = set()

    while True:
        ref = current.get("$ref")
        if not isinstance(ref, str) or not ref.startswith("#/"):
            return current
        if ref in seen_refs:
            raise ValueError(f"Schema ref cycle detected: {ref}")
        seen_refs.add(ref)

        node: Any = root_schema
        try:
            for part in ref[2:].split("/"):
                node = node[part]
        except Exception as exc:  # pragma: no cover - defensive schema failure
            raise ValueError(f"Schema ref could not be resolved: {ref}") from exc
        if not isinstance(node, dict):
            raise ValueError(f"Schema ref did not resolve to object: {ref}")
        current = node


def is_output_like_path_name(name: str, description: str | None = None) -> bool:
    lowered = f"{name} {description or ''}".lower()
    return any(token in lowered for token in _OUTPUT_LIKE_TOKENS)


def _iter_navigation_variants(
    schema: dict[str, Any], root_schema: dict[str, Any]
) -> list[dict[str, Any]]:
    resolved = resolve_local_ref(schema, root_schema)
    expanded: list[dict[str, Any]] = []

    for branch_key in ("anyOf", "oneOf", "allOf"):
        branches = resolved.get(branch_key)
        if isinstance(branches, list):
            for branch in branches:
                if isinstance(branch, dict):
                    expanded.extend(_iter_navigation_variants(branch, root_schema))
            if expanded:
                return expanded

    return [resolved]


def _schema_has_path_format(
    schema: dict[str, Any], root_schema: dict[str, Any]
) -> bool:
    return any(
        variant.get("format") in _PATH_FORMATS
        for variant in _iter_navigation_variants(schema, root_schema)
    )


def _schema_allows_string(
    schema: dict[str, Any], root_schema: dict[str, Any]
) -> bool:
    return any(
        variant.get("type") == "string"
        or variant.get("format") in _PATH_FORMATS
        for variant in _iter_navigation_variants(schema, root_schema)
    )


def _schema_has_nested_shape(
    schema: dict[str, Any], root_schema: dict[str, Any]
) -> bool:
    for variant in _iter_navigation_variants(schema, root_schema):
        if isinstance(variant.get("properties"), dict) and variant.get("properties"):
            return True
        if isinstance(variant.get("items"), dict):
            return True
    return False


def _maybe_collect_leaf_selector(
    prefix: str,
    schema: dict[str, Any],
    root_schema: dict[str, Any],
) -> set[str]:
    if not prefix:
        return set()

    if _schema_has_path_format(schema, root_schema):
        return {prefix}

    name = prefix.rsplit(".", 1)[-1]
    if name.endswith("[]"):
        name = name[:-2]

    resolved = resolve_local_ref(schema, root_schema)
    description = resolved.get("description")
    if (
        name.endswith("_path")
        and not is_output_like_path_name(name, description)
        and _schema_allows_string(schema, root_schema)
    ):
        return {prefix}

    return set()


def collect_path_selectors(
    schema: dict[str, Any] | None,
    prefix: str = "",
    *,
    root_schema: dict[str, Any] | None = None,
) -> set[str]:
    """Collect path-bearing selectors from a schema, including nested refs/arrays."""
    if not isinstance(schema, dict):
        return set()

    root = root_schema or schema
    resolved = resolve_local_ref(schema, root)
    selectors = _maybe_collect_leaf_selector(prefix, resolved, root)

    properties = resolved.get("properties")
    if isinstance(properties, dict):
        for key, child in properties.items():
            if not isinstance(child, dict):
                continue
            child_prefix = f"{prefix}.{key}" if prefix else key
            selectors.update(
                collect_path_selectors(child, child_prefix, root_schema=root)
            )

    items = resolved.get("items")
    if isinstance(items, dict):
        selectors.update(_maybe_collect_leaf_selector(prefix, items, root))
        if _schema_has_nested_shape(items, root):
            item_prefix = f"{prefix}[]" if prefix else "[]"
            selectors.update(
                collect_path_selectors(items, item_prefix, root_schema=root)
            )

    for branch_key in ("anyOf", "oneOf", "allOf"):
        branches = resolved.get(branch_key)
        if isinstance(branches, list):
            for branch in branches:
                if isinstance(branch, dict):
                    selectors.update(
                        collect_path_selectors(branch, prefix, root_schema=root)
                    )

    return selectors


def _parse_selector(selector: str) -> tuple[str, ...]:
    parts = tuple(part.strip() for part in selector.split(".") if part.strip())
    if not parts:
        raise ValueError(f"Invalid empty selector: {selector!r}")
    return parts


def _selector_schema_candidates(
    schema: dict[str, Any],
    selector: str,
) -> list[dict[str, Any]]:
    root = schema
    candidates: list[dict[str, Any]] = [schema]

    for segment in _parse_selector(selector):
        wants_items = segment.endswith("[]")
        field_name = segment[:-2] if wants_items else segment
        next_candidates: list[dict[str, Any]] = []

        for candidate in candidates:
            for variant in _iter_navigation_variants(candidate, root):
                properties = variant.get("properties")
                if not isinstance(properties, dict):
                    continue
                child = properties.get(field_name)
                if not isinstance(child, dict):
                    continue

                child = resolve_local_ref(child, root)
                if wants_items:
                    for child_variant in _iter_navigation_variants(child, root):
                        items = child_variant.get("items")
                        if isinstance(items, dict):
                            next_candidates.append(items)
                else:
                    next_candidates.append(child)

        candidates = next_candidates
        if not candidates:
            return []

    return [resolve_local_ref(candidate, root) for candidate in candidates]


def validate_selector_paths(schema: dict[str, Any], selectors: set[str]) -> None:
    missing = sorted(
        selector
        for selector in selectors
        if not _selector_schema_candidates(schema, selector)
    )
    if missing:
        raise ValueError(f"Unknown path selector(s): {missing}")


def _selector_tokens(selector: str) -> tuple[str, ...]:
    return _parse_selector(selector)


def _has_selector_prefix(
    selector_map: dict[tuple[str, ...], str], prefix: tuple[str, ...]
) -> bool:
    return any(tokens[: len(prefix)] == prefix for tokens in selector_map)


def _selector_schema(
    schema: dict[str, Any] | None, selector: str
) -> dict[str, Any] | None:
    if not isinstance(schema, dict):
        return None

    candidates = _selector_schema_candidates(schema, selector)
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    return {"anyOf": candidates}


def _enum_contains_value(
    schema: dict[str, Any] | None,
    value: Any,
    root_schema: dict[str, Any] | None,
) -> bool:
    if not isinstance(schema, dict) or not isinstance(value, str):
        return False

    root = root_schema or schema
    for variant in _iter_navigation_variants(schema, root):
        enum_values = variant.get("enum")
        if isinstance(enum_values, list) and value in enum_values:
            return True
    return False


def _array_items_schema(
    schema: dict[str, Any] | None,
    root_schema: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(schema, dict):
        return None

    root = root_schema or schema
    item_schemas: list[dict[str, Any]] = []

    for variant in _iter_navigation_variants(schema, root):
        items = variant.get("items")
        if isinstance(items, dict):
            item_schemas.append(resolve_local_ref(items, root))

    if not item_schemas:
        return None
    if len(item_schemas) == 1:
        return item_schemas[0]
    return {"anyOf": item_schemas}


def _is_null_like(value: Any) -> bool:
    return value is None or (
        isinstance(value, str) and value.strip().lower() in {"", "none", "null"}
    )


def _rewrite_exact_value(
    value: Any,
    *,
    selector: str,
    schema_leaf: dict[str, Any] | None,
    root_schema: dict[str, Any] | None,
    rewrite_leaf: Callable[[str, Any, dict[str, Any] | None], Any],
) -> Any:
    if _is_null_like(value):
        return value

    if isinstance(value, list):
        item_schema = _array_items_schema(schema_leaf, root_schema) or schema_leaf
        return [
            item
            if _is_null_like(item) or _enum_contains_value(item_schema, item, root_schema)
            else rewrite_leaf(selector, item, item_schema)
            for item in value
        ]

    if isinstance(value, str) and _enum_contains_value(schema_leaf, value, root_schema):
        return value

    if isinstance(value, (str, bytes, int, float, bool)):
        return rewrite_leaf(selector, value, schema_leaf)

    return value


def rewrite_selected_paths(
    payload: Any,
    *,
    selectors: set[str],
    schema: dict[str, Any] | None = None,
    rewrite_leaf: Callable[[str, Any, dict[str, Any] | None], Any],
) -> Any:
    """Rewrite only selected leaves in a nested payload."""
    selector_map = {tuple(_selector_tokens(selector)): selector for selector in selectors}

    def _rewrite(value: Any, prefix: tuple[str, ...]) -> Any:
        selector = selector_map.get(prefix)
        if selector is not None:
            return _rewrite_exact_value(
                value,
                selector=selector,
                schema_leaf=_selector_schema(schema, selector),
                root_schema=schema,
                rewrite_leaf=rewrite_leaf,
            )

        if isinstance(value, dict):
            rewritten = dict(value)
            for key, child in value.items():
                leaf_prefix = prefix + (key,)
                array_prefix = prefix + (f"{key}[]",)
                if isinstance(child, list) and _has_selector_prefix(
                    selector_map, array_prefix
                ):
                    rewritten[key] = [_rewrite(item, array_prefix) for item in child]
                elif _has_selector_prefix(selector_map, leaf_prefix):
                    rewritten[key] = _rewrite(child, leaf_prefix)
            return rewritten

        return value

    return _rewrite(payload, ())
