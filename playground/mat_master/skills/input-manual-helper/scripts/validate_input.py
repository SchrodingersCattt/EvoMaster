"""
Validate a written input file against its software manual.

Parses the input file, loads the manual JSON, and cross-validates
parameter names, section placement, and value types.

Supported paradigms
-------------------
- KEY_VALUE   (VASP INCAR, ABACUS INPUT, etc.)
- HIERARCHICAL_BLOCK  (CP2K)
- KEYWORD_LINE  (LAMMPS)
- JSON  (DeePMD-kit, DP-GEN, DPGEN2)
- NAMELIST  (Quantum Espresso)
- GAUSSIAN_ROUTE  (Gaussian .com/.gjf — validates route section keywords only)

Usage
-----
  python validate_input.py --input_file /path/to/INCAR --software VASP
  python validate_input.py --input_file /path/to/cp2k.inp --software CP2K
  python validate_input.py --input_file /path/to/input.json --software DeePMD-kit
  python validate_input.py --input_file /path/to/pw.in --software QE
  python validate_input.py --input_file /path/to/in.lammps --software LAMMPS
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Manual loading (shared with peek_manual.py)
# ---------------------------------------------------------------------------

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
    return Path(__file__).resolve().parent.parent / "data"


def _find_manual_path(software: str, data_dir: Path | None = None) -> Path:
    if data_dir is None:
        data_dir = _resolve_data_dir()
    key = software.upper().replace("_", "-")
    fname = _LABEL_TO_FILE.get(key)
    if fname:
        p = data_dir / fname
        if p.exists():
            return p
    for p in data_dir.glob("*.json"):
        if software.lower().replace(" ", "_") in p.stem.lower():
            return p
    raise FileNotFoundError(
        f"No manual found for '{software}'. Available: {', '.join(sorted(_LABEL_TO_FILE.keys()))}"
    )


def _load_params(path: Path) -> tuple[str, list[dict[str, Any]], dict]:
    """Load manual parameters and any extra data (methods, basis_sets, etc.).

    Returns:
        (software_name, parameters_list, extra_data_dict)
    """
    with open(path, "rb") as f:
        data = json.loads(f.read().decode("utf-8"))
    if isinstance(data, dict) and "parameters" in data:
        extra = {k: v for k, v in data.items() if k not in ("software", "parameters")}
        return data.get("software", path.stem), data["parameters"], extra
    if isinstance(data, list):
        sw = data[0].get("software", path.stem) if data else path.stem
        return sw, data, {}
    return path.stem, [], {}


# ---------------------------------------------------------------------------
# Levenshtein distance for "did you mean?" suggestions
# ---------------------------------------------------------------------------

def _levenshtein(a: str, b: str) -> int:
    """Compute Levenshtein edit distance between two strings."""
    if len(a) < len(b):
        return _levenshtein(b, a)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            cost = 0 if ca == cb else 1
            curr.append(min(curr[j] + 1, prev[j + 1] + 1, prev[j] + cost))
        prev = curr
    return prev[-1]


def _suggest(name: str, known_names: list[str], max_dist: int = 3) -> str | None:
    """Find the closest known name within max_dist edits, or by prefix/substring match."""
    best_name = None
    best_dist = max_dist + 1
    name_upper = name.upper()

    # 1. Try Levenshtein distance
    for kn in known_names:
        d = _levenshtein(name_upper, kn.upper())
        if d < best_dist:
            best_dist = d
            best_name = kn
    if best_name and best_dist <= max_dist:
        return best_name

    # 2. Fallback: prefix match (input is prefix of a known name, or vice versa)
    prefix_hits: list[str] = []
    for kn in known_names:
        kn_upper = kn.upper()
        if kn_upper.startswith(name_upper) or name_upper.startswith(kn_upper):
            prefix_hits.append(kn)
    if prefix_hits:
        # Return shortest match (most specific)
        prefix_hits.sort(key=len)
        return prefix_hits[0]

    # 3. Fallback: substring match
    for kn in known_names:
        if name_upper in kn.upper():
            return kn

    return None


# ---------------------------------------------------------------------------
# Input file parsers
# ---------------------------------------------------------------------------

class ParsedTag:
    """A single tag/keyword found in the input file."""
    __slots__ = ("name", "value", "line_num", "section_path")

    def __init__(self, name: str, value: str, line_num: int, section_path: str = ""):
        self.name = name
        self.value = value
        self.line_num = line_num
        self.section_path = section_path

    def __repr__(self) -> str:
        return f"ParsedTag({self.name!r}, line={self.line_num}, section={self.section_path!r})"


def parse_key_value(text: str) -> list[ParsedTag]:
    """Parse KEY = VALUE format (VASP INCAR, ABACUS INPUT, etc.).

    Handles:
      TAG = VALUE
      TAG VALUE
      TAG=VALUE
    Skips comment lines (# or !).
    """
    tags: list[ParsedTag] = []
    for i, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("!"):
            continue
        # Handle inline comments
        for comment_char in ("#", "!"):
            if comment_char in stripped:
                stripped = stripped[:stripped.index(comment_char)].strip()
        # Try TAG = VALUE
        m = re.match(r"([A-Za-z_]\w*)\s*=\s*(.*)", stripped)
        if m:
            tags.append(ParsedTag(m.group(1).strip(), m.group(2).strip(), i))
            continue
        # Try TAG VALUE (space-separated, first token is tag)
        parts = stripped.split(None, 1)
        if parts and re.match(r"[A-Za-z_]\w*$", parts[0]):
            val = parts[1] if len(parts) > 1 else ""
            tags.append(ParsedTag(parts[0], val, i))
    return tags


def parse_cp2k(text: str) -> list[ParsedTag]:
    """Parse CP2K hierarchical block format.

    &SECTION_NAME
      KEYWORD VALUE
      &SUBSECTION
        ...
      &END SUBSECTION
    &END SECTION_NAME
    """
    # Sections whose body contains raw data (coordinates, velocities) rather
    # than keyword-value pairs.  Lines inside these must be skipped.
    _DATA_SECTIONS = frozenset({
        "COORD", "SHELL_COORD", "CORE_COORD",
        "VELOCITY", "SHELL_VELOCITY", "CORE_VELOCITY",
    })

    tags: list[ParsedTag] = []
    section_stack: list[str] = []
    # Track depth inside a data-only section (0 = not inside one)
    data_section_depth: int = 0

    for i, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("!"):
            continue

        # Section start: &SECTION [params]
        m = re.match(r"&(\w+)\s*(.*)", stripped)
        if m and not stripped.upper().startswith("&END"):
            sec_name = m.group(1).upper()
            section_stack.append(sec_name)
            if data_section_depth > 0:
                data_section_depth += 1        # nested section inside data section
            elif sec_name in _DATA_SECTIONS:
                data_section_depth = 1          # entered a data section
            continue

        # Section end: &END [SECTION]
        if stripped.upper().startswith("&END"):
            if section_stack:
                section_stack.pop()
            if data_section_depth > 0:
                data_section_depth -= 1
            continue

        # If inside a data section, skip all content lines
        if data_section_depth > 0:
            continue

        # Keyword: KEYWORD VALUE (or KEYWORD = VALUE)
        m = re.match(r"([A-Za-z_]\w*)\s*=?\s*(.*)", stripped)
        if m:
            section_path = "/".join(section_stack)
            tags.append(ParsedTag(m.group(1).strip().upper(), m.group(2).strip(), i, section_path))

    return tags


def parse_namelist(text: str) -> list[ParsedTag]:
    """Parse Fortran namelist format (Quantum Espresso).

    &CONTROL
      calculation = 'scf',
      prefix = 'si',
    /
    ATOMIC_SPECIES
      Si 28.086 Si.pbe-n-rrkjus_psl.1.0.0.UPF
    """
    tags: list[ParsedTag] = []
    current_namelist = ""

    for i, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("!"):
            continue

        # Namelist start
        m = re.match(r"&(\w+)", stripped)
        if m:
            current_namelist = m.group(1).upper()
            continue

        # Namelist end
        if stripped == "/" or stripped == "&END":
            current_namelist = ""
            continue

        # Card header (e.g. ATOMIC_SPECIES, ATOMIC_POSITIONS { ... })
        m = re.match(r"([A-Z_]+)\s*(.*)", stripped)
        if m and not current_namelist and re.match(r"[A-Z_]{3,}$", m.group(1)):
            current_namelist = m.group(1)
            continue

        # Inside a namelist: key = value
        if current_namelist:
            # Handle comma-separated assignments on one line
            assignments = re.findall(r"([A-Za-z_]\w*(?:\([^)]*\))?)\s*=\s*([^,=]+)", stripped)
            for key, val in assignments:
                tags.append(ParsedTag(key.strip(), val.strip().rstrip(","), i, current_namelist))

    return tags


def parse_lammps(text: str) -> list[ParsedTag]:
    """Parse LAMMPS input script.

    command arg1 arg2 ...
    Lines starting with # are comments.
    """
    tags: list[ParsedTag] = []
    for i, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # Handle inline comments
        if "#" in stripped:
            stripped = stripped[:stripped.index("#")].strip()
        parts = stripped.split()
        if not parts:
            continue
        # The command is the first 1-2 words (e.g. "pair_style" or "fix npt")
        # For LAMMPS manual, names like "pair lj/cut" are stored with space
        cmd = parts[0]
        rest = " ".join(parts[1:]) if len(parts) > 1 else ""
        tags.append(ParsedTag(cmd, rest, i))
    return tags


def parse_gaussian(text: str) -> list[ParsedTag]:
    """Parse Gaussian input file (.com / .gjf).

    Gaussian format:
        %mem=16GB                          ← Link 0 commands (% prefix), skip
        %nprocshared=8                     ← Link 0 commands, skip
        #p B3LYP/6-311+G(d,p) Polar CPHF=RdFreq  ← Route section: VALIDATE THESE
                                           ← blank line
        Title line                         ← free text, skip
                                           ← blank line
        0 1                                ← charge & multiplicity, skip
        C   0.93  -0.06  0.03             ← coordinates, skip
        ...
                                           ← blank line
        additional input                   ← additional input sections, skip

    Only the route section keywords are validated against the manual.
    """
    tags: list[ParsedTag] = []
    lines = text.splitlines()

    # Phase 1: Collect route lines (start with # and continue until blank line)
    route_lines: list[tuple[int, str]] = []  # (line_num, text)
    in_route = False

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Skip Link 0 commands
        if stripped.startswith("%"):
            continue

        # Route section starts with #
        if stripped.startswith("#"):
            in_route = True
            # Strip the #, #p, #n, #t prefix
            route_text = re.sub(r"^#[pntPNT]?\s*", "", stripped)
            route_lines.append((i + 1, route_text))
            continue

        # Route continues on next non-blank lines
        if in_route:
            if not stripped:
                # Blank line ends route section
                break
            route_lines.append((i + 1, stripped))

    # Phase 2: Parse route keywords
    # Combine all route lines into one string
    full_route = " ".join(text for _, text in route_lines)
    first_route_line = route_lines[0][0] if route_lines else 1

    # Split by spaces; each token is a keyword or keyword=option or method/basis
    tokens = full_route.split()
    for token in tokens:
        # Skip method/basis set specifications (contain / like B3LYP/6-311+G(d,p))
        if "/" in token and not token.startswith("/"):
            # Could be a method/basis combo — skip it
            continue

        # Extract keyword name (before = or ( if present)
        # e.g. "Polar" → "Polar", "CPHF=RdFreq" → "CPHF", "Opt(Tight)" → "Opt"
        m = re.match(r"([A-Za-z_]\w*)", token)
        if m:
            keyword = m.group(1)
            option = token[len(keyword):] if len(token) > len(keyword) else ""
            tags.append(ParsedTag(keyword, option, first_route_line, "route"))

    return tags


def parse_orca(text: str) -> list[ParsedTag]:
    """Parse ORCA input file format.

    ORCA format:
        ! keyword1 keyword2 method/basis ...   <- Route line (keywords to validate)
        %maxcore 1000                          <- Simple setting
        %pal nprocs 4 end                      <- Block setting (single line)
        %block                                 <- Block setting (multi-line)
          key value
        end
        %cpcm                                  <- Named block
          smd true
          SMDsolvent "water"
        end
        * xyz 0 1                              <- Coordinate start
        O  0.0  0.0  0.1                       <- Skip coordinates
        H  0.0  0.7 -0.4
        *                                      <- Coordinate end

    Only the ! keywords and %block settings are validated.
    """
    tags: list[ParsedTag] = []
    lines = text.splitlines()
    in_coords = False
    in_block = ""

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped:
            continue

        # Coordinate block: skip between * markers
        if stripped.startswith("*"):
            in_coords = not in_coords
            continue
        if in_coords:
            continue

        # Route line: ! keyword1 keyword2 ...
        if stripped.startswith("!"):
            route_text = stripped.lstrip("!").strip()
            for token in route_text.split():
                # Skip method/basis combos (contain /)
                if "/" in token and not token.startswith("/"):
                    continue
                # Match keyword including optional parenthesized part
                # e.g. def2-SV(P), CCSD(T), def2-TZVP(-f)
                m = re.match(r"([A-Za-z_][\w-]*(?:\([^)]*\))?(?:-[\w]+)*)", token)
                if m:
                    tags.append(ParsedTag(m.group(1), "", i, "route"))
            continue

        # Block settings: %blockname ... end
        if stripped.startswith("%"):
            content = stripped[1:].strip()
            parts = content.split(None, 1)
            if not parts:
                continue
            block_name = parts[0]
            rest = parts[1] if len(parts) > 1 else ""

            # Single-line block: %pal nprocs 4 end
            if rest.strip().lower().endswith("end"):
                inner = rest.strip()[:-3].strip()
                for kv in inner.split():
                    m = re.match(r"([A-Za-z_]\w*)", kv)
                    if m:
                        tags.append(ParsedTag(m.group(1), "", i, f"%{block_name}"))
                continue

            # Simple setting: %maxcore 1000
            if rest and not rest.strip().lower() == "end":
                tags.append(ParsedTag(block_name, rest.strip(), i, "%"))
                continue

            # Multi-line block start: %blockname
            in_block = block_name
            continue

        # Inside a multi-line %block
        if in_block:
            if stripped.lower() == "end":
                in_block = ""
                continue
            # Parse key value pairs inside block
            m = re.match(r"([A-Za-z_]\w*)\s+(.*)", stripped)
            if m:
                tags.append(ParsedTag(m.group(1), m.group(2).strip(), i, f"%{in_block}"))
            continue

    return tags


def parse_psi4(text: str) -> list[ParsedTag]:
    """Parse PSI4 input file format (Python-like).

    PSI4 format:
        memory 4 gb
        molecule name {                        <- Skip molecule block
          0 1
          O  0.0  0.0  0.1
          --
          0 1
          H  0.0  0.7 -0.4
        }
        set {                                  <- Settings block
          basis aug-cc-pVTZ
          scf_type df
          freeze_core true
        }
        set module_name {                      <- Module settings
          key value
        }
        energy('sapt2+(3)dmp2')               <- Task call
        optimize('mp2')

    Validates set block keys and top-level directives.
    """
    tags: list[ParsedTag] = []
    lines = text.splitlines()
    brace_depth = 0
    in_molecule = False
    in_set = False
    set_module = ""

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # Track brace depth
        open_braces = stripped.count("{")
        close_braces = stripped.count("}")

        # Molecule block: skip entirely
        if re.match(r"molecule\s+", stripped, re.IGNORECASE) or \
           (stripped.lower().startswith("molecule") and "{" in stripped):
            in_molecule = True
            brace_depth += open_braces - close_braces
            if brace_depth <= 0:
                in_molecule = False
                brace_depth = 0
            continue
        if in_molecule:
            brace_depth += open_braces - close_braces
            if brace_depth <= 0:
                in_molecule = False
                brace_depth = 0
            continue

        # Set block
        m_set = re.match(r"set\s+(\w+)?\s*\{", stripped, re.IGNORECASE)
        if m_set or (stripped.lower().startswith("set") and "{" in stripped):
            in_set = True
            if m_set and m_set.group(1) and m_set.group(1) != "{":
                set_module = m_set.group(1)
            else:
                set_module = ""
            brace_depth += open_braces - close_braces
            if brace_depth <= 0:
                in_set = False
                brace_depth = 0
            continue
        if in_set:
            brace_depth += open_braces - close_braces
            if "}" in stripped:
                # Last line of set block
                content = stripped.replace("}", "").strip()
                if content:
                    parts = content.split(None, 1)
                    if parts and re.match(r"[A-Za-z_]\w*$", parts[0]):
                        val = parts[1] if len(parts) > 1 else ""
                        section = f"set/{set_module}" if set_module else "set"
                        tags.append(ParsedTag(parts[0], val, i, section))
                if brace_depth <= 0:
                    in_set = False
                    brace_depth = 0
                continue
            # Normal set key-value line
            parts = stripped.split(None, 1)
            if parts and re.match(r"[A-Za-z_]\w*$", parts[0]):
                val = parts[1] if len(parts) > 1 else ""
                section = f"set/{set_module}" if set_module else "set"
                tags.append(ParsedTag(parts[0], val, i, section))
            continue

        # Top-level: set key value (without braces)
        m_set_single = re.match(r"set\s+(\w+)\s+(.+)", stripped, re.IGNORECASE)
        if m_set_single and "{" not in stripped:
            tags.append(ParsedTag(m_set_single.group(1), m_set_single.group(2), i, "set"))
            continue

        # Task calls: energy('method'), optimize('method')
        m_task = re.match(r"(energy|optimize|gradient|frequency|properties)\s*\(", stripped, re.IGNORECASE)
        if m_task:
            tags.append(ParsedTag(m_task.group(1), stripped, i, "task"))
            continue

        # Top-level directives: memory, basis, etc.
        parts = stripped.split(None, 1)
        if parts and re.match(r"[A-Za-z_]\w*$", parts[0]):
            val = parts[1] if len(parts) > 1 else ""
            tags.append(ParsedTag(parts[0], val, i, ""))

    return tags


def parse_json_input(text: str) -> list[ParsedTag]:
    """Parse JSON input (DeePMD-kit, DP-GEN, DPGEN2).

    Walk the JSON tree and collect all keys with their path.
    """
    tags: list[ParsedTag] = []
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        tags.append(ParsedTag("__JSON_PARSE_ERROR__", str(e), 1))
        return tags

    def _walk(obj: Any, path: str, depth: int = 0) -> None:
        if isinstance(obj, dict):
            for key, val in obj.items():
                full_key = f"{path}/{key}" if path else key
                # Record the key itself
                if isinstance(val, (str, int, float, bool)) or val is None:
                    tags.append(ParsedTag(key, str(val), 0, path or "(root)"))
                elif isinstance(val, list):
                    tags.append(ParsedTag(key, f"[list, len={len(val)}]", 0, path or "(root)"))
                else:
                    tags.append(ParsedTag(key, "{dict}", 0, path or "(root)"))
                    _walk(val, full_key, depth + 1)
        # We don't recurse into list items for validation purposes

    _walk(data, "")
    return tags


# ---------------------------------------------------------------------------
# Detect paradigm / auto-select parser
# ---------------------------------------------------------------------------

_SOFTWARE_PARADIGM: dict[str, str] = {
    "VASP": "KEY_VALUE",
    "ABACUS": "KEY_VALUE",
    "CP2K": "HIERARCHICAL_BLOCK",
    "LAMMPS": "KEYWORD_LINE",
    "QE": "NAMELIST",
    "QUANTUM ESPRESSO": "NAMELIST",
    "DEEPMD-KIT": "JSON",
    "DEEPMD": "JSON",
    "DP-GEN": "JSON",
    "DPGEN": "JSON",
    "DPGEN2": "JSON",
    "GAUSSIAN": "GAUSSIAN_ROUTE",
    "ORCA": "ORCA",
    "PSI4": "PSI4",
    "PYATB": "KEY_VALUE",
    "PLUMED": "KEYWORD_LINE",
    "ASE": "KEY_VALUE",
    "PYMATGEN": "KEY_VALUE",
    "PYSCF": "KEY_VALUE",
}

_PARSER_MAP = {
    "KEY_VALUE": parse_key_value,
    "HIERARCHICAL_BLOCK": parse_cp2k,
    "KEYWORD_LINE": parse_lammps,
    "NAMELIST": parse_namelist,
    "JSON": parse_json_input,
    "GAUSSIAN_ROUTE": parse_gaussian,
    "ORCA": parse_orca,
    "PSI4": parse_psi4,
}


def _detect_paradigm(software: str, file_path: str) -> str:
    """Detect paradigm from software name or file extension."""
    key = software.upper().replace("_", "-")
    if key in _SOFTWARE_PARADIGM:
        return _SOFTWARE_PARADIGM[key]
    # Fallback by extension
    ext = Path(file_path).suffix.lower()
    if ext == ".json":
        return "JSON"
    return "KEY_VALUE"


# ---------------------------------------------------------------------------
# Validation logic
# ---------------------------------------------------------------------------

class Issue:
    """A validation issue (error or warning)."""
    __slots__ = ("severity", "line_num", "message")

    def __init__(self, severity: str, line_num: int, message: str):
        self.severity = severity  # "ERROR" or "WARNING"
        self.line_num = line_num
        self.message = message


def _build_manual_index(
    params: list[dict],
    extra_data: dict | None = None,
) -> tuple[dict[str, list[dict]], dict[str, list[str]]]:
    """Build lookup structures from the manual.

    Returns:
        name_index: {UPPERCASE_NAME: [param_dict, ...]}  (list because a name can appear in multiple sections)
        section_index: {SECTION_PATH: [param_names]}
    """
    name_index: dict[str, list[dict]] = {}
    section_index: dict[str, list[str]] = {}

    for p in params:
        name = p.get("name", "").strip()
        name_upper = name.upper()
        # Some manuals have trailing colons (DeePMD: "model:")
        name_clean = name_upper.rstrip(":")
        name_index.setdefault(name_upper, []).append(p)
        if name_clean != name_upper:
            name_index.setdefault(name_clean, []).append(p)

        ps = p.get("parent_section", "") or ""
        section_index.setdefault(ps, []).append(name)

    # Also index methods and basis_sets if present (for ORCA, PSI4, etc.)
    if extra_data:
        for method in extra_data.get("methods", []):
            mname = method.get("name", "").strip().upper()
            if mname:
                name_index.setdefault(mname, []).append(method)
                # Also add without parentheses: CCSD(T) -> CCSD
                base = re.sub(r"\(.*\)", "", mname)
                if base != mname:
                    name_index.setdefault(base, []).append(method)
        for basis in extra_data.get("basis_sets", []):
            bname = basis.get("name", "").strip().upper()
            if bname:
                name_index.setdefault(bname, []).append(basis)

    return name_index, section_index


def _is_path_like(section: str) -> bool:
    if not section:
        return False
    if len(section) > 200:
        return False
    parts = section.split("/")
    return all(len(p.strip()) < 60 and " " not in p.strip() for p in parts)


def _check_type(value: str, expected_dtype: str) -> bool:
    """Heuristic type check. Returns True if value seems to match dtype."""
    dtype = expected_dtype.upper()
    val = value.strip().strip("'\"")

    if dtype in ("INT", "INTEGER"):
        return bool(re.match(r"^-?\d+$", val))
    if dtype in ("FLOAT", "REAL", "DOUBLE"):
        return bool(re.match(r"^-?(\d+\.?\d*|\.\d+)([eEdD][+-]?\d+)?$", val))
    if dtype in ("BOOL", "LOGICAL"):
        return val.upper() in (
            ".TRUE.", ".FALSE.", "T", "F", "TRUE", "FALSE", "YES", "NO",
            "0", "1",
        )
    # STRING or unknown: accept anything
    return True


def validate(
    parsed_tags: list[ParsedTag],
    params: list[dict],
    software: str,
    paradigm: str,
    raw_text: str = "",
    extra_data: dict | None = None,
) -> list[Issue]:
    """Cross-validate parsed tags against the manual.

    Args:
        raw_text: Optional raw input file text. When provided, enables
                  structural checks (e.g. detecting keywords incorrectly
                  used as ``&SECTION`` blocks in CP2K).
        extra_data: Optional dict with additional data from the JSON (methods,
                    basis_sets, etc.) to build the name index from.
    """
    issues: list[Issue] = []
    name_index, section_index = _build_manual_index(params, extra_data)
    known_names = list(name_index.keys())

    # For CP2K, section placement matters
    check_sections = paradigm == "HIERARCHICAL_BLOCK"
    # For QE namelist, section placement also matters but parent_section is text-based
    # We skip section validation for QE since parent_section is not path-like

    # Pre-check: detect if dtype data is unreliable (e.g., QE has all FLOAT)
    all_dtypes = set()
    for p in params:
        args = p.get("arguments", [])
        if args:
            all_dtypes.add(args[0].get("dtype", ""))
    skip_type_check = len(all_dtypes) == 1  # All same dtype → unreliable

    # ---- CP2K known-good whitelist ----
    # Many valid CP2K keywords are missing from the JSON manual because the
    # manual scraper did not capture them (they are section-level syntactic
    # constructs, not named parameters in the JSON schema).  We whitelist
    # them here to avoid false-positive "Unknown tag" errors.
    _CP2K_WHITELIST: set[str] = set()
    # Typo map: common misspellings → correct keyword (emit WARNING, not ERROR)
    _CP2K_TYPO_MAP: dict[str, str] = {}
    if paradigm == "HIERARCHICAL_BLOCK" and software.upper() in ("CP2K",):
        _CP2K_WHITELIST = {
            # &CELL: lattice vectors / constants
            "A", "B", "C", "ABC", "ALPHA_BETA_GAMMA",
            # &KPOINTS: scheme definition
            "SCHEME",
            # &KPOINT_SET / &BANDSTRUCTURE: special k-points
            "SPECIAL_POINT",
            # &SMEAR: smearing keywords
            "ELECTRONIC_TEMPERATURE",
            # &KIND: basis/potential (indexed only under other sections in JSON)
            "BASIS_SET", "POTENTIAL", "ELEMENT",
            # &COORD: sometimes has SCALED keyword outside COORD body
            # &TOPOLOGY
            "COORD_FILE_NAME", "COORD_FILE_FORMAT",
            # &PRINT subsection control keywords
            "FILENAME", "LOG_PRINT_KEY", "COMMON_ITERATION_LEVELS",
            # Section parameter keywords (often first word after &SECTION)
            "ON", "OFF", "TRUE", "FALSE", "T", "F",
            # &XC_FUNCTIONAL: functional name as section parameter
            "PBE", "BLYP", "B3LYP", "PBE0", "TPSS", "HSE06", "LDA",
            # &GLOBAL
            "PROJECT",
            # &SCF: DIIS-related keywords (there is NO &DIIS subsection!)
            "MAX_DIIS", "EPS_DIIS",
            # &NOSE thermostat
            "LENGTH", "TIMECON", "MTS",
            # &CELL_OPT
            "EXTERNAL_PRESSURE", "PRESSURE_TOLERANCE",
            # &MD / &EACH: iteration counters
            "MD", "GEO_OPT", "CELL_OPT",

            # ---- Multiwfn-generated keywords missing from JSON manual ----
            # &DFT: restart / file paths
            "WFN_RESTART_FILE_NAME", "BASIS_SET_FILE_NAME",
            "POTENTIAL_FILE_NAME", "AUTO_BASIS",
            # &QS
            "EPS_PGF_ORB",
            # &POISSON
            "PSOLVER",
            # &SCF / &MIXING
            "NBROYDEN",
            # &HF / &SCREENING
            "FRACTION", "EPS_SCHWARZ", "SCREEN_ON_INITIAL_P",
            # &HF / &INTERACTION_POTENTIAL
            "POTENTIAL_TYPE", "CUTOFF_RADIUS", "OMEGA",
            # &HF / &MEMORY
            "MAX_MEMORY", "EPS_STORAGE_SCALING",
            # &XC_FUNCTIONAL: functional scaling parameters
            "SCALE_X", "SCALE_C", "SCALE_X0", "SCALE",
            # &XC_FUNCTIONAL children: functional-specific
            "FUNCTIONAL_TYPE",
            # &VDW_POTENTIAL / &PAIR_POTENTIAL
            "PARAMETER_FILE_NAME", "REFERENCE_FUNCTIONAL", "TYPE",
            # &VDW_POTENTIAL / &NON_LOCAL
            "PARAMETERS", "KERNEL_FILE_NAME",
            # &WF_CORRELATION / &RI_MP2 / &RI_RPA
            "QUADRATURE_POINTS", "EPS_GRID",
            "SCALE_S", "SCALE_T",
            # &WF_CORRELATION: general
            "MEMORY", "GROUP_SIZE",
            # &GW
            "CORR_MOS_OCC", "CORR_MOS_VIRT", "EV_GW_ITER",
            "SC_GW0_ITER", "UPDATE_XC_ENERGY", "RI_SIGMA_X",
            "PERIODIC_CORRECTION", "KPOINTS_SELF_ENERGY",
            # &SCF / &OT
            "MINIMIZER", "LINESEARCH", "PRECONDITIONER",
            # &BSSE / &FRAGMENT
            "LIST", "GLB_CONF", "SUB_CONF",
            # &BAND (NEB/CI-NEB)
            "K_SPRING", "BAND_TYPE", "NUMBER_OF_REPLICA",
            "NPROC_REP", "ALIGN_FRAMES", "ROTATE_FRAMES",
            "OPTIMIZE_END_POINTS", "MAX_STEPSIZE",
            "NSTEPS_IT", "INITIAL_CONFIGURATION_INFO",
            "MAX_DR", "MAX_FORCE", "RMS_DR", "RMS_FORCE",
            # &DIMER (TS search)
            "ANGLE_TOLERANCE",
            # &NMR / &CURRENT / &LINRES
            "GAUGE", "ORBITAL_CENTER", "CHI_PBC",
            # &PRINT / &MOMENTS
            "MAGNETIC", "REFERENCE", "MAX_MOMENT",
            "PERIODIC_DIPOLE_OPERATOR",
            # &REAL_TIME_PROPAGATION
            "DELTA_PULSE_DIRECTION", "APPLY_DELTA_PULSE",
            # &SCCS (solvation)
            "DIELECTRIC_CONSTANT", "RHO_MIN", "RHO_MAX",
            "AUTO_VDW_RADII_TABLE", "AUTO_RMIN_SCALE", "AUTO_RMAX_SCALE",
            # &XAS_TDP
            "GRID", "ENERGY_RANGE",
            "DEFINE_EXCITED", "ATOM_LIST", "STATE_TYPES", "N_SEARCH",
            "RI_REGION",
            # &XTB
            "DO_EWALD", "CHECK_ATOMIC_CHARGES",
            # &PIMD / &PINT
            "P",
            # &PRINT / &MO_MOLDEN
            "NDIGITS",
            # &EACH iteration counter types
            "PINT", "QS_SCF",
            # &PRINT misc
            "STRIDE", "THERMOCHEMISTRY",
        }
        # Known typos → correct keyword.  Validation will emit a WARNING
        # with the correct name so the agent can fix it immediately instead
        # of looping through fruitless manual searches.
        _CP2K_TYPO_MAP = {
            "SPECIAL_KPOINT": "SPECIAL_POINT",
            "SPECIAL_KPOINTS": "SPECIAL_POINT",
            "KPOINT": "SPECIAL_POINT",
            "KPOINTS": "SCHEME",
            # &DIIS does NOT exist — it's not a section
            "DIIS": "MAX_DIIS",
        }

    # ---- CP2K structural check: keywords incorrectly used as &SECTION blocks ----
    # Strategy: build the set of known-valid section names from TWO sources:
    #   1. Manual JSON parent_section paths (definitely valid — these sections
    #      contain indexed parameters).
    #   2. A SMALL curated supplement of real CP2K sections that the JSON scraper
    #      missed (verified against CP2K source / official docs).
    # Any &FOO not in this combined set is checked:
    #   - If FOO is a keyword in the manual → ERROR (keyword used as subsection)
    #   - If FOO is in the keyword whitelist → ERROR
    #   - Otherwise → WARNING ("section not in our reference, may be invalid")
    _cp2k_manual_sections: set[str] = set()
    if paradigm == "HIERARCHICAL_BLOCK" and software.upper() in ("CP2K",):
        # Source 1: extract section names from parent_section paths in the manual
        for p in params:
            ps = (p.get("parent_section") or "").strip().strip("/")
            if ps:
                for part in ps.split("/"):
                    _cp2k_manual_sections.add(part.upper())
        # Source 2: curated supplement — real CP2K subsections NOT in the JSON
        # because the scraper only captured parameters, not empty container
        # sections.  Each entry below is verified against CP2K 2024.1 docs.
        _CP2K_VERIFIED_SECTIONS = frozenset({
            # SCF sub-blocks (verified: manual.cp2k.org/trunk/.../SCF.html)
            "OT", "OUTER_SCF", "SMEAR", "DIAGONALIZATION", "MIXING",
            "MOM",  # Maximum Overlap Method — valid SCF subsection
            # XC sub-blocks (verified: manual.cp2k.org/trunk/.../XC.html)
            "XC_FUNCTIONAL", "VDW_POTENTIAL", "XC_GRID", "HF",
            "XC_POTENTIAL", "XC_KERNEL", "WF_CORRELATION",
            "HFX_KERNEL", "GCP_POTENTIAL", "ADIABATIC_RESCALING",
            # HF sub-blocks
            "INTERACTION_POTENTIAL", "SCREENING", "MEMORY",
            # VDW_POTENTIAL sub-blocks
            "PAIR_POTENTIAL", "NON_LOCAL",
            # XC_FUNCTIONAL children (functional names as sections)
            "PBE", "BLYP", "B3LYP", "PBE0", "TPSS", "HSE06", "LDA",
            "BECKE88", "LYP", "PW92", "PADE", "XALPHA", "OPTX", "OLYP",
            "KE_GGA", "BEEF", "MGGA", "BECKE_ROUSSEL", "BECKE97",
            "GGA_X_WPBEH", "LIBXC", "XGGA",
            # PRINT children (valid as &SECTION under &PRINT blocks)
            "MO_CUBES", "PDOS", "LDOS", "MOMENTS", "DIPOLE",
            "FORCES", "STRESS_TENSOR", "ATOMIC_FORCES",
            "BAND_STRUCTURE", "MULLIKEN", "HIRSHFELD", "LOWDIN",
            "ENERGY", "TRAJECTORY", "VELOCITIES", "CELL_MOTION",
            "RESTART_HISTORY",
            # MOTION sub-blocks (verified: manual.cp2k.org/trunk/.../MOTION.html)
            "COLVAR", "COLLECTIVE",
            # GEO_OPT / CELL_OPT sub-blocks
            "BFGS", "CG", "LBFGS", "TRANSITION_STATE",
            # MD sub-blocks (verified: manual.cp2k.org/trunk/.../MD.html)
            "THERMOSTAT", "BAROSTAT", "LANGEVIN",
            "RESPA", "ADIABATIC_DYNAMICS",
            "CASCADE", "MSST", "REFTRAJ",
            # THERMOSTAT sub-blocks
            "NOSE",
            # SUBSYS sub-blocks
            "SHELL_COORD", "CORE_COORD", "VELOCITY",
            "SHELL_VELOCITY", "CORE_VELOCITY",
            # MM / FORCEFIELD sub-blocks
            "NONBONDED", "NONBONDED14", "EWALD",
            "CHARGE", "MULTIPOLE", "SPLINE",
            "GENPOT", "LENNARD_JONES", "BMHFT",
            "BUCK4RANGES", "BUCKMORSE", "EAM", "GOODWIN", "IPBV",
            "QUIP", "TERSOFF", "SIEPMANN", "TABPOT",
            "BOND", "BEND", "TORSION", "IMPROPER", "OPBEND",
            "GENERATE", "SHELL",
            # Semi-empirical / DFTB / xTB
            "SE", "DFTB", "XTB", "SCPTB",
            # POISSON
            "MULTIPOLE_EWALD", "ISOLATED", "PERIODIC",
            # Response / properties
            "LINRES", "NMR", "EPR", "POLAR", "SPINSPIN",
            "EXCITED_STATES", "TDDFPT", "TDDFT",
            "WFN_MIX", "RAMAN", "ETS",
            # Transport / NEGF
            "TRANSPORT",
            # Misc
            "EACH", "LOW", "MEDIUM", "HIGH", "SILENT", "DEBUG",
            "WANNIER_CENTERS", "WANNIER_STATES", "WANNIER_PROJECTION",
            "RESP", "BLOCK_DIAG", "ELECTRIC_FIELD",
            # Multiwfn-generated sections missing from JSON scraper
            # &XC_FUNCTIONAL children (LibXC functional subsections)
            "MGGA_X_R2SCAN", "MGGA_C_R2SCAN",
            "MGGA_X_SCAN", "MGGA_C_SCAN",
            "MGGA_XC_B97M_V",
            "HYB_MGGA_X_M06_2X", "MGGA_C_M06_2X",
            "HYB_GGA_XC_BHANDHLYP",
            "GGA_X_PBE", "P86C", "XWPBE", "VWN",
            # &WF_CORRELATION children
            "RI_MP2", "RI_RPA", "GW", "INTEGRALS", "WFC_GPW",
            # &BAND children (NEB)
            "OPTIMIZE_BAND", "CI_NEB", "CONVERGENCE_INFO",
            "CONVERGENCE_CONTROL", "PROGRAM_RUN_INFO", "REPLICA",
            "DIIS",  # valid under OPTIMIZE_BAND for band/NEB optimization
            # &GEO_OPT children
            "LINE_SEARCH", "DIMER", "ROT_OPT",
            # &BSSE
            "FRAGMENT",
            # &LINRES children
            "CURRENT",
            # &SCCS children / &PRINT
            "POLARISATION_CHARGE_DENSITY", "DIELECTRIC_FUNCTION",
            "ANDREUSSI", "SPHERE_SAMPLING",
            "COORD_FIT_POINTS", "RESP_CHARGES_TO_FILE",
            # &PRINT / molden
            "MO_MOLDEN",
            # &XAS_TDP children
            "DONOR_STATES", "KERNEL", "EXACT_EXCHANGE", "XAS_TDP",
            # &DFT children
            "AUXILIARY_DENSITY_MATRIX_METHOD",
            # &PINT (path integral)
            "STAGING",
        })
        _cp2k_manual_sections |= _CP2K_VERIFIED_SECTIONS

    # Known-INVALID section names: names that LLMs commonly hallucinate as
    # subsections but are definitely NOT valid CP2K sections.
    # Maps fake section → correct usage hint.
    _CP2K_FAKE_SECTIONS: dict[str, str] = {}
    if paradigm == "HIERARCHICAL_BLOCK" and software.upper() in ("CP2K",):
        _CP2K_FAKE_SECTIONS = {
            # NOTE: &DIIS is valid under OPTIMIZE_BAND (NEB), but NOT under SCF.
            # Since structural check is context-free, we keep it out of fakes.
            "ADDED_MOS": "Use keyword ADDED_MOS 30 directly inside &SCF",
            # NOTE: STRESS_TENSOR and CELL_MOTION are valid subsections under
            # &PRINT, so they are in _CP2K_VERIFIED_SECTIONS and should NOT
            # be listed here (they'd be unreachable dead code).
        }

    if paradigm == "HIERARCHICAL_BLOCK" and software.upper() in ("CP2K",) and raw_text:
        for m_sec in re.finditer(r'^\s*&(\w+)', raw_text, re.MULTILINE):
            sec_name = m_sec.group(1).upper()
            if sec_name == "END":
                continue
            if sec_name in _cp2k_manual_sections:
                continue  # Known valid section name (from manual or verified list)
            # Check known-invalid sections first (definite ERRORs)
            if sec_name in _CP2K_FAKE_SECTIONS:
                line_num = raw_text[:m_sec.start()].count('\n') + 1
                hint = _CP2K_FAKE_SECTIONS[sec_name]
                issues.append(Issue(
                    "ERROR", line_num,
                    f'"&{sec_name} ... &END {sec_name}" is WRONG — '
                    f'&{sec_name} is NOT a valid CP2K subsection. '
                    f'Correct usage: {hint}.',
                ))
                continue
            # If this "section" name is actually a keyword in the manual...
            entries = name_index.get(sec_name)
            if entries:
                line_num = raw_text[:m_sec.start()].count('\n') + 1
                parent = (entries[0].get("parent_section") or "").split("/")[-1] or "?"
                syntax = entries[0].get("syntax_template", f"{sec_name} <value>")
                issues.append(Issue(
                    "ERROR", line_num,
                    f'"&{sec_name} ... &END {sec_name}" is WRONG — '
                    f'{sec_name} is a keyword, not a subsection. '
                    f'Remove the & prefix and &END block. '
                    f'Correct syntax: {syntax} (inside &{parent})',
                ))
            elif sec_name in _CP2K_WHITELIST:
                line_num = raw_text[:m_sec.start()].count('\n') + 1
                issues.append(Issue(
                    "ERROR", line_num,
                    f'"&{sec_name} ... &END {sec_name}" is WRONG — '
                    f'{sec_name} is a keyword, not a subsection. '
                    f'Remove the & prefix and &END block. '
                    f'Use it as a single line: {sec_name} <value>',
                ))
            else:
                # Unknown: not a recognized section AND not a known keyword.
                # Issue a WARNING so the LLM investigates.
                line_num = raw_text[:m_sec.start()].count('\n') + 1
                issues.append(Issue(
                    "WARNING", line_num,
                    f'"&{sec_name}" is not a recognized CP2K section in our '
                    f'reference. Verify that &{sec_name} ... &END {sec_name} '
                    f'is valid in your CP2K version. Common mistakes: '
                    f'&DIIS (use MAX_DIIS keyword), &ADDED_MOS (use ADDED_MOS '
                    f'keyword), or other keywords incorrectly wrapped in & blocks.',
                ))

    # Collect all known section paths from the manual for subsection fallback
    _manual_sections: set[str] = set()
    if check_sections:
        for p in params:
            ps = (p.get("parent_section") or "").strip().strip("/")
            if ps:
                _manual_sections.add(ps)

    # CP2K section aliases: the JSON manual indexes some keywords under one
    # section path, but they are equally valid in another.  Map actual→accepted.
    _CP2K_SECTION_ALIASES: dict[str, set[str]] = {}
    if paradigm == "HIERARCHICAL_BLOCK" and software.upper() in ("CP2K",):
        _CP2K_SECTION_ALIASES = {
            # BASIS_SET and POTENTIAL are used in SUBSYS/KIND but indexed elsewhere
            "FORCE_EVAL/SUBSYS/KIND": {
                "OPTIMIZE_BASIS/FIT_KIND",          # BASIS_SET
                "FORCE_EVAL/DFT/RELATIVISTIC",      # POTENTIAL
                "TEST/ERI_MME_TEST",                 # POTENTIAL
            },
            # NPOINTS and SPECIAL_POINT are used in BANDSTRUCTURE/KPOINT_SET
            # but indexed under DFT/KPOINT_SET.
            # CP2K supports BANDSTRUCTURE under both PROPERTIES and DFT/PRINT paths.
            "FORCE_EVAL/PROPERTIES/BANDSTRUCTURE/KPOINT_SET": {
                "FORCE_EVAL/DFT/KPOINT_SET",
            },
            "FORCE_EVAL/DFT/PRINT/BAND_STRUCTURE/KPOINT_SET": {
                "FORCE_EVAL/DFT/KPOINT_SET",
            },
            # Some inputs use FORCE_EVAL/PROPERTIES/BANDSTRUCTURE directly
            "FORCE_EVAL/PROPERTIES/BANDSTRUCTURE": {
                "FORCE_EVAL/DFT/KPOINTS",
            },
            # ADDED_MOS is indexed under XAS but also valid in SCF
            "FORCE_EVAL/DFT/SCF": {
                "FORCE_EVAL/DFT/XAS",
            },
        }

    # ---- ORCA known-good whitelist ----
    # ORCA route keywords (! line), %block settings, and common options
    # that are not in the orca_parameters.json manual.
    _ORCA_WHITELIST: set[str] = set()
    if paradigm == "ORCA":
        _ORCA_WHITELIST = {
            # Task / job types
            "OPT", "OPTTS", "FREQ", "NUMFREQ", "ENGRAD", "MD",
            "NEB", "NEB-TS", "NEB-CI", "SCANRELAX",
            # SCF options
            "TIGHTSCF", "VERYTIGHTSCF", "NORMALSCF", "LOOSESCF",
            "NOAUTOSTART", "AUTOSTART",
            "SMALLPRINT", "MINIPRINT", "NORMALPRINT", "LARGEPRINT",
            "NOPOP", "ALLPOP",
            "RIJCOSX", "RIJONX", "RIJDX", "NORI", "RI",
            "NOFINALGRID", "GRID4", "GRID5", "GRID6", "GRID7",
            "DEFGRID1", "DEFGRID2", "DEFGRID3",
            "NOITER", "CONV", "UNCONVIF",
            "NOSOSCF", "SOSCF",
            "UHF", "UKS", "RHF", "RKS", "ROHF", "ROKS",
            "MOREAD", "PATOM", "PMODEL", "HUECKEL",
            "CPCM", "SMD",
            "SLOWCONV", "VERYSLOWCONV", "STRONGSCF",
            "KDIIS", "SOSCF", "DAMP",
            # Optimization / TS options
            "TIGHTOPT", "VERYTIGHTOPT", "NORMALOPT", "LOOSEOPT",
            "NUMHESS", "ANFREQ", "NUMGRAD",
            "RECALCHESS",
            # PNO thresholds
            "NORMALPNO", "LOOSEPNO", "TIGHTPNO",
            # Dispersion
            "D3", "D3BJ", "D3ZERO", "D4",
            # Composite / cheap methods
            "B97-3C", "R2SCAN-3C", "WB97M-V", "WB97X-D3",
            "PWPB95", "PWPB95-D4",
            "REVDSD-PBEP86-D4", "DSD-PBEP86", "DSD-PBEP86-D4",
            "RSX-QIDH", "RIJDOUBLEX",
            # Method keywords
            "DLPNO-CCSD", "DLPNO-CCSD(T)", "DLPNO-CCSD(T1)",
            "STEOM-DLPNO-CCSD", "EOM-CCSD",
            "CCSD(T)-F12", "CCSD(T)-F12/RI",
            "RI-B2PLYP", "RI-MP2",
            # Basis set families
            "DEF2-SVP", "DEF2-SV(P)", "DEF2-TZVP", "DEF2-TZVP(-F)",
            "DEF2-TZVPP", "DEF2-QZVPP", "DEF2-QZVP",
            "MA-DEF2-SVP", "MA-DEF2-TZVP", "MA-DEF2-TZVPP",
            "CC-PVDZ", "CC-PVTZ", "CC-PVQZ", "CC-PV5Z",
            "AUG-CC-PVDZ", "AUG-CC-PVTZ", "AUG-CC-PVQZ",
            "CC-PVDZ-F12", "CC-PVTZ-F12",
            # Auxiliary basis
            "DEF2/J", "DEF2/JK", "DEF2/C",
            "AUTOAUX", "DEF2-TZVPP/C", "DEF2-QZVPP/C",
            "CC-PVTZ/C", "CC-PVQZ/C",
            "CC-PVDZ-F12-CABS", "CC-PVTZ-F12-CABS",
            # Extrapolation
            "EXTRAPOLATE",
            # %block settings
            "MAXCORE", "NPROCS",
            # %tddft block settings
            "NROOTS", "IROOT", "TRIPLETS", "TAMMDAN", "DOTRANS",
            "MAXDIM", "MAXITER", "TDA",
            # %mdci / %method block
            "FROZENCORE", "FC_ELECTRONS",
            # BSSE / Counterpoise
            "BSSE",
            # STD-DFT / sTDA
            "STD-DFT",
            # %stddft block settings
            "MODE", "ETHRESH", "PTHRESH", "PTLIMIT",
            # %cpcm / SMD block settings
            "SMDSOLVENT", "EPSILON", "REFRAC",
            # %md block settings
            "TIMESTEP", "INITVEL", "THERMOSTAT", "DUMP", "RUN",
            "TEMP", "TIMEINT",
            # %geom block settings
            "CALC_HESS", "RECALC_HESS", "TRUST", "INHESS",
            "MAXSTEP", "CONVERGENCE",
            "TRUE", "FALSE",  # boolean values parsed as keywords
            # Misc
            "PRINTBASIS", "PRINTMOS", "KEEPDENS",
            "PAL", "NPROCS",
        }

    # ---- PSI4 known-good whitelist ----
    # Top-level PSI4 directives and task functions not in the JSON manual.
    _PSI4_WHITELIST: set[str] = set()
    if paradigm == "PSI4":
        _PSI4_WHITELIST = {
            # Top-level directives
            "MEMORY",
            # Task functions
            "ENERGY", "OPTIMIZE", "GRADIENT", "FREQUENCY", "PROPERTIES",
            # Common settings not in JSON
            "BASIS", "FREEZE_CORE",
            "SCF_TYPE", "MP2_TYPE", "CC_TYPE",
            "REFERENCE", "E_CONVERGENCE", "D_CONVERGENCE",
            "ROOTS_PER_IRREP", "EOM_REFERENCE",
            "SAPT_LEVEL", "DF_BASIS_SAPT", "DF_BASIS_ELST",
        }

    for tag in parsed_tags:
        tag_upper = tag.name.upper()

        # Special: JSON parse error
        if tag_upper == "__JSON_PARSE_ERROR__":
            issues.append(Issue("ERROR", tag.line_num, f"JSON parse error: {tag.value}"))
            continue

        # Look up in manual (returns list of matching entries)
        entries = name_index.get(tag_upper)

        # Handle array-index keys: celldm(1) → celldm, celldm(i)
        if entries is None and "(" in tag_upper:
            base = re.sub(r"\([^)]*\)", "", tag_upper)  # celldm(1) → celldm
            entries = name_index.get(base)
            if entries is None:
                generic = re.sub(r"\(\d+\)", "(i)", tag_upper)  # celldm(1) → celldm(i)
                entries = name_index.get(generic)
            if entries is None:
                generic2 = re.sub(r"\(\d+\)", "(I)", tag_upper)
                entries = name_index.get(generic2)

        # For LAMMPS, commands use underscores but manual uses spaces.
        # Also handle compound commands: pair_style lj/cut → "pair lj/cut" in manual.
        if entries is None and paradigm == "KEYWORD_LINE":
            # Try underscore→space: pair_coeff → "pair coeff"
            spaced = tag_upper.replace("_", " ")
            if spaced != tag_upper:
                entries = name_index.get(spaced)

            # Try combining command with first arg: pair_style lj/cut → "pair lj/cut"
            if entries is None:
                parts = tag.value.split(None, 1)
                if parts:
                    # pair_style lj/cut → cmd=pair_style, first_arg=lj/cut
                    cmd_base = tag.name.replace("_", " ").split()[0]  # pair
                    combined = f"{cmd_base} {parts[0]}"
                    entries = name_index.get(combined.upper())
                    if entries is None:
                        # Also try full name + arg: "pair_style lj/cut"
                        combined2 = f"{tag.name} {parts[0]}"
                        entries = name_index.get(combined2.upper())
                        if entries is None:
                            combined3 = f"{spaced} {parts[0]}"
                            entries = name_index.get(combined3.upper())

        if entries is None:
            # Check software-specific whitelists before reporting error
            if tag_upper in _CP2K_WHITELIST:
                continue
            if tag_upper in _ORCA_WHITELIST:
                continue
            if tag_upper in _PSI4_WHITELIST:
                continue

            # Check CP2K typo map — emit actionable WARNING instead of ERROR
            if tag_upper in _CP2K_TYPO_MAP:
                correct = _CP2K_TYPO_MAP[tag_upper]
                issues.append(Issue(
                    "WARNING",
                    tag.line_num,
                    f'"{tag.name}" is a common typo. The correct CP2K keyword is '
                    f'"{correct}". Replace "{tag.name}" → "{correct}" in your input file.',
                ))
                continue

            # LAMMPS: the manual (lammps_commands_sample.json) only covers a
            # subset of LAMMPS commands (mainly pair_style/fix/compute variants).
            # Many fundamental commands (boundary, atom_style, read_data,
            # neigh_modify, mass, dimension, create_box, …) are NOT in it.
            # Report as WARNING instead of ERROR so the agent is not misled.
            if paradigm == "KEYWORD_LINE" and software.upper() == "LAMMPS":
                suggestion = _suggest(tag_upper, known_names)
                msg = (
                    f'Command "{tag.name}" not in LAMMPS manual reference '
                    f'(note: the reference is incomplete and only covers ~{len(known_names)} '
                    f'command variants; this may be a perfectly valid LAMMPS command)'
                )
                if suggestion:
                    msg += f' -- closest match: "{suggestion}"'
                issues.append(Issue("WARNING", tag.line_num, msg))
                continue

            # Unknown tag
            suggestion = _suggest(tag_upper, known_names)
            msg = f'Unknown tag "{tag.name}"'
            if suggestion:
                msg += f' -- did you mean "{suggestion}"?'
            issues.append(Issue("ERROR", tag.line_num, msg))
            continue

        # Pick the best matching entry (prefer one whose section matches)
        manual_entry = entries[0]
        if check_sections and tag.section_path and len(entries) > 1:
            actual_section = tag.section_path.strip().strip("/")
            for e in entries:
                es = (e.get("parent_section") or "").strip().strip("/")
                if es == actual_section:
                    manual_entry = e
                    break

        # Section check (CP2K): only warn if tag doesn't appear in *any*
        # manual entry for this section.
        # Enhanced: also try matching after stripping subsections that don't
        # exist in the manual (e.g. FORCE_EVAL/DFT/SCF/SMEAR → try
        # FORCE_EVAL/DFT/SCF when SMEAR is not a known manual section).
        # Also uses _CP2K_SECTION_ALIASES for known misindexed sections.
        if check_sections and tag.section_path:
            actual_section = tag.section_path.strip().strip("/")
            valid_sections = {
                (e.get("parent_section") or "").strip().strip("/") for e in entries
            }
            if actual_section and valid_sections and actual_section not in valid_sections:
                # Check section aliases first
                aliases = _CP2K_SECTION_ALIASES.get(actual_section, set())
                if aliases & valid_sections:
                    pass  # Alias match — section is OK
                else:
                    # Try stripping trailing subsections not in the manual
                    matched_parent = False
                    parts = actual_section.split("/")
                    for n in range(len(parts) - 1, 0, -1):
                        candidate = "/".join(parts[:n])
                        if candidate in valid_sections:
                            matched_parent = True
                            break
                    if not matched_parent:
                        expected_list = ", ".join(sorted(valid_sections)[:3])
                        more = f" (and {len(valid_sections) - 3} more)" if len(valid_sections) > 3 else ""
                        issues.append(Issue(
                            "WARNING",
                            tag.line_num,
                            f'Tag "{tag.name}" is in section {actual_section} '
                            f'but manual says valid sections are: {expected_list}{more}',
                        ))

        # Type check (skip if manual dtype data is unreliable)
        if not skip_type_check:
            args = manual_entry.get("arguments", [])
            if args and tag.value:
                expected_dtype = args[0].get("dtype", "STRING")
                if expected_dtype and expected_dtype.upper() != "STRING":
                    # For multi-value lines, check just the first token
                    first_val = tag.value.split()[0] if tag.value.split() else tag.value
                    if not _check_type(first_val, expected_dtype):
                        issues.append(Issue(
                            "WARNING",
                            tag.line_num,
                            f'Tag "{tag.name}" value "{first_val}" '
                            f'may not match expected type {expected_dtype}',
                        ))

    # ---- Gaussian-specific structural validation ----
    if paradigm == "GAUSSIAN_ROUTE" and software.upper() in ("GAUSSIAN",) and raw_text:
        _gaussian_structural_check(raw_text, parsed_tags, issues)

    return issues


def _gaussian_structural_check(
    raw_text: str, parsed_tags: list[ParsedTag], issues: list[Issue]
) -> None:
    """Gaussian-specific structural checks beyond simple keyword validation.

    Checks:
    1. genecp vs gen: if ECP atoms are specified, route must use genecp or gen+pseudo=read
    2. Diffuse functions for NLO: if Polar/CPHF are used, basis must have diffuse (+ or aug-)
    3. Polar=DCSHG for frequency-dependent hyperpolarizability
    4. Section ordering sanity
    """
    lines = raw_text.splitlines()

    # --- Extract the route section ---
    route_parts = []
    in_route = False
    route_line_num = 1
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("%"):
            continue
        if stripped.startswith("#"):
            in_route = True
            route_line_num = i + 1
            route_parts.append(re.sub(r"^#[pntPNT]?\s*", "", stripped))
            continue
        if in_route:
            if not stripped:
                break
            route_parts.append(stripped)
    full_route = " ".join(route_parts).upper()

    # --- Check 1: genecp vs gen ---
    # After coordinates, look for ECP specification patterns
    # If "SDD" or "LANL2DZ" or "CRENBL" appears in additional input sections,
    # the route should have GENECP (not GEN)
    has_gen = bool(re.search(r'\bGEN\b', full_route))
    has_genecp = bool(re.search(r'\bGENECP\b', full_route))
    has_pseudo_read = bool(re.search(r'PSEUDO\s*=\s*READ', full_route))

    # Check for ECP basis sets in the file content (after coordinates)
    ecp_patterns = ["SDD", "LANL2DZ", "LANL2MB", "CRENBL", "CRENBS", "SBKJC",
                     "MHF", "MDF", "STUTTGART", "CEP-4G", "CEP-31G", "CEP-121G"]
    has_ecp_in_body = False
    for ecp in ecp_patterns:
        if re.search(r'\b' + ecp + r'\b', raw_text, re.IGNORECASE):
            has_ecp_in_body = True
            break

    if has_gen and not has_genecp and not has_pseudo_read and has_ecp_in_body:
        issues.append(Issue(
            "ERROR", route_line_num,
            'Route uses "gen" but the file contains ECP basis sets (SDD/LANL2DZ/etc). '
            'You MUST use "genecp" (or "gen pseudo=read") instead of "gen". '
            'With plain "gen", Gaussian skips the ECP section and will crash or give '
            'nonsense results.',
        ))

    # --- Check 2: Diffuse functions for NLO ---
    has_polar = bool(re.search(r'\bPOLAR\b', full_route))
    has_cphf = bool(re.search(r'\bCPHF\b', full_route))
    is_nlo = has_polar or has_cphf

    if is_nlo:
        # Check if basis set has diffuse functions
        # Look for ++ or + in basis name, or aug- prefix
        # Extract the method/basis token from route
        basis_match = re.search(r'/(\S+)', full_route)
        basis_name = basis_match.group(1) if basis_match else ""

        has_diffuse = bool(re.search(r'\+', basis_name)) or \
                      bool(re.search(r'AUG', basis_name, re.IGNORECASE))

        # Also check in gen/genecp additional input for diffuse basis
        if not has_diffuse and (has_gen or has_genecp):
            # Check the body for diffuse basis sets
            if re.search(r'6-311\+\+?G', raw_text) or \
               re.search(r'aug-cc-', raw_text, re.IGNORECASE) or \
               re.search(r'6-31\+\+?G', raw_text):
                has_diffuse = True

        if not has_diffuse:
            issues.append(Issue(
                "ERROR", route_line_num,
                'NLO/polarizability calculation (Polar/CPHF) detected but NO diffuse '
                'functions found in the basis set. Hyperpolarizability and polarizability '
                'are extremely sensitive to diffuse functions — results without them are '
                'physically meaningless. Use 6-311++G(d,p), aug-cc-pVDZ, or similar.',
            ))

    # --- Check 3: Polar=DCSHG for frequency-dependent β ---
    has_rdfreq = bool(re.search(r'RDFREQ', full_route))
    has_dcshg = bool(re.search(r'POLAR\s*=\s*DCSHG', full_route))
    has_plain_polar = has_polar and not has_dcshg and \
                      not re.search(r'POLAR\s*=', full_route)

    # If CPHF=RdFreq but Polar is plain (no =DCSHG), warn about SHG
    if has_rdfreq and has_plain_polar:
        issues.append(Issue(
            "WARNING", route_line_num,
            'Route has "Polar" and "CPHF=RdFreq" but not "Polar=DCSHG". '
            'Plain "Polar" computes static polarizability/hyperpolarizability. '
            'For frequency-dependent first hyperpolarizability (SHG, e.g. at 1064nm), '
            'use "Polar=DCSHG". For second hyperpolarizability, use "Polar=Gamma".',
        ))


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------

def format_report(
    issues: list[Issue],
    parsed_tags: list[ParsedTag],
    software: str,
    input_path: str,
) -> str:
    """Format validation results as a human-readable report."""
    errors = [i for i in issues if i.severity == "ERROR"]
    warnings = [i for i in issues if i.severity == "WARNING"]

    # Count valid tags (total - errors)
    error_names = set()
    for iss in errors:
        m = re.search(r'"([^"]+)"', iss.message)
        if m:
            error_names.add(m.group(1))
    total_tags = len(parsed_tags)
    valid_tags = total_tags - len(error_names)

    if not errors and not warnings:
        status = "PASS"
    elif not errors:
        status = f"PASS with {len(warnings)} warning(s)"
    else:
        status = f"FAIL ({len(errors)} error(s), {len(warnings)} warning(s))"

    lines = [
        f"Validation: {software} -- {Path(input_path).name}",
        f"Status: {status}",
        f"Tags checked: {total_tags} total, {valid_tags} valid",
        "",
    ]

    if errors:
        lines.append("ERRORS:")
        for idx, e in enumerate(errors, 1):
            loc = f"Line {e.line_num}" if e.line_num > 0 else "N/A"
            lines.append(f"  [E{idx}] {loc}: {e.message}")
        lines.append("")

    if warnings:
        lines.append("WARNINGS:")
        for idx, w in enumerate(warnings, 1):
            loc = f"Line {w.line_num}" if w.line_num > 0 else "N/A"
            lines.append(f"  [W{idx}] {loc}: {w.message}")
        lines.append("")

    if not errors and not warnings:
        lines.append("All tags are valid and match the manual.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate an input file against its software manual."
    )
    parser.add_argument(
        "--input_file", required=True,
        help="Path to the input file to validate.",
    )
    parser.add_argument(
        "--software", required=True,
        help="Software name (e.g. VASP, CP2K, LAMMPS, QE, DeePMD-kit).",
    )
    parser.add_argument(
        "--data-dir",
        help="Override data directory path.",
    )
    parser.add_argument(
        "--paradigm",
        choices=list(_PARSER_MAP.keys()),
        help="Override auto-detected paradigm.",
    )

    args = parser.parse_args()

    # Load manual
    data_dir = Path(args.data_dir) if args.data_dir else None
    try:
        manual_path = _find_manual_path(args.software, data_dir)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    sw, params, extra_data = _load_params(manual_path)
    if not params:
        print(f"Manual for {args.software} is empty or could not be loaded.", file=sys.stderr)
        sys.exit(1)

    # Read input file
    input_path = Path(args.input_file)
    if not input_path.exists():
        print(f"Input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    with open(input_path, "r", encoding="utf-8", errors="replace") as f:
        input_text = f.read()

    # Detect paradigm and parse
    paradigm = args.paradigm or _detect_paradigm(args.software, str(input_path))
    parser_fn = _PARSER_MAP.get(paradigm)
    if parser_fn is None:
        print(f"No parser available for paradigm '{paradigm}'.", file=sys.stderr)
        sys.exit(1)

    parsed_tags = parser_fn(input_text)

    if not parsed_tags:
        print(f"No tags/keywords found in {input_path}. File may be empty or in an unexpected format.")
        sys.exit(0)

    # Validate
    issues = validate(parsed_tags, params, sw, paradigm, raw_text=input_text, extra_data=extra_data)

    # Report
    report = format_report(issues, parsed_tags, sw, str(input_path))
    print(report)

    # Exit code: 0 if pass, 1 if errors
    errors = [i for i in issues if i.severity == "ERROR"]
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
