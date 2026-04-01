"""Tests for ``utils.env`` (constants resolved at import; reload under patched ``os.environ``)."""

from __future__ import annotations

import importlib
import os
from unittest.mock import patch


def _reload_utils_env() -> object:
    import utils.env as m

    return importlib.reload(m)


def test_service_env_and_url_part_and_tools_server_default() -> None:
    base = dict(os.environ)
    base.pop("MATMASTER_TOOLS_SERVER", None)
    base["SERVICE_ENV"] = "test"
    with patch.dict(os.environ, base, clear=True):
        m = _reload_utils_env()
        assert m.SERVICE_ENV == "test"
        assert m.URL_PART == ".test"
        assert (
            m.MATMASTER_TOOLS_SERVER
            == "https://matmaster-tools-server.test.bohrium.com"
        )


def test_prod_no_url_part() -> None:
    base = dict(os.environ)
    base.pop("MATMASTER_TOOLS_SERVER", None)
    base["SERVICE_ENV"] = "prod"
    with patch.dict(os.environ, base, clear=True):
        m = _reload_utils_env()
        assert m.URL_PART == ""
        assert m.MATMASTER_TOOLS_SERVER == "https://matmaster-tools-server.bohrium.com"


def test_matmaster_tools_server_env_override() -> None:
    base = dict(os.environ)
    base["SERVICE_ENV"] = "test"
    base["MATMASTER_TOOLS_SERVER"] = "https://custom.example"
    with patch.dict(os.environ, base, clear=True):
        m = _reload_utils_env()
        assert m.MATMASTER_TOOLS_SERVER == "https://custom.example"


def test_evaluation_bearer_env_optional() -> None:
    base = dict(os.environ)
    base.pop("MATMASTER_TOOLS_EVALUATION_BEARER", None)
    base["SERVICE_ENV"] = "test"
    with patch.dict(os.environ, base, clear=True):
        m = _reload_utils_env()
        assert m.MATMASTER_TOOLS_EVALUATION_BEARER is None

    base2 = dict(os.environ)
    base2["SERVICE_ENV"] = "test"
    base2["MATMASTER_TOOLS_EVALUATION_BEARER"] = " k1 "
    with patch.dict(os.environ, base2, clear=True):
        m2 = _reload_utils_env()
        assert m2.MATMASTER_TOOLS_EVALUATION_BEARER == "k1"
