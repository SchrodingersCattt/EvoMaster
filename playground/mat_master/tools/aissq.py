"""Built-in tools: AIS Square (aissq) model/dataset search and download.

AIS Square (https://www.aissquare.com) is a community platform for sharing
machine learning interatomic potentials (MLIPs) and datasets — analogous to
Hugging Face for the materials science domain.

No authentication required — all public resources are freely accessible.

Tools:
    aissq_search  — search/list models and datasets by keyword
    aissq_download — download a specific resource by exact name
"""

import json
import logging
import time
from pathlib import Path
from typing import Any, ClassVar, Optional

import requests
from requests.adapters import HTTPAdapter
from pydantic import Field
from urllib3.util.retry import Retry

from evomaster.agent.tools.base import BaseTool, BaseToolParams

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# AIS Square HTTP client (no auth required)
# ---------------------------------------------------------------------------


class AissqError(Exception):
    """Base exception for AIS Square client errors."""


class ResourceNotFoundError(AissqError):
    """Raised when a requested resource cannot be found."""


def _format_size(size_bytes: int) -> str:
    """Format a byte count as a human-readable string."""
    if size_bytes <= 0:
        return "unknown size"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"


class AissqClient:
    """HTTP client for the AIS Square platform API.

    All listing, detail, and download operations are public — no login required.

    Args:
        verify_ssl: Whether to verify SSL certificates.
        timeout: Request timeout in seconds (default: 30).
    """

    BASE_URL = "https://backend.aissquare.com"

    def __init__(
        self,
        verify_ssl: bool = True,
        timeout: int = 30,
    ):
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        self.session = requests.Session()

        retry_strategy = Retry(
            total=3,
            backoff_factor=1.0,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

        self.session.headers.update(
            {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "matmaster-aissq/1.0",
            }
        )

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        """Perform a GET request and return the parsed JSON response."""
        url = f"{self.BASE_URL}{path}"
        resp = self.session.get(
            url, params=params, verify=self.verify_ssl, timeout=self.timeout
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code", 0) != 0:
            raise AissqError(
                f"API error {data.get('code')}: {data.get('message', 'Unknown error')}"
            )
        return data

    def list_resources(
        self, resource_type: str, page: int = 1, page_size: int = 300
    ) -> dict:
        """List resources of a given type (one page).

        Args:
            resource_type: "models" or "datasets".
            page: Page number (1-based).
            page_size: Number of items per page.

        Returns:
            Dict with keys: items, total, page, perPage.
        """
        data = self._get(
            f"/content/{resource_type}",
            params={"page": page, "pageSize": page_size},
        )
        return data.get("data", {})

    def list_all_resources(self, resource_type: str) -> list:
        """Fetch all resources of a given type, auto-paginating as needed."""
        page = 1
        page_size = 300
        all_items: list = []

        while True:
            result = self.list_resources(resource_type, page=page, page_size=page_size)
            items = result.get("items", [])
            all_items.extend(items)

            total = result.get("total", 0)
            if len(all_items) >= total or len(items) == 0:
                break

            page += 1

        return all_items

    def search_by_keyword(self, keyword: str, resource_type: str) -> list:
        """Search resources by keyword (case-insensitive substring match on name).

        Args:
            keyword: Search keyword.
            resource_type: "models" or "datasets".

        Returns:
            List of matching resource dicts.
        """
        keyword_lower = keyword.lower()
        items = self.list_all_resources(resource_type)
        return [
            item for item in items
            if keyword_lower in item.get("name", "").lower()
        ]

    def find_by_name(self, name: str, resource_type: str) -> Optional[dict]:
        """Find a resource by exact name (client-side filtering).

        Args:
            name: Exact resource name to search for.
            resource_type: "models" or "datasets".

        Returns:
            The matching resource dict, or None if not found.
        """
        items = self.list_all_resources(resource_type)
        for item in items:
            if item.get("name") == name:
                return item
        return None

    def get_detail(
        self, resource_id: int, resource_type: str, name: str = ""
    ) -> dict:
        """Get detailed information for a resource, including file download links.

        Uses the public GET /dpa/detail/{type} endpoint — no authentication required.

        Args:
            resource_id: The numeric ID of the resource.
            resource_type: "models" or "datasets".
            name: Resource name (optional, improves server-side lookup).

        Returns:
            Detail dict with keys: files, name, description, license, etc.
            files is a list of dicts with keys: fileName, downloadLink, size.
        """
        params: dict = {"id": resource_id}
        if name:
            params["name"] = name
        data = self._get(f"/dpa/detail/{resource_type}", params=params)
        return data.get("data", {})

    def download_resource(
        self,
        name: str,
        resource_type: str,
        output_dir: Path,
    ) -> list[dict]:
        """Full download flow: find resource → get file list → download all files.

        No authentication required.

        Args:
            name: Exact resource name (e.g. "DPA-3.2-5M").
            resource_type: "models" or "datasets".
            output_dir: Directory to save files into. Files are saved under
                        output_dir/name/fileName.

        Returns:
            List of dicts with keys: file_name, local_path, size, size_human.

        Raises:
            ResourceNotFoundError: If the resource is not found.
            AissqError: On download failures.
        """
        resource = self.find_by_name(name, resource_type)
        if resource is None:
            raise ResourceNotFoundError(
                f"Resource '{name}' not found in {resource_type}."
            )

        resource_id = resource["ID"]
        _logger.info("Found '%s' (ID=%s)", name, resource_id)

        detail = self.get_detail(resource_id, resource_type=resource_type, name=name)
        files = detail.get("files", [])
        if not files:
            _logger.info("No files found for '%s'.", name)
            return []

        _logger.info("Found %d file(s) to download for '%s'.", len(files), name)

        save_dir = output_dir / name
        save_dir.mkdir(parents=True, exist_ok=True)

        downloaded: list[dict] = []
        for i, file_info in enumerate(files):
            file_name = file_info.get("fileName", f"file_{i}")
            download_link = file_info.get("downloadLink", "")
            file_size = file_info.get("size", 0)

            if not download_link:
                _logger.warning("Skipping '%s': no download link.", file_name)
                continue

            output_path = save_dir / file_name
            _logger.info(
                "Downloading [%d/%d]: %s (%s)",
                i + 1,
                len(files),
                file_name,
                _format_size(file_size),
            )

            self.download_file(
                download_link,
                output_path,
                expected_size=file_size,
            )
            downloaded.append(
                {
                    "file_name": file_name,
                    "local_path": str(output_path.resolve()),
                    "size": file_size,
                    "size_human": _format_size(file_size),
                }
            )

            if i < len(files) - 1:
                time.sleep(1)

        _logger.info(
            "Download complete. %d file(s) saved to '%s'.",
            len(downloaded),
            save_dir,
        )
        return downloaded

    def download_file(
        self,
        url: str,
        output_path: Path,
        expected_size: int = 0,
        chunk_size: int = 8192,
        max_retries: int = 3,
    ) -> None:
        """Download a single file from a URL with streaming and retry.

        Args:
            url: Direct download URL.
            output_path: Local path to save the file.
            expected_size: Expected file size in bytes (for logging).
            chunk_size: Streaming chunk size in bytes.
            max_retries: Number of retry attempts on failure.

        Raises:
            AissqError: If download fails after all retries.
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)

        last_exc = None
        for attempt in range(1, max_retries + 1):
            try:
                resp = self.session.get(
                    url,
                    stream=True,
                    verify=self.verify_ssl,
                    timeout=self.timeout,
                )
                resp.raise_for_status()

                total_size = expected_size or int(
                    resp.headers.get("Content-Length", 0)
                )
                downloaded_bytes = 0

                with open(output_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=chunk_size):
                        if chunk:
                            f.write(chunk)
                            downloaded_bytes += len(chunk)

                if total_size > 0:
                    _logger.debug(
                        "Downloaded %s: %s / %s",
                        output_path.name,
                        _format_size(downloaded_bytes),
                        _format_size(total_size),
                    )
                return

            except (requests.RequestException, OSError) as exc:
                last_exc = exc
                if attempt < max_retries:
                    wait = 2 ** (attempt - 1)
                    _logger.warning(
                        "Attempt %d failed for '%s': %s. Retrying in %ds...",
                        attempt,
                        url,
                        exc,
                        wait,
                    )
                    time.sleep(wait)
                else:
                    raise AissqError(
                        f"Failed to download '{url}' after {max_retries} attempts: {exc}"
                    ) from exc

    @staticmethod
    def parse_author(author_field: str) -> list:
        """Parse the JSON-encoded author field from listing responses."""
        if not author_field:
            return []
        try:
            return json.loads(author_field)
        except (json.JSONDecodeError, TypeError):
            return []

    @staticmethod
    def format_authors(author_field: str) -> str:
        """Format the author field as a display string."""
        authors = AissqClient.parse_author(author_field)
        if not authors:
            return "Unknown"
        names = []
        for a in authors:
            first = a.get("firstName", "")
            last = a.get("lastName", "")
            name = f"{first} {last}".strip()
            if name:
                names.append(name)
        return ", ".join(names) if names else "Unknown"


# ---------------------------------------------------------------------------
# aissq_search tool
# ---------------------------------------------------------------------------


class AissqSearchToolParams(BaseToolParams):
    """Search for DP/MLIP models or fine-tuning datasets on AIS Square.

    AIS Square (https://www.aissquare.com) is a community platform for sharing
    machine learning interatomic potentials (MLIPs) and datasets.

    Use this tool when:
    - The user needs a DP potential for a specific material system not covered
      by built-in DPA pretrained models (DPA-2.4-7M, DPA-3.1-3M, DPA-3.2-5M)
    - The user needs a DPA fine-tuning dataset for their material system

    Returns a list of matching resources with name, ID, authors, download count,
    and modification date. Use aissq_download to download a specific resource.
    """

    name: ClassVar[str] = "aissq_search"

    keyword: str = Field(
        description=(
            "Search keyword for filtering resources by name "
            "(case-insensitive substring match). "
            "Examples: 'DPA', 'copper', 'perovskite', 'water', 'Li'. "
            "Use an empty string or broad term to list all resources."
        )
    )
    resource_type: str = Field(
        default="models",
        description=(
            "Type of resource to search: 'models' for DP/MLIP potential models, "
            "'datasets' for training/fine-tuning datasets."
        ),
    )
    page_size: int = Field(
        default=50,
        description="Maximum number of results to return (default: 50, max: 300).",
    )


class AissqSearchTool(BaseTool):
    """Built-in tool: search AIS Square for DP/MLIP models and datasets."""

    name: ClassVar[str] = "aissq_search"
    params_class: ClassVar[type[BaseToolParams]] = AissqSearchToolParams

    def execute(self, session: Any, args_json: str) -> tuple[str, dict]:
        try:
            params = self.parse_params(args_json)
            assert isinstance(params, AissqSearchToolParams)

            resource_type = (params.resource_type or "models").strip().lower()
            if resource_type not in ("models", "datasets"):
                result = {
                    "status": "error",
                    "results": [],
                    "message": "resource_type must be 'models' or 'datasets'",
                }
                return json.dumps(result), {"result": result}

            keyword = (params.keyword or "").strip()
            page_size = max(1, min(int(params.page_size), 300))

            client = AissqClient()

            if keyword:
                items = client.search_by_keyword(keyword, resource_type)
            else:
                result_page = client.list_resources(
                    resource_type, page=1, page_size=page_size
                )
                items = result_page.get("items", [])

            # Trim to page_size
            items = items[:page_size]

            formatted = []
            for item in items:
                formatted.append(
                    {
                        "name": item.get("name", ""),
                        "id": item.get("ID", ""),
                        "type": item.get("type", resource_type),
                        "authors": AissqClient.format_authors(
                            item.get("author", "")
                        ),
                        "downloads": item.get("downloadCount", 0),
                        "views": item.get("viewCount", 0),
                        "modified": item.get("modifyDate", ""),
                        "prefix": item.get("prefix", ""),
                    }
                )

            result = {
                "status": "success",
                "resource_type": resource_type,
                "keyword": keyword,
                "total_returned": len(formatted),
                "results": formatted,
                "note": (
                    "Use aissq_download with the exact 'name' value to download a resource."
                ),
            }
            return json.dumps(result, ensure_ascii=False), {"result": result}

        except Exception as exc:
            self.logger.warning("aissq_search failed: %s", exc)
            result = {
                "status": "error",
                "results": [],
                "message": f"{type(exc).__name__}: {exc}",
            }
            return json.dumps(result), {"result": result}


# ---------------------------------------------------------------------------
# aissq_download tool
# ---------------------------------------------------------------------------


class AissqDownloadToolParams(BaseToolParams):
    """Download a DP/MLIP model or dataset from AIS Square by exact name.

    Downloads all files for the named resource to the workspace under
    aissq_downloads/<resource_name>/. No authentication required.

    Use aissq_search first to find the exact resource name.
    """

    name: ClassVar[str] = "aissq_download"

    resource_name: str = Field(
        description=(
            "Exact resource name as returned by aissq_search "
            "(e.g. 'DPA-3.2-5M', 'DeepEMs-25'). Must match exactly."
        )
    )
    resource_type: str = Field(
        default="models",
        description=(
            "Type of resource: 'models' for DP/MLIP potential models, "
            "'datasets' for training/fine-tuning datasets."
        ),
    )


class AissqDownloadTool(BaseTool):
    """Built-in tool: download a DP/MLIP model or dataset from AIS Square."""

    name: ClassVar[str] = "aissq_download"
    params_class: ClassVar[type[BaseToolParams]] = AissqDownloadToolParams

    def execute(self, session: Any, args_json: str) -> tuple[str, dict]:
        try:
            params = self.parse_params(args_json)
            assert isinstance(params, AissqDownloadToolParams)

            resource_name = (params.resource_name or "").strip()
            if not resource_name:
                result = {
                    "status": "error",
                    "message": "resource_name is required",
                }
                return json.dumps(result), {"result": result}

            resource_type = (params.resource_type or "models").strip().lower()
            if resource_type not in ("models", "datasets"):
                result = {
                    "status": "error",
                    "message": "resource_type must be 'models' or 'datasets'",
                }
                return json.dumps(result), {"result": result}

            # Resolve download directory from session workspace
            ws = getattr(getattr(session, "config", None), "workspace_path", None)
            if ws:
                download_base = Path(ws) / "aissq_downloads"
            else:
                download_base = Path("./aissq_downloads")

            self.logger.info(
                "Downloading '%s' (%s) to '%s'",
                resource_name,
                resource_type,
                download_base,
            )

            client = AissqClient()
            downloaded_files = client.download_resource(
                name=resource_name,
                resource_type=resource_type,
                output_dir=download_base,
            )

            download_dir = str((download_base / resource_name).resolve())

            result = {
                "status": "success",
                "resource_name": resource_name,
                "resource_type": resource_type,
                "download_dir": download_dir,
                "files_downloaded": len(downloaded_files),
                "files": downloaded_files,
            }
            return json.dumps(result, ensure_ascii=False), {"result": result}

        except ResourceNotFoundError as exc:
            self.logger.warning("aissq_download: resource not found: %s", exc)
            result = {
                "status": "error",
                "message": str(exc),
                "hint": (
                    "Use aissq_search to find the exact resource name. "
                    "Names are case-sensitive."
                ),
            }
            return json.dumps(result), {"result": result}

        except Exception as exc:
            self.logger.warning("aissq_download failed: %s", exc)
            result = {
                "status": "error",
                "message": f"{type(exc).__name__}: {exc}",
            }
            return json.dumps(result), {"result": result}


# ---------------------------------------------------------------------------
# Factory functions
# ---------------------------------------------------------------------------


def get_aissq_search_tool() -> AissqSearchTool:
    """Return an AissqSearchTool instance for registration."""
    return AissqSearchTool()


def get_aissq_download_tool() -> AissqDownloadTool:
    """Return an AissqDownloadTool instance for registration."""
    return AissqDownloadTool()
