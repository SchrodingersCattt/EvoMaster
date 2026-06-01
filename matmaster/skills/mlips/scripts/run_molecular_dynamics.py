"""run_molecular_dynamics.py — Multi-stage MD with an MLIP calculator.

Usage::

    python run_molecular_dynamics.py --structure input.cif --model DPA3.1-3M \\
        --stages stages.json [--head head_name] [--save-interval 100] \\
        [--seed 42] [--charge 0] [--spin 1]

The ``--stages`` JSON file is a list of stage dicts, e.g.::

    [
      {"mode": "NVT", "temperature_K": 300, "runtime_ps": 5},
      {"mode": "NPT-aniso", "temperature_K": 300, "pressure": 0.0, "runtime_ps": 10}
    ]

Stage keys:
    mode            NVT | NVT-Berendsen | NVT-Langevin | NPT-aniso | NPT-tri | NVE
    runtime_ps      Duration in picoseconds
    temperature_K   Temperature (K) — required for NVT/NPT
    pressure        Pressure (GPa) — required for NPT
    timestep_ps     Time step (default 0.0005 = 0.5 fs)
    tau_t_ps        Thermostat coupling (default 0.01)
    tau_p_ps        Barostat coupling (default 0.1)
    equil_frac      Fraction of stage samples discarded as equilibration before
                    computing the reported mean T/P (default 0.0; e.g. 0.2 =
                    drop first 20%)

Outputs:
    trajs/stage{N}_{mode}_{T}K.extxyz  — per-stage trajectories
    final_structure.xyz                 — final structure
    md_simulation.log                   — thermo log incl. pressure (GPa)
    result.json                         — summary with per-stage mean T (K),
                                          pressure (GPa) and volume (A^3)

Pressure is computed via ``atoms.get_stress(include_ideal_gas=True)`` so it
includes the kinetic (ideal-gas) contribution, matching the instantaneous
mechanical pressure used by ASE's ``MDLogger`` (in GPa). The reported scalar
pressure is ``P = -(sxx + syy + szz) / 3`` of the Voigt stress tensor —
i.e. positive = compression. If the calculator does not implement stress,
pressure fields are omitted from the log and reported as ``null``.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path

import numpy as np
from _calculator import build_calculator, build_fparam, set_fparam
from ase import units
from ase.io import read, write
from ase.md.langevin import Langevin
from ase.md.npt import NPT
from ase.md.nvtberendsen import NVTBerendsen

try:
    from ase.md.nose_hoover_chain import NoseHooverChainNVT
except ImportError:
    NoseHooverChainNVT = None
from ase.md.velocitydistribution import (
    MaxwellBoltzmannDistribution,
    Stationary,
    ZeroRotation,
)
from ase.md.verlet import VelocityVerlet

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="MLIP molecular dynamics")
    p.add_argument("--structure", required=True, help="Input structure file")
    p.add_argument("--model", default="DPA3.1-3M", help="Model name/path/URL")
    p.add_argument("--head", default=None, help="Model head (DP family)")
    p.add_argument("--stages", required=True, help="JSON file with stage definitions")
    p.add_argument("--save-interval", type=int, default=100, help="Save every N steps")
    p.add_argument("--seed", type=int, default=42, help="Random seed")
    p.add_argument("--charge", type=int, default=None)
    p.add_argument("--spin", type=int, default=None)
    return p.parse_args()


# ---------------------------------------------------------------------------


def _build_dynamics(atoms, stage: dict, seed: int):
    """Return an ASE dynamics object for the given stage config."""
    mode = stage["mode"]
    ts_ps = stage.get("timestep_ps", 0.0005)
    ts_fs = ts_ps * 1000
    dt = ts_fs * units.fs
    T = stage.get("temperature_K")
    P = stage.get("pressure")
    tau_t = stage.get("tau_t_ps", 0.01) * 1000 * units.fs
    tau_p = stage.get("tau_p_ps", 0.1) * 1000 * units.fs
    rng = np.random.RandomState(seed)

    if mode in ("NVT", "NVT-NH"):
        if NoseHooverChainNVT is not None:
            return NoseHooverChainNVT(atoms, timestep=dt, temperature_K=T, tdamp=tau_t)
        return Langevin(
            atoms, timestep=dt, temperature_K=T, friction=1.0 / tau_t, rng=rng
        )
    if mode == "NVT-Berendsen":
        return NVTBerendsen(atoms, timestep=dt, temperature_K=T, taut=tau_t)
    if mode in ("NVT-Langevin", "Langevin"):
        return Langevin(
            atoms, timestep=dt, temperature_K=T, friction=1.0 / tau_t, rng=rng
        )
    if mode in ("NPT-aniso", "NPT-tri"):
        if P is None:
            raise ValueError(f"Pressure required for {mode}")
        mask = np.eye(3, dtype=bool) if mode == "NPT-aniso" else None
        return NPT(
            atoms,
            timestep=dt,
            temperature_K=T,
            externalstress=P * units.GPa,
            ttime=tau_t,
            pfactor=tau_p,
            mask=mask,
        )
    if mode == "NVE":
        return VelocityVerlet(atoms, timestep=dt)
    raise ValueError(f"Unknown MD mode: {mode}")


def _instantaneous_pressure_gpa(atoms):
    """Return ``(P_iso_GPa, stress_voigt_GPa)`` or ``(None, None)`` on failure.

    Uses ``include_ideal_gas=True`` so the kinetic contribution is added,
    matching ASE's MDLogger convention. Voigt order is ``(xx, yy, zz, yz, xz, xy)``.
    """
    try:
        s = atoms.get_stress(include_ideal_gas=True) / units.GPa
    except Exception as exc:
        log.warning("Stress unavailable from calculator: %s", exc)
        return None, None
    p_iso = -(float(s[0]) + float(s[1]) + float(s[2])) / 3.0
    return p_iso, [float(x) for x in s]


def _run_stage(
    atoms, stage: dict, stage_idx: int, save_interval: int, seed: int, log_fh
):
    """Run a single MD stage; return ``(atoms, stats)``."""
    mode = stage["mode"]
    T = stage.get("temperature_K")
    P_target = stage.get("pressure")
    ts_ps = stage.get("timestep_ps", 0.0005)
    runtime_ps = stage["runtime_ps"]
    total_steps = int(runtime_ps / ts_ps)
    equil_frac = float(stage.get("equil_frac", 0.0))

    if stage_idx == 0 and T is not None:
        MaxwellBoltzmannDistribution(
            atoms, temperature_K=T, rng=np.random.RandomState(seed)
        )
        Stationary(atoms)
        ZeroRotation(atoms)

    dyn = _build_dynamics(atoms, stage, seed)

    tag = f"stage{stage_idx + 1}_{mode}_{T}K"
    traj_file = f"trajs/{tag}.extxyz"
    os.makedirs("trajs", exist_ok=True)

    samples = {
        "step": [],
        "T_K": [],
        "P_GPa": [],
        "V_A3": [],
        "E_pot_eV": [],
        "E_kin_eV": [],
    }

    def _save():
        frame = atoms.copy()
        frame.info["energy"] = float(atoms.get_potential_energy())
        frame.arrays["force"] = atoms.get_forces()
        write(traj_file, frame, format="extxyz", append=True)

    def _log():
        e_pot = float(atoms.get_potential_energy())
        e_kin = float(atoms.get_kinetic_energy())
        temp = float(atoms.get_temperature())
        vol = float(atoms.get_volume())
        p_iso, _ = _instantaneous_pressure_gpa(atoms)

        samples["step"].append(int(dyn.nsteps))
        samples["T_K"].append(temp)
        samples["P_GPa"].append(p_iso if p_iso is not None else float("nan"))
        samples["V_A3"].append(vol)
        samples["E_pot_eV"].append(e_pot)
        samples["E_kin_eV"].append(e_kin)

        p_str = f"{p_iso:.5f}" if p_iso is not None else "nan"
        log_fh.write(
            f"{dyn.nsteps} stage{stage_idx + 1} {e_pot:.4f} {e_kin:.4f} "
            f"{temp:.2f} {p_str} {vol:.3f}\n"
        )
        log_fh.flush()

    dyn.attach(_save, interval=save_interval)
    dyn.attach(_log, interval=save_interval)

    log.info(
        "[Stage %d] %s  T=%s K  P=%s GPa  steps=%d  (equil_frac=%.2f)",
        stage_idx + 1,
        mode,
        T,
        P_target,
        total_steps,
        equil_frac,
    )
    dyn.run(total_steps)

    n = len(samples["T_K"])
    start = int(n * equil_frac) if n > 1 else 0
    T_arr = np.asarray(samples["T_K"][start:], dtype=float)
    P_arr = np.asarray(samples["P_GPa"][start:], dtype=float)
    V_arr = np.asarray(samples["V_A3"][start:], dtype=float)
    P_finite = P_arr[np.isfinite(P_arr)] if P_arr.size else P_arr

    def _stat(arr):
        if arr.size == 0:
            return None, None
        return float(np.mean(arr)), float(np.std(arr, ddof=0))

    T_mean, T_std = _stat(T_arr)
    P_mean, P_std = _stat(P_finite)
    V_mean, V_std = _stat(V_arr)

    stats = {
        "stage_idx": stage_idx + 1,
        "mode": mode,
        "T_target_K": T,
        "P_target_GPa": P_target,
        "runtime_ps": runtime_ps,
        "timestep_ps": ts_ps,
        "n_samples": int(n),
        "n_samples_averaged": int(P_finite.size if P_finite.size else T_arr.size),
        "equil_frac": equil_frac,
        "T_mean_K": T_mean,
        "T_std_K": T_std,
        "P_mean_GPa": P_mean,
        "P_std_GPa": P_std,
        "V_mean_A3": V_mean,
        "V_std_A3": V_std,
        "trajectory": traj_file,
    }
    log.info(
        "[Stage %d] mean T = %s K  mean P = %s GPa  (n=%d after equil)",
        stage_idx + 1,
        f"{T_mean:.2f}" if T_mean is not None else "n/a",
        f"{P_mean:.5f}" if P_mean is not None else "n/a",
        stats["n_samples_averaged"],
    )
    return atoms, stats


def main() -> None:
    args = parse_args()
    stages = json.loads(Path(args.stages).read_text())

    atoms = read(args.structure)
    calc = build_calculator(args.model, head=args.head)
    atoms.calc = calc

    fparam = build_fparam(args.charge, args.spin)
    set_fparam(atoms, fparam)

    stage_stats = []
    with open("md_simulation.log", "w") as log_fh:
        log_fh.write("# step stage E_pot(eV) E_kin(eV) T(K) P(GPa) V(A^3)\n")
        for i, stage in enumerate(stages):
            atoms, stats = _run_stage(
                atoms, stage, i, args.save_interval, args.seed, log_fh
            )
            stage_stats.append(stats)

    write("final_structure.xyz", atoms)

    result = {
        "model": args.model,
        "num_stages": len(stages),
        "final_structure": "final_structure.xyz",
        "trajectory_dir": "trajs",
        "log_file": "md_simulation.log",
        "stages": stage_stats,
    }
    Path("result.json").write_text(json.dumps(result, indent=2))
    log.info("MD complete. See result.json.")


if __name__ == "__main__":
    main()
