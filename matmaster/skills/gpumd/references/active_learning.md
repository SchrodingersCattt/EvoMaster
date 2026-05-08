# GPUMD Active Learning Reference

Use this file for on-the-fly active learning and NEP committee uncertainty.

Official sources:

- `active`: https://gpumd.org/gpumd/input_parameters/active.html
- `active.xyz`: https://gpumd.org/gpumd/output_files/active_xyz.html
- `active.out`: https://gpumd.org/gpumd/output_files/active_out.html
- `dump_observer`: https://gpumd.org/gpumd/input_parameters/dump_observer.html

## Scope

The `active` keyword is only supported with NEP potentials. It uses a committee
of supplied NEP models to estimate force uncertainty. MD is propagated using the
first NEP potential listed in `run.in`.

## Syntax

```text
active <interval> <has_velocity> <has_force> <has_uncertainty> <threshold>
```

Arguments:

- `interval`: check uncertainty every this many steps.
- `has_velocity`: `1` to include velocities in `active.xyz`, else `0`.
- `has_force`: `1` to include forces in `active.xyz`, else `0`.
- `has_uncertainty`: `1` to include per-atom uncertainty in `active.xyz`, else `0`.
- `threshold`: non-negative uncertainty threshold in eV/Angstrom.

The uncertainty is the maximum force sample standard deviation over atoms across
the model committee.

## Template

```text
potential nep0.txt
potential nep1.txt
potential nep2.txt
potential nep3.txt
potential nep4.txt

velocity 500
time_step 1.0

ensemble nvt_ber 500 500 100
active 10 1 1 1 0.01
dump_observer observe 100 1000 1 1
dump_thermo 1000
run 500000
```

## Outputs

`active.out`:

- Always written when `active` is invoked.
- Contains two columns: time in fs and uncertainty in eV/Angstrom.
- A row is written every uncertainty-check interval.

`active.xyz`:

- Written only when at least one checked structure exceeds the threshold.
- Extended XYZ format.
- Append mode.
- May be absent if no structure crosses the threshold.

Observer outputs:

- Optional but useful for debugging committee behavior.
- `dump_observer observe ...` writes per-potential thermo and exyz outputs.

## Guards

- Use at least two NEP potentials; otherwise committee uncertainty is not
  meaningful.
- Keep species order identical across all supplied NEP potentials.
- The first potential drives MD in active learning and observer `observe` mode.
- `active` is not propagating; re-specify it in every later `run` block that
  should continue uncertainty checks.
- If the system explodes, unphysical structures can be saved because there is no
  upper uncertainty bound. Inspect `active.xyz` before adding structures to a
  training set.
- Do not use `active` with EAM, Tersoff, ADP, FCP, or DP potentials.
