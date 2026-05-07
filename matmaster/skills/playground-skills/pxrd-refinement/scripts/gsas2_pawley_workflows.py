"""CLI workflow modes and chain-cell helpers for gsas2_pawley.py."""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

import numpy as np

from gsas2_pawley_core import (  # noqa: E402
    cell_dict_to_list,
    cell_volume,
    make_instprm_file,
    parse_cell_string,
    parse_wide_csv,
    read_xy_file,
    refine_one_pattern,
)

def _refine_kwargs_from_args(args) -> dict:
    """Common keyword args shared by run_single / run_directory / run_wide_csv."""
    return {
        "wavelength": args.wavelength,
        "dmin": args.dmin,
        "dmax": args.dmax,
        "two_theta_min": args.tmin,
        "two_theta_max": args.tmax,
        "debug_plot": args.debug_plot,
        "curation_mode": args.curation_mode,
        "baseline_method": args.baseline_method,
        "multi_start": args.multi_start,
        "multi_start_seed": args.multi_start_seed,
        "multi_start_len_sigma": args.multi_start_len_sigma,
        "multi_start_ang_sigma": args.multi_start_ang_sigma,
        "standardize_cell_mode": getattr(args, "standardize_cell", None),
    }


def _accept_chain_promotion(
    prev: dict | None,
    curr: dict,
    wr_max: float,
    vol_jump_max: float,
) -> tuple[bool, str]:
    """Quality gate for cell promotion across a chained temperature series."""
    if not curr.get("success"):
        return False, "current refinement failed"
    wr = curr.get("wR")
    if wr is None or wr > wr_max:
        return False, f"wR={wr} exceeds gate {wr_max}"
    if prev is not None and prev.get("success"):
        v_prev = prev.get("volume")
        v_curr = curr.get("volume")
        if v_prev and v_curr:
            jump = abs(v_curr - v_prev) / v_prev
            if jump > vol_jump_max:
                return False, f"ΔV/V={jump:.3f} exceeds gate {vol_jump_max}"
    return True, "ok"


def _maybe_promote_cell(
    args,
    last_accepted: dict | None,
    curr: dict,
) -> tuple[list[float] | None, dict | None, str]:
    """Decide whether curr should be promoted to seed the next pattern."""
    if not args.chain_cell:
        return None, last_accepted, "chain disabled"
    accept, reason = _accept_chain_promotion(
        last_accepted, curr, args.chain_wr_max, args.chain_vol_jump_max
    )
    if not accept:
        return None, last_accepted, reason
    next_cell = [
        curr["a"],
        curr["b"],
        curr["c"],
        curr["alpha"],
        curr["beta"],
        curr["gamma"],
    ]
    return next_cell, curr, reason


def _chain_anchor_volume(args, last_accepted: dict | None) -> float | None:
    """Volume anchor passed into the multi-start picker for chain-cell runs."""
    if not args.chain_cell or not last_accepted or not last_accepted.get("success"):
        return None
    volume = last_accepted.get("volume")
    return float(volume) if volume is not None else None


def _clone_args_with_direction(args, direction: str):
    values = vars(args).copy()
    values["chain_cell_direction"] = direction
    return argparse.Namespace(**values)


_BOTH_OFF_REF_WR_GATE = 10.0
_BOTH_OFF_REF_DV_FRACTION = 0.01
_BOTH_OFF_REF_CELL_FRACTION = 0.01


def _relative_cell_distance(result: dict, ref_cell: list[float] | None) -> float | None:
    """L1 sum of relative differences over a/b/c plus any non-90° angle.

    Volume is intentionally NOT included: V can match by chance even when
    individual axes diverge (in monoclinic, V = a·b·c·sin(β), so
    different (a, c, β) combinations can give the same volume). This
    metric is the discriminating signal that V proximity misses.
    """
    if ref_cell is None or len(ref_cell) < 6:
        return None
    if any(result.get(k) is None for k in ("a", "b", "c")):
        return None
    d = 0.0
    for i, k in enumerate(("a", "b", "c")):
        if ref_cell[i] > 0:
            d += abs(float(result[k]) - ref_cell[i]) / ref_cell[i]
    for i, k in enumerate(("alpha", "beta", "gamma"), start=3):
        ref_ang = ref_cell[i]
        if abs(ref_ang - 90.0) > 0.5 and result.get(k) is not None and ref_ang > 0:
            d += abs(float(result[k]) - ref_ang) / ref_ang
    return d


