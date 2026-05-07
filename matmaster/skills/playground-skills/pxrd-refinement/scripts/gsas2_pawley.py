#!/usr/bin/env python3
"""
gsas2_pawley.py — CLI wrapper for GSAS-II Pawley refinement of PXRD data.

The implementation is split across sibling modules to keep each source file
small enough for the repository line-count check:

- ``gsas2_pawley_cell.py``: lattice conversion and cell standardisation.
- ``gsas2_pawley_core.py``: GSAS-II setup, single-pattern refinement, parsing.
- ``gsas2_pawley_workflows.py``: directory / wide-csv / chain-cell workflows.

When staging for Bohrium, copy ``gsas2_pawley*.py`` plus ``curation.py`` into
the same flat input directory.

Usage:
  # Single pattern:
  python gsas2_pawley.py \
    --data pattern.xye --space-group "F d -3 m" \
    --cell "a=5.43,b=5.43,c=5.43" --wavelength 1.5406 -o result.json

  # Directory of patterns (e.g. one condition per file):
  python gsas2_pawley.py \
    --data /path/to/patterns/ --space-group "<SG>" \
    --cell "a=<A>,b=<B>,c=<C>,beta=<BETA>" -o results.json

  # Wide-table CSV (paired angle/intensity columns in one file):
  python gsas2_pawley.py \
    --data multi_temp.txt --wide-csv --space-group "<SG>" \
    --cell "a=<A>,b=<B>,c=<C>,beta=<BETA>" -o results.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gsas2_pawley_cell import (  # noqa: E402,F401
    cell_to_lattice,
    lattice_to_cell,
    niggli_reduce_cell,
    standardize_cell,
)
from gsas2_pawley_core import (  # noqa: E402,F401
    DEFAULT_GSAS2_PATH,
    cell_volume,
    pick_best_candidate,
    refine_one_pattern,
    setup_gsas2,
)
from gsas2_pawley_workflows import (  # noqa: E402,F401
    merge_chain_directions,
    run_directory,
    run_single,
    run_wide_csv,
    self_heal_chain_outliers,
)


def main() -> None:
    print("[gsas2_pawley] booting argv=", sys.argv, flush=True)
    ap = argparse.ArgumentParser(
        description="GSAS-II Pawley refinement for PXRD data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--data",
        required=True,
        nargs="+",
        help="PXRD data input. Accepts: (1) one .xye/.xy/.dat/.csv file "
        "(single-pattern mode), (2) one directory of patterns (chain-cell "
        "mode, files auto-globbed), (3) two or more files (chain-cell mode, "
        "files used in argv order — convenient when bash glob expansion "
        "passes them in directly), or (4) one wide-table CSV with --wide-csv. "
        "Mixing files and directories in (3) is rejected.",
    )
    ap.add_argument(
        "--space-group",
        required=True,
        help='GSAS-II space group string, e.g. "P 21/c" or "P n m a"',
    )
    ap.add_argument(
        "--cell",
        required=True,
        help='Initial lattice params (e.g. "a=10.0,b=9.5,c=8.2,beta=99.0")',
    )
    ap.add_argument(
        "--wavelength",
        type=float,
        default=1.5406,
        help="X-ray wavelength in Å (default: Cu Kα1 = 1.5406)",
    )
    ap.add_argument("--dmin", type=float, default=2.0)
    ap.add_argument("--dmax", type=float, default=None)
    ap.add_argument("--tmin", type=float, default=None)
    ap.add_argument("--tmax", type=float, default=None)
    ap.add_argument(
        "--two-theta-range",
        nargs=2,
        type=float,
        metavar=("TMIN", "TMAX"),
        default=None,
        help="Alias for --tmin TMIN --tmax TMAX. Accepted to avoid wasting "
        "remote submissions on common wrapper spelling.",
    )
    ap.add_argument("--instprm", default=None)
    ap.add_argument(
        "--gsas2-path",
        default=DEFAULT_GSAS2_PATH,
        help=f"Path to GSAS-II GSASII directory (default: {DEFAULT_GSAS2_PATH})",
    )
    ap.add_argument("--wide-csv", action="store_true")
    ap.add_argument("--chain-cell", action="store_true")
    ap.add_argument(
        "--chain-cell-direction",
        choices=["forward", "reverse", "both"],
        default="forward",
    )
    ap.add_argument("--chain-wr-max", type=float, default=25.0)
    ap.add_argument("--chain-vol-jump-max", type=float, default=0.03)
    ap.add_argument("--multi-start", type=int, default=1)
    ap.add_argument("--multi-start-seed", type=int, default=42)
    ap.add_argument("--multi-start-len-sigma", type=float, default=0.005)
    ap.add_argument("--multi-start-ang-sigma", type=float, default=0.5)
    ap.add_argument("--debug-plot", default=None)
    ap.add_argument(
        "--curation-mode",
        choices=["off", "auto", "strict"],
        default="auto",
    )
    ap.add_argument(
        "--baseline-method",
        choices=["piecewise_linear", "linear", "mor", "none"],
        default="piecewise_linear",
    )
    ap.add_argument(
        "--standardize-cell",
        choices=["ref", "niggli"],
        default=None,
        help="Post-refinement cell standardisation: 'ref' aligns to the "
        "initial cell via axis-permutation search; 'niggli' additionally "
        "Niggli-reduces (requires spglib) before aligning. Default: off.",
    )
    ap.add_argument(
        "--self-heal-chain",
        dest="self_heal_chain",
        action="store_true",
        default=True,
        help="After a multi-pattern --chain-cell run, scan the result list for "
        "outliers whose volume jumps >--self-heal-v-jump-threshold from the "
        "average of their immediate successful neighbours. Re-refine each "
        "outlier in-process with --self-heal-multi-start restarts and a "
        "neighbour-average cell as initial guess; replace the original only "
        "if the retry lands closer to the neighbour average. Cost: each rescued "
        "pattern adds roughly one K-start single-pattern refinement "
        "(K = --self-heal-multi-start, default 5). Default: on.",
    )
    ap.add_argument(
        "--no-self-heal-chain",
        dest="self_heal_chain",
        action="store_false",
        help="Disable post-chain outlier rescue.",
    )
    ap.add_argument(
        "--self-heal-v-jump-threshold",
        type=float,
        default=0.02,
        help="Relative volume jump above which a chain element is treated as "
        "an outlier candidate for self-healing (default: 0.02 = 2%%).",
    )
    ap.add_argument(
        "--self-heal-multi-start",
        type=int,
        default=5,
        help="Multi-start budget used by the rescue refinement on each "
        "outlier (default: 5).",
    )
    ap.add_argument("-o", "--output", help="Write JSON output to this file")
    args = ap.parse_args()
    if args.two_theta_range is not None:
        if args.tmin is None:
            args.tmin = args.two_theta_range[0]
        if args.tmax is None:
            args.tmax = args.two_theta_range[1]

    setup_gsas2(args.gsas2_path)

    data_paths = [Path(p) for p in args.data]
    args._explicit_files = None

    if args.wide_csv:
        if len(data_paths) != 1:
            print(
                json.dumps(
                    {"success": False, "error": "--wide-csv accepts exactly one path"}
                ),
            )
            sys.exit(1)
        if not data_paths[0].is_file():
            print(
                json.dumps(
                    {
                        "success": False,
                        "error": f"Wide-CSV file not found: {args.data[0]}",
                    }
                ),
            )
            sys.exit(1)
        args.data = str(data_paths[0])
        mode = "wide_csv"
    elif len(data_paths) == 1:
        p = data_paths[0]
        if not (p.is_dir() or p.is_file()):
            print(
                json.dumps({"success": False, "error": f"Not found: {p}"}),
            )
            sys.exit(1)
        args.data = str(p)
        mode = "directory" if p.is_dir() else "single"
    else:
        for p in data_paths:
            if not p.is_file():
                print(
                    json.dumps(
                        {
                            "success": False,
                            "error": (
                                f"Multi-file --data mode requires every path to be a "
                                f"file; not a file: {p}"
                            ),
                        }
                    ),
                )
                sys.exit(1)
        ordered: list[Path] = []
        seen: set[Path] = set()
        for p in (candidate.resolve() for candidate in data_paths):
            if p not in seen:
                seen.add(p)
                ordered.append(p)
        args._explicit_files = ordered
        args.data = str(args._explicit_files[0].parent)
        mode = "directory"

    with redirect_stdout(sys.stderr):
        if mode == "wide_csv":
            result = run_wide_csv(args)
        elif mode == "directory":
            result = run_directory(args)
        else:
            result = run_single(args)

    json_str = json.dumps(result, indent=2, ensure_ascii=False)
    print(json_str)
    if args.output:
        Path(args.output).write_text(json_str, encoding="utf-8")
        print(f"Saved to {args.output}", file=sys.stderr)
    print("[gsas2_pawley] done success=", result.get("success"), flush=True)


if __name__ == "__main__":
    main()
