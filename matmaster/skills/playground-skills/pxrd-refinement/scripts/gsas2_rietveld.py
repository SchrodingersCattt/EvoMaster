#!/usr/bin/env python3
"""
gsas2_rietveld.py — GSAS-II Rietveld refinement for PXRD data.

Refines a crystal structure against powder XRD data using GSAS-II full-pattern
Rietveld refinement. Requires a CIF file as the starting structural model.
Outputs cell parameters, R-factors, atomic parameters, and optionally a
refined CIF file.

GSAS-II path: /root/g2full/GSAS-II/GSASII  (override with --gsas2-path)

Refinement levels:
  basic    — background + scale + cell + peak shape (no atomic params)
  standard — basic + atomic coordinates + isotropic Uiso  [default]
  full     — standard + occupancy factors + anisotropic Uani

Usage:
  python gsas2_rietveld.py \\
    --data pattern.xye \\
    --cif structure.cif \\
    --wavelength 1.5406 \\
    --refine-level standard \\
    --export-cif refined.cif \\
    -o result.json

Output JSON:
  {
    "success": true,
    "file": "pattern.xye",
    "cell": {
      "a": 10.826, "b": 10.172, "c": 9.197,
      "alpha": 90.0, "beta": 99.07, "gamma": 90.0,
      "volume": 1000.2
    },
    "cell_esd": {"a": 0.001, "b": 0.002, ...},
    "r_factors": {"Rp": 8.5, "Rwp": 11.2, "GOF": 1.3},
    "n_atoms": 24,
    "cif_file": "refined.cif"
  }
"""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

import numpy as np

DEFAULT_GSAS2_PATH = "/root/g2full/GSAS-II/GSASII"

DEFAULT_INSTPRM = """\
#GSAS-II instrument parameter file; do not add/delete items!
Type: PXC
Bank: 1
Lam: 1.5406
Polariz.: 0.99
Azimuth: 0.0
Zero: 0.0
U: 2.0
V: -2.0
W: 5.0
X: 0.0
Y: 0.0
Z: 0.0
SH/L: 0.002
"""


def setup_gsas2(gsas2_path: str) -> None:
    if gsas2_path not in sys.path:
        sys.path.insert(0, gsas2_path)


def make_instprm_file(wavelength: float, tmpdir: str) -> str:
    content = DEFAULT_INSTPRM.replace("Lam: 1.5406", f"Lam: {wavelength:.6f}")
    path = os.path.join(tmpdir, "instrument.instprm")
    with open(path, "w") as f:
        f.write(content)
    return path


def preprocess_to_xye(
    two_theta: np.ndarray,
    intensity: np.ndarray,
    outpath: str,
) -> None:
    """Subtract baseline, scale to reasonable count level, write .xye."""
    baseline = np.percentile(intensity, 5)
    y = (intensity - baseline) * 1e4
    y = np.maximum(y, 1.0)
    sigma = np.sqrt(y)
    np.savetxt(outpath, np.column_stack([two_theta, y, sigma]), fmt="%.7f")


def read_xy_file(filepath: str) -> tuple[np.ndarray, np.ndarray]:
    """Read a two-column (2theta, intensity) text file."""
    import re

    data = []
    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("!"):
                continue
            parts = re.split(r"[,\t\s]+", line)
            try:
                vals = [float(p) for p in parts if p]
                if len(vals) >= 2:
                    data.append((vals[0], vals[1]))
            except ValueError:
                continue
    if not data:
        raise ValueError(f"No numeric data in {filepath}")
    arr = np.array(data)
    return arr[:, 0], arr[:, 1]


def _safe_refine(gpx, step_name: str, verbose: bool = True) -> bool:
    """Run one refinement cycle; return True if successful."""
    try:
        gpx.do_refinements([{}])
        return True
    except Exception as exc:
        if verbose:
            print(f"  [warn] {step_name}: {exc}", file=sys.stderr)
        return False


