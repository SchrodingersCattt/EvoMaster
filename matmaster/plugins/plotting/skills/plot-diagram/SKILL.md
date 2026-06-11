---
name: plot-diagram
description: "Hand-draw flowcharts, architecture/structure diagrams, and mechanism schematics as SVG, rasterize to PNG, and attach as answer figures. Use when the user asks to draw, sketch or illustrate a workflow, process, pipeline, architecture, containment structure, or how a mechanism works — figures built from shapes and labels rather than plotted data arrays. Numeric data plots go to plot-chart; canonical materials plots go to plot-materials."
---

# Plot Diagram

Produce reference diagrams (flowcharts, structural) and intuition diagrams
(illustrative schematics) as hand-written SVG, rasterized to PNG and published
with AttachFigure.

## Step 0 — mandatory

Read `${PLUGIN_DIR}/shared/style-contract.md` with the Read tool before doing
anything else. It is the single source of truth for figure delivery and style.
Do not produce or attach any figure without having read it in this session.

## Step 1 — route and budget

Read `${SKILL_DIR}/references/svg-discipline.md` in full before writing any
SVG element — it carries the figure-type routing table (§8) and every
coordinate rule. Then decide:

1. Figure type from the user's VERB, not the noun: flowchart / structural /
   illustrative (discipline §8).
2. Complexity budget: ≤4-5 nodes per figure, box subtitles ≤5 words, ≤2 color
   ramps plus gray. A request naming 6+ components becomes a stripped
   overview figure plus one figure per sub-flow, delivered as a narrative
   with connecting prose (contract rules).

## Step 2 — write the SVG

- Start from `${PLUGIN_DIR}/shared/svg_prelude.txt`: copy it into the
  workspace as `<name>.svg`, replace all three REPLACE_HEIGHT tokens with
  the computed height, and delete the example block (keep the closing
  </svg>).
- Inline presentation attributes only — every element carries its own
  font-family/font-size/fill/stroke. No class, no <style>, no CSS: the
  rasterizer supports a narrow SVG subset and silently drops styling it does
  not understand.
- Compute every coordinate before drawing (text widths → box widths → tier
  packing → connector routes), then run the discipline §2 checklist over the
  finished file.

## Step 3 — rasterize and self-check

```bash
python3 ${PLUGIN_DIR}/shared/svg2png.py /abs/workspace/<name>.svg /abs/workspace/<name>.png
```

View the PNG with the Read tool before attaching. Fix the SVG and re-rasterize
if any of these appear: missing glyphs (tofu boxes — usually the declared font
lacks that glyph; swap it for a supported character or plain text), text
overflowing a box or touching a stroke, absent or misrotated arrowheads,
content clipped at an edge. Iterate until clean — never attach an unchecked
figure.

## Step 4 — deliver

One AttachFigure call for the answer's figures (all-or-nothing batch), then
reference each as [[fig:<figure_id>]] with connecting prose between figures
(contract rules).

## Channel limits

The figure is a static PNG: no interactivity, no animation, no click-through,
no hover, no steppers. Sequence is expressed as several figures with prose
between; state change as before/after panels; cycles as a linear stage row
plus a "↻ returns to …" note — never a ring layout (discipline §6).