def _pick_chain_merge_candidate(
    forward: dict,
    reverse: dict,
    reference_volume: float,
    reference_cell: list[float] | None = None,
    high_wr: float = 10.0,
    wr_tie: float = 3.0,
) -> tuple[dict, dict]:
    """Pick one forward/reverse result using the PXRD merge contract."""
    f_ok = forward.get("success")
    r_ok = reverse.get("success")
    f_wr = forward.get("wR")
    r_wr = reverse.get("wR")
    f_vol = forward.get("volume")
    r_vol = reverse.get("volume")
    f_dv = abs(float(f_vol) - reference_volume) if f_vol is not None else None
    r_dv = abs(float(r_vol) - reference_volume) if r_vol is not None else None
    f_cd = _relative_cell_distance(forward, reference_cell)
    r_cd = _relative_cell_distance(reverse, reference_cell)

    if f_ok and not r_ok:
        source, reason = "forward", "reverse failed"
    elif r_ok and not f_ok:
        source, reason = "reverse", "forward failed"
    elif not f_ok and not r_ok:
        source, reason = "forward", "both failed"
    elif (
        f_wr is not None
        and r_wr is not None
        and f_wr > high_wr
        and r_wr > high_wr
        and abs(f_wr - r_wr) < wr_tie
    ):
        if f_cd is not None and r_cd is not None:
            source = "forward" if f_cd <= r_cd else "reverse"
            reason = "both high-wR/tied; picked closer to reference cell"
        elif f_dv is not None and r_dv is not None:
            source = "forward" if f_dv <= r_dv else "reverse"
            reason = "both high-wR/tied; picked closer to reference volume"
        else:
            source = "forward" if (f_wr or 0) <= (r_wr or 0) else "reverse"
            reason = "both high-wR/tied; missing cell/volume; kept lower wR"
    elif f_wr is not None and r_wr is not None:
        source = "forward" if f_wr <= r_wr else "reverse"
        reason = "picked lower wR"
    else:
        source = "forward"
        reason = "missing wR; kept forward"

    warning: str | None = None
    if (
        f_ok
        and r_ok
        and f_wr is not None
        and r_wr is not None
        and f_wr > _BOTH_OFF_REF_WR_GATE
        and r_wr > _BOTH_OFF_REF_WR_GATE
    ):
        v_off = (
            reference_volume > 0
            and f_dv is not None
            and r_dv is not None
            and (f_dv / reference_volume) > _BOTH_OFF_REF_DV_FRACTION
            and (r_dv / reference_volume) > _BOTH_OFF_REF_DV_FRACTION
        )
        cell_off = (
            f_cd is not None
            and r_cd is not None
            and f_cd > _BOTH_OFF_REF_CELL_FRACTION
            and r_cd > _BOTH_OFF_REF_CELL_FRACTION
        )
        if v_off or cell_off:
            warning = "both_directions_off_ref"

    chosen = dict(forward if source == "forward" else reverse)
    chosen["merge_source"] = source
    chosen["merge_reason"] = reason
    if warning:
        chosen["merge_warning"] = warning
    table_row = {
        "file": forward.get("file") or reverse.get("file"),
        "temp_c": forward.get("temp_c", reverse.get("temp_c")),
        "temp_label": forward.get("temp_label", reverse.get("temp_label")),
        "wR_forward": f_wr,
        "V_forward": f_vol,
        "dV_ref_forward": f_dv,
        "cell_dist_forward": f_cd,
        "wR_reverse": r_wr,
        "V_reverse": r_vol,
        "dV_ref_reverse": r_dv,
        "cell_dist_reverse": r_cd,
        "chosen": source,
        "reason": reason,
        "warning": warning,
    }
    return chosen, table_row


