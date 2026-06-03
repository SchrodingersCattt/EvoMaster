# QHA Thermal Expansion Workflow

Quasi-Harmonic Approximation (QHA) for computing thermal expansion coefficients using DPA MLIPs.

## Import & API

The correct import in the `dpa-calculator:5bdb7c53` image (phonopy 2.34):

```python
from phonopy import PhonopyQHA
```

**NOT** `from phonopy.qha import QHA` — that path raises `ImportError`.

### PhonopyQHA constructor

```python
qha = PhonopyQHA(
    volumes=volumes,                # (n_vol,), Angstrom^3
    electronic_energies=energies,   # (n_vol,), eV
    temperatures=temperatures,      # (n_temps,), K
    free_energy=fe_phonon,          # (n_temps, n_vol), kJ/mol
    cv=cv,                          # (n_temps, n_vol), J/K/mol
    entropy=entropy,                # (n_temps, n_vol), J/K/mol
    eos='vinet',
    t_max=1000.0,
)

# Properties (no .run() call needed):
qha.thermal_expansion        # beta(T), volumetric, K^-1
qha.volume_temperature       # V_eq(T), Angstrom^3
qha.bulk_modulus_temperature  # B(T), GPa
```

### Data shapes

`free_energy`, `cv`, `entropy` must be shaped `(n_temps, n_vol)`.

If your phonon loop collects per-volume arrays of shape `(n_temps,)`, stack them and **transpose** before passing to PhonopyQHA:

```python
# fe_list is a list of n_vol arrays, each (n_temps,)
fe_phonon = np.array(fe_list).T   # -> (n_temps, n_vol)
```

### Linear CTE

The volumetric thermal expansion beta from PhonopyQHA relates to the linear CTE alpha by:

```
alpha_linear = beta_volumetric / 3
```

## Workflow Steps

1. **Relax equilibrium structure** — cell + positions (`--relax-cell --fmax 0.01`)
2. **Generate volume points** — 5 volumes at +/-2% around equilibrium (uniform spacing)
3. **Phonon at each volume** — finite displacement phonon calculation, extract thermal properties (free energy, Cv, entropy) over the target temperature range
4. **Feed to PhonopyQHA** — use Vinet EOS, extract alpha(T)

## Important Notes

- **Do NOT use manual polynomial fitting** of F(V) curves — it is numerically unstable with only 5 volume points. PhonopyQHA with Vinet EOS is much more robust.
- **Known DPA3.1-3M limitation**: For diamond Si at 300 K, the model predicts NEGATIVE linear CTE (~-3e-6 /K). The experimental value is +2.6e-6 /K. This is a model limitation (incorrect sign of the Gruneisen parameter), not a code bug. Report the result with this caveat.