def run_rietveld(
    data_file: str,
    cif_file: str,
    wavelength: float,
    refine_level: str,
    two_theta_min: float,
    two_theta_max: float,
    instprm_path: str,
    export_cif: str | None,
    workdir: str,
) -> dict:
    """
    Run GSAS-II Rietveld refinement.

    Args:
        data_file: Path to .xye file (already preprocessed).
        cif_file: Path to starting structure CIF.
        refine_level: 'basic', 'standard', or 'full'.
        export_cif: If set, write refined structure to this path.
        workdir: Temporary directory for GSAS-II project files.

    Returns:
        Result dict with cell, R-factors, atoms info.
    """
    import GSASIIscriptable as G2sc

    G2sc.SetPrintLevel("warn")

    gpx_path = os.path.join(workdir, "rietveld.gpx")
    gpx = G2sc.G2Project(newgpx=gpx_path)

    hist = gpx.add_powder_histogram(data_file, iparams=instprm_path)
    phase = gpx.add_phase(
        phasefile=cif_file,
        histograms=[hist],
    )

    hist.set_refinements({"Limits": [two_theta_min, two_theta_max]})

    gpx.set_Controls("cycles", 10)

    # ── Round 1: Background + phase fraction ────────────────────────────
    hist.set_refinements({"Background": {"no. coeffs": 6, "refine": True}})
    phase.set_HAP_refinements({"Scale": True})
    _safe_refine(gpx, "Background+Scale")

    # ── Round 2: Cell parameters ─────────────────────────────────────────
    phase.set_refinements({"Cell": True})
    _safe_refine(gpx, "Cell")

    # ── Round 3: Peak shape (Caglioti U, V, W) ───────────────────────────
    hist.set_refinements({"Instrument Parameters": ["U", "V", "W"]})
    _safe_refine(gpx, "UVW")

    # ── Round 4: Zero-point + asymmetry ──────────────────────────────────
    hist.set_refinements({"Instrument Parameters": ["Zero", "SH/L"]})
    _safe_refine(gpx, "Zero+SH/L")

    if refine_level == "basic":
        # Converge with basic params only
        for _ in range(3):
            _safe_refine(gpx, "converge")
    else:
        # ── Round 5: Atomic coordinates + Uiso ─────────────────────────────
        phase.set_refinements({"Atoms": {"all": "XU"}})
        _safe_refine(gpx, "Atoms XU")

        # A few convergence cycles
        for _ in range(3):
            _safe_refine(gpx, "converge-1")

        if refine_level == "full":
            # ── Round 6: Occupancy + anisotropic Uani ──────────────────────
            try:
                phase.set_refinements({"Atoms": {"all": "FXU"}})
                _safe_refine(gpx, "Atoms FXU")
            except Exception:
                pass

            # ── Round 7: Try anisotropic Uani if symmetry allows ───────────
            try:
                phase.set_refinements({"Atoms": {"all": "FXUA"}})
                _safe_refine(gpx, "Atoms FXUA")
            except Exception:
                pass

        # Final convergence
        hist.set_refinements({"Background": {"no. coeffs": 12, "refine": True}})
        for _ in range(5):
            _safe_refine(gpx, "converge-final")

    # ── Extract results ──────────────────────────────────────────────────
    cell = phase.get_cell()
    try:
        cell_esd_data = phase.get_cell_and_esd()
        # get_cell_and_esd returns (cell_dict, esd_dict) with keys like 'length_a'
        if isinstance(cell_esd_data, (tuple, list)) and len(cell_esd_data) >= 2:
            esd_raw = cell_esd_data[1]
            key_map = {
                "a": "length_a",
                "b": "length_b",
                "c": "length_c",
                "alpha": "angle_alpha",
                "beta": "angle_beta",
                "gamma": "angle_gamma",
            }
            cell_esd = {
                p: round(float(esd_raw.get(k, 0.0) or 0.0), 6)
                for p, k in key_map.items()
            }
        else:
            cell_esd = {}
    except Exception:
        cell_esd = {}

    wR = hist.get_wR()

    # R-factors from residuals dict (keys: R, Rb, wR, wRb, wRmin)
    try:
        res = hist.residuals
        rp = round(res.get("R", 0.0), 3) if res else None
        rwp = round(res.get("wR", 0.0), 3) if res else None
    except Exception:
        rp = rwp = None

    # Atom list
    atoms_out = []
    try:
        for atom in phase.atoms():
            atoms_out.append(
                {
                    "label": atom.label,
                    "element": atom.element,
                    "x": round(atom.coordinates[0], 5),
                    "y": round(atom.coordinates[1], 5),
                    "z": round(atom.coordinates[2], 5),
                    "occ": round(atom.occupancy, 4),
                    "uiso": round(atom.uiso, 5),
                }
            )
    except Exception:
        pass

    # Export refined CIF
    cif_out = None
    if export_cif:
        try:
            phase.export_CIF(export_cif)
            cif_out = export_cif
        except Exception as exc:
            print(f"  [warn] CIF export failed: {exc}", file=sys.stderr)

    gpx.save()

    result = {
        "success": True,
        "refine_level": refine_level,
        "cell": {
            "a": round(cell["length_a"], 5),
            "b": round(cell["length_b"], 5),
            "c": round(cell["length_c"], 5),
            "alpha": round(cell["angle_alpha"], 4),
            "beta": round(cell["angle_beta"], 4),
            "gamma": round(cell["angle_gamma"], 4),
            "volume": round(cell["volume"], 4),
        },
        "cell_esd": cell_esd,
        "r_factors": {
            "Rp": rp,
            "Rwp": rwp,
            "wR": round(wR, 3) if wR is not None else None,
        },
        "n_atoms": len(atoms_out),
        "atoms": atoms_out,
    }
    if cif_out:
        result["cif_file"] = cif_out

    return result


