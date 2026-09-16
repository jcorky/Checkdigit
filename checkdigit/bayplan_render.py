"""
bayplan_render.py
=================
Render a parsed BAPLIE Scene (baplie_scene.Scene) as ISOMETRIC SVG -- realistic,
3D-like container drawings placed in their real stowage slots, with callout
fields for oversize / reefer / hazmat / undefined units.

Pure stdlib string assembly: no third-party deps, output is a self-contained
<svg> string that renders anywhere and prints cleanly (matching the project's
no-dependency ethos). One SVG per bay (a row x tier cross-section), each box
drawn as an isometric cuboid (top + left + right faces) scaled by the decoded
ISO 6346 dimensions.

Maritime conventions applied (industry convention, not codified standard --
see project research doc):
  * cross-section viewed LOOKING FORWARD: even rows (port) to the left, odd rows
    (starboard) to the right, row 00 (centreline) in the middle;
  * a hatch-cover line separates on-deck (tier >= 80, drawn above) from in-hold
    (tier < 80, below);
  * colour by ISO type group (dry/reefer/open-top/flat-rack/tank/bulk/named),
    undefined always grey with the raw code shown;
  * reefer = snowflake glyph; hazmat = red diamond + IMDG class; oversize =
    overhang arrows + cm; high-cube = top stripe; empty = hollow/!-filled.

The SVG is intentionally compact and dependency-free so it can be embedded in
the /correct JSON response and dropped straight into the SPA.
"""
from __future__ import annotations

import html
from typing import Dict, List, Optional, Tuple

import iso6346_sizetype as st
from baplie_scene import Scene, Unit


# --- isometric projection ---------------------------------------------------- #
# A unit cell is W wide (row axis) x H tall (tier axis); depth D gives the 3D look.
_CELL_W = 54
_CELL_H = 30
_DEPTH = 16          # isometric depth offset (both x and y)
_PAD = 60
_GAP = 6             # gap between cells


