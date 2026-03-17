"""Shared constants and helpers for tool callbacks."""

import re
from typing import Any
from urllib.parse import urlparse

_DPA_MODEL_ALIAS_MAP: dict[str, str] = {
    'DPA2.4-7M': 'https://bohrium.oss-cn-zhangjiakou.aliyuncs.com/13756/27666/store/upload/cd12300a-d3e6-4de9-9783-dd9899376cae/dpa-2.4-7M.pt',
    'DPA3.1-3M': 'https://bohrium.oss-cn-zhangjiakou.aliyuncs.com/13756/27666/store/upload/18b8f35e-69f5-47de-92ef-af8ef2c13f54/DPA-3.1-3M.pt',
    'DPA3.2-5M': 'https://dp-storage-test2.oss-cn-zhangjiakou.aliyuncs.com/bohrium-test/bohrium/feedback/attachment/01KF3BF3TX9GVTC96Q0PCV01H3/DPA-3.2-5M.pt',
}

_OSS_URL_RE = re.compile(r"https?://[^\s,'\"<>)}\]]+")
_DEFAULT_DOWNLOAD_SUBDIR = 'oss_downloaded_files'
_AUTO_DOWNLOAD_MAX_BYTES = 100 * 1024 * 1024


def is_error_artifact_url(url: str) -> bool:
    """Return True for OSS URLs that are malformed error artifacts, not real outputs.

    MCP tools occasionally return URLs when a job fails, e.g.:
    - Filename is '.' or '..' (tar entries for current/parent dir).
    - Filename starts with '..' or '._' (e.g. '..tgz', '._1.tgz' broken archives).
    Normal output files like 'results.tgz' are NOT filtered.

    Used both in the auto-download callback and the finish-report generator.
    """
    try:
        path = urlparse(url).path
        segments = path.rstrip('/').split('/')
        last_seg = segments[-1] if segments else ''
        if not last_seg:
            return True  # trailing slash or empty path
        if last_seg in ('.', '..'):
            return True  # directory entries, not real files
        return last_seg.startswith('..') or last_seg.startswith('._')
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Characterization file-transfer constants
# ---------------------------------------------------------------------------

_CHARACTERIZATION_PREFIXES: tuple[str, ...] = (
    'mat_nmr_',
    'mat_xrd_',
    'mat_electron_microscope_',
)

_CHARACTERIZATION_ARTIFACT_KEYS: frozenset[str] = frozenset(
    {
        'chart_option_path',
        'csv_path',
        'raw_data_path',
        'features_path',
        'top_phases_csv_path',
        'all_phases_path',
        'chart_json_path',
    }
)

_MOL_FILE_EXTS: frozenset[str] = frozenset(
    {
        '.xyz',
        '.pdb',
        '.sdf',
        '.mol',
        '.mol2',
        '.cif',
    }
)


def _normalize_alias(text: str) -> str:
    """Normalize DPA model alias for lookup (lowercase, alphanumeric only)."""
    return re.sub(r'[^a-z0-9]', '', text.lower())


_DPA_MODEL_ALIAS_NORM_MAP = {
    _normalize_alias(k): v for k, v in _DPA_MODEL_ALIAS_MAP.items()
}

_SN_TOP_LEVEL_FIELDS_TO_REMOVE: frozenset[str] = frozenset(
    {
        'userId',
        'globalId',
    }
)

_SN_PAPER_FIELDS_TO_REMOVE: frozenset[str] = frozenset(
    {
        'zhName',
        'zhAbstract',
        'paperId',
        'publicationId',
        'publicationCover',
        'graphicalAbstract',
        'languageType',
        'impactScore',
        'popularity',
        'good',
        'goodFlag',
        'readFlag',
        'addFlag',
        'openAccess',
        'pdfFlag',
        'title',
        'authorDetails',
        'alltext',
        'pieces',  # enAbstract 的完整重复，纯冗余（~800 chars/paper）
    }
)


def _extract_artifact_urls(obj: Any, keys: frozenset[str]) -> list[str]:
    """Recursively extract HTTP(S) URLs from known artifact-key values in a JSON object."""
    urls: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in keys and isinstance(v, str) and v.strip().startswith('http'):
                urls.append(v.strip())
            elif isinstance(v, (dict, list)):
                urls.extend(_extract_artifact_urls(v, keys))
    elif isinstance(obj, list):
        for item in obj:
            urls.extend(_extract_artifact_urls(item, keys))
    return urls
