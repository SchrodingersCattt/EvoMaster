#!/usr/bin/env python3
"""Stdlib-only client for the public AIS Square (https://www.aissquare.com) registry.

Subcommands:
  list     <models|datasets> [--page-size N] [--limit N] [--sort downloads|modified]
  search   <keyword>          --type <models|datasets>
  info     <name>             --type <models|datasets> [--full | --grep PAT [--context N]]
  grep     <pattern>          --type <models|datasets> [--name-filter REGEX]
                              [--max-items N] [--max-excerpts N] [--context N] [--jobs N]
  download <name>             --type <models|datasets> --output <dir>

All output is a single JSON object on stdout; errors go to stderr with exit code 1.
No authentication is required for any endpoint.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

BASE_URL = "https://backend.aissquare.com"
USER_AGENT = "matmaster-aissq-explorer/0.1 (stdlib)"
DEFAULT_TIMEOUT = 30
DEFAULT_PAGE_SIZE = 300


def _ssl_ctx(insecure: bool) -> ssl.SSLContext | None:
    if not insecure:
        return None
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _http_get_json(
    url: str, *, insecure: bool, timeout: int = DEFAULT_TIMEOUT
) -> dict[str, Any]:
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(
                req, timeout=timeout, context=_ssl_ctx(insecure)
            ) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            if isinstance(payload, dict) and payload.get("code", 0) != 0:
                raise RuntimeError(
                    f"AIS Square API error code={payload.get('code')} msg={payload.get('message')}"
                )
            return payload
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last_exc = exc
            time.sleep(2**attempt)
    raise RuntimeError(f"GET {url} failed after 3 attempts: {last_exc}")


def _list_page(
    resource_type: str, page: int, page_size: int, *, insecure: bool
) -> dict[str, Any]:
    qs = urllib.parse.urlencode({"page": page, "pageSize": page_size})
    return _http_get_json(
        f"{BASE_URL}/content/{resource_type}?{qs}", insecure=insecure
    ).get("data", {})


def _list_all(
    resource_type: str, *, insecure: bool, page_size: int = DEFAULT_PAGE_SIZE
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    page = 1
    while True:
        data = _list_page(resource_type, page, page_size, insecure=insecure)
        batch = data.get("items") or []
        items.extend(batch)
        total = int(data.get("total") or 0)
        if not batch or len(items) >= total:
            break
        page += 1
    return items


def _detail(
    resource_id: int, resource_type: str, name: str, *, insecure: bool
) -> dict[str, Any]:
    qs = urllib.parse.urlencode({"id": resource_id, "name": name})
    data = _http_get_json(
        f"{BASE_URL}/dpa/detail/{resource_type}?{qs}", insecure=insecure
    ).get("data", {})
    for f in data.get("files", []) or []:
        link = f.get("downloadLink") or ""
        if link:
            f["download_host"] = urllib.parse.urlparse(link).netloc
    return data


def _stream_download(
    url: str, out_path: Path, *, insecure: bool, chunk: int = 8192
) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            with (
                urllib.request.urlopen(
                    req, timeout=DEFAULT_TIMEOUT * 4, context=_ssl_ctx(insecure)
                ) as resp,
                out_path.open("wb") as fh,
            ):
                total = 0
                while True:
                    buf = resp.read(chunk)
                    if not buf:
                        break
                    fh.write(buf)
                    total += len(buf)
            return total
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last_exc = exc
            time.sleep(2**attempt)
    raise RuntimeError(f"download {url} failed after 3 attempts: {last_exc}")


def _slim(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "ID": item.get("ID"),
        "name": item.get("name"),
        "type": item.get("type"),
        "downloadCount": item.get("downloadCount"),
        "viewCount": item.get("viewCount"),
        "modifyDate": item.get("modifyDate"),
        "prefix": item.get("prefix"),
    }


def cmd_list(args: argparse.Namespace) -> dict[str, Any]:
    items = _list_all(
        args.resource_type, insecure=args.insecure, page_size=args.page_size
    )
    if args.sort == "downloads":
        items.sort(key=lambda x: int(x.get("downloadCount") or 0), reverse=True)
    elif args.sort == "modified":
        items.sort(key=lambda x: str(x.get("modifyDate") or ""), reverse=True)
    if args.limit:
        items = items[: args.limit]
    return {
        "resource_type": args.resource_type,
        "count": len(items),
        "items": [_slim(x) for x in items],
    }


def cmd_search(args: argparse.Namespace) -> dict[str, Any]:
    needle = args.keyword.lower()
    matches = [
        x
        for x in _list_all(args.type, insecure=args.insecure)
        if needle in (x.get("name") or "").lower()
    ]
    return {
        "keyword": args.keyword,
        "resource_type": args.type,
        "count": len(matches),
        "items": [_slim(x) for x in matches],
    }


def _find_by_name(
    name: str, resource_type: str, *, insecure: bool
) -> dict[str, Any] | None:
    for item in _list_all(resource_type, insecure=insecure):
        if item.get("name") == name:
            return item
    return None


DEFAULT_INFO_SUMMARY_CHARS = 1200


def _grep_text(
    text: str,
    pattern: str,
    *,
    context: int = 1,
    max_excerpts: int = 10,
    ignore_case: bool = True,
) -> list[dict[str, Any]]:
    """Return up to max_excerpts {line, context} hits of *pattern* in *text*."""
    if not text:
        return []
    flag_mask = re.IGNORECASE if ignore_case else 0
    try:
        compiled = re.compile(pattern, flag_mask)
    except re.error as exc:
        return [{"error": f"invalid regex: {exc}"}]
    lines = text.split("\n")
    excerpts: list[dict[str, Any]] = []
    for i, line in enumerate(lines):
        if compiled.search(line):
            start = max(0, i - context)
            end = min(len(lines), i + context + 1)
            excerpts.append(
                {
                    "line": i + 1,
                    "context": "\n".join(lines[start:end]),
                }
            )
            if len(excerpts) >= max_excerpts:
                break
    return excerpts


def cmd_info(args: argparse.Namespace) -> dict[str, Any]:
    item = _find_by_name(args.name, args.type, insecure=args.insecure)
    if item is None:
        raise RuntimeError(f"resource {args.name!r} not found in {args.type}")
    detail = _detail(int(item["ID"]), args.type, args.name, insecure=args.insecure)
    full_desc = detail.get("description") or ""
    out: dict[str, Any] = {
        "ID": item["ID"],
        "name": item.get("name"),
        "type": args.type,
        "modifyDate": item.get("modifyDate"),
        "downloadCount": item.get("downloadCount"),
        "files": detail.get("files", []),
        "description_length": len(full_desc),
    }
    grep_pattern = getattr(args, "grep", None)
    if grep_pattern:
        out["description_grep_pattern"] = grep_pattern
        out["description_matches"] = _grep_text(
            full_desc,
            grep_pattern,
            context=getattr(args, "context", 1),
            max_excerpts=getattr(args, "max_excerpts", 10),
        )
        return out
    if getattr(args, "full", False):
        out["description"] = full_desc
        return out
    summary_chars = getattr(args, "summary_chars", DEFAULT_INFO_SUMMARY_CHARS)
    out["description_summary"] = full_desc[:summary_chars]
    out["description_truncated"] = len(full_desc) > summary_chars
    return out


def cmd_grep(args: argparse.Namespace) -> dict[str, Any]:
    """Cross-corpus regex search over registry descriptions."""
    items = _list_all(args.type, insecure=args.insecure)
    if args.name_filter:
        try:
            name_re = re.compile(args.name_filter, re.IGNORECASE)
        except re.error as exc:
            raise RuntimeError(f"invalid --name-filter regex: {exc}") from exc
        items = [it for it in items if name_re.search(it.get("name") or "")]
    if args.max_items and args.max_items > 0:
        items = items[: args.max_items]

    def _scan(item: dict[str, Any]) -> dict[str, Any] | None:
        try:
            detail = _detail(
                int(item["ID"]),
                args.type,
                item.get("name") or "",
                insecure=args.insecure,
            )
        except Exception as exc:
            return {
                "ID": item.get("ID"),
                "name": item.get("name"),
                "error": str(exc),
            }
        desc = detail.get("description") or ""
        excerpts = _grep_text(
            desc,
            args.pattern,
            context=args.context,
            max_excerpts=args.max_excerpts,
        )
        if not excerpts:
            return None
        return {
            "ID": item.get("ID"),
            "name": item.get("name"),
            "modifyDate": item.get("modifyDate"),
            "description_length": len(desc),
            "n_hits": len(excerpts),
            "excerpts": excerpts,
        }

    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(args.jobs, 1)) as ex:
        for res in ex.map(_scan, items):
            if res is None:
                continue
            if "error" in res:
                errors.append(res)
                continue
            results.append(res)
    return {
        "pattern": args.pattern,
        "type": args.type,
        "items_scanned": len(items),
        "n_matches": len(results),
        "n_errors": len(errors),
        "results": results,
        "errors": errors,
    }


def cmd_download(args: argparse.Namespace) -> dict[str, Any]:
    info = cmd_info(
        argparse.Namespace(name=args.name, type=args.type, insecure=args.insecure)
    )
    out_root = Path(args.output) / args.name
    saved: list[dict[str, Any]] = []
    for f in info.get("files", []):
        link = f.get("downloadLink")
        if not link:
            continue
        target = out_root / f.get("fileName", "asset.bin")
        n = _stream_download(link, target, insecure=args.insecure)
        saved.append(
            {
                "fileName": f.get("fileName"),
                "local_path": str(target.resolve()),
                "size_bytes": n,
                "expected_size_bytes": f.get("size"),
                "download_host": f.get("download_host")
                or urllib.parse.urlparse(link).netloc,
            }
        )
    return {
        "name": args.name,
        "type": args.type,
        "output_dir": str(out_root.resolve()),
        "files": saved,
    }


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="aissq_client",
        description="Stdlib client for the public AIS Square registry.",
    )
    p.add_argument(
        "--insecure",
        action="store_true",
        help="Disable SSL verification (last-resort fallback)",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    pl = sub.add_parser("list", help="List models or datasets")
    pl.add_argument("resource_type", choices=["models", "datasets"])
    pl.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE)
    pl.add_argument("--limit", type=int, default=None)
    pl.add_argument("--sort", choices=["downloads", "modified"], default=None)
    pl.set_defaults(func=cmd_list)

    ps = sub.add_parser(
        "search", help="Search by keyword (substring, case-insensitive)"
    )
    ps.add_argument("keyword")
    ps.add_argument("--type", required=True, choices=["models", "datasets"])
    ps.set_defaults(func=cmd_search)

    pi = sub.add_parser(
        "info",
        help="Get detail (files, downloadLink, size, description) for a resource",
    )
    pi.add_argument("name")
    pi.add_argument("--type", required=True, choices=["models", "datasets"])
    pi.add_argument(
        "--full",
        action="store_true",
        help="Return the full markdown description (may be many KB).",
    )
    pi.add_argument(
        "--grep",
        type=str,
        default=None,
        help=(
            "Regex to search inside the description; returns only matching "
            "context lines instead of the full text."
        ),
    )
    pi.add_argument(
        "--context",
        type=int,
        default=1,
        help="Number of context lines around each --grep match (default: 1)",
    )
    pi.add_argument(
        "--max-excerpts",
        type=int,
        default=10,
        help="Maximum number of --grep excerpts to return (default: 10)",
    )
    pi.add_argument(
        "--summary-chars",
        type=int,
        default=DEFAULT_INFO_SUMMARY_CHARS,
        help=(
            "Characters of description to include as description_summary when "
            "neither --full nor --grep is given (default: 1200)."
        ),
    )
    pi.set_defaults(func=cmd_info)

    pg = sub.add_parser(
        "grep",
        help=(
            "Regex-search descriptions across all resources of a type; returns "
            "[{name, excerpts}, ...]. Use before info/download when looking for "
            "a feature (e.g. 'fparam', 'charge') rather than a known name."
        ),
    )
    pg.add_argument("pattern", help="Regex pattern (case-insensitive)")
    pg.add_argument("--type", required=True, choices=["models", "datasets"])
    pg.add_argument(
        "--name-filter",
        type=str,
        default=None,
        help="Pre-filter items by name regex before fetching descriptions.",
    )
    pg.add_argument(
        "--max-items",
        type=int,
        default=0,
        help="Cap on items scanned (default: 0 = no cap).",
    )
    pg.add_argument(
        "--max-excerpts",
        type=int,
        default=5,
        help="Maximum excerpts per matching item (default: 5).",
    )
    pg.add_argument(
        "--context",
        type=int,
        default=1,
        help="Number of context lines around each match (default: 1).",
    )
    pg.add_argument(
        "--jobs",
        type=int,
        default=8,
        help="Parallel HTTP workers for description fetching (default: 8).",
    )
    pg.set_defaults(func=cmd_grep)

    pd = sub.add_parser(
        "download", help="Download all files of a resource into <output>/<name>/"
    )
    pd.add_argument("name")
    pd.add_argument("--type", required=True, choices=["models", "datasets"])
    pd.add_argument("--output", default="./downloads")
    pd.set_defaults(func=cmd_download)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        out = args.func(args)
    except Exception as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1
    json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
