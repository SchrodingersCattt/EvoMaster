"""
Generate publication-quality figures from parsed JSON (no commercial-software output).

Usage:
  python plot_publication.py --data convergence.json --plot_type convergence --output fig.png
  python plot_publication.py --data eos.json --plot_type eos --output eos.png

Plot types: convergence (energy/force vs step), eos (energy vs volume). Data from JSON only.
Defaults: DPI=300, single column width 3.25 inch, Arial/Helvetica, minimal style.
"""

import argparse
import json
import sys
from pathlib import Path

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
except ImportError:
    plt = None
    np = None


# Publication defaults
DPI = 300
SINGLE_COLUMN_WIDTH = 3.25  # inch
DOUBLE_COLUMN_WIDTH = 6.5
FONT_SIZE = 10
FIG_SIZE_SINGLE = (SINGLE_COLUMN_WIDTH, 3.0)


def _setup_style():
    if plt is None:
        return
    try:
        plt.style.use("seaborn-v0_8-paper")
    except OSError:
        try:
            plt.style.use("seaborn-paper")
        except OSError:
            pass
    plt.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans"],
        "font.size": FONT_SIZE,
        "axes.labelsize": FONT_SIZE,
        "axes.titlesize": FONT_SIZE + 1,
        "xtick.labelsize": FONT_SIZE - 1,
        "ytick.labelsize": FONT_SIZE - 1,
        "figure.dpi": DPI,
        "savefig.dpi": DPI,
        "savefig.bbox": "tight",
    })


def plot_convergence(data_path: Path, output_path: Path) -> str:
    """Plot energy/force vs step from JSON. JSON should have lists: steps, energies, [forces]."""
    if plt is None:
        return "Error: matplotlib not installed"
    with open(data_path, "r") as f:
        data = json.load(f)
    steps = data.get("steps", data.get("step", []))
    energies = data.get("energies", data.get("energy", []))
    forces = data.get("forces", data.get("max_force", []))
    if not steps and energies:
        steps = list(range(len(energies)))
    if not steps:
        return "Error: no steps/energies in data"
    _setup_style()
    fig, ax1 = plt.subplots(figsize=FIG_SIZE_SINGLE)
    ax1.plot(steps, energies, color="C0", linewidth=1.2, label="Energy")
    ax1.set_xlabel("Step")
    ax1.set_ylabel("Energy (eV)")
    ax1.legend(loc="upper right", fontsize=FONT_SIZE - 1)
    if forces and len(forces) == len(steps):
        ax2 = ax1.twinx()
        ax2.plot(steps, forces, color="C1", linewidth=1.2, alpha=0.8, label="Max force")
        ax2.set_ylabel("Max force (eV/Å)")
        ax2.legend(loc="right", fontsize=FONT_SIZE - 1)
    out = str(output_path)
    fig.savefig(out, dpi=DPI, bbox_inches="tight")
    pdf_path = output_path.with_suffix(".pdf")
    fig.savefig(str(pdf_path), dpi=DPI, bbox_inches="tight")
    plt.close()
    return f"Saved {out} and {pdf_path}"


def plot_eos(data_path: Path, output_path: Path) -> str:
    """Plot Energy vs Volume (EOS). JSON: volumes, energies."""
    if plt is None:
        return "Error: matplotlib not installed"
    with open(data_path, "r") as f:
        data = json.load(f)
    volumes = data.get("volumes", data.get("v", []))
    energies = data.get("energies", data.get("energy", data.get("e", [])))
    if not volumes or not energies:
        return "Error: provide volumes and energies in JSON"
    _setup_style()
    fig, ax = plt.subplots(figsize=FIG_SIZE_SINGLE)
    ax.plot(volumes, energies, "o-", color="C0", markersize=4, linewidth=1.2)
    ax.set_xlabel("Volume (Å³)")
    ax.set_ylabel("Energy (eV)")
    out = str(output_path)
    fig.savefig(out, dpi=DPI, bbox_inches="tight")
    fig.savefig(str(output_path.with_suffix(".pdf")), dpi=DPI, bbox_inches="tight")
    plt.close()
    return f"Saved {out}"


def main() -> None:
    ap = argparse.ArgumentParser(description="Publication-quality plots from JSON (convergence, eos).")
    ap.add_argument("--data", required=True, help="Path to JSON (e.g. convergence.json, eos.json)")
    ap.add_argument("--plot_type", required=True, choices=["convergence", "eos"])
    ap.add_argument("--output", default="fig.png", help="Output figure path")
    args = ap.parse_args()
    data_path = Path(args.data)
    if not data_path.exists():
        print("Error: data file not found", file=sys.stderr)
        sys.exit(1)
    output_path = Path(args.output)
    if args.plot_type == "convergence":
        msg = plot_convergence(data_path, output_path)
    else:
        msg = plot_eos(data_path, output_path)
    if msg.startswith("Error"):
        print(msg, file=sys.stderr)
        sys.exit(1)
    print(msg)


if __name__ == "__main__":
    main()
