"""Parse ``--slices`` expressions: ``cap cap[dom] cap[d1,d2]`` (OR of slices)."""

from __future__ import annotations

from evaluation.core.schemas import CapabilitySlice


def _split_slice_segments(raw: str) -> list[str]:
    """Split on whitespace outside ``[...]``; ``[...]`` must not contain whitespace."""
    segments: list[str] = []
    buf: list[str] = []
    depth = 0
    for ch in raw:
        if ch == '[':
            depth += 1
            buf.append(ch)
        elif ch == ']':
            if depth < 1:
                raise ValueError('unbalanced "]" in slices expression')
            depth -= 1
            buf.append(ch)
        elif ch.isspace() and depth == 0:
            seg = ''.join(buf).strip()
            if seg:
                segments.append(seg)
            buf = []
        else:
            if ch.isspace() and depth > 0:
                raise ValueError(
                    'spaces are not allowed inside "[...]" in slices expression'
                )
            buf.append(ch)
    if depth != 0:
        raise ValueError('unbalanced "[" in slices expression')
    tail = ''.join(buf).strip()
    if tail:
        segments.append(tail)
    return segments


def parse_slices_expression(expr: str) -> list[CapabilitySlice]:
    """Parse a slice string into OR-of-slices.

    - Whitespace **outside** ``[...]`` separates slices (OR).
    - ``cap`` alone: that capability, any domain.
    - ``cap[a]`` or ``cap[a,b]``: capability must match and domain in the listed set (OR).
    - No whitespace inside ``[...]`` (use commas only, e.g. ``[a,b]`` not ``[a, b]``).

    Raises:
        ValueError: empty input, malformed brackets, empty domain list, or space inside brackets.
    """
    raw = expr.strip()
    if not raw:
        raise ValueError('slices expression cannot be empty')

    out: list[CapabilitySlice] = []
    for seg in _split_slice_segments(raw):
        if '[' not in seg:
            out.append(CapabilitySlice(capability=seg, domains=None))
            continue
        if seg.count('[') != 1 or seg.count(']') != 1:
            raise ValueError(f'malformed slice (brackets): {seg!r}')
        lb, rb = seg.index('['), seg.rindex(']')
        if rb <= lb:
            raise ValueError(f'malformed slice (brackets): {seg!r}')
        cap = seg[:lb].strip()
        inner = seg[lb + 1 : rb]
        if any(c.isspace() for c in inner):
            raise ValueError(f'no whitespace allowed inside "[...]" in slice {seg!r}')
        if not cap:
            raise ValueError(f'missing capability before "[" in {seg!r}')
        if not inner:
            raise ValueError(f'empty domain list in {seg!r}')
        domains = [d.strip() for d in inner.split(',') if d.strip()]
        if not domains:
            raise ValueError(f'empty domain list in {seg!r}')
        out.append(CapabilitySlice(capability=cap, domains=domains))

    return out
