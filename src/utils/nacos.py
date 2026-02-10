import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


@dataclass(frozen=True)
class MseNacosConfigRef:
    """MSE Nacos config reference."""

    endpoint: str
    instance_id: str
    namespace_id: str
    data_id: str
    group: str


@dataclass
class AllowlistResult:
    """Allowlist fetch result (RORO-ish return)."""

    is_ok: bool
    allowlist: Dict[str, Any]
    error: str = ''


_CACHE_EXPIRES_AT: float = 0.0
_CACHED_ALLOWLIST: Dict[str, Any] = {}

_EMAIL_CACHE_EXPIRES_AT: float = 0.0
_CACHED_EMAIL_LIST: List[str] = []


@dataclass
class EmailListResult:
    """Email allowlist fetch result (RORO-ish return)."""

    is_ok: bool
    email_list: List[str]
    error: str = ''


def _normalize_email_entries(*, email_list: Any) -> List[str]:
    """Normalize email allowlist entries to a lowercase, de-duplicated list."""
    if not isinstance(email_list, list):
        return []
    normalized: List[str] = []
    seen: set[str] = set()
    for item in email_list:
        if item is None:
            continue
        value = str(item).strip().lower()
        if not value:
            continue
        if value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


def parse_email_list_json(*, raw: str) -> EmailListResult:
    """Parse config JSON content and extract allowlist.email entries."""
    if not raw.strip():
        return EmailListResult(is_ok=False, email_list=[], error='empty_content')

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return EmailListResult(is_ok=False, email_list=[], error='invalid_json')

    allowlist = payload.get('allowlist')
    if not isinstance(allowlist, dict):
        return EmailListResult(is_ok=False, email_list=[], error='missing_allowlist')

    normalized = _normalize_email_entries(email_list=allowlist.get('email'))
    return EmailListResult(is_ok=True, email_list=normalized)


def _safe_mse_ref_log(*, mse_ref: MseNacosConfigRef) -> Dict[str, str]:
    """Build a safe-to-log dict for MSE ref (no secrets)."""
    return {
        'endpoint': mse_ref.endpoint,
        'instance_id': mse_ref.instance_id,
        'namespace_id': mse_ref.namespace_id,
        'data_id': mse_ref.data_id,
        'group': mse_ref.group,
    }


def get_cached_email_list(*, cache_ttl_seconds: int = 60) -> EmailListResult:
    """Fetch allowlist email entries from Nacos config with a small TTL cache."""
    global _EMAIL_CACHE_EXPIRES_AT, _CACHED_EMAIL_LIST

    now = time.time()
    if now < _EMAIL_CACHE_EXPIRES_AT and _CACHED_EMAIL_LIST:
        return EmailListResult(is_ok=True, email_list=_CACHED_EMAIL_LIST)

    # Cache expired, fetch from MSE Nacos
    try:
        mse_ref = build_mse_nacos_config_ref_from_env()
        logger.info(f"mse_ref: {_safe_mse_ref_log(mse_ref=mse_ref)}")
    except Exception as exc:
        return EmailListResult(is_ok=False, email_list=[], error=str(exc))

    ok, raw, err = _fetch_config_content(nacos_ref=mse_ref)

    if not ok:
        return EmailListResult(is_ok=False, email_list=[], error=err)

    parsed = parse_email_list_json(raw=raw)
    if not parsed.is_ok:
        return parsed

    _CACHED_EMAIL_LIST = parsed.email_list
    _EMAIL_CACHE_EXPIRES_AT = time.time() + max(1, cache_ttl_seconds)
    return parsed


def build_mse_nacos_config_ref_from_env() -> MseNacosConfigRef:
    """Build MSE Nacos config reference from environment variables."""
    endpoint = os.getenv('NACOS_CONFIG_ENDPOINT', '').strip()
    instance_id = os.getenv('NACOS_CONFIG_INSTANCE_ID', '').strip()
    namespace_id = os.getenv('NACOS_CONFIG_NAMESPACE_ID', '').strip()
    data_id = os.getenv('NACOS_CONFIG_DATA_ID', '').strip()
    group = os.getenv('NACOS_CONFIG_GROUP', 'DEFAULT_GROUP').strip()
    return MseNacosConfigRef(
        endpoint=endpoint,
        instance_id=instance_id,
        namespace_id=namespace_id,
        data_id=data_id,
        group=group,
    )


