"""
Parse ABACUS calculation output files and extract key results.

Usage:
  python parse_abacus.py --dir OUT.ABACUS --type scf
  python parse_abacus.py --dir OUT.ABACUS --type band --fermi 5.43
  python parse_abacus.py --dir OUT.ABACUS --type dos
  python parse_abacus.py --dir /path/to/result_dir --type all

Output: JSON to stdout with extracted properties.

Supports: SCF (total energy, Fermi energy, convergence, forces, stress, magnetization),
          Band structure (band gap, VBM, CBM, metallicity),
          DOS (energy range, data points).
"""

import argparse
import json
import re
import sys
from pathlib import Path


def parse_scf_log(log_path: Path) -> dict:
    """Parse ABACUS running log for SCF results."""
    result = {
        "converged": False,
        "total_energy_eV": None,
        "fermi_energy_eV": None,
        "n_scf_steps": 0,
        "total_magnetization": None,
        "max_force_eV_A": None,
        "warnings": [],
    }
    if not log_path.exists():
        result["warnings"].append(f"Log file not found: {log_path}")
        return result

    text = log_path.read_text(errors="ignore")

    # Total energy: !FINAL_ETOT_IS <energy> eV
    m = re.findall(r"!FINAL_ETOT_IS\s+([-\d.eE+]+)\s*eV", text)
    if m:
        result["total_energy_eV"] = float(m[-1])
    else:
        # Fallback: E_KS(e) or total energy lines
        m = re.findall(r"E_KS\(e\)\s*(?:=|:)\s*([-\d.eE+]+)", text)
        if m:
            result["total_energy_eV"] = float(m[-1])

    # Fermi energy: EFERMI = <energy> eV
    m = re.findall(r"EFERMI\s*=\s*([-\d.eE+]+)\s*eV", text)
    if m:
        result["fermi_energy_eV"] = float(m[-1])
    else:
        m = re.findall(r"E_Fermi\s*=\s*([-\d.eE+]+)", text)
        if m:
            result["fermi_energy_eV"] = float(m[-1])

    # Convergence
    if "charge density convergence is achieved" in text.lower() or "!FINAL_ETOT_IS" in text:
        result["converged"] = True

    # SCF iteration count
    steps = re.findall(r"ELEC\s*=\s*(\d+)", text)
    if steps:
        result["n_scf_steps"] = max(int(s) for s in steps)

    # Magnetization
    m = re.findall(r"total\s+magnetization\s*(?:=|:)\s*([-\d.eE+]+)", text, re.IGNORECASE)
    if m:
        result["total_magnetization"] = float(m[-1])

    # Forces: look for max force value
    force_vals = re.findall(r"(?:LARGEST GRADIENT|MAX_FORCE)\s*=?\s*([-\d.eE+]+)", text, re.IGNORECASE)
    if force_vals:
        result["max_force_eV_A"] = float(force_vals[-1])
    else:
        # Try to parse force block
        force_block = re.findall(
            r"TOTAL-FORCE \(eV/Angstrom\)\s*\n-+\n((?:\s*\S+\s+[-\d.eE+]+\s+[-\d.eE+]+\s+[-\d.eE+]+\s*\n)+)",
            text,
        )
        if force_block:
            forces = []
            for line in force_block[-1].strip().split("\n"):
                parts = line.split()
                if len(parts) >= 4:
                    try:
                        fx, fy, fz = float(parts[1]), float(parts[2]), float(parts[3])
                        forces.append((fx**2 + fy**2 + fz**2) ** 0.5)
                    except (ValueError, IndexError):
                        pass
            if forces:
                result["max_force_eV_A"] = max(forces)

    return result


def parse_bands(bands_path: Path, fermi_eV: float = None) -> dict:
    """Parse BANDS_1.dat for band edges and gap."""
    result = {
        "band_gap_eV": None,
        "vbm_eV": None,
        "cbm_eV": None,
        "is_metal": None,
        "n_kpoints": 0,
        "n_bands": 0,
        "warnings": [],
    }
    if not bands_path.exists():
        result["warnings"].append(f"Bands file not found: {bands_path}")
        return result

    try:
        lines = [l.strip() for l in bands_path.read_text().strip().split("\n") if l.strip()]

        # ABACUS BANDS_1.dat format:
        # Line 1: n_bands n_kpoints
        # Subsequent lines: kpoint_index  eigenvalue_1  eigenvalue_2  ...
        all_eigenvalues = []
        n_bands = 0
        n_kpoints = 0

        header_parts = lines[0].split()
        if len(header_parts) == 2:
            try:
                n_bands = int(header_parts[0])
                n_kpoints = int(header_parts[1])
            except ValueError:
                pass

        result["n_bands"] = n_bands
        result["n_kpoints"] = n_kpoints

        for line in lines[1:]:
            parts = line.split()
            if len(parts) < 2:
                continue
            try:
                # Skip the first column (k-point index or k-coordinate)
                vals = [float(x) for x in parts]
                # Heuristic: if first value looks like an index (integer), skip it
                if n_bands > 0 and len(vals) >= n_bands + 1:
                    eigvals = vals[1:]
                elif len(vals) >= 2:
                    eigvals = vals[1:] if vals[0] == int(vals[0]) else vals
                else:
                    eigvals = vals
                all_eigenvalues.append(eigvals)
            except ValueError:
                continue

        if all_eigenvalues and fermi_eV is not None:
            # Flatten all eigenvalues and find VBM/CBM
            all_eig = []
            for row in all_eigenvalues:
                all_eig.extend(row)

            below = [e for e in all_eig if e <= fermi_eV + 0.001]
            above = [e for e in all_eig if e > fermi_eV + 0.001]

            if below and above:
                vbm = max(below)
                cbm = min(above)
                gap = cbm - vbm
                result["vbm_eV"] = round(vbm, 6)
                result["cbm_eV"] = round(cbm, 6)
                result["band_gap_eV"] = round(max(0.0, gap), 6)
                result["is_metal"] = gap < 0.01
            else:
                result["is_metal"] = True
                result["band_gap_eV"] = 0.0
                result["warnings"].append(
                    "All eigenvalues on one side of Fermi level; system is metallic."
                )
        elif all_eigenvalues:
            result["warnings"].append(
                "Fermi energy not provided. Use --fermi <eV> or parse SCF first."
            )

    except Exception as e:
        result["warnings"].append(f"Error parsing bands: {e}")

    return result


