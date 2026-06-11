# SVG discipline — coordinate rules for hand-written diagrams

Ported from the Imagine visual-creation guide, adapted to this channel:
static PNG via `svg2png.py` (cairosvg), white opaque background, inline
presentation attributes only. No CSS classes, no `<style>` blocks, no
interactivity, no animation, no dark mode. Read fully before writing any SVG.

## 1. Canvas contract

- Root: `<svg width="680" viewBox="0 0 680 H" xmlns="http://www.w3.org/2000/svg">`.
  The 680 width is load-bearing — all width math below assumes it. Never
  shrink the viewBox to hug narrow content; center the content instead.
- First element: the white background rect from the prelude, full canvas.
- Safe area: x = 40..640, y = 40..(H−40). Nothing may sit outside x = 0..680.
- H = (lowest y of any element, text baselines + 4px descent included) + 40.
  Compute it after layout — never guess.
- Negative x or y coordinates are forbidden; the viewBox starts at 0,0.

## 2. Pre-rasterize checklist (run over the finished SVG, every time)

1. Lowest element: max(y + height) over rects, max(baseline y + 4) over text.
   H equals that value + 40.
2. Rightmost element: max(x + width) over rects ≤ 680; for
   text-anchor="start" text, x + estimated width ≤ 680.
3. text-anchor="end" extends LEFT from x: estimated width must be ≤ x —
   risky whenever x < 60; prefer anchor "start" and right-shift the column.
4. No unintended overlaps: for every pair of elements not meant to layer
   (label/label, label/stroke, box/box), bounding boxes must not intersect.
   Deliberate overlaps only: a label centered in its own box, an arrowhead
   touching its target, a highlight rect behind its subject.
5. Same-row boxes: left box (x + width) + 20 ≤ right box x.
6. Every connector `<line>`/`<path>`/`<polyline>` carries `fill="none"`.
7. Every `marker-end` URL points at a marker that exists in `<defs>`.
8. Every `<text>` carries explicit font-family, font-size and fill.

## 3. Text metrics (estimate widths before drawing)

| Content | Rule of thumb |
|---|---|
| Latin at 14px | ~8 px per character |
| Latin at 12px | ~7 px per character |
| CJK at any size | ~1.1 × font-size per character (≈15 px at 14px) |
| Formulas, sub/superscripts, ∑ ∫ √ Å Γ | add 30-50% to the estimate |

- Box width = max(title estimate, subtitle estimate) + 24 (2 × 12px padding).
- Worked example: "Glucose (C₆H₁₂O₆)" is 18 chars at 14px ≈ 144px, plus the
  formula surcharge ≈ 190px, +24 → the rect must be ≥ 214px wide. A 160px box
  WILL overflow — shorten the label or widen the box.
- SVG text never auto-wraps: a second line is a second `<text>` element. If a
  subtitle needs wrapping it is too long — cut it to ≤5 words.

## 4. Tier packing (compute before placing)

- One horizontal row holds at most 4 full-width boxes (~140px each). For 5+
  items: shrink to ≤110px, wrap to a second row, or split the figure.
- Budget check for 4 boxes in the 40..640 safe span (600px):
  - WRONG: x = 40,160,260,360 at width 160 → adjacent boxes overlap 40-60px.
  - RIGHT: width 130, gap 20 → 4×130 + 3×20 = 580 ≤ 600; x = 50,200,350,500.
- Trees: size the leaf tier first; a parent spans at least its children.

## 5. Boxes and in-box text

- Heights: single-line box 44px, two-line box 56px. Same content type = same
  height across the figure.
- Corner radius rx=4 default, rx=8 max for emphasis; rx ≥ height/2 reads as a
  pill — deliberate use only.
- Stroke width 0.5 on all box borders. Colors per ramp trio: 50 fill /
  600 stroke / 800 title / 600 subtitle (palette table in style-contract.md).
- Vertical centering: give every in-box `<text>` the attribute
  `dominant-baseline="central"` with y at the CENTER of the slot it occupies.
  Two-line 56px box with top edge y0: title y = y0+18, subtitle y = y0+38.
- 24px inner padding; ≥12px between text and box edge; ≥60px between boxes;
  sentence case; no emoji; subtitles ≤5 words.

## 6. Connectors and arrows

- Every connector carries `fill="none"` — paths default to black fill, and a
  curved connector without it renders as a filled black blob.
- Widths: 1 for arrows, 0.5 for leader lines and box borders. Arrowheads come
  from the prelude markers (`arrow-gray`, `arrow-purple`, `arrow-teal`) — use
  the one matching the line color; clone the marker in defs (new id, new
  stroke hex) for any other line color. Markers are fixed-color because the
  rasterizer does not support context-stroke.
- Arrowheads stop 10px before the target box edge.
- Intersection check before each connector: trace its segments against every
  box already placed; if it crosses any unrelated rect's interior, re-route
  with an L-bend: `<path d="M x1 y1 L x1 ym L x2 ym L x2 y2" fill="none"/>`.
