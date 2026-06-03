# DPA + LAMMPS (requires freeze)

DPA3 checkpoints (`DPA3.1-3M.pt`, `DPA3.2-5M.pt`) are **multi-task / multi-head** models and **cannot be loaded directly by LAMMPS**. You must first freeze a single head/branch into a `.pth` file. This is DPA-specific (MACE/SevenNet/MatterSim do not need this step).

> Requires `deepmd-kit >= 3.1.0` (check with `dp --version`; the multi-family image reports `v1.3.3.dev2445` which **is** the v3.x codebase).

**Step 1 — list available branches (heads):**

```bash
dp --pt show DPA-3.2-5M.pt model-branch
```

Typical DPA3 branches: `OMat24` (default inorganic), `OMol25`, `OC22`, `Organic_Reactions`, `ODAC23`, plus `RANDOM` (randomly initialized fitting net). Pick the branch whose training data best matches your system.

**Step 2 — freeze the chosen branch:**

```bash
# --model-branch (preferred) or --head both work
dp --pt freeze -c DPA-3.2-5M.pt -o frozen_model.pth --model-branch [head_name]
```

**Step 3 — use the frozen `.pth` in LAMMPS** (via the `deepmd` pair style):

```
pair_style  deepmd frozen_model.pth
pair_coeff  * *
```

**Type-map alignment:** The frozen model keeps the full-element type_map by default. LAMMPS data file atom types must use the same element indices (e.g., Fe=26, Ni=28 — not compact 1,2). See `reference/dpa_models.md` § "Use in LAMMPS" for full details and the compact `--type-map` alternative.

**Inspecting type_map of a model file:** Use `dp --pt show <model.pt> type-map` to print the element ordering. For user-provided custom models, this is the correct way to determine what elements the model covers and their ordering — do NOT attempt `torch.load`, `zipfile`, or binary parsing of `.pt` files.

## DPA4-Neo LAMMPS path

DPA4 raw checkpoints (`DPA4-Neo-OMat24*.pt`) cannot be loaded directly by
LAMMPS. Freeze them into a `.pt2` artifact first, then run LAMMPS with the
DPA4 image:

```bash
dp --pt freeze -c DPA4-Neo-OMat24.pt -o dpa4_frozen.pt2
```

Use:

```text
atom_modify map yes
pair_style  deepmd dpa4_frozen.pt2
pair_coeff  * * Si
```

`atom_modify map yes` is required for DPA4 `.pt2` in LAMMPS; without it, the
model may load but atom-ID mapping fails. For DPA4 LAMMPS jobs, submit with:

```text
registry.dp.tech/dptech/dp/native/hub/custom_images/dpa4:260601-1780311840
```

Compatibility boundary from runtime testing:
- `dpa4:260601-1780311840` supports DPA3 and DPA4 ASE/LAMMPS workflows.
- DPA4 LAMMPS requires freeze to `.pt2`; raw checkpoints report "Cannot
  detect the backend".
- DPA1 TensorFlow `.pb` is not ready because this image has no TensorFlow
  backend.
- DPA1 legacy TorchScript `.pth` is not ready in LAMMPS because the C++
  interface misses `has_message_passing`.
- DPA2.4 freeze to LAMMPS is not ready in this image because the checkpoint
  state dict is incompatible (`repinit.type_embd_data` missing).

Keep existing non-DPA4 LAMMPS images/workflows unless the task explicitly uses
DPA4 `.pt2`.

Notes:
- The frozen `.pth` is also directly usable by ASE: `from deepmd.calculator import DP; atoms.calc = DP("frozen_model.pth")`.
- The ASE workflows provided by this skill (optimize/phonon/MD/elastic/NEB/adsorption) load the **multi-head `.pt`** directly and select the head via `--head`, so freezing is only required when you actually need LAMMPS.
- For a new downstream system, optionally run `dp --pt change-bias <model.pt> -s <system> --model-branch <Branch>` before freezing to better align the per-element energy bias.