def merge_chain_directions(
    forward_results: list[dict],
    reverse_results: list[dict],
    reference_volume: float,
    reference_cell: list[float] | None = None,
) -> tuple[list[dict], dict]:
    if len(forward_results) != len(reverse_results):
        raise ValueError(
            "forward/reverse chain result lengths differ: "
            f"{len(forward_results)} != {len(reverse_results)}"
        )

    merged: list[dict] = []
    table: list[dict] = []
    for fwd, rev in zip(forward_results, reverse_results):
        chosen, row = _pick_chain_merge_candidate(
            fwd, rev, reference_volume, reference_cell=reference_cell
        )
        merged.append(chosen)
        table.append(row)
    warnings = [
        {
            "file": row.get("file"),
            "issue": row["warning"],
            "wR_forward": row.get("wR_forward"),
            "wR_reverse": row.get("wR_reverse"),
            "dV_ref_forward": row.get("dV_ref_forward"),
            "dV_ref_reverse": row.get("dV_ref_reverse"),
            "cell_dist_forward": row.get("cell_dist_forward"),
            "cell_dist_reverse": row.get("cell_dist_reverse"),
        }
        for row in table
        if row.get("warning")
    ]
    audit: dict = {
        "reference_volume": round(float(reference_volume), 4),
        "table": table,
        "warnings": warnings,
    }
    if reference_cell is not None and len(reference_cell) >= 6:
        audit["reference_cell"] = [round(float(x), 5) for x in reference_cell[:6]]
    return merged, audit


# ---------------------------------------------------------------------------
# Chain self-healing (post-chain outlier rescue)
# ---------------------------------------------------------------------------


def _result_volume(r: dict | None) -> float | None:
    if not r or not r.get("success"):
        return None
    v = r.get("volume")
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _result_cell_list(r: dict | None) -> list[float] | None:
    if not r or not r.get("success"):
        return None
    try:
        return [
            float(r["a"]),
            float(r["b"]),
            float(r["c"]),
            float(r["alpha"]),
            float(r["beta"]),
            float(r["gamma"]),
        ]
    except (KeyError, TypeError, ValueError):
        return None


_SELF_HEAL_GOOD_WR_GATE = 10.0
_SELF_HEAL_NEIGHBOUR_RADIUS = 2
_SELF_HEAL_CELL_TOL = 0.005