- Arrow labels are a last resort — prefer the source/target box subtitle or
  the answer prose. A necessary label sits in clear space ≥8px from strokes.
- One flow direction per figure: all top-down or all left-right.
- Cycles are never drawn as rings: lay the stages in a line and close the
  loop with a text note "↻ returns to <first stage>" near the last stage.
  Cyclic processes with per-stage detail become several figures delivered
  with connecting prose (contract narrative rules).

## 7. Labels outside boxes

- Minimize standalone labels: every text should live in a box or the legend.
- When margin labels are needed (typical in illustrative figures): pick ONE
  side — default right, text-anchor="start" — and reserve ≥140px of margin on
  that side. Connect with dashed leaders (stroke-dasharray="3 3", width 0.5,
  stroke #888780). Keep 8px clear air between any text and any stroke.
- Legend, when color encodes meaning: one row of 12×12 rx=2 swatch rects with
  12px labels, placed in the top or bottom margin clear of all shapes.

## 8. Routing: pick the figure type from the user's verb

| Request sounds like | Type | Rules |
|---|---|---|
| "walk me through", "what are the steps", "what's the flow / pipeline" | Flowchart | §9 |
| "what's the architecture", "what's inside", "how is it organized" | Structural | §10 |
| "how does X actually work", "explain X", "give me an intuition" | Illustrative | §11 |
| "database schema", "ERD", field lists | Not a diagram | markdown table in prose |

Same noun, different verb → different figure: "transformer architecture" is
structural; "how does attention work" is illustrative. The default for an
unqualified "how does X work" is illustrative — do not retreat to a flowchart
because it feels safer.

## 9. Flowchart specifics

- Max 4-5 nodes per figure. A request naming 6+ components becomes a stripped
  overview (boxes plus 1-2 main arrows, no fan-outs) plus one figure per
  interesting sub-flow, each with 3-4 nodes and room to breathe.
- Components: single-line node (44px), two-line node (56px, title + ≤5-word
  subtitle), gray trio for start/end/generic steps.
- Decision branches: put the condition in the TARGET box subtitle ("yes — …" /
  "no — …") instead of floating text on the lines.

## 10. Structural diagram specifics

- Containers: outermost rounded rect rx=20-24, ramp 50 fill + 600 stroke at
  0.5px, label INSIDE at top-left (x+20, baseline ≈ y+28; 14px weight 500,
  ramp 800).
- Inner regions: rx=8-12. Pick a RELATED ramp for related substructure and a
  CONTRASTING ramp for functionally different regions; parent and child never
  share the same fill (the hierarchy flattens visually).
- ≥20px padding inside every container; ≥16px gap between sibling regions;
  ≤3 nesting levels at 680px width.
- External inputs/outputs sit outside the outermost container, arrows
  pointing in/out, one-word or short labels.
- Regions contain text only: name (14px) + role (12px). No flowchart boxes,
  icons, or illustrations inside regions.
- Schematic containment beats literal shapes: a dashed rect
  (stroke-dasharray="4 3") labelled "Reactor vessel" reads cleaner than a
  drawn vessel outline that clips its content.

## 11. Illustrative diagram specifics

- Draw the mechanism, not boxes about it. Physical subjects get simplified
  cross-sections; abstract subjects get a spatial metaphor that makes the
  mechanism obvious (a stack of layers, a funnel into buckets, a ball on a
  surface). A good illustrative figure still works with the labels removed.
- Fidelity ceiling: every shape reads at a glance; a `<path>` needing more
  than ~6 segments is too detailed — simplify. Recognizable silhouette beats
  accurate contour.
- Color encodes intensity, not category: warm ramps (amber/coral/red) for
  heat/energy/active, cool (blue/teal) for cold/dormant, gray for inert
  structure. All hexes from the palette table.
- Shapes MAY layer deliberately (z-order = source order): a pipe entering a
  tank, lines fanning through layers. Text may NEVER be crossed by a stroke —
  labels go to the quiet margin with leaders (§7). No quiet region left means
  the drawing is too dense: remove something or split into two figures.
- Small state indicators are encouraged when they show physical state:
  triangles for flames, circles for particles/bubbles, wavy lines for
  steam/heat, short parallel lines for vibration. Simple primitives only.
- ONE `<linearGradient>` (exactly two stops, same ramp) is allowed per figure
  to show a continuous physical property (temperature stratification,
  pressure drop). No radial gradients, no multi-stop fades, no decoration.
  If two stacked flat rects say the same thing, use them instead.
- Lines stop at component edges: compute the boundary coordinate and end the
  segment there — never draw through a shape relying on a fill to hide it.

## 12. What does not exist on this channel

The Imagine guide assumes a live HTML/SVG renderer; here the SVG becomes a
static PNG. Therefore: no class / `<style>` / CSS variables, no
onclick/sendPrompt, no hover, no animation or @keyframes, no steppers or
tabs, no links, no dark mode. Express sequence with multiple figures and
prose; express state changes with before/after panels instead of toggles.
