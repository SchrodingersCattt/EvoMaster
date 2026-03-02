"""
Fetch a structure file from a direct URL, or extract structure file links from an HTML page.

Usage:
  python fetch_web_structure.py --url "http://example.com/file.cif"
  python fetch_web_structure.py --page "https://www.ccdc.cam.ac.uk/structures/search?identifier=AABHTZ"

Output: JSON to stdout.

--url  success: {"success": true, "file": "<path>", "source_url": "<url>"}
--page single match: same as --url success (auto-downloaded)
--page multiple matches: {"success": false, "reason": "multiple_candidates", "candidates": [{"href": "...", "text": "..."},...]}
--page no matches: {"success": false, "reason": "no_structure_links", "page_links_sample": [...top-20 links...]}
any:   {"success": false, "reason": "missing_dependency", "missing": "...", "install": "..."}
any:   {"success": false, "reason": "http_error", "status_code": <n>, "tried_url": "..."}
any:   {"success": false, "reason": "error", "message": "..."}
"""

import argparse
import json
import sys
from pathlib import Path

try:
    import requests
except ImportError:
    requests = None

STRUCTURE_EXTENSIONS = {".cif", ".poscar", ".vasp", ".xyz", ".res", ".pdb", ".mol2", ".sdf"}


def _project_tmp() -> Path:
    cwd = Path.cwd()
    for p in [cwd, cwd.parent, cwd.parent.parent]:
        t = p / "_tmp"
        if t.exists():
            return t
    d = cwd / "_tmp"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _check_requests() -> dict | None:
    if not requests:
        return {
            "success": False,
            "reason": "missing_dependency",
            "missing": "requests",
            "install": "pip install requests",
        }
    return None


def fetch_url(url: str, out_dir: Path) -> dict:
    """Download a direct structure file URL. Returns result dict."""
    dep_err = _check_requests()
    if dep_err:
        return dep_err
    try:
        r = requests.get(url, timeout=30, stream=True)
        if not r.ok:
            return {
                "success": False,
                "reason": "http_error",
                "status_code": r.status_code,
                "tried_url": url,
            }
        name = url.strip("/").split("/")[-1].split("?")[0] or "structure.cif"
        if "." not in name:
            name = "structure.cif"
        path = out_dir / name
        path.write_bytes(r.content)
        return {"success": True, "file": str(path), "source_url": url}
    except Exception as e:
        return {"success": False, "reason": "error", "message": str(e)}


def extract_page_links(page_url: str, out_dir: Path) -> dict:
    """Fetch an HTML page, extract structure file links, download if unambiguous."""
    dep_err = _check_requests()
    if dep_err:
        return dep_err

    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return {
            "success": False,
            "reason": "missing_dependency",
            "missing": "beautifulsoup4",
            "install": "pip install beautifulsoup4",
        }

    try:
        r = requests.get(page_url, timeout=30)
        if not r.ok:
            return {
                "success": False,
                "reason": "http_error",
                "status_code": r.status_code,
                "tried_url": page_url,
            }
    except Exception as e:
        return {"success": False, "reason": "error", "message": str(e)}

    soup = BeautifulSoup(r.text, "html.parser")

    # Collect all <a href> links
    all_links = []
    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        text = tag.get_text(strip=True)
        all_links.append({"href": href, "text": text})

    # Resolve relative URLs
    from urllib.parse import urljoin, urlparse
    base = page_url

    candidates = []
    for link in all_links:
        href = link["href"]
        abs_href = urljoin(base, href)
        path_part = urlparse(abs_href).path.lower()
        ext = Path(path_part).suffix
        if ext in STRUCTURE_EXTENSIONS:
            candidates.append({"href": abs_href, "text": link["text"]})

    if len(candidates) == 1:
        return fetch_url(candidates[0]["href"], out_dir)

    if len(candidates) > 1:
        return {
            "success": False,
            "reason": "multiple_candidates",
            "candidates": candidates,
        }

    # No structure links found — return a sample of all page links for LLM inspection
    sample = all_links[:20]
    return {
        "success": False,
        "reason": "no_structure_links",
        "page_links_sample": sample,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Fetch structure from direct URL or HTML page.")
    ap.add_argument("--url", help="Direct URL to a CIF/POSCAR/XYZ/etc. file")
    ap.add_argument("--page", help="URL of an HTML page to scan for structure file links")
    args = ap.parse_args()

    out_dir = _project_tmp()

    if args.url:
        result = fetch_url(args.url, out_dir)
    elif args.page:
        result = extract_page_links(args.page, out_dir)
    else:
        result = {"success": False, "reason": "error", "message": "Provide --url or --page"}
        print(json.dumps(result))
        sys.exit(1)

    print(json.dumps(result))
    if not result.get("success"):
        sys.exit(1)


if __name__ == "__main__":
    main()
