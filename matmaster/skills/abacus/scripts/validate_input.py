#!/usr/bin/env python3
"""validate_input.py — Pre-flight validation for ABACUS input files.

Checks the most common silent-failure mistakes BEFORE Bohrium submission.
Run this after generating INPUT/STRU/KPT files and before calling Bohrium(submit).

Usage::

    python validate_input.py [--dir <workspace_dir>]

Reads all INPUT* files in the directory (or current dir) and validates against
the corresponding STRU/KPT files. Reports PASS/FAIL with actionable messages.

Exit code: 0 if all checks pass, 1 if any FAIL.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


def _load_reference_set(path: Path) -> set[str]:
    """Load a one-item-per-line reference list, skipping metadata files."""
    refs: set[str] = set()
    if not path.is_file():
        return refs
    for raw in path.read_text().splitlines():
        item = raw.strip()
        if not item or item.startswith("#"):
            continue
        lowered = item.lower()
        if lowered.endswith(".json") or lowered.endswith(".txt"):
            continue
        refs.add(item)
    return refs


def _extract_element(token: str) -> str:
    """Extract element symbol prefix from a filename-like token."""
    match = re.match(r"^([A-Z][a-z]?)", token.strip())
    return match.group(1) if match else ""


def _build_element_index(filenames: set[str]) -> dict[str, set[str]]:
    """Build element -> candidate filenames map from a reference set."""
    index: dict[str, set[str]] = {}
    for name in filenames:
        element = _extract_element(name)
        if not element:
            continue
        index.setdefault(element, set()).add(name)
    return index


def _parse_input(path: Path) -> dict[str, str]:
    """Parse an ABACUS INPUT file into a key-value dict."""
    params: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        if line.upper() == "INPUT_PARAMETERS":
            continue
        parts = line.split(None, 1)
        if len(parts) == 2:
            params[parts[0].lower()] = parts[1].strip()
        elif len(parts) == 1:
            params[parts[0].lower()] = ""
    return params


def _count_stru_species(
    stru_path: Path,
) -> tuple[int, bool, list[tuple[str, str]], list[tuple[str, str]]]:
    """Count species and return PP/orbital entries as (element, filename)."""
    text = stru_path.read_text()
    lines = text.splitlines()

    species_count = 0
    has_orbital = False
    pp_entries: list[tuple[str, str]] = []
    orbital_raw: list[str] = []
    species_order: list[str] = []

    in_atomic_species = False
    in_numerical_orbital = False

    for line in lines:
        stripped = line.strip()
        upper = stripped.upper()

        if upper == "ATOMIC_SPECIES":
            in_atomic_species = True
            in_numerical_orbital = False
            continue
        elif upper == "NUMERICAL_ORBITAL":
            in_atomic_species = False
            in_numerical_orbital = True
            has_orbital = True
            continue
        elif upper in (
            "LATTICE_CONSTANT",
            "LATTICE_VECTORS",
            "ATOMIC_POSITIONS",
        ):
            in_atomic_species = False
            in_numerical_orbital = False
            continue

        if in_atomic_species and stripped and not stripped.startswith("#"):
            parts = stripped.split()
            if len(parts) >= 3:
                species_count += 1
                species = parts[0]
                pp_file = parts[2]
                species_order.append(species)
                pp_entries.append((species, pp_file))

        if in_numerical_orbital and stripped and not stripped.startswith("#"):
            orbital_raw.append(stripped.split()[0])

    orb_entries: list[tuple[str, str]] = []
    if species_order and len(orbital_raw) == len(species_order):
        orb_entries = list(zip(species_order, orbital_raw))
    else:
        orb_entries = [("", orb) for orb in orbital_raw]

    return species_count, has_orbital, pp_entries, orb_entries


def validate_workspace(workspace: Path) -> list[str]:
    """Validate all INPUT* files in workspace. Returns list of messages."""
    messages: list[str] = []
    ref_dir = Path(__file__).resolve().parent.parent / "references"
    apns_pseudo_refs = _load_reference_set(ref_dir / "apns_pseudopotentials_v1.list")
    apns_orb_refs = _load_reference_set(ref_dir / "apns_orbitals_efficiency_v1.list")
    apns_pseudo_by_element = _build_element_index(apns_pseudo_refs)
    apns_orb_by_element = _build_element_index(apns_orb_refs)

    input_files = sorted(workspace.glob("INPUT*"))
    # Filter out non-files and backup files
    input_files = [f for f in input_files if f.is_file() and not f.name.endswith("~")]

    if not input_files:
        messages.append("FAIL: No INPUT files found in workspace.")
        return messages

    all_files = {f.name for f in workspace.iterdir() if f.is_file()}

    for input_file in input_files:
        prefix = f"[{input_file.name}]"
        try:
            params = _parse_input(input_file)
        except Exception as e:
            messages.append(f"FAIL {prefix}: Cannot parse INPUT: {e}")
            continue

        calc = params.get("calculation", "scf")
        pseudo_dir = params.get("pseudo_dir", "")
        orbital_dir = params.get("orbital_dir", "")
        using_apns_pseudo = "apns-pseudopotentials-v1" in pseudo_dir
        using_apns_orb = "apns-orbitals-efficiency-v1" in orbital_dir

        # --- Check stru_file / kpoint_file references ---
        stru_name = params.get("stru_file", "STRU")
        kpt_name = params.get("kpoint_file", "KPT")

        stru_path = workspace / stru_name
        if stru_name not in all_files:
            messages.append(
                f"FAIL {prefix}: stru_file '{stru_name}' not found. "
                f"Available files: {sorted(f for f in all_files if 'stru' in f.lower() or f == 'STRU')}"
            )
        elif stru_path.is_file():
            # Validate ntype
            species_count, has_orbital, pp_entries, orb_entries = _count_stru_species(
                stru_path
            )
            ntype_str = params.get("ntype", "")
            if ntype_str:
                try:
                    ntype_val = int(ntype_str)
                    if ntype_val != species_count:
                        messages.append(
                            f"FAIL {prefix}: ntype={ntype_val} but STRU has "
                            f"{species_count} species in ATOMIC_SPECIES."
                        )
                    else:
                        messages.append(
                            f"PASS {prefix}: ntype={ntype_val} matches STRU species count."
                        )
                except ValueError:
                    messages.append(
                        f"WARN {prefix}: ntype='{ntype_str}' is not an integer."
                    )

            # Validate basis_type vs NUMERICAL_ORBITAL
            basis = params.get("basis_type", "pw")
            if has_orbital and basis == "pw":
                messages.append(
                    f"WARN {prefix}: STRU has NUMERICAL_ORBITAL but basis_type=pw. "
                    f"Consider basis_type=lcao."
                )
            elif not has_orbital and basis == "lcao":
                messages.append(
                    f"FAIL {prefix}: basis_type=lcao but STRU has no NUMERICAL_ORBITAL section. "
                    f"ABACUS will crash."
                )
            else:
                messages.append(
                    f"PASS {prefix}: basis_type={basis} consistent with STRU."
                )

            # Check ecutwfc baseline
            ecutwfc_str = params.get("ecutwfc", "")
            if ecutwfc_str:
                try:
                    ecutwfc = float(ecutwfc_str)
                    if basis == "lcao" and ecutwfc < 50:
                        messages.append(
                            f"WARN {prefix}: ecutwfc={ecutwfc} is low for LCAO (standard: 100)."
                        )
                    elif basis == "pw" and ecutwfc < 30:
                        messages.append(
                            f"WARN {prefix}: ecutwfc={ecutwfc} is low for PW (standard: 50+). If this is a low-cost benchmark, this may be intentional."
                        )
                except ValueError:
                    pass

            # Check referenced PP/orbital files exist (workspace or configured runtime lists)
            for species, pp_file in pp_entries:
                pp_in_workspace = pp_file in all_files
                pp_in_apns_list = pp_file in apns_pseudo_refs
                if not pp_in_workspace and not (using_apns_pseudo and pp_in_apns_list):
                    messages.append(
                        f"FAIL {prefix}: STRU references pseudopotential '{pp_file}' but it is not found "
                        f"in workspace and not validated by APNS pseudopotential list."
                    )
                if using_apns_pseudo and not pp_in_apns_list:
                    messages.append(
                        f"FAIL {prefix}: pseudo_dir points to APNS, but '{pp_file}' is not in "
                        f"references/apns_pseudopotentials_v1.list."
                    )
                guessed_pp = bool(
                    re.fullmatch(rf"{re.escape(species)}\.upf", pp_file, re.IGNORECASE)
                )
                has_better_pp = any(
                    cand.lower() != pp_file.lower()
                    for cand in apns_pseudo_by_element.get(species, set())
                )
                if using_apns_pseudo and guessed_pp and has_better_pp:
                    messages.append(
                        f"FAIL {prefix}: '{pp_file}' looks guessed for element {species}; use the APNS "
                        f"filename listed in references/apns_pseudopotentials_v1.list."
                    )

            for species, orb_file in orb_entries:
                orb_in_workspace = orb_file in all_files
                orb_in_apns_list = orb_file in apns_orb_refs
                if not orb_in_workspace and not (using_apns_orb and orb_in_apns_list):
                    messages.append(
                        f"FAIL {prefix}: STRU references orbital '{orb_file}' but it is not found "
                        f"in workspace and not validated by APNS orbital list."
                    )
                if using_apns_orb and not orb_in_apns_list:
                    messages.append(
                        f"FAIL {prefix}: orbital_dir points to APNS, but '{orb_file}' is not in "
                        f"references/apns_orbitals_efficiency_v1.list."
                    )
                if species:
                    has_element_orb = species in apns_orb_by_element
                    guessed_orb = orb_file.lower().startswith(f"{species.lower()}_")
                    if (
                        using_apns_orb
                        and guessed_orb
                        and not orb_in_apns_list
                        and has_element_orb
                    ):
                        messages.append(
                            f"FAIL {prefix}: '{orb_file}' looks guessed for element {species}; use the APNS "
                            f"filename listed in references/apns_orbitals_efficiency_v1.list."
                        )

        # Check kpoint_file if not using kspacing
        has_kspacing = "kspacing" in params
        if not has_kspacing and kpt_name not in all_files:
            if calc != "md":  # MD doesn't always need KPT
                messages.append(
                    f"FAIL {prefix}: kpoint_file '{kpt_name}' not found and no kspacing set."
                )

        # --- Relaxation checks ---
        if calc in ("relax", "cell-relax"):
            if params.get("cal_force") != "1":
                messages.append(
                    f"FAIL {prefix}: calculation={calc} but cal_force is not 1. "
                    f"Forces will not be computed — relaxation silently broken."
                )
            else:
                messages.append(f"PASS {prefix}: cal_force=1 for {calc}.")

            if "force_thr" in params and "force_thr_ev" not in params:
                messages.append(
                    f"WARN {prefix}: Using force_thr (Ry/Bohr) instead of force_thr_ev (eV/Å). "
                    f"Are units correct?"
                )

            if "relax_nmax" not in params:
                messages.append(
                    f"WARN {prefix}: relax_nmax not set for {calc}. Default may be too low."
                )

        if calc == "cell-relax":
            if params.get("cal_stress") != "1":
                messages.append(
                    f"FAIL {prefix}: calculation=cell-relax but cal_stress is not 1. "
                    f"Cell vectors will not be optimized."
                )
            else:
                messages.append(f"PASS {prefix}: cal_stress=1 for cell-relax.")

        # --- NSCF checks ---
        if calc == "nscf":
            if params.get("init_chg") != "file":
                messages.append(
                    f"FAIL {prefix}: calculation=nscf but init_chg is not 'file'. "
                    f"NSCF will re-run SCF from scratch."
                )
            if params.get("symmetry", "1") != "0":
                messages.append(
                    f"WARN {prefix}: calculation=nscf but symmetry is not 0. "
                    f"K-path may be folded for band structure."
                )
            if "nbands" not in params:
                messages.append(
                    f"WARN {prefix}: calculation=nscf but nbands not set. "
                    f"May not compute enough bands."
                )
            if params.get("out_band") != "1" and params.get("out_dos") != "1":
                messages.append(
                    f"WARN {prefix}: calculation=nscf but neither out_band nor out_dos is set."
                )

        # --- SCF feeding NSCF check ---
        if calc == "scf" and params.get("out_chg") != "1":
            # Check if there's a companion NSCF INPUT
            nscf_inputs = (
                [
                    f
                    for f in input_files
                    if f != input_file
                    and "nscf" in _parse_input(f).get("calculation", "").lower()
                ]
                if len(input_files) > 1
                else []
            )
            if nscf_inputs:
                messages.append(
                    f"FAIL {prefix}: SCF with companion NSCF but out_chg is not 1. "
                    f"NSCF will not find charge density."
                )

        # --- Non-default filename check ---
        stru_files_in_dir = [f for f in all_files if "stru" in f.lower() or f == "STRU"]
        kpt_files_in_dir = [f for f in all_files if "kpt" in f.lower() or f == "KPT"]

        if len(stru_files_in_dir) > 1 and "stru_file" not in params:
            messages.append(
                f"WARN {prefix}: Multiple STRU-like files ({stru_files_in_dir}) but no stru_file set. "
                f"ABACUS will use 'STRU' by default."
            )
        if (
            len(kpt_files_in_dir) > 1
            and "kpoint_file" not in params
            and not has_kspacing
        ):
            messages.append(
                f"WARN {prefix}: Multiple KPT-like files ({kpt_files_in_dir}) but no kpoint_file set. "
                f"ABACUS will use 'KPT' by default."
            )

    # --- Check run.sh if present ---
    run_sh = workspace / "run.sh"
    if run_sh.is_file():
        content = run_sh.read_text()
        messages.append(f"PASS: run.sh found ({len(content)} bytes).")
        # Check that it references existing files
        for match in re.findall(r"cp\s+(\S+)\s+", content):
            if match not in all_files and not match.startswith("$"):
                messages.append(
                    f"WARN [run.sh]: 'cp {match} ...' but '{match}' not found in workspace."
                )

    return messages


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate ABACUS input files before submission."
    )
    parser.add_argument(
        "--dir",
        default=".",
        help="Workspace directory containing INPUT/STRU/KPT files (default: current dir)",
    )
    args = parser.parse_args()

    workspace = Path(args.dir).resolve()
    if not workspace.is_dir():
        print(f"ERROR: '{workspace}' is not a directory.", file=sys.stderr)
        sys.exit(1)

    messages = validate_workspace(workspace)

    has_fail = False
    for msg in messages:
        if msg.startswith("FAIL"):
            has_fail = True
            print(f"❌ {msg}")
        elif msg.startswith("WARN"):
            print(f"⚠️  {msg}")
        else:
            print(f"✅ {msg}")

    print()
    if has_fail:
        print("RESULT: VALIDATION FAILED — fix the issues above before submitting.")
        sys.exit(1)
    else:
        print("RESULT: All checks passed.")
        sys.exit(0)


if __name__ == "__main__":
    main()