def self_heal_chain_outliers(
    chain_results: list[dict],
    args,
    reference_cell: list[float],
    reference_volume: float,
    v_jump_threshold: float,
    multi_start: int,
) -> tuple[list[dict], dict]:
    """Post-chain rescue for single-pattern wrong-basin convergence.

    For each chain element whose refined volume drifts ``> v_jump_threshold``
    from the volume target derived from its trustworthy neighbours,
    re-refine that pattern in-process with a higher ``multi_start`` budget
    and a robust initial cell guess.  Replace the chain result only if the
    retry both (a) lands closer in volume to the neighbour target than the
    original AND (b) does not move further away from the reference cell
    shape.  The latter guard prevents a single-pattern rescue from picking
    a wrong-basin retry whose volume happens to match the (also-wrong)
    neighbour mean but whose ``a/b/c/β`` are degenerate alternatives — a
    real failure mode for monoclinic / orthorhombic settings.

    Init / target selection (in priority order):
      1. low-wR (< ``_SELF_HEAL_GOOD_WR_GATE``) successful neighbours within
         radius ``_SELF_HEAL_NEIGHBOUR_RADIUS``.
      2. if none qualify, the reference cell / volume passed in.

    The helper does NOT consume any chain-position metadata (e.g. temperature)
    beyond list order; it operates purely on the order in which patterns were
    chained, which is what ``--chain-cell`` already encodes.
    """
    n = len(chain_results)
    if n < 3:
        return list(chain_results), {
            "v_jump_threshold": v_jump_threshold,
            "multi_start": multi_start,
            "outliers": [],
            "skipped_reason": f"chain too short ({n}<3) for neighbour rescue",
        }

    healed = list(chain_results)
    audit_entries: list[dict] = []

    successes = [
        (i, _result_volume(r), _result_cell_list(r)) for i, r in enumerate(healed)
    ]
    successes = [(i, v, c) for i, v, c in successes if v is not None and c is not None]
    if len(successes) < 2:
        return healed, {
            "v_jump_threshold": v_jump_threshold,
            "multi_start": multi_start,
            "outliers": [],
            "skipped_reason": "fewer than 2 successful chain elements",
        }

    success_by_idx = {i: (v, c) for i, v, c in successes}

    def _trustworthy_neighbours(idx: int) -> list[tuple[int, float, list[float]]]:
        out: list[tuple[int, float, list[float]]] = []
        for j, v, c in successes:
            if j == idx:
                continue
            if abs(j - idx) > _SELF_HEAL_NEIGHBOUR_RADIUS:
                continue
            wr = healed[j].get("wR")
            if wr is None or wr >= _SELF_HEAL_GOOD_WR_GATE:
                continue
            out.append((j, v, c))
        return out

    def _immediate_neighbours(idx: int) -> list[tuple[int, float, list[float]]]:
        return [
            (j, *success_by_idx[j])
            for j in (idx - 1, idx + 1)
            if j in success_by_idx and j != idx
        ]

    with tempfile.TemporaryDirectory() as tmpdir:
        instprm = args.instprm or make_instprm_file(args.wavelength, tmpdir)
        for idx in range(n):
            v_orig = _result_volume(healed[idx])
            cell_orig = _result_cell_list(healed[idx])
            if v_orig is None or cell_orig is None:
                continue

            jump_neigh = _immediate_neighbours(idx)
            if not jump_neigh:
                continue
            jump_target = float(np.mean([v for _, v, _ in jump_neigh]))
            rel = abs(v_orig - jump_target) / max(abs(jump_target), 1e-9)
            if rel <= v_jump_threshold:
                continue

            fpath = healed[idx].get("file") or ""
            audit_row: dict = {
                "file": fpath,
                "v_original": round(v_orig, 4),
                "v_neighbour_target": round(jump_target, 4),
                "rel_jump": round(rel, 4),
            }

            trust = _trustworthy_neighbours(idx)
            if trust:
                init_cell = np.array([c for _, _, c in trust]).mean(axis=0).tolist()
                v_target = float(np.mean([v for _, v, _ in trust]))
                init_source = (
                    f"low-wR neighbours within radius {_SELF_HEAL_NEIGHBOUR_RADIUS}"
                )
            else:
                init_cell = list(reference_cell)
                v_target = float(reference_volume)
                init_source = "reference cell (no low-wR neighbour)"
            audit_row["init_source"] = init_source
            audit_row["init_cell"] = [round(x, 5) for x in init_cell]
            audit_row["v_target"] = round(v_target, 4)

            if not fpath or not Path(fpath).is_file():
                audit_row["decision"] = "skipped_no_file"
                audit_entries.append(audit_row)
                continue

            try:
                two_theta, intensity = read_xy_file(fpath)
                kwargs = _refine_kwargs_from_args(args)
                kwargs["multi_start"] = multi_start
                retry = refine_one_pattern(
                    two_theta=two_theta,
                    intensity=intensity,
                    space_group=args.space_group,
                    cell_list=init_cell,
                    instprm_path=instprm,
                    workdir=tmpdir,
                    label=Path(fpath).stem + "_self_heal",
                    anchor_volume=v_target,
                    anchor_max_jump=max(
                        args.chain_vol_jump_max, v_jump_threshold * 1.5
                    ),
                    reference_volume=reference_volume,
                    **kwargs,
                )
                retry["file"] = fpath
                retry["self_heal_origin"] = "chain_outlier_retry"
                retry_v = _result_volume(retry)
                retry_cell = _result_cell_list(retry)
                if retry_v is None or retry_cell is None:
                    audit_row["decision"] = "skipped_retry_failed"
                    audit_row["retry_error"] = retry.get("error") or "no volume"
                    audit_entries.append(audit_row)
                    continue
                rel_retry = abs(retry_v - v_target) / max(abs(v_target), 1e-9)
                cell_dist_orig = _relative_cell_distance(healed[idx], reference_cell)
                cell_dist_retry = _relative_cell_distance(retry, reference_cell)
                audit_row["v_retry"] = round(retry_v, 4)
                audit_row["rel_retry"] = round(rel_retry, 4)
                audit_row["wR_original"] = healed[idx].get("wR")
                audit_row["wR_retry"] = retry.get("wR")
                audit_row["cell_dist_original"] = (
                    None if cell_dist_orig is None else round(cell_dist_orig, 5)
                )
                audit_row["cell_dist_retry"] = (
                    None if cell_dist_retry is None else round(cell_dist_retry, 5)
                )
                v_better = rel_retry < rel
                cell_not_worse = (
                    cell_dist_retry is None
                    or cell_dist_orig is None
                    or cell_dist_retry <= cell_dist_orig + _SELF_HEAL_CELL_TOL
                )
                if v_better and cell_not_worse:
                    audit_row["decision"] = "replaced"
                    healed[idx] = retry
                elif v_better and not cell_not_worse:
                    audit_row["decision"] = "kept_chain_cell_drift"
                else:
                    audit_row["decision"] = "kept_chain"
            except Exception as exc:
                audit_row["decision"] = "skipped_retry_exception"
                audit_row["retry_error"] = str(exc)
            audit_entries.append(audit_row)

    return healed, {
        "v_jump_threshold": v_jump_threshold,
        "multi_start": multi_start,
        "neighbour_wr_gate": _SELF_HEAL_GOOD_WR_GATE,
        "neighbour_radius": _SELF_HEAL_NEIGHBOUR_RADIUS,
        "outliers": audit_entries,
    }


