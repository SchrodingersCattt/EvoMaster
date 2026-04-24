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
    warnings: list[str] | None = None,
) -> dict:
    """
    Adaptive preprocessing — see gsas2_pawley.preprocess_to_xye for rationale.

    For dynamic range < 10 (DFT-style flat data), subtract 5th-percentile
    baseline and scale by 1e4. Otherwise pass intensity through unchanged
    (real experimental counts already have a meaningful Poisson sigma).
    """
    if warnings is None:
        warnings = []
    p5 = float(np.percentile(intensity, 5))
    pmax = float(np.max(intensity))
    pmin = float(np.min(intensity))
    denom = max(p5, 1e-9)
    dyn_range = (pmax - pmin) / denom if denom > 0 else float("inf")

    if dyn_range < 10.0:
        y = (intensity - p5) * 1e4
        mode = "dft_scaled"
        info = {"baseline": p5, "scale": 1e4}
        warnings.append(
            f"preprocess: low dynamic range ({dyn_range:.2f}) detected, "
            f"applied baseline subtraction (-{p5:.4f}) and scale ×10000"
        )
    else:
        y = intensity.astype(float)
        mode = "passthrough"
        info = {"baseline": 0.0, "scale": 1.0}

    y = np.maximum(y, 1.0)
    sigma = np.sqrt(y)
    np.savetxt(outpath, np.column_stack([two_theta, y, sigma]), fmt="%.7f")
    return {"mode": mode, "dynamic_range": round(dyn_range, 3), **info}


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


def _safe_refine(
    gpx, step_name: str, warnings: list[str], verbose: bool = True
) -> bool:
    """Run one refinement cycle; return True on success, log on failure."""
    try:
        gpx.do_refinements([{}])
        return True
    except Exception as exc:
        msg = f"refine step '{step_name}' raised {type(exc).__name__}: {exc}"
        warnings.append(msg)
        if verbose:
            print(f"  [warn] {msg}", file=sys.stderr)
        return False


def run_rietveld(
    data_file: str,
    cif_file: str,
    wavelength: float,
    refine_level: str,
    two_theta_min: float | None,
    two_theta_max: float | None,
    instprm_path: str,
    export_cif: str | None,
    workdir: str,
    warnings: list[str] | None = None,
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

    if warnings is None:
        warnings = []

    G2sc.SetPrintLevel("warn")

    gpx_path = os.path.join(workdir, "rietveld.gpx")
    gpx = G2sc.G2Project(newgpx=gpx_path)

    hist = gpx.add_powder_histogram(data_file, iparams=instprm_path)
    phase = gpx.add_phase(
        phasefile=cif_file,
        histograms=[hist],
    )

    # Default to full data range when limits unspecified.
    xdata = hist.getdata("x")
    lim_lo = float(two_theta_min) if two_theta_min is not None else float(xdata.min())
    lim_hi = float(two_theta_max) if two_theta_max is not None else float(xdata.max())
    hist.set_refinements({"Limits": [lim_lo, lim_hi]})

    gpx.set_Controls("cycles", 10)

    hist.set_refinements({"Background": {"no. coeffs": 6, "refine": True}})
    phase.set_HAP_refinements({"Scale": True})
    _safe_refine(gpx, "Background+Scale", warnings)

    phase.set_refinements({"Cell": True})
    _safe_refine(gpx, "Cell", warnings)

    hist.set_refinements({"Instrument Parameters": ["U", "V", "W"]})
    _safe_refine(gpx, "UVW", warnings)

    hist.set_refinements({"Instrument Parameters": ["Zero", "SH/L"]})
    _safe_refine(gpx, "Zero+SH/L", warnings)

    if refine_level == "basic":
        for i in range(3):
            _safe_refine(gpx, f"converge_{i + 1}", warnings)
    else:
        phase.set_refinements({"Atoms": {"all": "XU"}})
        _safe_refine(gpx, "Atoms XU", warnings)

        for i in range(3):
            _safe_refine(gpx, f"converge_post_atoms_{i + 1}", warnings)

        if refine_level == "full":
            try:
                phase.set_refinements({"Atoms": {"all": "FXU"}})
                _safe_refine(gpx, "Atoms FXU", warnings)
            except Exception as exc:
                warnings.append(f"set 'FXU' refine flag failed: {exc}")

            try:
                phase.set_refinements({"Atoms": {"all": "FXUA"}})
                _safe_refine(gpx, "Atoms FXUA", warnings)
            except Exception as exc:
                warnings.append(f"set 'FXUA' refine flag failed: {exc}")

        hist.set_refinements({"Background": {"no. coeffs": 12, "refine": True}})
        for i in range(5):
            _safe_refine(gpx, f"converge_final_{i + 1}", warnings)

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

    cif_out = None
    if export_cif:
        try:
            phase.export_CIF(export_cif)
            cif_out = export_cif
        except Exception as exc:
            warnings.append(f"CIF export failed: {exc}")
            print(f"  [warn] CIF export failed: {exc}", file=sys.stderr)

    gpx.save()

    if wR is not None and wR > 25.0:
        warnings.append(
            f"high wR ({wR:.2f}%); structure or peak-shape model may be wrong"
        )

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
        "limits": [round(lim_lo, 4), round(lim_hi, 4)],
        "n_atoms": len(atoms_out),
        "atoms": atoms_out,
        "warnings": warnings,
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
        default=None,
        help="Lower 2θ limit for refinement (default: None = full data range)",
    )
    ap.add_argument(
        "--tmax",
        type=float,
        default=None,
        help="Upper 2θ limit for refinement (default: None = full data range)",
    )
    ap.add_argument(
        "--instprm",
        default=None,
        help="Path to GSAS-II instrument parameter file. Default auto-generates "
        "a Cu Kα template; for lab diffractometers you SHOULD provide one "
        "calibrated against a standard.",
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

    # GSAS-II writes progress/SVD warnings to stdout via bare print(); we
    # swap stdout to stderr during refinement so the final JSON is clean.
    real_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            instprm = args.instprm or make_instprm_file(args.wavelength, tmpdir)

            warnings: list[str] = []
            data_path = Path(args.data)
            if data_path.suffix.lower() in (".xye",):
                xye_path = str(data_path)
                preprocess_info = {"mode": "passthrough_xye", "scale": 1.0}
            else:
                two_theta, intensity = read_xy_file(str(data_path))
                xye_path = os.path.join(tmpdir, data_path.stem + ".xye")
                preprocess_info = preprocess_to_xye(
                    two_theta, intensity, xye_path, warnings
                )

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
                    warnings=warnings,
                )
                result["preprocess"] = preprocess_info
            except Exception as exc:
                result = {
                    "success": False,
                    "error": str(exc),
                    "preprocess": preprocess_info,
                    "warnings": warnings,
                }
    finally:
        sys.stdout = real_stdout

    result["file"] = args.data
    result["cif"] = args.cif

    json_str = json.dumps(result, indent=2, ensure_ascii=False)
    print(json_str)
    if args.output:
        Path(args.output).write_text(json_str, encoding="utf-8")
        print(f"Saved to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
