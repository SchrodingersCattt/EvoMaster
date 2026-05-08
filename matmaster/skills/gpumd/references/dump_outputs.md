# GPUMD Dump Keywords Reference

Use this file when a task asks for trajectories, forces, velocities, observer
outputs, restarts, or custom extended XYZ output.

Official sources:

- `dump_thermo`: https://gpumd.org/gpumd/input_parameters/dump_thermo.html
- `dump_position`: https://gpumd.org/gpumd/input_parameters/dump_position.html
- `dump_force`: https://gpumd.org/gpumd/input_parameters/dump_force.html
- `dump_velocity`: https://gpumd.org/gpumd/input_parameters/dump_velocity.html
- `dump_exyz`: https://gpumd.org/gpumd/input_parameters/dump_exyz.html
- `dump_xyz`: https://gpumd.org/gpumd/input_parameters/dump_xyz.html
- `dump_observer`: https://gpumd.org/gpumd/input_parameters/dump_observer.html
- `dump_restart`: https://gpumd.org/gpumd/input_parameters/dump_restart.html

All dump keywords must appear before the `run` they belong to. They are not
propagating, so re-specify them in each later `run` block that needs output.

## Thermodynamic Output

Syntax:

```text
dump_thermo <interval>
```

Output:

- `thermo.out`

Example:

```text
dump_thermo 1000
```

## Positions

Syntax:

```text
dump_position <interval> [group <grouping_method> <group_id>] [precision single|double]
```

Output:

- `movie.xyz`

Examples:

```text
dump_position 1000
dump_position 1000 group 2 1
dump_position 1000 group 2 1 precision double
dump_position 1000 precision double group 2 1
```

Notes:

- Without `group`, positions are dumped for all atoms.
- `precision single` uses `%0.9f`; `precision double` uses `%0.17f`.
- The output appends to a single `movie.xyz`.

## Forces

Syntax:

```text
dump_force <interval> [group <grouping_method> <group_id>]
```

Output:

- `force.out`

Examples:

```text
dump_force 10
dump_force 10 group 2 1
```

## Velocities

Syntax:

```text
dump_velocity <interval> [group <grouping_method> <group_id>]
```

Output:

- `velocity.out`

Examples:

```text
dump_velocity 10
dump_velocity 10 group 2 1
```

## Custom Extended XYZ

### `dump_exyz`

Syntax:

```text
dump_exyz <interval> <has_velocity>
dump_exyz <interval> <has_velocity> <has_force>
dump_exyz <interval> <has_velocity> <has_force> <has_potential>
dump_exyz <interval> <has_velocity> <has_force> <has_potential> <separated>
```

Output:

- `dump.xyz`, or separated `dump.<step>.xyz` files if `separated=1`.

Examples:

```text
dump_exyz 1000
dump_exyz 1000 1
dump_exyz 1000 1 1
dump_exyz 1000 1 1 1
dump_exyz 100 0 1 1 1
```

Notes:

- Positions are always included.
- `has_velocity`, `has_force`, `has_potential`, and `separated` are binary
  `0`/`1` flags.
- The normal non-separated output appends to one `dump.xyz`.

### `dump_xyz`

Syntax:

```text
dump_xyz <grouping_method> <group_id> <interval> <filename> [property_1 property_2 ...]
```

If `grouping_method` is negative, `group_id` is ignored and the whole system is
written.

Allowed optional properties include:

- `mass`
- `velocity`
- `force`
- `potential`
- `virial`
- `charge`
- `bec`
- `group`
- `unwrapped_position`

Examples:

```text
dump_xyz -1 1 1000 positions.xyz
dump_xyz 1 0 100 properties.xyz mass velocity potential force virial
```

Notes:

- Wrapped positions are always included.
- If `filename` ends with `*`, each frame is written to a separate file with the
  step number replacing `*`.
- This keyword may be invoked multiple times within one run.

## Observer Outputs

Official v5.2 syntax:

```text
dump_observer <mode> <interval_thermo> <interval_exyz> <has_velocity> <has_force>
```

Modes:

- `observe`: first NEP potential propagates MD; all potentials are evaluated.
- `average`: all supplied NEP potentials are averaged and the average potential
  propagates MD.

Examples:

```text
potential nep0.txt
potential nep1.txt
dump_observer observe 100 1000 1 1
```

```text
potential nep0.txt
potential nep1.txt
dump_observer average 100 1000 1 1
```

Outputs:

- `observe`: `observer0.out`, `observer1.out`, ..., plus corresponding
  `observer0.xyz`, `observer1.xyz`, ...
- `average`: `observer.out` and `observer.xyz`.

Guards:

- Requires multiple NEP potentials for meaningful observer/average workflows.
- All supplied NEP potentials must have their atomic species in the same order.
- This is not the same syntax as older shorthand examples such as
  `dump_observer 1000 observe`; use the v5.2 syntax above.

## Restart

Syntax:

```text
dump_restart <interval>
```

Example:

```text
dump_restart 100000
```

Use this for long runs where restart capability matters. The restart file is
updated/overwritten rather than appended like most trajectory outputs.
