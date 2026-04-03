"""Filesystem-oriented GPT-style tools."""

from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from typing import Any, ClassVar

from ..base import BaseTool
from ..models import ToolResult

try:
    import fitz  # PyMuPDF
except Exception:  # pragma: no cover - optional import
    fitz = None


MAX_DEFAULT_LINES = 2000
MAX_PDF_PAGES_PER_READ = 20
IMAGE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".bmp",
    ".tiff",
}


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _format_numbered_lines(lines: list[str], *, start_line: int = 1) -> str:
    if not lines:
        return "File is empty."
    return "\n".join(
        f"{line_no:6}\t{line}"
        for line_no, line in enumerate(lines, start=start_line)
    )


def _snippet_around_line(content: str, line_number: int, radius: int = 4) -> str:
    lines = content.splitlines()
    if not lines:
        return "File is empty."
    start = max(1, line_number - radius)
    end = min(len(lines), line_number + radius)
    return _format_numbered_lines(lines[start - 1 : end], start_line=start)


def _parse_pdf_pages(raw_pages: str, total_pages: int) -> list[int]:
    parts = raw_pages.split("-", maxsplit=1)
    if len(parts) == 1:
        page = int(parts[0])
        if not 1 <= page <= total_pages:
            raise ValueError(f"pages must be within [1, {total_pages}]")
        return [page]

    start = int(parts[0])
    end = int(parts[1])
    if start > end:
        raise ValueError("pages range start must be <= end")
    if not 1 <= start <= total_pages or not 1 <= end <= total_pages:
        raise ValueError(f"pages must be within [1, {total_pages}]")
    selected = list(range(start, end + 1))
    if len(selected) > MAX_PDF_PAGES_PER_READ:
        raise ValueError(
            f"pages range exceeds the maximum of {MAX_PDF_PAGES_PER_READ} pages"
        )
    return selected


class ReadTool(BaseTool):
    """Backend-only file reader aligned with the reference Read tool."""

    name: ClassVar[str] = "Read"
    description: ClassVar[str] = (
        "Reads a file from the local filesystem using an absolute path. "
        "Supports text files, PDFs, images, and Jupyter notebooks. "
        "Use offset/limit for large text files and pages for PDFs."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Absolute path to the file."},
            "offset": {"type": "integer", "description": "1-indexed start line."},
            "limit": {"type": "integer", "description": "Number of lines to read."},
            "pages": {
                "type": "string",
                "description": "PDF page range, for example '1-5' or '3'.",
            },
        },
        "required": ["file_path"],
        "additionalProperties": False,
    }

    def _execute(self, arguments: dict[str, Any]) -> ToolResult:
        path = self.context.resolve_absolute_path(arguments["file_path"])
        if not path.exists():
            return ToolResult.error(f"Error: file does not exist: {path}")
        if path.is_dir():
            return ToolResult.error(f"Error: path is a directory, not a file: {path}")

        suffix = path.suffix.lower()
        if suffix == ".pdf":
            return self._read_pdf(path, arguments.get("pages"))
        if suffix == ".ipynb":
            return self._read_notebook(path)
        if suffix in IMAGE_EXTENSIONS:
            return self._read_image(path)
        if arguments.get("pages"):
            return ToolResult.error("Error: pages is only supported for PDF files.")
        return self._read_text_file(
            path,
            offset=arguments.get("offset"),
            limit=arguments.get("limit"),
        )

    def _read_text_file(
        self,
        path: Path,
        *,
        offset: int | None,
        limit: int | None,
    ) -> ToolResult:
        if offset is not None and offset < 1:
            return ToolResult.error("Error: offset must be >= 1.")
        if limit is not None and limit < 1:
            return ToolResult.error("Error: limit must be >= 1.")

        content = _read_text(path)
        lines = content.splitlines()
        total_lines = len(lines)

        start = offset or 1
        if total_lines and start > total_lines:
            return ToolResult.error(
                f"Error: offset {start} is outside the file line range [1, {total_lines}]."
            )

        if limit is None:
            read_limit = MAX_DEFAULT_LINES if offset is None else total_lines
        else:
            read_limit = limit

        selected = lines[start - 1 : start - 1 + read_limit]
        is_partial = (start != 1) or (start - 1 + len(selected) < total_lines)
        self.context.read_state.record(
            path,
            is_partial=is_partial,
            offset=offset,
            limit=limit,
        )

        if not lines:
            return ToolResult.ok("File is empty.", file_path=str(path), is_partial=False)

        rendered = _format_numbered_lines(selected, start_line=start)
        if is_partial:
            rendered += (
                "\n\n[Output truncated. Use offset/limit to read another range or "
                "perform a fresh full read before editing.]"
            )
        return ToolResult.ok(
            rendered,
            file_path=str(path),
            total_lines=total_lines,
            is_partial=is_partial,
        )

    def _read_pdf(self, path: Path, pages: str | None) -> ToolResult:
        if fitz is None:
            return ToolResult.error(
                "Error: PyMuPDF is required for PDF reading but is not available."
            )

        document = fitz.open(path)
        try:
            total_pages = document.page_count
            if total_pages > 10 and not pages:
                return ToolResult.error(
                    "Error: large PDFs require the pages parameter. "
                    "Provide a range like '1-5'."
                )

            if pages:
                selected_pages = _parse_pdf_pages(pages, total_pages)
            else:
                selected_pages = list(range(1, total_pages + 1))

            text_parts: list[str] = []
            for page_number in selected_pages:
                page = document.load_page(page_number - 1)
                text_parts.append(f"## Page {page_number}\n{page.get_text().strip()}")

            self.context.read_state.record(
                path,
                is_partial=len(selected_pages) != total_pages,
                pages=pages,
            )
            return ToolResult.ok(
                "\n\n".join(text_parts).strip() or "PDF is empty.",
                file_path=str(path),
                total_pages=total_pages,
                pages=selected_pages,
            )
        except ValueError as exc:
            return ToolResult.error(f"Error: {exc}")
        finally:
            document.close()

    def _read_notebook(self, path: Path) -> ToolResult:
        raw = json.loads(_read_text(path))
        rendered_cells: list[str] = []
        cells = raw.get("cells", [])
        for index, cell in enumerate(cells):
            cell_type = cell.get("cell_type", "unknown")
            source = "".join(cell.get("source", []))
            outputs = []
            for output in cell.get("outputs", []):
                if "text" in output:
                    outputs.append("".join(output["text"]))
                elif "data" in output and "text/plain" in output["data"]:
                    outputs.append("".join(output["data"]["text/plain"]))
            rendered = f"## Cell {index} [{cell_type}]\n{source.rstrip()}"
            if outputs:
                rendered += "\n\n### Output\n" + "\n".join(outputs).rstrip()
            rendered_cells.append(rendered.rstrip())

        self.context.read_state.record(path, is_partial=False)
        return ToolResult.ok(
            "\n\n".join(rendered_cells) if rendered_cells else "Notebook is empty.",
            file_path=str(path),
            cell_count=len(cells),
        )

    def _read_image(self, path: Path) -> ToolResult:
        mime_type, _ = mimetypes.guess_type(path.name)
        self.context.read_state.record(path, is_partial=False)
        return ToolResult.ok(
            f"Image file read in backend-only mode: {path}",
            file_path=str(path),
            mime_type=mime_type or "application/octet-stream",
            size_bytes=path.stat().st_size,
        )