def main() -> None:
    ap = argparse.ArgumentParser(
        description="GSAS-II Rietveld refinement for PXRD data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--data",
        required=True,
        help="PXRD data file (.xye/.xy/.dat/.csv)",
    )
    ap.add_argument(
        "--cif",
        required=True,
        help="Starting structural model CIF file",
    )
    ap.add_argument(
        "--wavelength",
        type=float,
        default=1.5406,
        help="X-ray wavelength in Å (default: Cu Kα1 = 1.5406)",
    )
    ap.add_argument(
        "--refine-level",
        choices=["basic", "standard", "full"],
        default="standard",
        help=(
            "Refinement depth: "
            "basic=cell+background+peakshape; "
            "standard=+atom coords+Uiso; "
            "full=+occupancy+Uani (default: standard)"
        ),
    )
    ap.add_argument(
        "--tmin",
        type=float,
        default=8.0,
        help="Lower 2θ limit for refinement (default: 8.0°)",
    )
    ap.add_argument(
        "--tmax",
        type=float,
        default=50.0,
        help="Upper 2θ limit for refinement (default: 50.0°)",
    )
    ap.add_argument(
        "--instprm",
        default=None,
        help="Path to GSAS-II instrument parameter file (default: auto Cu Kα)",
    )
    ap.add_argument(
        "--gsas2-path",
        default=DEFAULT_GSAS2_PATH,
        help=f"Path to GSAS-II GSASII directory (default: {DEFAULT_GSAS2_PATH})",
    )
    ap.add_argument(
        "--export-cif",
        default=None,
        help="Write refined structure to this CIF file",
    )
    ap.add_argument("-o", "--output", help="Write JSON output to this file")
    args = ap.parse_args()

    setup_gsas2(args.gsas2_path)

    import GSASIIscriptable  # noqa: F401 — verify import works

    with tempfile.TemporaryDirectory() as tmpdir:
        instprm = args.instprm or make_instprm_file(args.wavelength, tmpdir)

        # Pre-process data into xye in workdir
        data_path = Path(args.data)
        if data_path.suffix.lower() in (".xye",):
            # Already in correct format — pass through
            xye_path = str(data_path)
        else:
            two_theta, intensity = read_xy_file(str(data_path))
            xye_path = os.path.join(tmpdir, data_path.stem + ".xye")
            preprocess_to_xye(two_theta, intensity, xye_path)

        try:
            result = run_rietveld(
                data_file=xye_path,
                cif_file=args.cif,
                wavelength=args.wavelength,
                refine_level=args.refine_level,
                two_theta_min=args.tmin,
                two_theta_max=args.tmax,
                instprm_path=instprm,
                export_cif=args.export_cif,
                workdir=tmpdir,
            )
        except Exception as exc:
            result = {"success": False, "error": str(exc)}

    result["file"] = args.data
    result["cif"] = args.cif

    json_str = json.dumps(result, indent=2, ensure_ascii=False)
    print(json_str)
    if args.output:
        Path(args.output).write_text(json_str, encoding="utf-8")
        print(f"Saved to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
