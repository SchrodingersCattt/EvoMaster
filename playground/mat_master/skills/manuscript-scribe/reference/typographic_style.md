# Typographic Style Guide (Manuscript-Scribe)

Mandatory formatting rules for all manuscript-scribe output. The export scripts (`export_docx.py`, `export_latex.py`) apply many of these automatically, but the **LLM must produce correctly formatted Markdown** for the conversion to work.

## 1. Content and Expression

- **No raw input keywords or variable names** in computational reports. Do NOT write `RUN_TYPE ENERGY`, `EPS_SCF`, `CUTOFF 600`, `&DFT ... &END DFT`. Instead use physical descriptions: "single-point total-energy calculation", "self-consistent convergence threshold", "plane-wave cutoff of 600 Ry", "projected density of states".
- **No file names or paths** in computational reports. Do NOT write `cp2k.inp`, `output.log`, `*.pdos`, `HOCO_CUBE.cube`. Instead use: "the input file", "the output log", "the PDOS data", "the orbital cube file".
- **Mechanism-oriented narrative**: Computation reports should build toward physical interpretation — e.g. use HOCO/LUCO spatial separation and PDOS decomposition to support a charge-transfer assignment (MLCT, MLLCT, etc.), not just list numbers.

## 2. Terminology and Periodic Systems

- **Define every abbreviation** at first use: "density functional theory (DFT)", "projected density of states (PDOS)". After the definition, use only the abbreviation.
- **Periodic systems**: Use **HOCO / LUCO** (highest occupied / lowest unoccupied crystal orbital), NOT HOMO / LUMO. At first use, add one sentence: "In periodic systems these are crystal orbitals rather than molecular orbitals, denoted HOCO and LUCO."
- **Band-edge terminology**: Use "valence-band maximum (VBM)" and "conduction-band minimum (CBM)" for periodic solids, not "HOMO energy" / "LUMO energy".

## 3. Physical Quantities and Font Conventions

### In Markdown (what the LLM writes)

Use these Markdown conventions so export scripts can convert correctly:

- **Italic for scalar physical quantities**: `*U*`, `*E*`, `*k*`, `*T*`, `*V*`. Bold for vectors: `**F**`, `**k**`.
- **Subscripts**: Use `_{text}` notation: `*U*_{eff}`, `*E*_{F}`, `*E*_{g}`, `*k*_{B}`. The export scripts convert these to proper subscripts (Word subscript formatting / LaTeX `_{\mathrm{...}}`).
- **Superscripts**: Use `^{text}` notation: `10^{−6}`, `cm^{−1}`, `Å^{2}`. The export scripts convert these to proper superscripts.
- **Subscripts in running text** (not in math): descriptive subscripts are roman (upright), not italic. The export scripts handle this: `*U*_{eff}` → *U* (italic) + eff (roman subscript).

### Rules (applied by writer AND export)

- Physical quantity symbols: **always italic** (*U*, *E*, *T*, *k*, *V*, *P*, *H*, *G*, *S*).
- Descriptive subscripts/superscripts: **always roman** (upright). Examples:
  - *U*_{eff} — *U* italic, "eff" roman subscript
  - *E*_{F} — *E* italic, "F" roman subscript (Fermi)
  - *E*_{g} — *E* italic, "g" roman subscript (gap)
  - *k*_{B} — *k* italic, "B" roman subscript (Boltzmann)
- **No Unicode fake superscripts/subscripts**: Do NOT use `³`, `₂`, `⁻⁶` etc. Use `^{3}`, `_{2}`, `^{−6}`. The export scripts produce proper Word superscript/subscript formatting or LaTeX markup.
  - Exception: In pure Markdown output (no export), Unicode is acceptable as a fallback.

## 4. Symbols: Dash and Minus

- **Range / connection: en-dash "–"** (U+2013), NOT hyphen "-". Examples: `1.88–1.89 Å`, `pp. 57–70`, `Cu–N bond`.
- **Negative sign: minus "−"** (U+2212), NOT hyphen. Examples: `−6`, `−0.5 eV`, `10^{−6}`.
- **Hyphen "-"** only for compound adjectives: `self-consistent`, `plane-wave`, `Broyden-type`.

The LLM must use the correct Unicode characters (–, −) in the Markdown source. The export scripts preserve them.

## 5. Chemical Formula Formatting

- **Element order**: Follow Hill system — C first, H second, then remaining elements alphabetically. For inorganic: electropositive first. Examples: `CH_{4}`, `C_{79}H_{88}N_{23}O_{12}S_{16}Cu_{19}`.
- **No spaces** between elements in a formula.
- **Element counts as subscripts**: Write `C_{7}H_{8}N_{2}O` in Markdown. The export scripts convert the `_{n}` to proper subscripts in Word/LaTeX.
- For simple inline mentions where subscript notation is cumbersome, the export scripts also auto-detect patterns like `CO2`, `H2O`, `Fe2O3` and subscript the numbers.

## 6. Significant Figures

- **Match precision to method accuracy**: DFT bond lengths to 2 decimal places (e.g. 1.89 Å, not 1.893742 Å). Band gaps to 2 decimal places (e.g. 2.34 eV). Lattice parameters to 3–4 decimal places.
- **Use "≈"** for approximate or rounded values in conclusions: "the band gap is ≈ 2.3 eV".
- **Do not pile unnecessary decimals**: `5 × 10^{−6}` not `5.000000 × 10^{−6}`.

## 7. Citations and References (typography)

### In-text citations
- Citation numbers `[n]` must be **superscript** and placed **immediately after** the relevant clause or sentence, before the period. Example: "... as reported previously^{[1]}."
- In Markdown, write `[n](url)`. The export scripts render as superscript with hyperlink.

### References section format
- Fixed format: `[n] Authors. Title. *Journal*, **Year**, Volume, Pages. URL`
- **Journal name**: italic (*J. Chem. Phys.*)
- **Year**: bold (**2020**)
- **Page range**: en-dash (477–506, not 477-506)
- **Article numbers**: acceptable in place of page numbers (e.g. 194103)

The export scripts auto-format the References section: detect journal names (italic), years (bold), and page ranges (en-dash).

## 8. Summary: What the LLM Must Do vs What Export Scripts Do

| Rule | LLM responsibility (Markdown) | Export script responsibility |
|------|-------------------------------|----------------------------|
| Italic physics quantities | Write `*E*`, `*U*` | Convert to italic font |
| Subscripts | Write `_{eff}`, `_{2}` | Word subscript / LaTeX `_{\mathrm{}}` |
| Superscripts | Write `^{−6}`, `^{2}` | Word superscript / LaTeX `^{}` |
| Chemical formula subscripts | Write `C_{7}H_{8}` or `CO2` | Auto-detect and subscript numbers |
| En-dash for ranges | Write `–` (U+2013) | Preserve |
| Minus sign | Write `−` (U+2212) | Preserve |
| No raw keywords | Describe physically | N/A (content rule) |
| No file paths | Use generic descriptions | N/A (content rule) |
| HOCO/LUCO terminology | Use correct terms | N/A (content rule) |
| Reference formatting | Write `*Journal*, **Year**, Vol, Pages` | Auto-detect and format |
| Significant figures | Round appropriately | N/A (content rule) |