# ---------------------------------------------------------------------------
# CLI modes
# ---------------------------------------------------------------------------


def run_single(args) -> dict:
    """Refine a single PXRD file."""
    cell_dict = parse_cell_string(args.cell)
    cell_list = cell_dict_to_list(cell_dict)
    ref_vol = cell_volume(cell_list)

    two_theta, intensity = read_xy_file(args.data)

    with tempfile.TemporaryDirectory() as tmpdir:
        instprm = args.instprm or make_instprm_file(args.wavelength, tmpdir)
        result = refine_one_pattern(
            two_theta=two_theta,
            intensity=intensity,
            space_group=args.space_group,
            cell_list=cell_list,
            instprm_path=instprm,
            workdir=tmpdir,
            label=Path(args.data).stem,
            reference_volume=ref_vol,
            **_refine_kwargs_from_args(args),
        )
    result["file"] = args.data
    return result


def run_directory(args) -> dict:
    """Refine all PXRD files in a directory (or an explicit file list)."""
    explicit = getattr(args, "_explicit_files", None)
    if explicit:
        files = [Path(p) for p in explicit]
    else:
        data_dir = Path(args.data)
        exts = ("*.xye", "*.xy", "*.dat", "*.csv", "*.txt", "*.raw")
        files = []
        for ext in exts:
            files.extend(data_dir.glob(ext))
        files = sorted(set(files))

    if not files:
        return {"success": False, "error": f"No data files in {args.data}"}

    cell_dict = parse_cell_string(args.cell)
    cell_list = cell_dict_to_list(cell_dict)
    ref_vol = cell_volume(cell_list)
    canonical_index = {str(path): idx for idx, path in enumerate(files)}

    if args.chain_cell and args.chain_cell_direction == "both":
        forward = _run_directory_direction(
            _clone_args_with_direction(args, "forward"),
            files=files,
            cell_list=cell_list,
            ref_vol=ref_vol,
            canonical_index=canonical_index,
        )
        reverse = _run_directory_direction(
            _clone_args_with_direction(args, "reverse"),
            files=files,
            cell_list=cell_list,
            ref_vol=ref_vol,
            canonical_index=canonical_index,
        )
        merged, audit = merge_chain_directions(
            forward["results"],
            reverse["results"],
            ref_vol,
            reference_cell=cell_list,
        )
        out: dict = {
            "success": True,
            "chain_cell_direction": "both",
            "merge_strategy": (
                "high-wR reference-cell proximity (V proximity fallback), "
                "otherwise lower wR"
            ),
            "merge_audit": audit,
            "forward_results": forward["results"],
            "reverse_results": reverse["results"],
            "results": merged,
        }
        if getattr(args, "self_heal_chain", False):
            healed, heal_audit = self_heal_chain_outliers(
                merged,
                args=args,
                reference_cell=cell_list,
                reference_volume=ref_vol,
                v_jump_threshold=args.self_heal_v_jump_threshold,
                multi_start=args.self_heal_multi_start,
            )
            out["results"] = healed
            out["self_heal_audit"] = heal_audit
        return out

    direction_out = _run_directory_direction(
        args,
        files=files,
        cell_list=cell_list,
        ref_vol=ref_vol,
        canonical_index=canonical_index,
    )
    if args.chain_cell and getattr(args, "self_heal_chain", False):
        healed, heal_audit = self_heal_chain_outliers(
            direction_out["results"],
            args=args,
            reference_cell=cell_list,
            reference_volume=ref_vol,
            v_jump_threshold=args.self_heal_v_jump_threshold,
            multi_start=args.self_heal_multi_start,
        )
        direction_out["results"] = healed
        direction_out["self_heal_audit"] = heal_audit
    return direction_out


