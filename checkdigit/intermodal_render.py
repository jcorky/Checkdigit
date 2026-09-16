"""
intermodal_render.py
====================
Isometric SVG renderer for RAIL and TRUCK intermodal scenes
(intermodal_scene.IntermodalScene). Three views:

  doublestack : rail well car cross-section -- wells side by side, bottom/top
                tiers, containers placed by the DERIVED loading-rule inference.
  consist     : rail train -- cars drawn left-to-right in W2 segment order
                (the only ordering the EDI carries).
  chassis     : truck -- container(s) on a chassis side view, front/rear when a
                tandem carries 2x20ft (DERIVED).

Honesty rule (grounded in the research): any position that was INFERRED rather
than read from the EDI is drawn with a dashed outline + an "inferred" tint, and
the scene's inference_note is printed on the drawing. We never present derived
geometry as authoritative.

Pure stdlib string assembly, no dependencies; reuses the vessel renderer's
GROUP_COLOR palette and isometric-box helpers conceptually.
"""
from __future__ import annotations

import html
from typing import List

import iso6346_sizetype as st
from intermodal_scene import IntermodalScene, IUnit, RailCar


_CELL_W = 60
_CELL_H = 30
_DEPTH = 14


def _esc(s) -> str:
    return html.escape(str(s), quote=True)


def _darken(hex_color: str, factor: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    r, g, b = (max(0, min(255, int(c * factor))) for c in (r, g, b))
    return f"#{r:02x}{g:02x}{b:02x}"


def _unit_color(u: IUnit) -> str:
    grp = u.sizetype.get("group", "undefined")
    return st.GROUP_COLOR.get(grp, st.GROUP_COLOR["undefined"])


def _tooltip(u: IUnit) -> str:
    lines = [f"{u.container_id or '(no id)'}  {u.size_type_code or ('%dft' % u.length_ft if u.length_ft else '????')}"]
    s = u.sizetype
    lines.append(f"{s.get('length_label','?')} {s.get('type_label','?')}"
                 + (" HC" if s.get("high_cube") else ""))
    lines.append(f"class: {u.eq_class}")
    if u.full is not None:
        lines.append("full" if u.full else "EMPTY")
    if u.car_id:
        lines.append(f"car {u.car_id}" + (f" ({u.car_type})" if u.car_type else ""))
    if u.well:
        lines.append(f"well {u.well} platform {u.platform} tier {u.tier}  [inferred]")
    if u.chassis_pos and u.chassis_pos != "single":
        lines.append(f"chassis: {u.chassis_pos}  [inferred]")
    if u.consist_seq:
        lines.append(f"consist position {u.consist_seq}")
    if u.reefer:
        lines.append("reefer")
    if u.hazmat:
        lines.append(f"IMDG {u.hazmat}")
    for n in u.notes:
        lines.append("\u26a0 " + n)
    if u.inferred:
        lines.append("derived (not in EDI): " + ", ".join(sorted(set(u.inferred))))
    return _esc("\n".join(lines))


def _box(x, y, w, h, color, *, hc=False, empty=False, inferred=False, label="") -> str:
    d = _DEPTH
    top = _darken(color, 1.15 if not empty else 1.04)
    right = _darken(color, 0.72)
    face = color if not empty else "#eef0f2"
    stroke = _darken(color, 0.5)
    dash = ' stroke-dasharray="4 2"' if inferred else (' stroke-dasharray="3 2"' if empty else "")
    p = []
    p.append(f'<polygon points="{x+w},{y} {x+w+d},{y-d} {x+w+d},{y+h-d} {x+w},{y+h}" '
             f'fill="{right}" stroke="{stroke}" stroke-width="1"/>')
    p.append(f'<polygon points="{x},{y} {x+d},{y-d} {x+w+d},{y-d} {x+w},{y}" '
             f'fill="{top}" stroke="{stroke}" stroke-width="1"/>')
    p.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{face}" '
             f'stroke="{stroke}" stroke-width="1.2"{dash}/>')
    if hc:
        p.append(f'<rect x="{x}" y="{y}" width="{w}" height="4" fill="#1a1a1a" opacity="0.6"/>')
    if inferred:
        p.append(f'<text x="{x+w-3}" y="{y+h-3}" text-anchor="end" font-size="6.5" '
                 f'fill="#a05a00" font-style="italic">inf</text>')
    if label:
        p.append(f'<text x="{x+w/2}" y="{y+h/2+3}" text-anchor="middle" font-size="8" '
                 f'font-family="monospace" fill="#0b0b0b">{_esc(label)}</text>')
    return "".join(p)


