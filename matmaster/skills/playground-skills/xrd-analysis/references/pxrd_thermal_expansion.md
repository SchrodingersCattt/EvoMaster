# PXRD Thermal Expansion Analysis — Reference

## Workflow for Temperature-Dependent PXRD

1. **Organize data files**: Place all T-dependent patterns in one directory. Name files so
   temperature can be extracted (e.g. `pattern_303K.xy`, `T383.dat`).
2. **Identify crystal system**: From prior knowledge or `mat_xrd_xrd_phase_identification`.
   Determine approximate lattice parameters at one temperature.
3. **Run multi-temperature refinement**:
   ```
   python ${SKILL_DIR}/scripts/refine_lattice_pxrd.py \
     --dir /data/ --crystal-system tetragonal \
     --initial-params "a=10.8,c=6.5" --wavelength 1.5406 --multi-temp
   ```
4. **Interpret output**: The script auto-detects phase transitions and fits
   each phase independently.

## Phase Transition Detection

The script tests whether a two-phase linear model fits significantly better than
a single-phase model. If the two-phase residual is <40% of the single-phase
residual, a phase transition is reported.

**Reported fields** when a transition is found:
- `transition_temperature_K` — midpoint between the last RTP and first HTP data points.
- `phase_1` / `phase_2` — each contains `V_slope`, `V_intercept`, `V_R_squared`,
  and per-parameter slopes/intercepts (e.g. `a_slope`, `c_slope`).

## Interpreting Thermal Expansion

- **Volume thermal expansion coefficient**: αᵥ = (1/V₀) · dV/dT. Compute from
  V_slope / V_intercept.
- **Linear thermal expansion**: For uniaxial systems (tetragonal, hexagonal),
  αₐ = (1/a₀) · da/dT, αc = (1/c₀) · dc/dT.
- **Negative thermal expansion**: A negative slope in one direction with positive
  in another suggests anisotropic NTE — discuss the structural mechanism.

## Common Crystal Systems for PXRD

| System       | Free params            | --initial-params example        |
|--------------|------------------------|---------------------------------|
| cubic        | a                      | `"a=5.43"`                      |
| tetragonal   | a, c                   | `"a=10.8,c=6.5"`               |
| hexagonal    | a, c                   | `"a=4.9,c=5.4"`                |
| orthorhombic | a, b, c                | `"a=5.0,b=7.2,c=9.1"`          |
| monoclinic   | a, b, c, beta          | `"a=10,b=5,c=12,beta=95"`      |
| triclinic    | a, b, c, α, β, γ      | `"a=7,b=8,c=9,alpha=80,beta=85,gamma=92"` |

## Troubleshooting

- **Too few peaks matched**: Lower `--tolerance` (default 0.3°) or improve
  initial parameters. Also try increasing `--max-index`.
- **Phase transition not detected**: Check if the temperature sampling is
  dense enough around the transition. Manual inspection of V vs T plot may help.
- **Poor fit at high angles**: High-angle peaks are more sensitive to lattice
  parameter errors. Ensure initial params are within ~2% of true values.
