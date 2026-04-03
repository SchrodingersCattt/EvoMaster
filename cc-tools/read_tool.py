"""Read tool -- CC-style file reader with PDF pages support.

Differences from matmaster read_file:
- Adds `pages` parameter for PDF page-range reading
- Same cat -n format output with offset/limit
"""

from __future__ import annotations

import posixpath
import subprocess
from pathlib import Path
from typing import Any, ClassVar

from .base import BuiltinTool, ToolResult

MAX_READ_LINES = 2000
MAX_READ_CHARS = 200_000
PREVIEW_LINES = 50
PDF_MAX_PAGES = 20


class ReadTool(BuiltinTool):
    """Read file content with line numbers, PDF page support."""

    name: ClassVar[str] = "Read"
    description: ClassVar[str] = (
        "Reads a file from the local filesystem.\n\n"
        "Usage:\n"
        "- file_path must be an absolute path\n"
        "- By default reads up to 2000 lines from the beginning\n"
        "- Use offset/limit for partial reads of large files\n"
        "- Results returned in cat -n format with line numbers starting at 1\n"
        "- Can read PDF files with pages parameter (e.g. pages: '1-5'). "
        "For large PDFs (>10 pages), you MUST provide pages parameter. Max 20 pages per request.\n"
        "- Can read images (PNG, JPG, etc.) for multimodal analysis\n"
        "- Can read Jupyter notebooks (.ipynb)\n"
        "- Always read a file before editing or overwriting it"
    )
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "The absolute path to the file to read",
            },
            "offset": {
                "type": "integer",
                "minimum": 0,
                "description": (
                    "The line number to start reading from. "
                    "Only provide if the file is too large to read at once"
                ),
            },
            "limit": {
                "type": "integer",
                "exclusiveMinimum": 0,
                "description": (
                    "The number of lines to read. "
                    "Only provide if the file is too large to read at once."
                ),
            },
            "pages": {
                "type": "string",
                "description": (
                    'Page range for PDF files (e.g., "1-5", "3", "10-20"). '
                    "Only applicable to PDF files. Maximum 20 pages per request."
                ),
            },
        },
        "required": ["file_path"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        *,
        session: Any | None = None,
        workdir: Any | None = None,
        tracker: Any | None = None,
    ) -> None:
        super().__init__(session=session, workdir=workdir)
        self._tracker = tracker

    def _execute(self, arguments: dict[str, Any]) -> str | ToolResult:
        file_path: str = arguments.get("file_path", "")
        offset: int | None = arguments.get("offset")
        limit: int | None = arguments.get("limit")
        pages: str | None = arguments.get("pages")

        if not file_path:
            return "Error: file_path is required"

        path = Path(file_path)

        # PDF handling
        if path.suffix.lower() == ".pdf":
            return self._read_pdf(path, pages)

        # Image handling (return placeholder -- actual multimodal handled by caller)
        if path.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"):
            if not path.exists():
                return f"Error: {file_path} does not exist"
            return ToolResult.ok(
                f"[Image file: {file_path}, {path.stat().st_size} bytes]",
                file_path=file_path,
                type="image",
            )

        # Text file reading
        if self._session is not None:
            session = self._require_session()
            if not session.is_file(file_path):
                return f"Error: {file_path} is not a file"
            content: str = session.read_file(file_path)
        else:
            if not path.is_file():
                return f"Error: {file_path} is not a file"
            content = path.read_text(errors="replace")

        lines = content.splitlines()
        total = len(lines)

        if offset is not None and offset < 0:
            return "Error: offset must be >= 0"
        if limit is not None and limit < 1:
            return "Error: limit must be >= 1"

        ranged = offset is not None or limit is not None
        if ranged:
            return self._ranged_read(file_path, lines, total, offset, limit)
        return self._full_read(file_path, lines, total)

    def _read_pdf(self, path: Path, pages: str | None) -> str | ToolResult:
        """Read PDF file, optionally extracting specific pages."""
        if not path.exists():
            return f"Error: {path} does not exist"

        try:
            import pdfplumber  # type: ignore[import-untyped]
        except ImportError:
            return "Error: pdfplumber not installed. Run: pip install pdfplumber"

        with pdfplumber.open(path) as pdf:
            total_pages = len(pdf.pages)

            if pages is not None:
                page_range = self._parse_page_range(pages, total_pages)
                if isinstance(page_range, str):
                    return page_range  # error message
                if len(page_range) > PDF_MAX_PAGES:
                    return (
                        f"Error: requested {len(page_range)} pages, "
                        f"max {PDF_MAX_PAGES} per request"
                    )
            else:
                if total_pages > 10:
                    return (
                        f"Error: PDF has {total_pages} pages. "
                        f'For large PDFs, provide pages parameter (e.g. pages="1-5")'
                    )
                page_range = list(range(total_pages))

            parts: list[str] = []
            for idx in page_range:
                page = pdf.pages[idx]
                text = page.extract_text() or ""
                parts.append(f"--- Page {idx + 1} ---\n{text}")

        result = "\n\n".join(parts)
        self._mark(str(path))
        return ToolResult.ok(result, type="pdf", total_pages=total_pages)

    @staticmethod
    def _parse_page_range(pages: str, total: int) -> list[int] | str:
        """Parse page range string like '1-5', '3', '1,3,5-7'."""
        result: list[int] = []
        for part in pages.split(","):
            part = part.strip()
            if "-" in part:
                lo, hi = part.split("-", 1)
                try:
                    lo_i, hi_i = int(lo), int(hi)
                except ValueError:
                    return f"Error: invalid page range '{part}'"
                if lo_i < 1 or hi_i > total:
                    return f"Error: page range {lo_i}-{hi_i} out of bounds (1-{total})"
                result.extend(range(lo_i - 1, hi_i))
            else:
                try:
                    p = int(part)
                except ValueError:
                    return f"Error: invalid page number '{part}'"
                if p < 1 or p > total:
                    return f"Error: page {p} out of bounds (1-{total})"
                result.append(p - 1)
        return result

    def _full_read(self, file_path: str, lines: list[str], total: int) -> str:
        if total <= MAX_READ_LINES:
            output = self._format_lines(lines, file_path, init_line=1)
            truncated, result = self._apply_char_limit(output)
            if not truncated:
                self._mark(file_path)
            return result

        preview = lines[:PREVIEW_LINES]
        preview_text = self._format_lines(preview, file_path, init_line=1)
        _, result = self._apply_char_limit(
            f"Error: file has {total} lines, exceeds read limit ({MAX_READ_LINES}).\n"
            f"Use offset and limit to read portions.\n\n"
            f"Preview (first {PREVIEW_LINES} lines):\n{preview_text}"
        )
        return result

    def _ranged_read(
        self,
        file_path: str,
        lines: list[str],
        total: int,
        offset: int | None,
        limit: int | None,
    ) -> str:
        start = (offset or 0) + 1 if offset is not None else 1
        if start < 1 or start > total:
            return f"Error: offset {start} is out of range [1, {total}]."

        remaining = total - start + 1
        requested = limit if limit is not None else remaining
        count = min(requested, remaining, MAX_READ_LINES)
        end = start + count - 1

        selected = lines[start - 1 : end]
        output = self._format_lines(selected, file_path, init_line=start)

        if count < requested:
            output += (
                f"\n[Note: showing {count} of {remaining} remaining lines. "
                f"Use offset={end} to continue.]"
            )

        truncated, result = self._apply_char_limit(output)
        if not truncated:
            self._mark(file_path)
        return result

    def _mark(self, file_path: str) -> None:
        if self._tracker is not None:
            self._tracker.mark_read(posixpath.normpath(file_path))

    @staticmethod
    def _format_lines(lines: list[str], descriptor: str, init_line: int = 1) -> str:
        numbered = "\n".join(
            f"{i + init_line:6}\t{line}" for i, line in enumerate(lines)
        )
        return f"Here's the result of running `cat -n` on {descriptor}:\n{numbered}"

    @staticmethod
    def _apply_char_limit(output: str) -> tuple[bool, str]:
        if len(output) <= MAX_READ_CHARS:
            return False, output
        return True, (
            output[:MAX_READ_CHARS]
            + "\n[Output truncated. Use offset/limit for smaller ranges.]"
        )