def _badges(x, y, u: IUnit) -> str:
    g = []
    bx, by = x + 3, y + 11
    if u.reefer:
        g.append(f'<text x="{bx}" y="{by}" font-size="11" fill="#0a6">\u2744</text>')
        bx += 11
    if u.hazmat:
        cls = u.hazmat or "!"
        g.append(f'<polygon points="{bx+4},{by-9} {bx+9},{by-4} {bx+4},{by+1} {bx-1},{by-4}" '
                 f'fill="#d33" stroke="#7a0000" stroke-width="0.7"/>'
                 f'<text x="{bx+4}" y="{by-2}" text-anchor="middle" font-size="5.5" '
                 f'fill="#fff" font-weight="bold">{_esc(cls)}</text>')
    if not u.sizetype.get("defined", True):
        g.append(f'<text x="{x+_CELL_W-4}" y="{by}" text-anchor="end" font-size="9" '
                 f'fill="#b00" font-weight="bold">?</text>')
    return "".join(g)


def _svg_open(w, h, title, subtitle="") -> List[str]:
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
         f'font-family="sans-serif" viewBox="0 0 {w} {h}">',
         f'<rect x="0" y="0" width="{w}" height="{h}" fill="#fbfbfc"/>',
         f'<text x="20" y="24" font-size="15" font-weight="bold" fill="#111">{_esc(title)}</text>']
    if subtitle:
        s.append(f'<text x="20" y="40" font-size="10" fill="#666">{_esc(subtitle)}</text>')
    return s


def _inference_banner(x, y, text) -> str:
    if not text:
        return ""
    return (f'<g transform="translate({x},{y})">'
            f'<rect x="0" y="-11" width="14" height="14" fill="#fff3e0" '
            f'stroke="#e0a13a" stroke-width="1"/>'
            f'<text x="3" y="0" font-size="10" fill="#a05a00">i</text>'
            f'<text x="20" y="0" font-size="9" fill="#7a5a20" font-style="italic">'
            f'{_esc(text)}</text></g>')


