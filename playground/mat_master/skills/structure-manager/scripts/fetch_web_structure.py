"""
Fetch a structure file from a URL or search the web for a CIF/POSCAR and download.

Usage:
  python fetch_web_structure.py --url "http://example.com/file.cif"
  python fetch_web_structure.py --search "TATB crystal structure cif"

Output: Prints the path of the saved file or "Not Found".
"""

import argparse
import re
import sys
from pathlib import Path

try:
    import requests
except ImportError:
    requests = None


def _project_tmp() -> Path:
    cwd = Path.cwd()
    for p in [cwd, cwd.parent, cwd.parent.parent]:
        t = p / "_tmp"
        if t.exists() or t.mkdir(parents=True, exist_ok=True):
            return t
    (cwd / "_tmp").mkdir(parents=True, exist_ok=True)
    return cwd / "_tmp"


def fetch_url(url: str, out_dir: Path) -> str:
    """Download file from URL; save with extension from URL or .cif."""
    if not requests:
        return "Not Found (requests not installed)"
    try:
        r = requests.get(url, timeout=30, stream=True)
        r.raise_for_status()
        name = url.strip("/").split("/")[-1].split("?")[0] or "structure.cif"
        if "." not in name:
            name = "structure.cif"
        path = out_dir / name
        path.write_bytes(r.content)
        return str(path)
    except Exception as e:
        return f"Not Found ({e})"


def search_and_download(search_term: str, out_dir: Path) -> str:
    """
    Placeholder: real implementation would use a search API (e.g. SerpAPI, MCP browser).
    Returns instruction or 'Not Found' so agent can use MCP/web to find URL then call --url.
    """
    # No server-side search without API keys; suggest agent uses MCP or provides URL
    return (
        "Not Found (web search not implemented in script; use MCP browser or web search to find a CIF/POSCAR URL, "
        "then run this script with --url <direct_download_link>)"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Fetch structure from URL or search.")
    ap.add_argument("--url", help="Direct URL to CIF/POSCAR/XYZ file")
    ap.add_argument("--search", help="Search query to find structure (script may not have search API; use --url when possible)")
    args = ap.parse_args()
    out_dir = _project_tmp()
    if args.url:
        result = fetch_url(args.url, out_dir)
    elif args.search:
        result = search_and_download(args.search, out_dir)
    else:
        print("Not Found (provide --url or --search)", file=sys.stderr)
        result = "Not Found (provide --url or --search)"
    print(result)


if __name__ == "__main__":
    main()
