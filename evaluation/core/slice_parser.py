"""Parse ``--slices`` expressions: ``cap cap[dom] cap[d1,d2] cap@t1,t2 @t1 #scope`` (OR of slices)."""

from __future__ import annotations

from evaluation.core.schemas import CapabilitySlice


def _split_slice_segments(raw: str) -> list[str]:
    """Split on whitespace outside ``[...]``; ``[...]`` must not contain whitespace.

    Whitespace after ``@`` (before the first tag) or after a comma in the tag list
    stays in the same segment so ``cap@ wf_batch`` / ``cap@t1, t2`` surface the
    tag-list rules (no spaces) instead of being split into a bare ``cap@`` slice.
    """
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
            cur = ''.join(buf)
            stripped = cur.strip()
            # Keep one segment: cap@ … or cap@[dom]@…, … (comma-space in tag list).
            if stripped.endswith('@') or stripped.endswith(','):
                buf.append(ch)
                continue
            seg = stripped
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


def _parse_capability_domain_token(seg: str) -> tuple[str, list[str] | None]:
    """Parse ``cap`` or ``cap[a,b]`` (no ``@`` or ``#`` in *seg*)."""
    if '[' not in seg:
        return seg.strip(), None
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
    return cap, domains


def _parse_tags_after_at(*, piece: str, rest: str) -> list[str]:
    """Parse comma-separated tags after a single ``@``; no whitespace in *rest*."""
    if not rest:
        raise ValueError(f'empty tag list after "@" in slice {piece!r}')
    if any(c.isspace() for c in rest):
        raise ValueError(
            f'no whitespace allowed after "@" in slice {piece!r} '
            '(use commas only, e.g. cap@tag1,tag2)'
        )
    tags: list[str] = []
    for p in rest.split(','):
        t = p.strip()
        if not t:
            raise ValueError(f'empty tag entry after "@" in slice {piece!r}')
        tags.append(t)
    return tags


_VALID_SCOPES = {'platform', 'knowledge'}


def _extract_scope(piece: str) -> tuple[str, str | None]:
    """Extract ``#scope`` from a slice segment, return (remainder, scope).

    ``#`` may appear at any position in the segment:
    - ``#platform`` → ('', 'platform')
    - ``input_generation#knowledge`` → ('input_generation', 'knowledge')
    - ``@eng_vasp#platform`` → ('@eng_vasp', 'platform')
    - ``input_generation@eng_vasp#platform`` → ('input_generation@eng_vasp', 'platform')

    At most one ``#`` is allowed per segment.
    """
    if '#' not in piece:
        return piece, None
    if piece.count('#') > 1:
        raise ValueError(
            f'each slice may contain at most one "#" scope marker: {piece!r}'
        )
    left, scope_str = piece.split('#', 1)
    scope_str = scope_str.strip()
    if not scope_str:
        raise ValueError(f'empty scope after "#" in slice {piece!r}')
    if scope_str not in _VALID_SCOPES:
        raise ValueError(
            f'unknown scope {scope_str!r} in slice {piece!r}; '
            f'valid scopes: {sorted(_VALID_SCOPES)}'
        )
    return left, scope_str


def parse_slices_expression(expr: str) -> list[CapabilitySlice]:
    """Parse a slice string into OR-of-slices.

    - Whitespace **outside** ``[...]`` separates slices (OR).
    - ``cap`` alone: that capability, any domain, any tags, any scope.
    - ``cap[a]`` or ``cap[a,b]``: capability must match and domain in the listed set (OR).
    - ``cap@t1`` or ``cap@t1,t2``: each slice uses **at most one** ``@``; after capability /
      ``[domains]``, optional tags are comma-separated; multiple tags are **AND**.
    - ``@t1`` or ``@t1,t2``: tag-only filter — matches across **all** capabilities.
    - ``#platform`` or ``#knowledge``: scope-only filter — matches across all capabilities.
    - ``cap#platform``, ``@t1#knowledge``: scope combined with other filters.
    - No whitespace inside ``[...]`` (use commas only). No whitespace after ``@`` in the
      tag list (use ``@t1,t2`` not ``@t1, t2``).
    - More than one ``@`` in a slice is invalid — use ``cap@tag1,tag2`` instead of
      ``cap@tag1@tag2``.

    Raises:
        ValueError: empty input, malformed brackets, empty domain/tag list, or space inside brackets.
    """
    raw = expr.strip()
    if not raw:
        raise ValueError('slices expression cannot be empty')

    out: list[CapabilitySlice] = []
    for piece in _split_slice_segments(raw):
        # Extract scope (#platform / #knowledge) first
        piece_no_scope, scope = _extract_scope(piece)

        tags: list[str] | None = None
        if '@' in piece_no_scope:
            if piece_no_scope.count('@') > 1:
                raise ValueError(
                    'each slice may contain at most one "@"; '
                    'use commas for multiple tags, e.g. cap@tag1,tag2'
                )
            cap_dom, rest = piece_no_scope.split('@', 1)
            tags = _parse_tags_after_at(piece=piece, rest=rest)
            if cap_dom.strip():
                cap, domains = _parse_capability_domain_token(cap_dom)
            else:
                cap, domains = None, None
        else:
            if piece_no_scope.strip():
                cap, domains = _parse_capability_domain_token(piece_no_scope)
            else:
                cap, domains = None, None
        out.append(
            CapabilitySlice(capability=cap, domains=domains, tags=tags, scope=scope)
        )

    return out