# --------------------------- rail: doublestack ------------------------------ #
def render_doublestack(scene: IntermodalScene, *, max_cars: int = 8) -> str:
    cars = [c for c in scene.cars if c.units][:max_cars]
    if not cars:
        return "".join(_svg_open(360, 60, "Rail \u2014 no placed equipment") + ["</svg>"])

    car_gap = 30
    well_w = _CELL_W + 8
    # width: sum of (wells per car * well_w) + gaps
    total_wells = sum(max(c.wells, (len(c.units) + 1) // 2) for c in cars)
    width = 40 + total_wells * well_w + (len(cars)) * car_gap + 40
    height = 150
    s = _svg_open(width, height, "Rail \u2014 doublestack well cars",
                  "looking at the train side \u00b7 bottom / top tiers per well")

    x = 40
    base_y = 96         # bottom-tier top edge
    for c in cars:
        wells = max(c.wells, (len(c.units) + 1) // 2)
        car_x0 = x
        # group units by well
        by_well = {}
        for u in c.units:
            if u.well:
                by_well.setdefault(u.well, {})[u.tier or "bottom"] = u
        # draw well frames + containers
        for w in range(1, wells + 1):
            wx = x
            # well frame (the car structure)
            s.append(f'<rect x="{wx-2}" y="{base_y+_CELL_H+2}" width="{well_w-4}" height="8" '
                     f'fill="#cfd4da" stroke="#9aa0a6"/>')
            slot = by_well.get(w, {})
            bottom = slot.get("bottom")
            top = slot.get("top")
            if bottom:
                s.append(f'<g><title>{_tooltip(bottom)}</title>')
                s.append(_box(wx, base_y, _CELL_W, _CELL_H, _unit_color(bottom),
                              hc=bottom.sizetype.get("high_cube"), empty=bottom.full is False,
                              inferred=bool(bottom.inferred)))
                s.append(_badges(wx, base_y, bottom))
                s.append("</g>")
            if top:
                ty = base_y - _CELL_H - 4
                s.append(f'<g><title>{_tooltip(top)}</title>')
                s.append(_box(wx, ty, _CELL_W, _CELL_H, _unit_color(top),
                              hc=top.sizetype.get("high_cube"), empty=top.full is False,
                              inferred=bool(top.inferred)))
                s.append(_badges(wx, ty, top))
                s.append("</g>")
            x += well_w
        # wheels + car label
        s.append(f'<circle cx="{car_x0+8}" cy="{base_y+_CELL_H+14}" r="5" fill="#444"/>')
        s.append(f'<circle cx="{x-12}" cy="{base_y+_CELL_H+14}" r="5" fill="#444"/>')
        s.append(f'<text x="{(car_x0+x)/2}" y="{base_y+_CELL_H+30}" text-anchor="middle" '
                 f'font-size="9" fill="#555">{_esc(c.car_id)}'
                 f'{" \u00b7 " + _esc(c.car_type) if c.car_type else ""}</text>')
        x += car_gap

    s.append(_inference_banner(20, height - 8, scene.inference_note))
    s.append("</svg>")
    return "".join(s)


# --------------------------- rail: consist ---------------------------------- #
def render_consist(scene: IntermodalScene, *, max_cars: int = 40) -> str:
    cars = scene.cars[:max_cars]
    if not cars:
        return "".join(_svg_open(360, 60, "Rail consist \u2014 empty") + ["</svg>"])
    car_w = 46
    gap = 6
    width = 40 + len(cars) * (car_w + gap) + 40
    height = 130
    s = _svg_open(width, height, f"Rail consist \u2014 {len(scene.cars)} cars in order",
                  "drawn left\u2192right in W2 segment order (the only sequence the 418 carries)")
    x = 40
    y = 70
    for c in cars:
        loaded = (c.status == "L")
        color = "#5b8def" if loaded else "#c3c8ce"
        s.append(f'<g><title>{_esc(f"#{c.seq} {c.car_id} {c.car_type} "
                                   f"{'loaded' if loaded else 'empty'} block {c.block}")}</title>')
        # simple boxcar/flat glyph
        s.append(_box(x, y, car_w, 26, color, empty=not loaded))
        s.append(f'<circle cx="{x+8}" cy="{y+33}" r="4" fill="#444"/>')
        s.append(f'<circle cx="{x+car_w-8}" cy="{y+33}" r="4" fill="#444"/>')
        s.append(f'<text x="{x+car_w/2}" y="{y+15}" text-anchor="middle" font-size="7" '
                 f'fill="#fff">{c.seq}</text>')
        s.append("</g>")
        # coupler line to next
        s.append(f'<line x1="{x+car_w}" y1="{y+13}" x2="{x+car_w+gap}" y2="{y+13}" '
                 f'stroke="#888" stroke-width="2"/>')
        x += car_w + gap
    s.append(f'<text x="40" y="{y-12}" font-size="9" fill="#555">'
             f'\u25b6 direction of travel</text>')
    s.append(_inference_banner(20, height - 8, scene.inference_note))
    s.append("</svg>")
    return "".join(s)


# --------------------------- truck: chassis --------------------------------- #
def render_chassis(scene: IntermodalScene) -> str:
    units = [u for u in scene.units if u.eq_class in ("container", "trailer")]
    if not units:
        return "".join(_svg_open(360, 60, "Truck \u2014 no container") + ["</svg>"])
    # chassis length scaled to total container feet
    total_ft = sum((u.length_ft or (20 if u.sizetype.get("teu") == 1.0 else 40))
                   for u in units)
    px_per_ft = 6
    chassis_len = max(total_ft, 40) * px_per_ft
    width = 120 + chassis_len + 60
    height = 150
    s = _svg_open(width, height, "Truck \u2014 container(s) on chassis",
                  f"tractor + chassis side view"
                  + (" \u00b7 front/rear inferred" if any(u.inferred for u in units) else ""))
    # tractor (cab)
    cab_x = 40
    road_y = 104
    s.append(f'<rect x="{cab_x}" y="{road_y-30}" width="34" height="30" rx="4" '
             f'fill="#566573" stroke="#333"/>')
    s.append(f'<rect x="{cab_x+6}" y="{road_y-26}" width="16" height="12" fill="#bcd" stroke="#333"/>')
    # chassis rail
    chx = cab_x + 40
    s.append(f'<rect x="{chx}" y="{road_y-4}" width="{chassis_len}" height="6" '
             f'fill="#444"/>')
    # containers along the chassis
    x = chx + 6
    for u in units:
        ft = u.length_ft or (20 if u.sizetype.get("teu") == 1.0 else 40)
        w = ft * px_per_ft - 6
        cy = road_y - 4 - _CELL_H
        s.append(f'<g><title>{_tooltip(u)}</title>')
        s.append(_box(x, cy, w, _CELL_H, _unit_color(u),
                      hc=u.sizetype.get("high_cube"), empty=u.full is False,
                      inferred=bool(u.inferred),
                      label=u.size_type_code or f"{ft}ft"))
        s.append(_badges(x, cy, u))
        # position label
        if u.chassis_pos and u.chassis_pos != "single":
            s.append(f'<text x="{x+w/2}" y="{cy-6}" text-anchor="middle" font-size="8" '
                     f'fill="#a05a00" font-style="italic">{_esc(u.chassis_pos)} (inferred)</text>')
        s.append("</g>")
        x += w + 6
    # wheels
    for wx in (chx + chassis_len - 20, chx + chassis_len - 36, cab_x + 10, cab_x + 26):
        s.append(f'<circle cx="{wx}" cy="{road_y+6}" r="6" fill="#222"/>')
    s.append(f'<line x1="0" y1="{road_y+12}" x2="{width}" y2="{road_y+12}" '
             f'stroke="#999" stroke-width="1"/>')
    s.append(_inference_banner(20, height - 8, scene.inference_note))
    s.append("</svg>")
    return "".join(s)


# --------------------------- entry point ------------------------------------ #
def render_intermodal(scene: IntermodalScene) -> dict:
    if scene.view == "consist":
        svg = render_consist(scene)
    elif scene.view == "doublestack":
        svg = render_doublestack(scene)
    else:
        svg = render_chassis(scene)
    return {
        "transport": scene.transport, "view": scene.view, "mode": scene.mode,
        "counts": scene.counts, "inference_note": scene.inference_note,
        "svg": svg,
        "units": [u.as_dict() for u in scene.units],
    }
