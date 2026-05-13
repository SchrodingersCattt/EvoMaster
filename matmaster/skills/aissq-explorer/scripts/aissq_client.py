#!/usr/bin/env python3
"""Stdlib-only client for the public AIS Square (https://www.aissquare.com) registry.

Subcommands:
  list     <models|datasets> [--page-size N] [--limit N] [--sort downloads|modified]
  search   <keyword> --type <models|datasets>
  info     <name>    --type <models|datasets>
  download <name>    --type <models|datasets> --output <dir>

All output is a single JSON object on stdout; errors go to stderr with exit code 1.
No authentication is required for any endpoint.
"""

from __future__ import annotations

import argparse
import json
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


def cmd_info(args: argparse.Namespace) -> dict[str, Any]:
    item = _find_by_name(args.name, args.type, insecure=args.insecure)
    if item is None:
        raise RuntimeError(f"resource {args.name!r} not found in {args.type}")
    detail = _detail(int(item["ID"]), args.type, args.name, insecure=args.insecure)
    return {
        "ID": item["ID"],
        "name": item.get("name"),
        "type": args.type,
        "modifyDate": item.get("modifyDate"),
        "downloadCount": item.get("downloadCount"),
        "files": detail.get("files", []),
        "description_preview": (detail.get("description") or "")[:400],
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
        "info", help="Get detail (files, downloadLink, size) for a resource by name"
    )
    pi.add_argument("name")
    pi.add_argument("--type", required=True, choices=["models", "datasets"])
    pi.set_defaults(func=cmd_info)

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