def _darken(hex_color: str, factor: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    # clamp to 0..255 so brighten factors (>1.0) can't overflow into 3-digit hex
    r, g, b = (max(0, min(255, int(c * factor))) for c in (r, g, b))
    return f"#{r:02x}{g:02x}{b:02x}"


def _esc(s: str) -> str:
    return html.escape(str(s), quote=True)


def _cuboid(x: float, y: float, w: float, h: float, color: str, *,
            hc: bool, empty: bool, label: str = "") -> str:
    """One isometric box at top-left (x,y), footprint w x h, with top+right faces."""
    d = _DEPTH
    top = _darken(color, 1.18 if not empty else 1.05)
    right = _darken(color, 0.72)
    face = color if not empty else "#eef0f2"
    stroke = _darken(color, 0.5)
    parts = []
    # right face (parallelogram)
    parts.append(
        f'<polygon points="{x+w},{y} {x+w+d},{y-d} {x+w+d},{y+h-d} {x+w},{y+h}" '
        f'fill="{right}" stroke="{stroke}" stroke-width="1"/>')
    # top face
    parts.append(
        f'<polygon points="{x},{y} {x+d},{y-d} {x+w+d},{y-d} {x+w},{y}" '
        f'fill="{top}" stroke="{stroke}" stroke-width="1"/>')
    # front face
    parts.append(
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{face}" '
        f'stroke="{stroke}" stroke-width="1.2"'
        + (' stroke-dasharray="3 2"' if empty else "") + '/>')
    # high-cube top stripe (mnemonic for HC corner markings)
    if hc:
        parts.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="4" fill="#1a1a1a" opacity="0.65"/>')
    if label:
        parts.append(
            f'<text x="{x+w/2}" y="{y+h/2+3}" text-anchor="middle" '
            f'font-size="8" font-family="monospace" fill="#0b0b0b">{_esc(label)}</text>')
    return "".join(parts)


def _badges(x: float, y: float, w: float, u: Unit) -> str:
    """Reefer/hazmat/oversize/undefined glyphs over a unit's front face."""
    g = []
    bx = x + 3
    by = y + 10
    if u.reefer:
        g.append(f'<text x="{bx}" y="{by}" font-size="11" fill="#0a6">\u2744</text>')  # snowflake
        bx += 11
    if u.hazmat:
        cls = u.hazmat.imdg_class or "!"
        g.append(
            f'<polygon points="{bx+4},{by-9} {bx+9},{by-4} {bx+4},{by+1} {bx-1},{by-4}" '
            f'fill="#d33" stroke="#7a0000" stroke-width="0.7"/>'
            f'<text x="{bx+4}" y="{by-2}" text-anchor="middle" font-size="5.5" '
            f'fill="#fff" font-weight="bold">{_esc(cls)}</text>')
        bx += 12
    if not u.sizetype.get("defined", True):
        g.append(f'<text x="{x+w-4}" y="{by}" text-anchor="end" font-size="9" '
                 f'fill="#b00" font-weight="bold">?</text>')
    return "".join(g)


def _oversize_arrows(x: float, y: float, w: float, h: float, u: Unit) -> str:
    """Overhang arrows + cm for an oversize unit, drawn outside the footprint."""
    if not (u.oversize and u.oversize.any):
        return ""
    o = u.oversize
    a = []
    arrow = ('stroke="#b00" stroke-width="1.4" marker-end="url(#ovh)"')
    if o.height:
        a.append(f'<line x1="{x+w/2}" y1="{y}" x2="{x+w/2}" y2="{y-14}" {arrow}/>'
                 f'<text x="{x+w/2}" y="{y-16}" text-anchor="middle" font-size="7" '
                 f'fill="#b00">+{o.height}cm</text>')
    if o.left:
        a.append(f'<line x1="{x}" y1="{y+h/2}" x2="{x-12}" y2="{y+h/2}" {arrow}/>'
                 f'<text x="{x-13}" y="{y+h/2-2}" text-anchor="end" font-size="7" '
                 f'fill="#b00">+{o.left}</text>')
    if o.right:
        a.append(f'<line x1="{x+w}" y1="{y+h/2}" x2="{x+w+12}" y2="{y+h/2}" {arrow}/>'
                 f'<text x="{x+w+13}" y="{y+h/2-2}" font-size="7" fill="#b00">+{o.right}</text>')
    return "".join(a)


def _tooltip(u: Unit) -> str:
    """<title> hover text with the full field set."""
    pos = u.position
    lines = [f"{u.container_id or '(no id)'}  {u.size_type_code or '????'}"]
    stp = u.sizetype
    lines.append(f"{stp.get('length_label','?')} {stp.get('type_label','?')}"
                 + (" HC" if stp.get("high_cube") else ""))
    if pos:
        lines.append(f"slot {pos.raw}  bay {pos.bay} row {pos.row} tier {pos.tier} "
                     f"({pos.side}, {'on deck' if pos.on_deck else 'in hold'})")
    if u.gross_weight_kg is not None:
        lines.append(f"{u.weight_kind or 'weight'}: {u.gross_weight_kg:g} kg")
    if u.full is not None:
        lines.append("full" if u.full else "EMPTY")
    if u.pod:
        lines.append(f"POD {u.pod}" + (f"  POL {u.pol}" if u.pol else ""))
    if u.carrier:
        lines.append(f"carrier {u.carrier}")
    if u.reefer:
        t = u.reefer
        lines.append(f"reefer {t.temperature}{t.unit}".strip())
    if u.hazmat:
        h = u.hazmat
        lines.append(f"IMDG {h.imdg_class}" + (f" UN{h.un_number}" if h.un_number else "")
                     + (f" ({h.regulation})" if h.regulation else ""))
    if u.oversize and u.oversize.any:
        o = u.oversize
        parts = [f"{k} +{v}cm" for k, v in
                 (("front", o.front), ("back", o.back), ("left", o.left),
                  ("right", o.right), ("over-height", o.height)) if v]
        lines.append("OOG: " + ", ".join(parts))
    for n in u.notes:
        lines.append("\u26a0 " + n)
    return _esc("\n".join(lines))


def _legend() -> str:
    items = [("dry", "Dry"), ("reefer", "Reefer"), ("open_top", "Open-top"),
             ("flat_rack", "Flat rack"), ("tank", "Tank"), ("bulk", "Bulk"),
             ("undefined", "Undefined")]
    out = ['<g font-family="sans-serif" font-size="10">']
    x = 0
    for grp, label in items:
        c = st.GROUP_COLOR[grp]
        out.append(f'<rect x="{x}" y="0" width="12" height="12" fill="{c}" '
                   f'stroke="#333" stroke-width="0.6"/>'
                   f'<text x="{x+16}" y="10" fill="#222">{label}</text>')
        x += 16 + 9 * len(label) + 14
    out.append('<text x="' + str(x) + '" y="10" fill="#0a6">\u2744 reefer</text>')
    out.append('<text x="' + str(x + 70) + '" y="10" fill="#d33">\u25c6 hazmat</text>')
    out.append("</g>")
    return "".join(out)


def render_bay(scene: Scene, bay: int) -> str:
    """Render one bay's cross-section (rows x tiers) as an isometric SVG string."""
    units = [u for u in scene.placed if u.position.bay == bay]
    if not units:
        return f'<svg xmlns="http://www.w3.org/2000/svg" width="200" height="40">' \
               f'<text x="10" y="24" font-family="sans-serif">Bay {bay}: empty</text></svg>'

    rows = sorted({u.position.row for u in units})
    tiers = sorted({u.position.tier for u in units}, reverse=True)  # top tier first (drawn high)
    # row order looking forward: port (even, desc) ... 00 ... starboard (odd, asc) -> left..right
    port = sorted((r for r in rows if r != 0 and r % 2 == 0), reverse=True)
    centre = [r for r in rows if r == 0]
    stbd = sorted(r for r in rows if r % 2 == 1)
    row_order = port + centre + stbd
    row_index = {r: i for i, r in enumerate(row_order)}

    by_slot: Dict[Tuple[int, int], Unit] = {}
    for u in units:
        by_slot[(u.position.row, u.position.tier)] = u

    n_rows = len(row_order)
    n_tiers = len(tiers)
    cell_w, cell_h = _CELL_W, _CELL_H
    width = _PAD * 2 + n_rows * (cell_w + _GAP) + _DEPTH + 30
    height = _PAD * 2 + n_tiers * (cell_h + _GAP) + _DEPTH + 40

    # hatch line between on-deck (>=80) and hold (<80), if both present
    on_deck_tiers = [t for t in tiers if t >= 80]
    hold_tiers = [t for t in tiers if t < 80]

    svg: List[str] = []
    svg.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'font-family="sans-serif" viewBox="0 0 {width} {height}">')
    svg.append('<defs><marker id="ovh" markerWidth="6" markerHeight="6" refX="5" refY="3" '
               'orient="auto"><path d="M0,0 L6,3 L0,6 z" fill="#b00"/></marker></defs>')
    svg.append(f'<rect x="0" y="0" width="{width}" height="{height}" fill="#fbfbfc"/>')
    svg.append(f'<text x="{_PAD}" y="26" font-size="15" font-weight="bold" fill="#111">'
               f'Bay {bay:02d} \u2014 {"40ft slot" if bay % 2 == 0 else "20ft slot"}'
               f'{"  \u00b7 " + _esc(scene.vessel_name) if scene.vessel_name else ""}</text>')
    svg.append(f'<text x="{_PAD}" y="40" font-size="10" fill="#666">looking forward '
               f'\u2014 port (even rows) left \u00b7 starboard (odd rows) right</text>')

    y0 = _PAD + 16
    # draw tier by tier (top to bottom)
    for ti, tier in enumerate(tiers):
        cy = y0 + ti * (cell_h + _GAP) + _DEPTH
        # tier label
        svg.append(f'<text x="{_PAD-10}" y="{cy+cell_h/2+3}" text-anchor="end" '
                   f'font-size="9" fill="#555">{tier:02d}</text>')
        # hatch line above the first hold tier
        if hold_tiers and tier == max(hold_tiers) and on_deck_tiers:
            ly = cy - _GAP/2
            svg.append(f'<line x1="{_PAD-20}" y1="{ly}" x2="{width-20}" y2="{ly}" '
                       f'stroke="#444" stroke-width="2" stroke-dasharray="6 3"/>')
            svg.append(f'<text x="{width-22}" y="{ly-3}" text-anchor="end" font-size="8" '
                       f'fill="#444">hatch cover \u2014 deck above / hold below</text>')
        for r in row_order:
            ri = row_index[r]
            cx = _PAD + ri * (cell_w + _GAP)
            u = by_slot.get((r, tier))
            if u is None:
                # empty slot outline
                svg.append(f'<rect x="{cx}" y="{cy}" width="{cell_w}" height="{cell_h}" '
                           f'fill="none" stroke="#dcdfe3" stroke-width="1" '
                           f'stroke-dasharray="2 3"/>')
                continue
            grp = u.sizetype.get("group", "undefined")
            color = st.GROUP_COLOR.get(grp, st.GROUP_COLOR["undefined"])
            hc = bool(u.sizetype.get("high_cube"))
            empty = u.full is False
            label = "" if u.sizetype.get("defined", True) else (u.size_type_code or "????")
            svg.append(f'<g><title>{_tooltip(u)}</title>')
            svg.append(_cuboid(cx, cy, cell_w, cell_h, color, hc=hc, empty=empty, label=label))
            svg.append(_badges(cx, cy, cell_w, u))
            svg.append(_oversize_arrows(cx, cy, cell_w, cell_h, u))
            svg.append("</g>")

    # row axis labels along the bottom
    by = y0 + n_tiers * (cell_h + _GAP) + _DEPTH + 6
    for r in row_order:
        ri = row_index[r]
        cx = _PAD + ri * (cell_w + _GAP) + cell_w/2
        side = "C" if r == 0 else ("P" if r % 2 == 0 else "S")
        svg.append(f'<text x="{cx}" y="{by}" text-anchor="middle" font-size="9" '
                   f'fill="#555">{r:02d}<tspan fill="#999">{side}</tspan></text>')

    svg.append(f'<g transform="translate({_PAD},{height-16})">{_legend()}</g>')
    svg.append("</svg>")
    return "".join(svg)


def render_scene(scene: Scene, *, max_bays: int = 24) -> Dict[str, object]:
    """Render every occupied bay. Returns {bay: svg, ...} plus an overview list.
    Caps at max_bays SVGs to bound payload; remaining bays are listed in 'omitted'."""
    bays = scene.bays[:max_bays]
    svgs = {str(b): render_bay(scene, b) for b in bays}
    return {
        "transport": scene.transport,
        "vessel_name": scene.vessel_name,
        "counts": scene.counts,
        "bays": scene.bays,
        "rendered_bays": bays,
        "omitted_bays": scene.bays[max_bays:],
        "svg_by_bay": svgs,
        "unplaced": [
            {"container_id": u.container_id, "size_type": u.size_type_code,
             "flags": u.flags, "notes": u.notes}
            for u in scene.unplaced
        ],
    }
