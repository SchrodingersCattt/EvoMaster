"""
Smart manual reader for input-manual-helper skill.

Instead of dumping the entire raw JSON (which can be 4 MB), this script
provides structured, LLM-friendly views of the parameter manuals.

Modes (combinable)
------------------
--tree              Show section hierarchy with param counts.
--section SEC       Show params for a specific section.
--sections S1,S2    Batch-query multiple sections in ONE call (comma-separated).
--search KW         Search param names/descriptions for a keyword.
(no flag)           Auto: if <=80 params show table; else show tree.

Combos
------
  --section SEC --tree       Show the *subtree* of section SEC (not the full tree).
  --section SEC --search KW  Search only within section SEC.
  --sections S1,S2,S3        Show params for each section in one call.

Usage
-----
  python peek_manual.py --software VASP --tree
  python peek_manual.py --software CP2K --section "FORCE_EVAL/DFT/SCF"
  python peek_manual.py --software CP2K --section "FORCE_EVAL/DFT" --tree
  python peek_manual.py --software CP2K --sections "FORCE_EVAL/DFT/SCF,FORCE_EVAL/DFT/KPOINTS,FORCE_EVAL/DFT/XC"
  python peek_manual.py --software VASP --search ENCUT
  python peek_manual.py --software VASP          # auto: compact table (<=80 params)
  python peek_manual.py --software CP2K           # auto: tree (>80 params)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Known filename -> software label (mirrors list_manuals.py)
_LABEL_TO_FILE: dict[str, str] = {
    "ABACUS": "abacus_parameters.json",
    "ABINIT": "abinit_parameters.json",
    "ASE": "ase_parameters.json",
    "CP2K": "cp2k_parameters.json",
    "DEEPMD-KIT": "deepmd_parameters.json",
    "DEEPMD": "deepmd_parameters.json",
    "DP-GEN": "dpgen_parameters.json",
    "DPGEN": "dpgen_parameters.json",
    "DPGEN2": "dpgen2_parameters.json",
    "GAUSSIAN": "gaussian_parameters.json",
    "LAMMPS": "lammps_commands_sample.json",
    "ORCA": "orca_parameters.json",
    "PLUMED": "plumed_parameters.json",
    "PSI4": "psi4_parameters.json",
    "PYATB": "pyatb_parameters.json",
    "PYMATGEN": "pymatgen_parameters.json",
    "PYSCF": "pyscf_manual.json",
    "QE": "quantum_espresso_parameters.json",
    "QUANTUM ESPRESSO": "quantum_espresso_parameters.json",
    "VASP": "vasp_parameters.json",
}


def _resolve_data_dir() -> Path:
    """Return the data/ directory bundled with this skill."""
    return Path(__file__).resolve().parent.parent / "data"


def _find_manual_path(software: str, data_dir: Path | None = None) -> Path:
    """Locate the manual JSON for *software*."""
    if data_dir is None:
        data_dir = _resolve_data_dir()
    key = software.upper().replace("_", "-")
    fname = _LABEL_TO_FILE.get(key)
    if fname:
        p = data_dir / fname
        if p.exists():
            return p
    # Fallback: glob for *software*_parameters.json
    for p in data_dir.glob("*.json"):
        if software.lower().replace(" ", "_") in p.stem.lower():
            return p
    raise FileNotFoundError(
        f"No manual found for '{software}'. Available: {', '.join(sorted(_LABEL_TO_FILE.keys()))}"
    )


def _load_params(path: Path) -> tuple[str, list[dict[str, Any]]]:
    """Load params list. Returns (software_label, params_list)."""
    with open(path, "rb") as f:
        data = json.loads(f.read().decode("utf-8"))
    if isinstance(data, dict) and "parameters" in data:
        sw = data.get("software", path.stem)
        return sw, data["parameters"]
    if isinstance(data, list):
        sw = data[0].get("software", path.stem) if data else path.stem
        return sw, data
    return path.stem, []


def _is_path_like(section: str) -> bool:
    """Heuristic: is the parent_section a hierarchical path (like CP2K) or free text (like QE)?"""
    if not section:
        return False
    # Path-like: short, may contain '/', no spaces in individual parts,
    # and total length < 200.
    if len(section) > 200:
        return False
    # Paths typically look like FORCE_EVAL/DFT/SCF or just "INCAR"
    parts = section.split("/")
    return all(len(p.strip()) < 60 and " " not in p.strip() for p in parts)


def _truncate(text: str, maxlen: int = 60) -> str:
    """Truncate text and add '...' if needed."""
    text = text.replace("\n", " ").strip()
    if len(text) <= maxlen:
        return text
    return text[: maxlen - 3] + "..."


def _short_default(raw: str | None) -> str:
    """Clean up default value for display."""
    if raw is None:
        return "none"
    # Strip trailing junk like 'ï' from some manuals
    cleaned = raw.strip().rstrip("ï").strip()
    # If it looks like "TAG = value", extract just value
    if "=" in cleaned:
        _, _, val = cleaned.partition("=")
        val = val.strip()
        return val if val else "none"
    return cleaned if cleaned else "none"


# ---------------------------------------------------------------------------
# Section tree building
# ---------------------------------------------------------------------------

class _SectionNode:
    """A node in the section hierarchy."""

    __slots__ = ("name", "children", "param_count", "param_names")

    def __init__(self, name: str):
        self.name = name
        self.children: dict[str, _SectionNode] = {}
        self.param_count: int = 0
        self.param_names: list[str] = []

    def get_or_create(self, part: str) -> _SectionNode:
        if part not in self.children:
            self.children[part] = _SectionNode(part)
        return self.children[part]


def _build_tree(params: list[dict]) -> tuple[_SectionNode, bool]:
    """Build section tree from parent_section fields.

    Returns (root_node, is_path_tree).
    is_path_tree is False when parent_sections are free text (like QE).
    """
    root = _SectionNode("ROOT")
    path_like_count = 0
    total = 0

    for p in params:
        ps = p.get("parent_section") or ""
        name = p.get("name", "?")
        total += 1
        if _is_path_like(ps):
            path_like_count += 1
            parts = [x.strip() for x in ps.split("/") if x.strip()]
            node = root
            for part in parts:
                node = node.get_or_create(part)
            node.param_count += 1
            node.param_names.append(name)
        else:
            # Group under a derived label
            label = ps[:50].strip() if ps else "(no section)"
            # Clean up label – take first recognizable word(s)
            label = re.sub(r"\s+", " ", label)
            node = root.get_or_create(label)
            node.param_count += 1
            node.param_names.append(name)

    is_path_tree = total > 0 and (path_like_count / total) > 0.5
    return root, is_path_tree


def _print_tree(node: _SectionNode, indent: int = 0, max_depth: int = 6) -> list[str]:
    """Render the tree as indented text lines."""
    lines: list[str] = []
    if indent > max_depth * 2:
        return lines
    for cname in sorted(node.children.keys()):
        child = node.children[cname]
        total = _subtree_count(child)
        own = child.param_count
        prefix = "  " * indent
        label = cname + "/"
        if child.children:
            lines.append(f"{prefix}{label} ({total} params, {own} own)")
            lines.extend(_print_tree(child, indent + 1, max_depth))
        else:
            lines.append(f"{prefix}{label} ({own} params)")
    return lines


def _subtree_count(node: _SectionNode) -> int:
    """Total params in subtree."""
    total = node.param_count
    for ch in node.children.values():
        total += _subtree_count(ch)
    return total


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------

def _compact_table(params: list[dict], section_label: str | None = None) -> str:
    """Render params as a compact table (name, dtype, default, description)."""
    lines: list[str] = []

    # Determine max name length for alignment
    max_name = max((len(p.get("name", "?")) for p in params), default=10)
    max_name = min(max_name, 30)  # Cap

    for p in params:
        name = p.get("name", "?")
        args = p.get("arguments", [])
        dtype = args[0].get("dtype", "?") if args else "?"
        default_raw = args[0].get("default_value") if args else None
        default = _short_default(default_raw)
        desc = _truncate(p.get("description", ""), 60)

        lines.append(
            f"  {name:<{max_name}s}  [{dtype:<7s}]  default={default:<16s}  -- {desc}"
        )

    header = ""
    if section_label:
        paradigm = params[0].get("paradigm", "?") if params else "?"
        header = f"Section: {section_label} ({len(params)} params), Paradigm: {paradigm}\n\n"

    return header + "\n".join(lines)


def _group_label_for_text_section(text: str) -> str:
    """Derive a short human-readable label from a long parent_section text."""
    if not text:
        return "(ungrouped)"
    # Take the first 'word' that looks like a variable name
    m = re.match(r"([A-Za-z_]\w*)", text)
    if m:
        return m.group(1)
    return text[:30] + "..."


# ---------------------------------------------------------------------------
# Main commands
# ---------------------------------------------------------------------------

def cmd_tree(software: str, params: list[dict]) -> None:
    """Print the section hierarchy with param counts."""
    root, is_path_tree = _build_tree(params)
    total = len(params)
    section_count = len(root.children)

    print(f"{software} Parameter Manual ({total} params, {section_count} top-level sections)\n")

    if is_path_tree:
        lines = _print_tree(root)
        print("\n".join(lines))
    else:
        # Non-path sections (e.g. QE): show groups with derived labels
        for cname in sorted(root.children.keys()):
            child = root.children[cname]
            label = _group_label_for_text_section(cname)
            sample_names = child.param_names[:5]
            sample_str = ", ".join(sample_names)
            if len(child.param_names) > 5:
                sample_str += ", ..."
            print(f"  {label} ({child.param_count} params): {sample_str}")

    print(f"\nUse --section \"<SECTION>\" to view params in a specific section.")
    print(f"Use --search \"<KEYWORD>\" to find params by name or description.")


def _suggest_sections(params: list[dict], query: str, top_n: int = 5) -> list[str]:
    """Return the closest section names matching *query* (case-insensitive)."""
    all_sections: set[str] = set()
    for p in params:
        ps = (p.get("parent_section") or "").strip().strip("/")
        if ps:
            all_sections.add(ps)
            # Also add all ancestor paths
            parts = ps.split("/")
            for i in range(1, len(parts)):
                all_sections.add("/".join(parts[:i]))

    query_lower = query.lower()
    scored: list[tuple[float, str]] = []
    for s in all_sections:
        sl = s.lower()
        # Exact substring match gets high priority
        if query_lower in sl:
            scored.append((0.0, s))
        elif query_lower.split("/")[-1] in sl:
            scored.append((0.5, s))
        else:
            # Simple char overlap ratio
            overlap = sum(1 for c in query_lower if c in sl) / max(len(query_lower), 1)
            if overlap > 0.3:
                scored.append((1.0 - overlap, s))

    scored.sort(key=lambda x: (x[0], len(x[1])))
    return [s for _, s in scored[:top_n]]


def cmd_section(
    software: str,
    params: list[dict],
    section: str,
    *,
    show_subtree: bool = False,
    search_within: str | None = None,
) -> None:
    """Print params for a specific section in compact table format.

    If *show_subtree* is True (``--section SEC --tree``), print the subtree
    of that section instead of the full manual tree.

    If *search_within* is provided (``--section SEC --search KW``), only
    show params in the section that match the keyword.
    """
    # For path-like sections, match parent_section exactly or as prefix
    matched: list[dict] = []
    child_sections: set[str] = set()

    section_normalized = section.strip().strip("/")

    for p in params:
        ps = (p.get("parent_section") or "").strip().strip("/")
        if not _is_path_like(ps) and not _is_path_like(section_normalized):
            # For text-based sections (QE), match by first keyword
            label = _group_label_for_text_section(ps)
            if label.lower() == section_normalized.lower():
                matched.append(p)
            continue

        if ps == section_normalized:
            matched.append(p)
        elif ps.startswith(section_normalized + "/"):
            # This is a child section – also count as "matched" if show_subtree
            if show_subtree:
                matched.append(p)
            remainder = ps[len(section_normalized) + 1:]
            child_name = remainder.split("/")[0]
            child_sections.add(child_name)

    if not matched and not child_sections:
        # Try case-insensitive / partial match
        for p in params:
            ps = (p.get("parent_section") or "").strip().strip("/")
            if section_normalized.lower() in ps.lower():
                matched.append(p)
        if not matched:
            print(f"No params found for section '{section}' in {software}.")
            suggestions = _suggest_sections(params, section)
            if suggestions:
                print(f"Did you mean one of: {', '.join(suggestions)} ?")
            print("Hint: use --tree to see available sections, or --search to find params by keyword.")
            print("IMPORTANT: Do NOT retry this exact section path. Try the suggestions above or proceed with what you have.")
            return

    # If searching within section, filter now
    if search_within:
        kw = search_within.lower()
        matched = [
            p for p in matched
            if kw in (p.get("name") or "").lower()
            or kw in (p.get("description") or "").lower()
        ]
        if not matched:
            print(f"No params matching '{search_within}' in section '{section_normalized}' of {software}.")
            print(f"IMPORTANT: Do NOT repeat this search. It may be a subsection name, not a parameter.")
            print(f"Proceed to write the input file using your domain knowledge for this feature.")
            return

    # -- subtree mode: show the tree rooted at the given section ----------
    if show_subtree and child_sections:
        subtree_params = [
            p for p in params
            if (p.get("parent_section") or "").strip().strip("/").startswith(section_normalized)
        ]
        root, is_path = _build_tree(subtree_params)
        total = len(subtree_params)
        print(f"{software} - Subtree of {section_normalized} ({total} params)\n")
        # Navigate to the requested node
        node = root
        for part in section_normalized.split("/"):
            if part in node.children:
                node = node.children[part]
            else:
                break
        lines = _print_tree(node)
        print("\n".join(lines))
        # Still show the direct params if there are any own params
        own_params = [
            p for p in params
            if (p.get("parent_section") or "").strip().strip("/") == section_normalized
        ]
        if own_params:
            print(f"\nDirect params in {section_normalized} ({len(own_params)}):")
            print(_compact_table(own_params, section_normalized))
        return

    # -- normal mode: show direct params + child summary ------------------
    print(f"{software} - Section: {section_normalized}")
    if matched:
        print(_compact_table(matched, section_normalized))
    else:
        print(f"  (no direct params in this section)")

    if child_sections:
        child_summaries = []
        for cs in sorted(child_sections):
            full = f"{section_normalized}/{cs}"
            cnt = sum(
                1 for p in params
                if (p.get("parent_section") or "").strip().strip("/").startswith(full)
            )
            child_summaries.append(f"{cs}/ ({cnt})")
        print(f"\nChild sections: {', '.join(child_summaries)}")
        print(f"  Use --section \"{section_normalized}/<CHILD>\" to drill into a child section.")


def cmd_search(software: str, params: list[dict], keyword: str) -> None:
    """Search params by name or description."""
    kw_lower = keyword.lower()
    hits: list[dict] = []
    for p in params:
        name = p.get("name", "")
        desc = p.get("description", "")
        if kw_lower in name.lower() or kw_lower in desc.lower():
            hits.append(p)

    if not hits:
        print(f"No params matching '{keyword}' found in {software}.")
        print(f"IMPORTANT: Do NOT repeat this same search. The parameter '{keyword}' is not in the manual.")
        print(f"If you need this feature, use your domain knowledge to write the correct syntax directly.")
        return

    print(f"{software} - Search results for '{keyword}' ({len(hits)} hits):\n")

    max_name = min(max((len(p.get("name", "?")) for p in hits), default=10), 30)
    for p in hits:
        name = p.get("name", "?")
        ps = p.get("parent_section", "")
        section_label = ps if _is_path_like(ps) else _group_label_for_text_section(ps)
        args = p.get("arguments", [])
        dtype = args[0].get("dtype", "?") if args else "?"
        default_raw = args[0].get("default_value") if args else None
        default = _short_default(default_raw)
        desc = _truncate(p.get("description", ""), 55)
        print(f"  {name:<{max_name}s}  [{dtype:<7s}]  section={section_label:<30s}  default={default}")
        print(f"  {'':>{max_name}s}   {desc}")
        print()


def cmd_auto(software: str, params: list[dict]) -> None:
    """Auto mode: if <=80 params show compact table, else show tree."""
    if len(params) <= 80:
        paradigm = params[0].get("paradigm", "?") if params else "?"
        section = params[0].get("parent_section", "") if params else ""
        section_label = section if _is_path_like(section) else software
        print(f"{software} Parameters ({len(params)} params), Paradigm: {paradigm}\n")
        print(_compact_table(params))
        print(f"\nUse --search \"<KEYWORD>\" to find specific params.")
    else:
        cmd_tree(software, params)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_sections_batch(software: str, params: list[dict], sections_csv: str) -> None:
    """Batch-query multiple sections in ONE call (comma-separated)."""
    sections = [s.strip() for s in sections_csv.split(",") if s.strip()]
    if not sections:
        print("Error: --sections requires comma-separated section names.")
        return
    print(f"{software} - Batch query ({len(sections)} sections)\n")
    print("=" * 72)
    for i, sec in enumerate(sections):
        if i > 0:
            print("\n" + "-" * 72)
        cmd_section(software, params, sec)
    print("=" * 72)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Smart manual reader for input-manual-helper skill."
    )
    parser.add_argument(
        "--software", required=True,
        help="Software name (e.g. VASP, CP2K, LAMMPS, QE, DeePMD-kit)."
    )
    parser.add_argument(
        "--tree", action="store_true",
        help="Show the section hierarchy with param counts."
    )
    parser.add_argument(
        "--section",
        help="Show params for a specific section (e.g. 'FORCE_EVAL/DFT/SCF')."
    )
    parser.add_argument(
        "--sections",
        help="Batch-query multiple sections in ONE call, comma-separated "
             "(e.g. 'FORCE_EVAL/DFT/SCF,FORCE_EVAL/DFT/XC,FORCE_EVAL/SUBSYS')."
    )
    parser.add_argument(
        "--search",
        help="Search param names and descriptions for a keyword."
    )
    parser.add_argument(
        "--data-dir",
        help="Override data directory path."
    )

    args = parser.parse_args()

    data_dir = Path(args.data_dir) if args.data_dir else None
    try:
        manual_path = _find_manual_path(args.software, data_dir)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    sw, params = _load_params(manual_path)
    if not params:
        print(f"Manual for {args.software} is empty or could not be loaded.")
        sys.exit(1)

    # ---- Handle flag combos correctly ----
    # Priority: --sections (batch) > --section (combos) > --search > --tree > auto
    # Note: --search takes priority over --tree when no --section is given,
    # so that `--search KW --tree` does NOT dump the full tree.
    if args.sections:
        cmd_sections_batch(sw, params, args.sections)
    elif args.section:
        cmd_section(
            sw, params, args.section,
            show_subtree=args.tree,
            search_within=args.search,
        )
    elif args.search:
        cmd_search(sw, params, args.search)
    elif args.tree:
        cmd_tree(sw, params)
    else:
        cmd_auto(sw, params)


if __name__ == "__main__":
    main()
