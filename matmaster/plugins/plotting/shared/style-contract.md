# Plotting style & delivery contract

Single source of truth for every plot-* skill. Read this before producing any
figure. Skills add domain rules on top; nothing below is repeated there.

## Delivery

- Generate figure files with Bash inside the session workspace, absolute
  paths. Final formats: .png (default), .jpg/.jpeg, .webp. Never deliver SVG
  or PDF as the published figure.
- Publish with one AttachFigure call per answer batch. Publishing is
  all-or-nothing: if any path is rejected nothing is published — fix the
  failing path and resend the whole batch.
- After a successful AttachFigure call, reference every figure in the answer
  body with its [[fig:<figure_id>]] marker. Published but unreferenced is a
  bug; promised in prose but not attached is a bug.
- Caption: one sentence, self-contained (readable without the answer text),
  in the user's language. The caption carries the accessibility burden of a
  bitmap — name what is shown, the encoding, and the one takeaway.
- Multi-figure answers are a narrative: figures appear in the order the text
  discusses them, with connecting prose between figure references — never a
  wall of consecutive markers.
- Tables are never images. Tabular results go in markdown tables in the
  answer body.
- Never invent data: every plotted point comes from a real computed result or
  a value the user explicitly provided. No illustrative fake numbers, no
  guessed coordinates, no "typical" curves. If the data is missing, say what
  must be computed instead of plotting.

## Style

- White opaque background, always:
  `fig.savefig(path, facecolor="white", dpi=200, bbox_inches="tight")` — or
  `mm_style.save_figure(fig, path)`, which does exactly this. SVG sources get
  the same via the prelude's white background rect.
- All color comes from the nine-ramp palette (constants in `mm_style.py`).
  Light-theme trio per ramp: 50 fill / 600 stroke and lines / 800 title text /
  600 secondary text. The text stops govern text on or labeling that ramp's
  colored elements; default chart text uses the neutral gray tiers — gray-800
  titles, gray-600 secondary (axis labels, ticks, legends) — wired as
  rcParams by `mm_style.apply()`.

| Ramp | 50 | 100 | 200 | 400 | 600 | 800 | 900 |
|---|---|---|---|---|---|---|---|
| purple | #EEEDFE | #CECBF6 | #AFA9EC | #7F77DD | #534AB7 | #3C3489 | #26215C |
| teal | #E1F5EE | #9FE1CB | #5DCAA5 | #1D9E75 | #0F6E56 | #085041 | #04342C |
| coral | #FAECE7 | #F5C4B3 | #F0997B | #D85A30 | #993C1D | #712B13 | #4A1B0C |
| pink | #FBEAF0 | #F4C0D1 | #ED93B1 | #D4537E | #993556 | #72243E | #4B1528 |
| gray | #F1EFE8 | #D3D1C7 | #B4B2A9 | #888780 | #5F5E5A | #444441 | #2C2C2A |
| blue | #E6F1FB | #B5D4F4 | #85B7EB | #378ADD | #185FA5 | #0C447C | #042C53 |
| green | #EAF3DE | #C0DD97 | #97C459 | #639922 | #3B6D11 | #27500A | #173404 |
| amber | #FAEEDA | #FAC775 | #EF9F27 | #BA7517 | #854F0B | #633806 | #412402 |
| red | #FCEBEB | #F7C1C1 | #F09595 | #E24B4A | #A32D2D | #791F1F | #501313 |

- Color encodes meaning, not sequence: the same category keeps the same ramp
  everywhere; gray marks neutral/structural elements. When color carries
  meaning, add a one-line legend.
- Ramp budget: ≤2 ramps per figure (gray excluded) when color groups
  categories — diagrams, fills, grouped bars. Multi-series line charts may
  take successive ramps from `mm_style.CATEGORY_ORDER`, one per series, each
  paired with its second cue.
- Prefer purple/teal/coral/pink for generic categories. Reserve
  blue/green/amber/red for genuine info/success/warning/error semantics —
  except illustrative figures mapping physical quantities (temperature,
  pressure, energy), which may use warm/cool ramps freely.
- Never separate series by color alone: pair every color with a second cue
  (linestyle, marker, or hatch) and show both in the legend.
- Two label text sizes only: 14 for axis/node labels and titles, 12 for
  secondary text (ticks, legends, annotations, subtitles). Headline numbers
  on metric cards are data marks, not labels, and may go larger. Sentence
  case for Latin-script text. No emoji.
- Display numbers at context precision: counts as integers, percentages with
  1-2 decimals. The minus sign precedes any currency/unit symbol
  (−$5, not $−5).
- In-figure text language follows the user's language. CJK works via
  `mm_style.apply()` (matplotlib) or the prelude's font setup (SVG).