def _extract_mse_config_content(*, mse_response: Any) -> Tuple[bool, str, str]:
    """Extract config content string from MSE SDK response."""
    if mse_response is None:
        return False, '', 'empty_mse_response'

    body = getattr(mse_response, 'body', None) or mse_response
    configuration_obj = body.configuration
    if configuration_obj is not None:
        value = configuration_obj.content
        if isinstance(value, str) and value.strip():
            return True, value, ''

    return False, '', 'mse_config_content_not_found'


def _fetch_config_content(*, nacos_ref: MseNacosConfigRef) -> Tuple[bool, str, str]:
    """Fetch raw config content via Alibaba Cloud MSE API."""
    from alibabacloud_credentials.client import Client as CredentialClient
    from alibabacloud_mse20190531 import models as mse_20190531_models
    from alibabacloud_mse20190531.client import Client as mse20190531Client
    from alibabacloud_tea_openapi import models as open_api_models
    from alibabacloud_tea_util import models as util_models

    try:
        access_key_id = os.getenv('NACOS_CONFIG_ACCESSKEY').strip()
        access_key_secret = os.getenv('NACOS_CONFIG_SECRETKEY').strip()
        credential = CredentialClient(
            open_api_models.Config(
                type='access_key',
                access_key_id=access_key_id,
                access_key_secret=access_key_secret,
            )
        )
        config = open_api_models.Config(credential=credential)
        config.endpoint = nacos_ref.endpoint
        client = mse20190531Client(config)

        get_nacos_config_request = mse_20190531_models.GetNacosConfigRequest(
            instance_id=nacos_ref.instance_id,
            data_id=nacos_ref.data_id,
            group=nacos_ref.group,
            namespace_id=nacos_ref.namespace_id,
        )
        runtime = util_models.RuntimeOptions()
        resp = client.get_nacos_config_with_options(get_nacos_config_request, runtime)
        ok, content, err = _extract_mse_config_content(mse_response=resp)
        if not ok:
            return False, '', err
        return True, content, ''
    except Exception as exc:
        return False, '', f"mse_request_failed {exc}"


def parse_allowlist_json(*, raw: str) -> AllowlistResult:
    """Parse allowlist JSON content into dict."""
    if not raw.strip():
        return AllowlistResult(is_ok=False, allowlist={}, error='empty_content')

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return AllowlistResult(is_ok=False, allowlist={}, error='invalid_json')

    allowlist = payload.get('allowlist')
    if not isinstance(allowlist, dict):
        return AllowlistResult(is_ok=False, allowlist={}, error='missing_allowlist')

    return AllowlistResult(is_ok=True, allowlist=allowlist)


def is_email_allowlisted(*, email: str, email_list: List[str]) -> bool:
    """Check whether an email is allowlisted.

    Supports both exact email entries and domain entries:
    - "alice@example.com" -> exact match
    - "example.com" -> domain match
    """
    normalized_email = email.strip().lower()
    if not normalized_email or '@' not in normalized_email:
        return False

    local_part, domain = normalized_email.rsplit('@', 1)
    if not local_part or not domain:
        return False

    normalized_entries = _normalize_email_entries(email_list=email_list)
    if not normalized_entries:
        return False
    if normalized_email in normalized_entries:
        return True

    # Domain allowlist
    if domain in normalized_entries:
        return True

    return False


def get_cached_allowlist(*, cache_ttl_seconds: int = 60) -> AllowlistResult:
    """Fetch allowlist from Nacos with a small in-memory TTL cache.

    Note: This function does not use locks. In high-concurrency scenarios,
    multiple requests may fetch from Nacos simultaneously when cache expires,
    which is acceptable for read-only config data.
    """
    global _CACHE_EXPIRES_AT, _CACHED_ALLOWLIST

    now = time.time()
    if now < _CACHE_EXPIRES_AT and _CACHED_ALLOWLIST:
        return AllowlistResult(is_ok=True, allowlist=_CACHED_ALLOWLIST)

    email_list_result = get_cached_email_list(cache_ttl_seconds=cache_ttl_seconds)
    if not email_list_result.is_ok:
        return AllowlistResult(is_ok=False, allowlist={}, error=email_list_result.error)

    # Keep backward compatibility: expose as {"email": [...]}
    allowlist = {'email': email_list_result.email_list}
    _CACHED_ALLOWLIST = allowlist
    _CACHE_EXPIRES_AT = time.time() + max(1, cache_ttl_seconds)
    return AllowlistResult(is_ok=True, allowlist=allowlist)
