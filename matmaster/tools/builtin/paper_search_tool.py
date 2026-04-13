"""PaperSearchTool — literature search via mat_sn MCP (search-papers-enhanced).

Returns a slimmed ``{"data": [...]}`` payload. Each item keeps one logical title and
one abstract (prefer English ``enName`` / ``enAbstract``, else Chinese ``zhName`` /
``zhAbstract``) stored under ``enName`` / ``enAbstract`` for compatibility with
``collect_evidence`` and similar scripts. Also passes through ``publicationEnName``
(journal), ``impactFactor`` (JCR-style IF when available), and ``citationNums`` when present.

``pieces``（MCP 原始字段）：服务端拼好的「英文题名 + 摘要」整段字符串，与 ``enName``/``enAbstract`` 内容重复、体积大，**slim 不返回**。

Slim 输出**不**包含 ``paperUrl`` / ``paperId``：有 ``doi`` 即可用 ``https://doi.org/<doi>`` 稳定链到文献；
``collect_evidence._build_url`` 在缺 ``paperUrl`` 时会回退到 DOI。

MCP 原始 JSON 字段说明（便于维护白名单；未列出的嵌套对象默认不返回）：

**响应根**
  ``userId``, ``globalId``, ``data``, ``code``, ``message``

**data[] 每条文献（顶层）**
  ``doi``, ``paperId``, ``publicationId``, ``publicationEnName``, ``publicationCover``,
  ``enName``, ``zhName``, ``enAbstract``, ``zhAbstract``, ``authors``,
  ``coverDateStart``, ``languageType``, ``impactFactor``, ``impactScore``,
  ``citationNums``, ``popularity``, ``good``, ``goodFlag``, ``readFlag``, ``addFlag``,
  ``paperUrl``, ``graphicalAbstract``, ``openAccess``, ``pdfFlag``, ``title`` (slug),
  ``authorDetails``, ``score``, ``pieces``, ``alltext``

**authorDetails[]（作者嵌套；默认整段省略以省 token）**
  ``scholarId``, ``avatar``, ``name``, ``originName``, ``paperNums``, ``citationNums``,
  ``nameZh``, ``scholarOrgNameEn``, ``scholarOrgNameZh``, ``userExtId``, ``userId``,
  ``isHighCited``, ``hIndex``
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, ClassVar

from matmaster.tools.builtin.base import BuiltinTool
from matmaster.tools.lazy_mcp import resolve_lazy_mcp_tool_timeout
from matmaster.tools.tool_result import ToolResult
from matmaster.types.tool_spec import ResourceClaim, ToolExecutionContext
from matmaster.types.topology import ToolPlane

logger = logging.getLogger(__name__)

MAT_SN_SERVER = "mat_sn"
REMOTE_TOOL = "search-papers-enhanced"

# 输出保留的顶层键（不含 paperUrl/paperId，与 doi 重复；不含 zhName/zhAbstract/pieces）
_SLIM_EXTRA_KEYS = frozenset(
    {
        "doi",
        "authors",
        "coverDateStart",
        "title",
        "publicationEnName",
        "citationNums",
        "impactFactor",
    }
)

_ABSTRACT_MAX = 500


def _s(val: Any) -> str:
    return val.strip() if isinstance(val, str) else ""


def _truncate(text: str, max_chars: int) -> str:
    if not text:
        return ""
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(" ", 1)[0] + " [...]"


def _mcp_content_to_text(result_content: list[Any]) -> str:
    parts: list[str] = []
    for item in result_content:
        if hasattr(item, "text"):
            parts.append(item.text)
        elif isinstance(item, dict) and "text" in item:
            parts.append(item["text"])
        else:
            parts.append(str(item))
    if not parts:
        return ""
    if len(parts) == 1:
        text = parts[0].strip()
        if text.startswith("{") or text.startswith("["):
            try:
                parsed = json.loads(text)
                return json.dumps(parsed, ensure_ascii=False, default=str)
            except json.JSONDecodeError:
                return text
        return text
    return "\n".join(parts)


def _slim_paper_item(item: dict[str, Any]) -> dict[str, Any]:
    """Single title + single abstract: prefer EN, else ZH; emit as enName / enAbstract."""
    out: dict[str, Any] = {}
    title = _s(item.get("enName")) or _s(item.get("zhName"))
    if title:
        out["enName"] = title
    abstract = _s(item.get("enAbstract")) or _s(item.get("zhAbstract"))
    if abstract:
        out["enAbstract"] = _truncate(abstract, _ABSTRACT_MAX)
    for k in _SLIM_EXTRA_KEYS:
        if k not in item:
            continue
        v = item[k]
        if v in (None, "", []):
            continue
        out[k] = v
    return out


def _default_date_range() -> tuple[str, str]:
    from datetime import date

    return "2000-01-01", date.today().isoformat()


class PaperSearchTool(BuiltinTool):
    """Search academic papers via mat_sn ``search-papers-enhanced`` (MCP), slim output."""

    name: ClassVar[str] = "PaperSearch"
    description: ClassVar[str] = (
        "Search academic papers by keywords and a research question. "
        "Returns a compact list: one title and one abstract per item (English preferred, "
        "else Chinese), plus DOI (use https://doi.org/<doi> to open), date, authors, "
        "journal name (publicationEnName), impact factor (impactFactor), "
        "citation count (citationNums), optional slug title. "
        "Omits paperUrl/paperId when DOI is enough. Uses the configured mat_sn MCP server."
    )
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "words": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Core keywords (e.g. materials, methods).",
            },
            "question": {
                "type": "string",
                "description": "Natural-language research question or search intent.",
            },
            "start_time": {
                "type": "string",
                "description": "Start date YYYY-MM-DD (optional; defaults to 2000-01-01).",
            },
            "end_time": {
                "type": "string",
                "description": "End date YYYY-MM-DD (optional; defaults to today).",
            },
            "page_size": {
                "type": "integer",
                "description": "Max papers to return (1–100, default 20).",
            },
            "rerank": {
                "type": "integer",
                "description": "Optional AI rerank: 0 off, 1 on.",
            },
        },
        "required": ["words", "question"],
    }
    resource_claims: ClassVar[tuple[ResourceClaim, ...]] = (
        ResourceClaim(resource="web", mode="counted", max_concurrent=3),
    )
    capabilities: ClassVar[frozenset[str]] = frozenset({"literature.search"})
    effect_level: ClassVar[str] = "external_effect"
    plane: ClassVar[ToolPlane] = ToolPlane.EXTERNAL_SERVICE
    stop_mode: ClassVar[str] = "best_effort"

    def __init__(
        self,
        *,
        connector: Any,
        mcp_config: dict[str, Any] | None = None,
        timeout: float | None = None,
        session: Any | None = None,
        workdir: Any | None = None,
    ) -> None:
        super().__init__(session=session, workdir=workdir)
        self._connector = connector
        self._mcp_config = mcp_config or {}
        resolved = resolve_lazy_mcp_tool_timeout(
            self._mcp_config,
            server_name=MAT_SN_SERVER,
            remote_tool_name=REMOTE_TOOL,
        )
        if timeout is not None:
            self._timeout = float(timeout)
        elif resolved is not None:
            self._timeout = float(resolved)
        else:
            self._timeout = 120.0

    def _execute(self, arguments: dict[str, Any]) -> str | ToolResult:
        raise NotImplementedError("PaperSearchTool uses async execute()")

    def _build_mcp_arguments(self, arguments: dict[str, Any]) -> dict[str, Any]:
        words = arguments.get("words") or []
        if not isinstance(words, list) or not words:
            raise ValueError("words must be a non-empty array of strings")
        words = [str(w).strip() for w in words if str(w).strip()]
        if not words:
            raise ValueError("words must contain at least one non-empty keyword")

        question = (arguments.get("question") or "").strip()
        if len(question) < 2:
            raise ValueError("question must be at least 2 characters")

        start_d, end_d = _default_date_range()
        st = (arguments.get("start_time") or "").strip()
        et = (arguments.get("end_time") or "").strip()
        if st:
            start_d = st
        if et:
            end_d = et

        payload: dict[str, Any] = {
            "words": words,
            "question": question,
            "start_time": start_d,
            "end_time": end_d,
        }
        ps = arguments.get("page_size")
        if ps is not None:
            try:
                n = int(ps)
            except (TypeError, ValueError) as e:
                raise ValueError("page_size must be an integer") from e
            payload["page_size"] = max(1, min(100, n))
        else:
            payload["page_size"] = 20

        rr = arguments.get("rerank")
        if rr is not None:
            try:
                payload["rerank"] = 1 if int(rr) else 0
            except (TypeError, ValueError) as e:
                raise ValueError("rerank must be 0 or 1") from e

        return payload

    def _slim_payload(self, raw: dict[str, Any]) -> dict[str, Any]:
        data = raw.get("data")
        if not isinstance(data, list):
            return {"data": []}
        slim: list[dict[str, Any]] = []
        for item in data:
            if isinstance(item, dict):
                slim.append(_slim_paper_item(item))
        return {"data": slim}

    async def _run_call(self, arguments: dict[str, Any]) -> ToolResult:
        try:
            mcp_args = self._build_mcp_arguments(arguments)
        except ValueError as e:
            return ToolResult(status="error", content=f"Error: {e}")

        try:
            raw_content = await self._connector.call_tool(
                MAT_SN_SERVER,
                REMOTE_TOOL,
                mcp_args,
            )
            text = _mcp_content_to_text(raw_content)
        except RuntimeError as e:
            return ToolResult(status="error", content=str(e))
        except Exception as e:
            logger.warning("PaperSearch MCP call failed: %s", e, exc_info=True)
            return ToolResult(
                status="error",
                content=f"Error: {type(e).__name__}: {e}",
            )

        if not text.strip():
            return ToolResult(
                status="success",
                content=json.dumps({"data": []}, ensure_ascii=False),
            )

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return ToolResult(status="success", content=text)

        if not isinstance(parsed, dict):
            return ToolResult(status="success", content=text)

        slim = self._slim_payload(parsed)
        return ToolResult(
            status="success",
            content=json.dumps(slim, ensure_ascii=False),
        )

    async def execute(self, arguments: dict[str, Any]) -> str | ToolResult:
        try:
            return await asyncio.wait_for(
                self._run_call(arguments), timeout=self._timeout
            )
        except TimeoutError:
            return ToolResult(
                status="timeout",
                content=f"PaperSearch timed out after {self._timeout:g}s",
                meta={"layer": "tool"},
            )
        except Exception as e:
            self.logger.error("PaperSearch failed: %s", e, exc_info=True)
            return ToolResult(status="error", content=f"Error: {e}")

    async def execute_with_context(
        self,
        arguments: dict[str, Any],
        exec_ctx: ToolExecutionContext | None,
    ) -> str | ToolResult:
        cancel_token = getattr(exec_ctx, "cancel_token", None) if exec_ctx else None
        if cancel_token is not None and cancel_token.is_cancelled:
            return ToolResult(status="cancelled", content="Run cancelled.")

        call_coro = asyncio.wait_for(self._run_call(arguments), timeout=self._timeout)

        if cancel_token is None:
            try:
                return await call_coro
            except TimeoutError:
                return ToolResult(
                    status="timeout",
                    content=f"PaperSearch timed out after {self._timeout:g}s",
                    meta={"layer": "tool"},
                )

        call_task = asyncio.create_task(call_coro)
        stop_task = asyncio.create_task(cancel_token.wait_async())
        done, pending = await asyncio.wait(
            {call_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, TimeoutError):
                pass

        if call_task in done:
            try:
                return call_task.result()
            except TimeoutError:
                return ToolResult(
                    status="timeout",
                    content=f"PaperSearch timed out after {self._timeout:g}s",
                    meta={"layer": "tool"},
                )

        return ToolResult(
            status="cancelled",
            content=(
                "Cancellation requested (best-effort). "
                "Tool may have partially completed."
            ),
        )
