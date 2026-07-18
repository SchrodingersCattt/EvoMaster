"""Fetch and extract structured information from a MatMaster shared session.

Usage:
    python3 fetch_shared_session.py <share_url_or_session_id> [--mode summary|full|raw] [--event-types query,response,...] [--max-content-len N]

Modes:
    summary (default) - Key signals only: query, thought, response, run_result, failed tool_results
    full              - All events with per-item content truncation
    raw               - Raw JSON events, optionally filtered by --event-types

Output: JSON to stdout.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from typing import Any

SHARE_URL_PATTERNS = [
    # chat/share 为现行路径；chat-evo/share 为历史路径，对外帮助文档仍有存量链接
    re.compile(r"https?://[^/]+/matmaster/chat(?:-evo)?/share/([a-f0-9]+)"),
]

API_PATH_TEMPLATE = (
    "/bohrapi/v1/matmaster-evo/pubapi/v1/chat/sessions/{session_id}/stream"
)

DEFAULT_MAX_CONTENT_LEN = 2000
SUMMARY_EVENT_TYPES = {"query", "thought", "response", "run_result"}


def parse_share_url(url_or_id: str) -> tuple[str, str]:
    """Extract (base_url, session_id) from a share URL or bare session ID."""
    url_or_id = url_or_id.strip()
    for pattern in SHARE_URL_PATTERNS:
        m = pattern.search(url_or_id)
        if m:
            session_id = m.group(1)
            base_end = url_or_id.find("/matmaster/chat/share/")
            if base_end == -1:
                base_end = url_or_id.find("/matmaster/chat-evo/share/")
            base_url = url_or_id[:base_end]
            return base_url, session_id
    if re.fullmatch(r"[a-f0-9]{16,64}", url_or_id):
        return "https://matmaster.test.bohrium.com", url_or_id
    raise ValueError(f"Cannot parse session ID from: {url_or_id}")


def fetch_sse_events(
    base_url: str, session_id: str, timeout: int = 30, max_seconds: int = 120
) -> list[dict]:
    """POST to the share stream endpoint and parse SSE events.

    回放历史里录有每一轮 run 结尾的 stream_closed，且无法与 live 事件区分，
    因此任何 stream_closed 都不作为终止信号——否则多轮会话被截到第一轮。
    终止只依赖两件事：服务端关闭连接（idle 会话回放完即关，实测秒级返回）
    或 max_seconds 墙钟上限（active 会话跟随 live 流最多等到这里）。
    """
    api_url = base_url + API_PATH_TEMPLATE.format(session_id=session_id)
    req = urllib.request.Request(
        api_url,
        method="POST",
        headers={"Content-Type": "application/json"},
        data=b"",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        raise RuntimeError(f"HTTP {e.code} from {api_url}: {error_body[:500]}") from e

    events: list[dict] = []
    started = time.monotonic()
    try:
        for raw_line in resp:
            if time.monotonic() - started > max_seconds:
                break
            line = raw_line.decode("utf-8", errors="replace").rstrip("\n\r")
            if not line.startswith("data: "):
                continue
            raw = line[6:]
            try:
                ev = json.loads(raw)
            except json.JSONDecodeError:
                continue
            events.append(ev)
    except OSError:
        pass
    finally:
        resp.close()
    return events


def truncate_content(content: Any, max_len: int) -> Any:
    """Truncate string/dict content that exceeds max_len characters."""
    if isinstance(content, str):
        if len(content) > max_len:
            return content[:max_len] + f"\n[truncated, original: {len(content)} chars]"
        return content
    if isinstance(content, dict):
        serialized = json.dumps(content, ensure_ascii=False)
        if len(serialized) > max_len:
            result_field = content.get("result")
            if isinstance(result_field, str) and len(result_field) > max_len:
                content = {
                    **content,
                    "result": result_field[:max_len]
                    + f"\n[truncated, original: {len(result_field)} chars]",
                }
            elif len(serialized) > max_len:
                return json.loads(
                    serialized[:max_len] + '"'
                )  # noqa: this won't work cleanly
        return content
    return content


def _safe_truncate_dict(content: dict, max_len: int) -> dict:
    """Safely truncate large string fields within a dict."""
    out = {}
    for k, v in content.items():
        if isinstance(v, str) and len(v) > max_len:
            out[k] = v[:max_len] + f"\n[truncated, original: {len(v)} chars]"
        elif isinstance(v, dict):
            out[k] = _safe_truncate_dict(v, max_len)
        elif isinstance(v, list) and len(json.dumps(v, ensure_ascii=False)) > max_len:
            out[k] = f"[list with {len(v)} items, truncated]"
        else:
            out[k] = v
    return out


def format_summary(events: list[dict], max_content_len: int) -> dict:
    """Extract key information from events in summary mode."""
    summary_events: list[dict] = []
    tool_call_names: list[str] = []
    failed_tools: list[dict] = []
    stats = {
        "total_events": len(events),
        "user_queries": 0,
        "responses": 0,
        "tool_calls": 0,
        "tool_results": 0,
    }

    for ev in events:
        ev_type = ev.get("type", "")
        source = ev.get("source", "")

        if ev_type == "tool_call":
            stats["tool_calls"] += 1
            content = ev.get("content", {})
            name = content.get("name", "") if isinstance(content, dict) else ""
            tool_call_names.append(name)
            continue

        if ev_type == "tool_result":
            stats["tool_results"] += 1
            content = ev.get("content", {})
            if isinstance(content, dict):
                is_error = content.get("is_error") or content.get("isError")
                if is_error:
                    failed_tools.append(
                        {
                            "name": content.get("name", ""),
                            "error": _truncate_str(
                                content.get("result", ""), max_content_len
                            ),
                        }
                    )
            continue

        if ev_type in SUMMARY_EVENT_TYPES:
            if ev_type == "query":
                stats["user_queries"] += 1
            elif ev_type == "response":
                stats["responses"] += 1

            content = ev.get("content", "")
            if isinstance(content, dict):
                content = _safe_truncate_dict(content, max_content_len)
            elif isinstance(content, str):
                content = _truncate_str(content, max_content_len)

            summary_events.append(
                {
                    "source": source,
                    "type": ev_type,
                    "content": content,
                }
            )

    # Deduplicate consecutive tool names into a compact trace
    tool_trace = _compact_tool_trace(tool_call_names)

    return {
        "mode": "summary",
        "stats": stats,
        "tool_trace": tool_trace,
        "failed_tools": failed_tools,
        "events": summary_events,
    }


def format_full(events: list[dict], max_content_len: int) -> dict:
    """All events with content truncation."""
    formatted: list[dict] = []
    for ev in events:
        ev_type = ev.get("type", "")
        if ev_type in ("session_status", "stream_closed", "ping"):
            continue
        content = ev.get("content", "")
        if isinstance(content, dict):
            content = _safe_truncate_dict(content, max_content_len)
        elif isinstance(content, str):
            content = _truncate_str(content, max_content_len)
        formatted.append(
            {
                "source": ev.get("source", ""),
                "type": ev_type,
                "content": content,
            }
        )
    return {
        "mode": "full",
        "total_events": len(formatted),
        "events": formatted,
    }


def format_raw(
    events: list[dict], event_types: list[str] | None, max_content_len: int
) -> dict:
    """Raw events optionally filtered by type, with truncation."""
    if event_types:
        events = [e for e in events if e.get("type") in event_types]
    formatted: list[dict] = []
    for ev in events:
        content = ev.get("content", "")
        if isinstance(content, dict):
            content = _safe_truncate_dict(content, max_content_len)
        elif isinstance(content, str):
            content = _truncate_str(content, max_content_len)
        formatted.append(
            {
                "source": ev.get("source", ""),
                "type": ev.get("type", ""),
                "content": content,
            }
        )
    return {
        "mode": "raw",
        "filter": event_types,
        "total_events": len(formatted),
        "events": formatted,
    }


def _truncate_str(s: str, max_len: int) -> str:
    if len(s) <= max_len:
        return s
    return s[:max_len] + f"\n[truncated, original: {len(s)} chars]"


def _compact_tool_trace(names: list[str]) -> list[str]:
    """Compress ['Bohrium','Bohrium','Bohrium','Bash'] → ['Bohrium x3','Bash']."""
    if not names:
        return []
    trace: list[str] = []
    current = names[0]
    count = 1
    for name in names[1:]:
        if name == current:
            count += 1
        else:
            trace.append(f"{current} x{count}" if count > 1 else current)
            current = name
            count = 1
    trace.append(f"{current} x{count}" if count > 1 else current)
    return trace


def main():
    parser = argparse.ArgumentParser(
        description="Fetch and analyze a MatMaster shared session"
    )
    parser.add_argument("url", help="Share URL or session ID")
    parser.add_argument(
        "--mode",
        choices=["summary", "full", "raw"],
        default="summary",
        help="Output mode (default: summary)",
    )
    parser.add_argument(
        "--event-types",
        help="Comma-separated event types to include (raw mode only)",
    )
    parser.add_argument(
        "--max-content-len",
        type=int,
        default=DEFAULT_MAX_CONTENT_LEN,
        help=f"Max content length per event before truncation (default: {DEFAULT_MAX_CONTENT_LEN})",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="HTTP request timeout in seconds (default: 30)",
    )
    parser.add_argument(
        "--max-seconds",
        type=int,
        default=120,
        help="Wall-clock cap on stream reading (default: 120)",
    )
    args = parser.parse_args()

    try:
        base_url, session_id = parse_share_url(args.url)
    except ValueError as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)

    try:
        events = fetch_sse_events(
            base_url, session_id, timeout=args.timeout, max_seconds=args.max_seconds
        )
    except RuntimeError as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)

    if not events:
        print(json.dumps({"error": "No events returned (session may not be shared)"}))
        sys.exit(1)

    if args.mode == "summary":
        result = format_summary(events, args.max_content_len)
    elif args.mode == "full":
        result = format_full(events, args.max_content_len)
    else:
        event_types = (
            [t.strip() for t in args.event_types.split(",")]
            if args.event_types
            else None
        )
        result = format_raw(events, event_types, args.max_content_len)

    result["session_id"] = session_id
    result["source_url"] = f"{base_url}/matmaster/chat/share/{session_id}"
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