class EditTool(BaseTool):
    """Exact string replacement tool with read-before-edit enforcement."""

    name: ClassVar[str] = "Edit"
    description: ClassVar[str] = (
        "Performs exact string replacements in existing files. "
        "Requires a fresh full Read before editing. "
        "Use replace_all to replace every occurrence of old_string."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Absolute file path."},
            "old_string": {"type": "string", "description": "Text to replace."},
            "new_string": {"type": "string", "description": "Replacement text."},
            "replace_all": {
                "type": "boolean",
                "default": False,
                "description": "Replace every occurrence of old_string.",
            },
        },
        "required": ["file_path", "old_string", "new_string"],
        "additionalProperties": False,
    }

    def _execute(self, arguments: dict[str, Any]) -> ToolResult:
        path = self.context.resolve_absolute_path(arguments["file_path"])
        old_string = arguments["old_string"]
        new_string = arguments["new_string"]
        replace_all = bool(arguments.get("replace_all", False))

        if old_string == new_string:
            return ToolResult.error(
                "Error: old_string and new_string must be different."
            )

        if path.exists():
            if path.is_dir():
                return ToolResult.error(
                    f"Error: path is a directory, not a file: {path}"
                )
            if path.suffix.lower() == ".ipynb":
                return ToolResult.error(
                    "Error: notebook files are not supported by Edit; use a notebook-specific tool."
                )

            validation_error = self.context.read_state.validate_full_fresh_read(path)
            if validation_error:
                return ToolResult.error(validation_error)
            content = _read_text(path)
        else:
            if old_string:
                return ToolResult.error(
                    "Error: old_string must be empty when creating a new file with Edit."
                )
            content = ""

        if old_string == "":
            if content:
                return ToolResult.error(
                    "Error: old_string can only be empty for a missing or empty file."
                )
            new_content = new_string
            line_number = 1
        else:
            occurrences = content.count(old_string)
            if occurrences == 0:
                return ToolResult.error(
                    f"Error: old_string was not found in {path}."
                )
            if occurrences > 1 and not replace_all:
                return ToolResult.error(
                    "Error: old_string is not unique. Provide more context or set replace_all=true."
                )
            match_index = content.index(old_string)
            line_number = content.count("\n", 0, match_index) + 1
            if replace_all:
                new_content = content.replace(old_string, new_string)
            else:
                new_content = content.replace(old_string, new_string, 1)

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new_content, encoding="utf-8")
        self.context.read_state.record(path, is_partial=False)

        return ToolResult.ok(
            f"Updated {path}.\n\n{_snippet_around_line(new_content, line_number)}",
            file_path=str(path),
            replace_all=replace_all,
        )


class WriteTool(BaseTool):
    """Create or overwrite a file with complete content."""

    name: ClassVar[str] = "Write"
    description: ClassVar[str] = (
        "Writes a file to the local filesystem. "
        "Existing files require a fresh full Read before overwrite."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Absolute file path."},
            "content": {"type": "string", "description": "Complete file contents."},
        },
        "required": ["file_path", "content"],
        "additionalProperties": False,
    }

    def _execute(self, arguments: dict[str, Any]) -> ToolResult:
        path = self.context.resolve_absolute_path(arguments["file_path"])
        content = arguments["content"]
        existed_before = path.exists()

        if existed_before:
            if path.is_dir():
                return ToolResult.error(
                    f"Error: path is a directory, not a file: {path}"
                )
            validation_error = self.context.read_state.validate_full_fresh_read(path)
            if validation_error:
                return ToolResult.error(validation_error)

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        self.context.read_state.record(path, is_partial=False)

        action = "updated" if existed_before else "created"
        return ToolResult.ok(
            f"File {action} successfully: {path}",
            file_path=str(path),
        )