def _run_directory_direction(
    args,
    files: list[Path],
    cell_list: list[float],
    ref_vol: float,
    canonical_index: dict[str, int],
) -> dict:
    results = []
    run_files = list(files)
    if args.chain_cell and args.chain_cell_direction == "reverse":
        run_files = list(reversed(run_files))

    with tempfile.TemporaryDirectory() as tmpdir:
        instprm = args.instprm or make_instprm_file(args.wavelength, tmpdir)
        current_cell = list(cell_list)
        last_accepted: dict | None = None

        for fpath in run_files:
            try:
                two_theta, intensity = read_xy_file(str(fpath))
                anchor_volume = _chain_anchor_volume(args, last_accepted)
                r = refine_one_pattern(
                    two_theta=two_theta,
                    intensity=intensity,
                    space_group=args.space_group,
                    cell_list=current_cell,
                    instprm_path=instprm,
                    workdir=tmpdir,
                    label=fpath.stem,
                    anchor_volume=anchor_volume,
                    anchor_max_jump=args.chain_vol_jump_max,
                    reference_volume=ref_vol,
                    **_refine_kwargs_from_args(args),
                )
                r["file"] = str(fpath)
                r["seed_cell"] = [round(float(v), 5) for v in current_cell]
                next_cell, last_accepted, reason = _maybe_promote_cell(
                    args, last_accepted, r
                )
                r["chain_decision"] = (
                    "promoted" if next_cell is not None else f"rejected: {reason}"
                )
                if next_cell is not None:
                    current_cell = next_cell
            except Exception as exc:
                r = {"success": False, "file": str(fpath), "error": str(exc)}
            results.append(r)

    results = sorted(
        results,
        key=lambda r: canonical_index.get(str(r.get("file")), len(canonical_index)),
    )
    return {
        "success": True,
        "chain_cell_direction": args.chain_cell_direction,
        "results": results,
    }


