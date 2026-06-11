"""Matplotlib styling assets for the plotting plugin.

Import from the plugin's shared directory inside the sandbox:

    import sys

    sys.path.insert(0, "<plugin dir>/shared")  # skills give the real path
    import mm_style

    mm_style.apply()

All colors come from the nine-ramp palette in style-contract.md (the single
source of truth). Light-theme trio: 50 fill / 600 stroke and series lines /
800 title text / 600 secondary text.
"""

from __future__ import annotations

import matplotlib
from cycler import cycler

STOPS = (50, 100, 200, 400, 600, 800, 900)

RAMPS: dict[str, tuple[str, ...]] = {
    "purple": (
        "#EEEDFE",
        "#CECBF6",
        "#AFA9EC",
        "#7F77DD",
        "#534AB7",
        "#3C3489",
        "#26215C",
    ),
    "teal": (
        "#E1F5EE",
        "#9FE1CB",
        "#5DCAA5",
        "#1D9E75",
        "#0F6E56",
        "#085041",
        "#04342C",
    ),
    "coral": (
        "#FAECE7",
        "#F5C4B3",
        "#F0997B",
        "#D85A30",
        "#993C1D",
        "#712B13",
        "#4A1B0C",
    ),
    "pink": (
        "#FBEAF0",
        "#F4C0D1",
        "#ED93B1",
        "#D4537E",
        "#993556",
        "#72243E",
        "#4B1528",
    ),
    "gray": (
        "#F1EFE8",
        "#D3D1C7",
        "#B4B2A9",
        "#888780",
        "#5F5E5A",
        "#444441",
        "#2C2C2A",
    ),
    "blue": (
        "#E6F1FB",
        "#B5D4F4",
        "#85B7EB",
        "#378ADD",
        "#185FA5",
        "#0C447C",
        "#042C53",
    ),
    "green": (
        "#EAF3DE",
        "#C0DD97",
        "#97C459",
        "#639922",
        "#3B6D11",
        "#27500A",
        "#173404",
    ),
    "amber": (
        "#FAEEDA",
        "#FAC775",
        "#EF9F27",
        "#BA7517",
        "#854F0B",
        "#633806",
        "#412402",
    ),
    "red": (
        "#FCEBEB",
        "#F7C1C1",
        "#F09595",
        "#E24B4A",
        "#A32D2D",
        "#791F1F",
        "#501313",
    ),
}

# Preferred order for generic categorical series; blue/green/amber/red are
# reserved for info/success/warning/error semantics (style-contract.md).
CATEGORY_ORDER = ("purple", "teal", "coral", "pink")

_LINESTYLES = ("-", "--", "-.", ":")

# Noto hits first in the sandbox image; the rest cover local dev machines.
CJK_FONT_STACK = [
    "Noto Sans CJK SC",
    "PingFang SC",
    "Hiragino Sans GB",
    "Microsoft YaHei",
    "DejaVu Sans",
]


def stop(ramp: str, value: int) -> str:
    return RAMPS[ramp][STOPS.index(value)]


def fill(ramp: str) -> str:
    return stop(ramp, 50)


def stroke(ramp: str) -> str:
    return stop(ramp, 600)


def title_color(ramp: str) -> str:
    return stop(ramp, 800)


def hbar_figsize(n_bars: int, width: float = 8.0) -> tuple[float, float]:
    """Horizontal bar chart size: height = n x 0.4 in + fixed margins."""
    return (width, n_bars * 0.4 + 1.2)


def restyle(ax, width: float = 8.0, height: float = 5.0):
    """Re-impose contract text sizes on an Axes that a plotter restyled.

    pymatgen plotters route through pretty_plot, which overrides rcParams
    with 30-48pt text and its own figure size after apply() ran. Call this
    on the returned Axes, recolor lines as needed, then save_figure.
    """
    fig = ax.figure
    fig.set_size_inches(width, height)
    ax.title.set_size(14.0)
    for axis_label in (ax.xaxis.label, ax.yaxis.label):
        axis_label.set_size(14.0)
    ax.tick_params(axis="both", which="both", labelsize=12.0)
    legend = ax.get_legend()
    if legend is not None:
        for text in legend.get_texts():
            text.set_size(12.0)
    for text in ax.texts:
        text.set_size(12.0)
    return ax


def apply() -> None:
    """Apply the plugin rcParams: CJK-capable fonts, sizes, palette, grid."""
    rc = matplotlib.rcParams
    rc["font.family"] = "sans-serif"
    rc["font.sans-serif"] = CJK_FONT_STACK
    rc["axes.unicode_minus"] = False
    rc["font.size"] = 12.0
    rc["axes.titlesize"] = 14.0
    rc["axes.titleweight"] = "medium"
    rc["axes.labelsize"] = 14.0
    rc["xtick.labelsize"] = 12.0
    rc["ytick.labelsize"] = 12.0
    rc["legend.fontsize"] = 12.0
    rc["figure.titlesize"] = 14.0
    rc["figure.titleweight"] = "medium"
    rc["text.color"] = title_color("gray")
    rc["axes.titlecolor"] = title_color("gray")
    rc["axes.labelcolor"] = stroke("gray")
    rc["xtick.color"] = stroke("gray")
    rc["ytick.color"] = stroke("gray")
    rc["legend.labelcolor"] = stroke("gray")
    rc["axes.prop_cycle"] = cycler(
        color=[stroke(ramp) for ramp in CATEGORY_ORDER]
    ) + cycler(linestyle=list(_LINESTYLES))
    rc["lines.linewidth"] = 1.8
    rc["axes.grid"] = True
    rc["grid.color"] = stop("gray", 200)
    rc["grid.linewidth"] = 0.5
    rc["axes.spines.top"] = False
    rc["axes.spines.right"] = False
    rc["axes.edgecolor"] = stop("gray", 600)
    rc["axes.linewidth"] = 0.8
    rc["legend.frameon"] = False
    rc["figure.facecolor"] = "white"
    rc["axes.facecolor"] = "white"
    rc["savefig.facecolor"] = "white"
    rc["savefig.dpi"] = 200


def save_figure(fig, path: str) -> str:
    """Save with the contract's mandatory white opaque background settings."""
    fig.savefig(path, facecolor="white", dpi=200, bbox_inches="tight")
    return path