def parse_dos(dos_path: Path) -> dict:
    """Parse DOS1_smearing.dat for DOS features."""
    result = {
        "dos_file": str(dos_path),
        "energy_range_eV": None,
        "n_energy_points": 0,
        "warnings": [],
    }
    if not dos_path.exists():
        result["warnings"].append(f"DOS file not found: {dos_path}")
        return result

    try:
        energies = []
        for line in dos_path.read_text().strip().split("\n"):
            if line.startswith("#") or line.startswith("!"):
                continue
            parts = line.split()
            if len(parts) >= 2:
                try:
                    energies.append(float(parts[0]))
                except ValueError:
                    continue
        if energies:
            result["energy_range_eV"] = [round(min(energies), 4), round(max(energies), 4)]
            result["n_energy_points"] = len(energies)
    except Exception as e:
        result["warnings"].append(f"Error parsing DOS: {e}")

    return result


def find_output_dir(base_dir: Path) -> Path:
    """Find ABACUS output directory."""
    out_abacus = base_dir / "OUT.ABACUS"
    if out_abacus.exists():
        return out_abacus
    # Check subdirectories
    for d in sorted(base_dir.iterdir()):
        if d.is_dir() and d.name.startswith("OUT."):
            return d
    # If base_dir itself contains log files, use it
    if (base_dir / "running_scf.log").exists():
        return base_dir
    return base_dir


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Parse ABACUS output files to JSON. Supports SCF, band structure, and DOS."
    )
    ap.add_argument(
        "--dir",
        required=True,
        help="Path to ABACUS output directory (OUT.ABACUS) or Bohrium result directory",
    )
    ap.add_argument(
        "--type",
        required=True,
        choices=["scf", "band", "dos", "all"],
        help="What to parse: scf, band, dos, or all",
    )
    ap.add_argument(
        "--fermi",
        type=float,
        default=None,
        help="Fermi energy in eV for band gap calculation (auto-detected from SCF if omitted)",
    )
    args = ap.parse_args()

    base = Path(args.dir)
    if not base.exists():
        print(json.dumps({"error": f"Directory not found: {base}"}), file=sys.stderr)
        sys.exit(1)

    out_dir = find_output_dir(base)
    result = {"output_dir": str(out_dir)}
    fermi = args.fermi

    if args.type in ("scf", "all"):
        scf_parsed = False
        for log_name in [
            "running_scf.log",
            "running_nscf.log",
            "running_relax.log",
            "running_cell-relax.log",
            "running_md.log",
        ]:
            log_path = out_dir / log_name
            if log_path.exists():
                result["scf"] = parse_scf_log(log_path)
                result["scf"]["log_file"] = log_name
                if fermi is None and result["scf"].get("fermi_energy_eV") is not None:
                    fermi = result["scf"]["fermi_energy_eV"]
                scf_parsed = True
                break
        if not scf_parsed:
            # Try top-level 'log' file (Bohrium unified log)
            log_path = base / "log"
            if log_path.exists():
                result["scf"] = parse_scf_log(log_path)
                result["scf"]["log_file"] = "log"
                if fermi is None and result["scf"].get("fermi_energy_eV") is not None:
                    fermi = result["scf"]["fermi_energy_eV"]
            else:
                result["scf"] = {"warning": "No ABACUS log file found in " + str(out_dir)}

    if args.type in ("band", "all"):
        bands_path = out_dir / "BANDS_1.dat"
        result["band"] = parse_bands(bands_path, fermi)

    if args.type in ("dos", "all"):
        dos_path = out_dir / "DOS1_smearing.dat"
        result["dos"] = parse_dos(dos_path)

    # List available output files for reference
    if args.type == "all":
        try:
            result["available_files"] = sorted(
                str(f.relative_to(out_dir)) for f in out_dir.iterdir() if f.is_file()
            )
        except Exception:
            pass

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