def run_wide_csv(args) -> dict:
    """Parse wide-table CSV (multiple temperatures), refine each column."""
    patterns = parse_wide_csv(args.data)
    if not patterns:
        return {"success": False, "error": "No temperature columns found in wide CSV"}

    cell_dict = parse_cell_string(args.cell)
    cell_list = cell_dict_to_list(cell_dict)
    ref_vol = cell_volume(cell_list)
    canonical_index = {
        (pat["temp_c"], pat["temp_label"]): idx for idx, pat in enumerate(patterns)
    }

    if args.chain_cell and args.chain_cell_direction == "both":
        forward = _run_wide_csv_direction(
            _clone_args_with_direction(args, "forward"),
            patterns=patterns,
            cell_list=cell_list,
            ref_vol=ref_vol,
            canonical_index=canonical_index,
        )
        reverse = _run_wide_csv_direction(
            _clone_args_with_direction(args, "reverse"),
            patterns=patterns,
            cell_list=cell_list,
            ref_vol=ref_vol,
            canonical_index=canonical_index,
        )
        merged, audit = merge_chain_directions(
            forward["results"],
            reverse["results"],
            ref_vol,
            reference_cell=cell_list,
        )
        out: dict = {
            "success": True,
            "chain_cell_direction": "both",
            "merge_strategy": (
                "high-wR reference-cell proximity (V proximity fallback), "
                "otherwise lower wR"
            ),
            "merge_audit": audit,
            "forward_results": forward["results"],
            "reverse_results": reverse["results"],
            "results": merged,
        }
        if getattr(args, "self_heal_chain", False):
            healed, heal_audit = self_heal_chain_outliers(
                merged,
                args=args,
                reference_cell=cell_list,
                reference_volume=ref_vol,
                v_jump_threshold=args.self_heal_v_jump_threshold,
                multi_start=args.self_heal_multi_start,
            )
            out["results"] = healed
            out["self_heal_audit"] = heal_audit
        return out

    direction_out = _run_wide_csv_direction(
        args,
        patterns=patterns,
        cell_list=cell_list,
        ref_vol=ref_vol,
        canonical_index=canonical_index,
    )
    if args.chain_cell and getattr(args, "self_heal_chain", False):
        healed, heal_audit = self_heal_chain_outliers(
            direction_out["results"],
            args=args,
            reference_cell=cell_list,
            reference_volume=ref_vol,
            v_jump_threshold=args.self_heal_v_jump_threshold,
            multi_start=args.self_heal_multi_start,
        )
        direction_out["results"] = healed
        direction_out["self_heal_audit"] = heal_audit
    return direction_out


def _run_wide_csv_direction(
    args,
    patterns: list[dict],
    cell_list: list[float],
    ref_vol: float,
    canonical_index: dict[tuple, int],
) -> dict:
    results = []
    run_patterns = list(patterns)
    if args.chain_cell and args.chain_cell_direction == "reverse":
        run_patterns = list(reversed(run_patterns))

    with tempfile.TemporaryDirectory() as tmpdir:
        instprm = args.instprm or make_instprm_file(args.wavelength, tmpdir)
        current_cell = list(cell_list)
        last_accepted: dict | None = None

        for pat in run_patterns:
            label = f"T{pat['temp_c']}C"
            try:
                anchor_volume = _chain_anchor_volume(args, last_accepted)
                r = refine_one_pattern(
                    two_theta=pat["two_theta"],
                    intensity=pat["intensity"],
                    space_group=args.space_group,
                    cell_list=current_cell,
                    instprm_path=instprm,
                    workdir=tmpdir,
                    label=label,
                    anchor_volume=anchor_volume,
                    anchor_max_jump=args.chain_vol_jump_max,
                    reference_volume=ref_vol,
                    **_refine_kwargs_from_args(args),
                )
                r["temp_c"] = pat["temp_c"]
                r["temp_label"] = pat["temp_label"]
                r["seed_cell"] = [round(float(v), 5) for v in current_cell]
                next_cell, last_accepted, reason = _maybe_promote_cell(
                    args, last_accepted, r
                )
                r["chain_decision"] = (
                    "promoted" if next_cell is not None else f"rejected: {reason}"
                )
                if next_cell is not None:
                    current_cell = next_cell
            except Exception as exc:
                r = {
                    "success": False,
                    "temp_c": pat["temp_c"],
                    "temp_label": pat["temp_label"],
                    "error": str(exc),
                }
            results.append(r)

    results = sorted(
        results,
        key=lambda r: canonical_index.get(
            (r.get("temp_c"), r.get("temp_label")),
            len(canonical_index),
        ),
    )
    return {
        "success": True,
        "chain_cell_direction": args.chain_cell_direction,
        "results": results,
    }
