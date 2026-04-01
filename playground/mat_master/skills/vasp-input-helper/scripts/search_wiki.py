"""Search VASP wiki — human-like navigation: exact → category → fuzzy → fulltext.

Usage:
  python search_wiki.py --query "ISMEAR"
  python search_wiki.py --query "hybrid functionals" --max-results 3
"""

import argparse
import json
import re
import sys
from pathlib import Path

_WIKI_DIR = Path(__file__).resolve().parent.parent / "data" / "vasp_wiki" / "pages"
_KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "data" / "vasp_wiki" / "knowledge"

sys.path.insert(0, str(_KNOWLEDGE_DIR))
from wiki_index import build_index, fuzzy_title_matches, get_category_members, lookup_page


def read_page(fpath: Path, max_chars: int = 3000) -> tuple[str, str, str, list[str]]:
    content = fpath.read_text(encoding="utf-8", errors="ignore")
    lines = content.split("\n")
    title = lines[0].replace("Title: ", "").strip() if lines else fpath.stem
    url = lines[1].replace("URL: ", "").strip() if len(lines) > 1 else ""
    # Skip header lines: "Title: ...", "URL: ...", "====...="
    body_lines = lines[3:] if len(lines) > 3 else lines
    # Also skip leading separator line if present
    while body_lines and body_lines[0].startswith("===="):
        body_lines = body_lines[1:]
    body = "\n".join(body_lines).strip()
    if max_chars and len(body) > max_chars:
        body = body[:max_chars] + "\n... [truncated]"

    related: list[str] = []
    m = re.search(r"Related tags and articles\n(.+?)(?:\n(?:Examples|Retrieved|$))", content, re.DOTALL)
    if m:
        for item in re.split(r"[,\n]", m.group(1)):
            item = re.split(r"\s*[—–\-]\s", item.strip())[0].strip()
            if item and len(item) > 1 and not item.startswith("Retrieved"):
                related.append(item)
    return title, url, body, related


def main():
    parser = argparse.ArgumentParser(description="Search VASP wiki")
    parser.add_argument("--query", "-q", required=True)
    parser.add_argument("--max-results", "-n", type=int, default=5)
    args = parser.parse_args()

    query = args.query.strip()
    max_results = min(max(args.max_results, 1), 10)
    build_index()

    # Strategy 1: exact page
    page_path, _ = lookup_page(query)
    if page_path and page_path.exists():
        title, url, body, related = read_page(page_path, max_chars=2000)
        print(f"[EXACT MATCH] {title}")
        print(f"URL: {url}\n")
        print(body)
        if related:
            print(f"\nRelated: {', '.join(related[:15])}")
            for rel in related[:3]:
                rp, _ = lookup_page(rel)
                if rp and rp.exists():
                    rt, ru, rb, _ = read_page(rp, max_chars=500)
                    print(f"\n--- Related: {rt} ---\n{ru}\n{rb[:500]}")
        return

    # Strategy 2: category
    members = get_category_members(query)
    if not members:
        for v in [query.replace(" ", "-"), query.replace("-", " ")]:
            members = get_category_members(v)
            if members:
                break
    if members:
        cat_path, _ = lookup_page(f"Category:{query}")
        if not cat_path:
            cat_path, _ = lookup_page(f"category {query}")
        if cat_path and cat_path.exists():
            title, url, body, _ = read_page(cat_path, max_chars=2000)
            print(f"[CATEGORY] {title}")
            print(f"URL: {url}\n")
            print(body)
        print(f"\nPages in category ({len(members)}):")
        for m in members[:20]:
            print(f"  - {m}")
        if len(members) > 20:
            print(f"  ... and {len(members) - 20} more")
        return

    # Strategy 3: fuzzy title
    matches = fuzzy_title_matches(query, limit=max_results * 2)
    if matches:
        seen = set()
        count = 0
        print(f"[TITLE MATCH] {len(matches)} pages with '{query}' in title:\n")
        for t in matches:
            p, _ = lookup_page(t)
            if p and p not in seen and p.exists():
                seen.add(p)
                title, url, body, related = read_page(p, max_chars=1200)
                print(f"## {title}\nURL: {url}\n\n{body}")
                if related:
                    print(f"  Related: {', '.join(related[:8])}")
                print("\n---\n")
                count += 1
                if count >= max_results:
                    break
        return

    # Strategy 4: fulltext
    keywords = [kw.lower() for kw in re.split(r'\s+', query) if len(kw) >= 2]
    if not keywords:
        print(f"No results for '{query}'.")
        return

    scored = []
    for txt_file in _WIKI_DIR.glob("*.txt"):
        try:
            content = txt_file.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        content_lower = content.lower()
        fname = txt_file.stem.lower().replace("_", " ")
        score = 0.0
        for kw in keywords:
            if kw == fname:
                score += 20.0
            elif kw in fname:
                score += 5.0
            score += min(content_lower.count(kw), 30) * 0.2
        if score > 0:
            scored.append((score, txt_file))

    scored.sort(key=lambda x: -x[0])
    top = scored[:max_results]
    if not top:
        print(f"No results for '{query}'.")
        return

    print(f"[FULLTEXT] {len(scored)} matches for '{query}', showing top {len(top)}:\n")
    for rank, (score, fpath) in enumerate(top, 1):
        title, url, body, related = read_page(fpath, max_chars=1000)
        print(f"## [{rank}] {title}  (score: {score:.1f})")
        print(f"URL: {url}\n\n{body}")
        if related:
            print(f"  Related: {', '.join(related[:8])}")
        print("\n---\n")


if __name__ == "__main__":
    main()
