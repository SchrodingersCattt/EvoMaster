# MD Output Format Reference

## stages.json input

```json
[{"mode": "NVT", "temperature_K": 300, "runtime_ps": 5, "timestep_ps": 0.0005}]
```

Modes: NVT, NVT-Berendsen, NVT-Langevin, NPT-aniso, NPT-tri, NVE.

Optional per-stage key `"equil_frac": 0.2` discards the first 20% of samples before averaging T/P for that stage (default 0.0 = average over the whole stage).

## result.json["stages"] output

Each stage entry reports:
- `T_mean_K`, `T_std_K`
- `P_mean_GPa`, `P_std_GPa`
- `V_mean_A3`, `V_std_A3`
- `n_samples_averaged`, `equil_frac`

Pressure fields are `null` if the calculator does not implement stress (rare — all built-in DPA/MACE/SevenNet/MatterSim calculators do).

## Pressure convention

Instantaneous pressure: `P = -(sxx+syy+szz)/3` from `atoms.get_stress(include_ideal_gas=True)` (matches ASE `MDLogger`; includes kinetic/ideal-gas contribution; units GPa, positive = compression).

## md_simulation.log

Per-step values written column-wise: `step stage E_pot E_kin T(K) P(GPa) V(A^3)` — for plotting or re-averaging.

## Reporting rules

- For NPT runs, always report `T_mean_K` and `P_mean_GPa` (with ±std).
- For multi-stage protocols (equilibration + production), report production-stage averages, not whole-trajectory averages.
- Set `"equil_frac": 0.2` (or larger) on a production stage when you want the script itself to drop the initial transient.

## Adsorption

Built-in adsorbates: H, C, O, N, CO, CO2, H2, H2O, OH, OOH, COOH, HCOO, CHO.

Copy both `_calculator.py` and `calculate_adsorption.py` to working directory.
