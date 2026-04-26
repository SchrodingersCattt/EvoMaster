# Ab-initio Auto-indexing (`gsas2_autoindex.py`)

Wraps GSAS-II's Visser-algorithm `DoIndexPeaks` and feeds it peaks from
`curation.py`. Used **only when no reference cell exists at all** for the
phase you are trying to refine.

## Read this first

Auto-indexing on PXRD is a hard problem on clean lab data and a much harder
problem on simulated / artifact-laden data. Before running, check that you
genuinely have no other cell source - user prompt, CIF, prior refinement, an
adjacent VT-PXRD point, literature for an isostructural compound. **Any of those
beats indexing.**

If you do run it, treat the output as a hypothesis to verify with a Pawley
refinement, not as a final answer. A "winning" candidate with M20 = 8 and 12
peaks is not a cell - it is a guess that survived the prefilter.

## Known failure mode: DFT-simulated PXRD with low-angle artifact

Most DFT-simulated VT-PXRD patterns we see exhibit a smooth monotonic descending
"hump" between roughly 5 deg and 13 deg in 2theta - low-angle scattering /
detector smear, not Bragg signal. Curation correctly clips that prefix
(`tmin_cut` lands around 13-15 deg).

The cost: the (010)/(020)/(100) reflections that, on real lab data, would sit
around 2theta = 10-13 deg are gone. What auto-indexing sees is 12-14 mid-2theta
peaks (15-35 deg). For a monoclinic cell that has 6 free parameters, those peaks
are usually under-determined, and `DoIndexPeaks` returns `candidates: []`
after burning the full timeout - or returns a sub-cell whose volume is
1/8 / 1/4 / 1/2 of the true volume. Materials Studio Reflex/TREOR can sometimes
crack the same data because (a) it is a different algorithm and (b) it does not
clip the artifact prefix the same way - but TREOR uses parts of the pattern
GSAS-II Visser is not designed to use.

**Practical rule:** if curation clipped a substantial prefix (say `tmin_cut` >
12 deg), do not expect Visser to converge. Tell the user the indexer cannot
recover the cell from the post-curation peak set and ask for a reference cell.

## Usage

    python3 gsas2_autoindex.py \
      --data pattern.xye \
      --bravais monoclinic-P \
      --wavelength 1.5406 \
      --top-n-peaks 18 \
      --tmax-index 35 \
      --v1-hint 0 \
      --timeout 200 \
      --debug-plot plots \
      -o candidates.json

## Parameter notes

- `--bravais` (required) — comma-separated Bravais families to try; e.g.
  `monoclinic-P` or `cubic-P,tetragonal-P,monoclinic-P`. Each family burns the
  full `--timeout` independently. **Always pass at least one** — running
  blind across all 14 families is hours of wasted compute.

- `--top-n-peaks` default 18 — soft upper bound on peaks fed to Visser.
  Empirically (from Materials Studio Reflex/TREOR practice) 12-17 strong peaks
  in the 10-35 deg range is the sweet spot. More peaks introduce overlap-
  ambiguous high-angle reflections that lower the figure of merit.

- `--tmax-index` default 35 deg — peaks above this are dropped before indexing.
  Above ~35 deg in monoclinic systems peak spacing is typically < 2 deg,
  ambiguating single-peak (h,k,l) assignment. Raise to ~50 deg only when the
  cell is very large (V > 5000 cubic angstroms) or symmetry is triclinic.

- `--v1-hint` default 0 — initial unit-cell volume guess for Visser. **Leave at
  0 unless you have a credible volume range** (e.g. literature value for an
  isostructural compound). A wrong non-zero hint locks Visser to that volume
  band and prevents the auto-sweep.

- `--timeout` default 200 s — per-Bravais wall budget, hard-enforced via
  `SIGALRM` because GSAS-II's own `timeout=` kwarg is advisory and the
  monoclinic/triclinic Visser searches will run for hours past it.

- `--tmin` not normally needed — `curation.detect_artifact_end` auto-clips the
  low-2theta artifact. Pass `--tmin 5` only when you can see real Bragg peaks
  below 13 deg in the raw data.

## Output JSON

    {
      "success": true,
      "n_peaks_picked": 12,
      "peaks_2theta": [...],
      "candidates": [
        {"bravais": "monoclinic-P", "a":..., "b":..., "c":..., "beta":..., "V":..., "M20":..., "X20":..., "Nc":...},
        ...
      ],
      "best": {<top-1 candidate>} | null,
      "curation": {<verdict + diagnostics>},
      "search":   {<families, timeout, hints>}
    }

`candidates[]` is sorted by M20. `best` is `candidates[0]` or `null` when
nothing passed the M20 threshold.

## Validating a candidate

Never adopt a candidate without these checks:

1. **Bravais matches your hint.** A `monoclinic-P` candidate from a request that
   listed `cubic-P` is suspicious.
2. **Volume is physically reasonable.** Compare to literature V/Z for an
   isostructural compound, or back-of-envelope from atom count and average
   bond length.
3. **M20 >= 10** is a soft floor for "worth trying". M20 < 10 with only 1-2
   candidates is usually noise.
4. **Run a Pawley refinement against the candidate cell.** If `wR > 0.20` after
   convergence, the cell is wrong even if M20 looked OK. See contract 3 in
   `SKILL.md`.

When `candidates: []`:

- If `n_peaks_picked < 8`: not enough peaks for any indexer; revisit curation
  parameters or stop.
- If `n_peaks_picked >= 8` but search timed out with nothing: try other
  Bravais families, but expect failure on artifact-laden DFT data per the
  failure-mode section above.
- Either way, if you have already tried 2-3 Bravais families with 0
  candidates, **stop indexing and ask the user for a reference cell**.
