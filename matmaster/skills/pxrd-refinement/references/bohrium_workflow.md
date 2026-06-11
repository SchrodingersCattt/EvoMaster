# Bohrium Submission Workflow for PXRD Refinement

GSAS-II is not available locally. All refinement runs go through the `Bohrium` builtin
tool, which submits to a Docker image with GSAS-II pre-installed.

## 1. Image and machine

| Item | Value |
|---|---|
| image | `registry.dp.tech/dptech/dp/native/prod-19853/xrd-app:dev-260119` |
| GSAS-II install | `/root/g2full/GSAS-II/GSASII` (the script default) |
| machine (single Pawley, basic Rietveld) | `c8_m32_cpu` |
| machine (directory batch >= 5 patterns, full Rietveld) | `c32_m128_cpu` |

Wall-time guidance: single-pattern Pawley 1-2 min; 8-pattern batch 5-10 min;
Rietveld standard 3-5 min; auto-index up to `--timeout` per Bravais family
(default 200 s, hard SIGALRM-enforced).

## 2. Command templates — always use a `run.sh` wrapper

**Critical:** Bohrium's job system silently drops commands that contain embedded
quotes in the `cmd` field. A `cmd` like
`python3 gsas2_pawley.py --space-group "P 21" --cell "a=10.83,..."` will appear
to succeed (job status = Finished) but produces **no log file and no output** — the
script never executes.

The reliable pattern is:

1. Write the full command into `input_dir/run.sh` (a plain shell script).
2. Submit with `cmd="bash run.sh > log 2>&1"` — this is the **only** cmd you
   should pass to the Bohrium tool.

All templates below show the **`run.sh` content** — copy into a file, then submit
with `cmd="bash run.sh > log 2>&1"`.

### Pawley, single pattern (`run.sh`)

```bash
#!/bin/bash
python3 gsas2_pawley.py --data pattern.xye \
  --space-group "<SG>" \
  --cell "a=<A>,b=<B>,c=<C>,beta=<BETA>" \
  --wavelength 1.5406 --multi-start 5 --debug-plot plots \
  -o result.json
```

### Pawley, directory batch (`run.sh`, preferred for multi-pattern series)

```bash
#!/bin/bash
python3 gsas2_pawley.py --data ./ \
  --space-group "<SG>" \
  --cell "a=<A>,b=<B>,c=<C>,beta=<BETA>" \
  --wavelength 1.5406 --multi-start 5 --chain-cell \
  --chain-cell-direction both --standardize-cell ref --debug-plot plots \
  -o results.json
```

`--multi-start 5` runs each pattern five times from deterministically perturbed seed
cells and keeps the lowest-wR result (~5x runtime per pattern; cheap insurance against
local-minimum traps — see `gsas2_refinement_guide.md` § "Multi-start"). **Do not raise
`--multi-start` above 5 in any standard run**: combined with
`--chain-cell-direction both` (which doubles work), each extra start adds noticeable
runtime and the agent will exceed the 1200 s online task timeout. Use the cell-distance
tiebreak + `merge_audit.warnings` to handle wrong-basin patterns instead. `--chain-cell`
promotes each accepted refinement to seed the next pattern, gated by `--chain-wr-max`
(default 25 %) and `--chain-vol-jump-max` (default 0.05) so a bad pattern can't poison
downstream patterns. `--chain-cell-direction both` runs forward and reverse internally
and returns merged `results` plus `merge_audit` (with `table` and `warnings`) /
`forward_results` / `reverse_results`. The `warnings` list flags any pattern that is
high-wR (>10%) in both directions AND > 1% off `reference_volume` in both — those
patterns are at risk of being a wrong-basin solution even after merge.
Bump machine to `c32_m128_cpu` whenever `--multi-start ≥ 5` or the directory has > 5
patterns.

`--self-heal-chain` is enabled by default for chained runs. It scans the merged
series for single-pattern volume outliers and re-refines each rescued pattern
in-process with `--self-heal-multi-start` starts (default 5). This improves wrong-basin
robustness, but each rescued outlier costs roughly one extra K-start single-pattern
refinement. Disable with `--no-self-heal-chain` when you intentionally need raw chain
output or cannot afford rescue work under the wall-clock budget.

### Rietveld (`run.sh`)

```bash
#!/bin/bash
python3 gsas2_rietveld.py --data pattern.xye --cif structure.cif \
  --wavelength 1.5406 --refine-level standard \
  --export-cif refined.cif -o result.json
```

### Auto-index (`run.sh`, last resort — see `autoindex.md` first)

```bash
#!/bin/bash
python3 gsas2_autoindex.py --data pattern.xye \
  --bravais monoclinic-P --wavelength 1.5406 \
  --timeout 200 --debug-plot plots \
  -o candidates.json
```

### Submitting

After writing `run.sh` into `input_dir/`:

    Bohrium(action="submit", ..., cmd="bash run.sh > log 2>&1")

Never put the python3 invocation directly in the `cmd` field.

