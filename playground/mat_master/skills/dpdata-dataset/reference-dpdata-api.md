# reference-dpdata-api.md — dpdata patterns for DeePMD NPY

Short reference for agents. Confirm against the installed `dpdata` version.

## Core types

| Class | Role |
|-------|------|
| `dpdata.System` | Unlabeled frames: coords, cells, atom types, names |
| `dpdata.LabeledSystem` | Adds energies, forces, virials, optional property fields |
| `dpdata.MultiSystems` | Container of many systems; mixed export |

## From pymatgen Structure

```python
import dpdata
s = structure  # pymatgen Structure
FULL_TYPE_MAP = [...]  # list of element symbols, same order as pretrained model
dp = dpdata.System(s, fmt="pymatgen/structure", type_map=FULL_TYPE_MAP)
```

- `type_map` forces a **global** index ordering for multi-element datasets.

## LabeledSystem from System data

```python
import numpy as np
dp.data["energies"] = np.array([value])
dp.data["forces"] = np.zeros_like(dp.data["coords"])
lab = dpdata.LabeledSystem(data=dp.data, type_map=FULL_TYPE_MAP)
lab.to_deepmd_npy("out/system_name")
```

Energy/forces keys are expected by many writers even for property-only training.

## Load existing DeepMD NPY

Single system:

```python
lsys = dpdata.LabeledSystem("path/to/system", fmt="deepmd/npy")
coords = lsys.data["coords"]          # (nframes, natoms, 3)
cells = lsys.data["cells"]            # (nframes, 3, 3) unless nopbc handling
atom_types = lsys.data["atom_types"] # int indices into atom_names
names = lsys.data["atom_names"]      # ndarray of symbol strings per index
```

Mixed systems:

```python
ms = dpdata.MultiSystems().load_systems_from_file("mixed_dir", fmt="deepmd/npy/mixed")
```

Exact `fmt` string can vary slightly by dpdata version — check `dpdata.system.LabeledSystem` docs or `dpdata --help` in project env.

## Inference type remapping

Model `type_map` may equal data `type_map`, but when comparing to `DeepProperty.get_type_map()`, remap:

```python
import numpy as np

def remap(atom_types, data_names, model_type_map):
    names = list(data_names)
    return np.array(
        [np.where(np.array(model_type_map) == names[t])[0][0] for t in atom_types],
        dtype=np.int32,
    )
```

## Common gotchas

1. **Molar vs mass fractions** — handled upstream before this skill; errors shift stoichiometry before integerization.
2. **type_map length** — universal DPA uses 118 symbols; sparse structures still index into the full list.
3. **Mixed vs plain paths** — different directory layout; splits must preserve fmt.
4. **nopbc molecules** — inference passes `cells=None`; data prep may still write dummy `box.npy` in some pipelines.
5. **Property storage** — ensure `property.npy` aligns with `nframes` and `task_dim`.

## Optional formats

dpdata supports VASP, XYZ, Amber, etc. For materials ML, **pymatgen/structure** or **deepmd/** IO is most common in the referenced projects.
