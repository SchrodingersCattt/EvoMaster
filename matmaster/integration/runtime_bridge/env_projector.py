"""Convert resolved credentials into env-var dicts for shell execution.

Generic projection layer -- service-specific field-to-env mappings
are defined in each adapter.
"""

from __future__ import annotations

from typing import Any


def project_to_env(
    values: dict[str, Any],
    field_to_env: dict[str, str],
) -> dict[str, str]:
    """Project credential values into a string env dict.

    Args:
        values: Resolved credential key-value pairs.
        field_to_env: Mapping from credential field name to env var name.

    Returns:
        Dict of ``{ENV_VAR: string_value}`` for non-empty fields.
    """
    env: dict[str, str] = {}
    for field, env_var in field_to_env.items():
        val = values.get(field)
        if val is None:
            continue
        str_val = str(val).strip()
        if not str_val or str_val == "-1":
            continue
        env[env_var] = str_val
    return env