`<SG>` is the Hermann-Mauguin space-group string (e.g. `P 21/c`, `F d -3 m`).
`<A>/<B>/<C>/<BETA>` come from a reference — never invented from peak positions.

## 3. Stage `input_dir/`

Flat layout, script and all data files at the top level:

    input_dir/
      run.sh                 (REQUIRED - the shell wrapper you write)
      gsas2_pawley*.py       (or gsas2_rietveld.py / gsas2_autoindex.py)
      curation.py            (REQUIRED - the script imports it)
      pattern_T1.xye
      pattern_T2.xye
      ...
      structure.cif          (Rietveld only)

Source: `matmaster/skills/pxrd-refinement/scripts/`. Always
copy `curation.py` alongside any of the three scripts; missing it produces an
immediate `ModuleNotFoundError`. Always write a `run.sh` that calls the script
with all arguments (see § 2 templates).

## 4. Submit / poll / download

Submit (returns `job_id`):

    Bohrium(action="submit",
            input_dir="input_dir",
            image="registry.dp.tech/dptech/dp/native/prod-19853/xrd-app:dev-260119",
            machine="c32_m128_cpu",
            cmd="bash run.sh > log 2>&1")

Query current status when needed (single-shot, no blocking wait):

    Bohrium(action="query", job_id=job_id)
    # status=Running              -> do other work; background monitoring continues
    # status=Finished              -> download
    # status=Failed                -> download anyway, read log

Download exactly once after a terminal state:

    Bohrium(action="download", job_id=job_id, result_dir="results/run_<job_id>")

### Polling discipline

- **Always `Bash(sleep 45)` before polling.** Never sleep > 60 s. A Pawley batch
  finishes in 3-8 min; 4-5 polls suffice.
- Submit ALL jobs first, then loop: `Bash(sleep 45)` → poll all → if any still
  Running, sleep again. Serial submit-poll-download triples wall time.

### Re-submitting

There is no scenario where re-submitting the same `input_dir + cmd` produces a
different result - the image is deterministic. If a job fails, change something
intentional (different cell, `--dmin`, `--tmin/--tmax`, space group) before the
next submit. Looping is wasted quota and wasted turns.

## 5. Output layout

After `Bohrium(action="download", ...)`:

    results/run_<job_id>/
      log                    # script stdout + stderr (GSAS-II progress, warnings)
      result.json            # or results.json (batch) / candidates.json (autoindex)
      plots/                 # if --debug-plot was passed
        pattern_T1_curation.png
        pattern_T1_pattern.csv
      refined.cif            # Rietveld only
      (copies of input files)

Read `log` first when `result.json` is missing or `success=false` - GSAS-II
warnings about SVD singularities, missing reflections, and wavelength mismatch
land there.

## 6. Failure triage

| Symptom | Likely cause | Fix |
|---|---|---|
| Result dir lacks `log` AND lacks `result.json` (only inputs + `mpi_debug.sh` come back) | The cmd never executed — almost always because the python3 invocation was placed directly in the `cmd` field instead of in `run.sh`. Bohrium silently drops commands with embedded quotes. | (1) Verify `run.sh` exists in `input_dir/` with the correct python3 invocation; (2) verify the submit used `cmd="bash run.sh > log 2>&1"` and nothing else; (3) ensure `curation.py` is in `input_dir/`; (4) re-submit. **Do NOT abandon Bohrium and write a local Python solver — that violates SKILL.md contract 1.** |
| `log` exists but has GSAS-II progress and no `result.json` | Script crashed mid-refinement | Read `log` tail for the traceback; usual culprits: SVD singular (cell badly off), wavelength mismatch, missing `--space-group` |
| `ModuleNotFoundError: curation` | `curation.py` not staged | Copy it into `input_dir/` and re-submit |
| `ModuleNotFoundError: GSASIIscriptable` | `--gsas2-path` mis-set | Omit `--gsas2-path`; the default works on this image |
| `success=true` but `wR > 0.20` (Pawley) / `Rwp > 0.15` (Rietveld) | Wrong initial cell or space group | See `gsas2_refinement_guide.md` "High wR causes and fixes" — get a better cell, **do not report the bad one** |
| `success=true` but volume is ~2x / 3x / 4x of initial | Refinement converged on a supercell — cell is WRONG | Reject result per SKILL.md contract 3; revisit initial cell |
| Auto-index returns `candidates: []` after full timeout | Likely the DFT-simulated low-angle artifact case | See `autoindex.md` — almost never recoverable; ask user for cell |

Deeper script-level errors (SVD singular, Pawley reflection generation, etc.)
in `gsas2_refinement_guide.md` section 8.

## 7. Dev/debug shell (humans only)

`ssh root@gqfj1207340.bohrium.tech` runs the same image at the same paths. The
agent should NOT SSH - production workflow is exclusively
`Bohrium(action=submit/poll/download)`. SSH is for engineers manually validating
script changes before the next eval run.
