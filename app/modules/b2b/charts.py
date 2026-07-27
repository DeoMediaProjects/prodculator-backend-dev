"""Inline SVG chart builders for the Business Intelligence PDF.

Ported from the client-approved generator in the template handoff pack
(`source/build_b2b_report.py`). The shapes, proportions and colour convention
are the approved design; only typing, guards for empty data and docstrings were
added.

WeasyPrint does not execute JavaScript, so every chart is hand-built inline SVG.
Do not swap these for a JS charting library: nothing would render.

Colour convention, applied consistently across every package: yellow alone
cannot carry a two-series chart, so each diverging or grouped chart pairs
GRAPHITE (declared / considering) with YELLOW (computed / recommended).
"""
from __future__ import annotations

import math
from html import escape

# Approved palette (developer notes, "Palette").
YEL = "#FFCF42"      # brand yellow: hero accent
YEL_D = "#E6B72E"    # darker yellow: eyebrows and yellow text on white
INK = "#15171C"      # text, primary bars, KPI tiles, footer strip
INK2 = "#2a2d34"
MUTED = "#6a6e77"
FAINT = "#9aa0a8"
LINE = "#e2e4e8"
LINE2 = "#eef0f3"
PANEL = "#f7f8fa"
PANEL2 = "#f1f2f5"
GRAPH = "#3D4350"    # graphite: the "considering" / declared series
GRAPH_L = "#5a6072"
POS = "#2f7d4f"
NEG = "#c0492f"
DARK = "#15171c"

_FONT = "Helvetica Neue,Arial"


def _t(value: object) -> str:
    """Escape a label for inclusion in SVG text."""
    return escape(str(value), quote=True)


def diverging(rows: list[tuple[str, int, int]], w: int = 470, barh: int = 13, gap: int = 15) -> str:
    """Considered vs recommended per territory, on a shared scale.

    rows: (label, considered, recommended). Graphite over yellow.
    """
    if not rows:
        return ""
    maxv = max(max(a, b) for _, a, b in rows) or 1
    h = len(rows) * (barh * 2 + gap) + 6
    labelw = 112
    track = w - labelw - 46
    o = [f'<svg viewBox="0 0 {w} {h}" width="100%" xmlns="http://www.w3.org/2000/svg" font-family="{_FONT}">']
    y = 4
    for label, con, rec in rows:
        o.append(f'<text x="0" y="{y+barh-1}" font-size="9.5" fill="{INK2}" font-weight="600">{_t(label)}</text>')
        cw = (con / maxv) * track
        rw = (rec / maxv) * track
        o.append(f'<rect x="{labelw}" y="{y}" width="{cw:.1f}" height="{barh-3}" rx="1.5" fill="{GRAPH}"/>')
        o.append(f'<text x="{labelw+cw+4:.1f}" y="{y+barh-4}" font-size="8" fill="{MUTED}">{con}</text>')
        o.append(f'<rect x="{labelw}" y="{y+barh}" width="{rw:.1f}" height="{barh-3}" rx="1.5" fill="{YEL}"/>')
        o.append(f'<text x="{labelw+rw+4:.1f}" y="{y+barh*2-4}" font-size="8" fill="{MUTED}">{rec}</text>')
        y += barh * 2 + gap
    o.append("</svg>")
    return "".join(o)


def hbars(
    rows: list[tuple],
    w: int = 440,
    barh: int = 15,
    gap: int = 10,
    color: str = INK,
    labelw: int = 120,
) -> str:
    """Horizontal bars for any single distribution. rows: (label, value[, caption])."""
    if not rows:
        return ""
    maxval = max((v for _, v, *_ in rows), default=1) or 1
    h = len(rows) * (barh + gap) + 4
    track = w - labelw - 70
    o = [f'<svg viewBox="0 0 {w} {h}" width="100%" xmlns="http://www.w3.org/2000/svg" font-family="{_FONT}">']
    y = 2
    for r in rows:
        label, val = r[0], r[1]
        extra = r[2] if len(r) > 2 else str(val)
        bw = (val / maxval) * track if maxval else 0
        o.append(f'<text x="0" y="{y+barh-3}" font-size="9.5" fill="{INK2}">{_t(label)}</text>')
        o.append(f'<rect x="{labelw}" y="{y}" width="{track}" height="{barh}" rx="2" fill="{LINE2}"/>')
        o.append(f'<rect x="{labelw}" y="{y}" width="{bw:.1f}" height="{barh}" rx="2" fill="{color}"/>')
        o.append(f'<text x="{w}" y="{y+barh-3}" font-size="8.3" fill="{MUTED}" text-anchor="end">{_t(extra)}</text>')
        y += barh + gap
    o.append("</svg>")
    return "".join(o)


def grouped_quarter(
    rows: list[tuple[str, list[int]]],
    months: list[str] | None = None,
    w: int = 470,
    h: int = 150,
) -> str:
    """Grouped bars, one group per category and one bar per month in the period."""
    if not rows:
        return ""
    months = months or ["Jan", "Feb", "Mar"]
    slots = max(len(v) for _, v in rows) or 1
    maxv = max((max(v) if v else 0) for _, v in rows) or 1
    n = len(rows)
    slot = (w - 40) / n
    gw = slot * 0.62
    bw = gw / slots
    top = 14
    bot = h - 26
    span = bot - top
    o = [f'<svg viewBox="0 0 {w} {h}" width="100%" xmlns="http://www.w3.org/2000/svg" font-family="{_FONT}">']
    cols = [GRAPH_L, GRAPH, YEL, YEL_D, MUTED]
    for gl in range(1, 4):
        gy = bot - (span * gl / 4)
        o.append(f'<line x1="20" y1="{gy:.0f}" x2="{w-20}" y2="{gy:.0f}" stroke="{LINE2}" stroke-width="0.7"/>')
    for i, (label, vals) in enumerate(rows):
        cx = 20 + slot * i + slot / 2
        for j, v in enumerate(vals):
            bh = (v / maxv) * span
            x = cx - gw / 2 + j * bw
            o.append(
                f'<rect x="{x:.1f}" y="{bot-bh:.1f}" width="{bw-1:.1f}" height="{bh:.1f}" '
                f'fill="{cols[j % len(cols)]}"/>'
            )
        o.append(
            f'<text x="{cx:.0f}" y="{bot+12:.0f}" font-size="8.5" fill="{INK2}" '
            f'text-anchor="middle" font-weight="600">{_t(label)}</text>'
        )
    lx = w - 50 * len(months)
    for j, m in enumerate(months):
        o.append(f'<rect x="{lx+j*50}" y="2" width="9" height="9" fill="{cols[j % len(cols)]}"/>')
        o.append(f'<text x="{lx+j*50+12}" y="10" font-size="8" fill="{MUTED}">{_t(m)}</text>')
    o.append("</svg>")
    return "".join(o)


def trendline(pts: list[tuple[str, int]], w: int = 470, h: int = 120) -> str:
    """Volume across periods. pts: (label, value)."""
    if len(pts) < 2:
        return ""
    maxv = (max(v for _, v in pts) * 1.2) or 1
    n = len(pts)
    left = 60
    right = w - 30
    top = 16
    bot = h - 24
    span = bot - top
    step = (right - left) / (n - 1)
    o = [f'<svg viewBox="0 0 {w} {h}" width="100%" xmlns="http://www.w3.org/2000/svg" font-family="{_FONT}">']
    o.append(f'<line x1="{left}" y1="{bot}" x2="{right}" y2="{bot}" stroke="{LINE}" stroke-width="1"/>')
    coords = []
    for i, (lab, v) in enumerate(pts):
        x = left + step * i
        y = bot - (v / maxv) * span
        coords.append((x, y, v, lab))
    o.append(
        '<polyline points="'
        + " ".join(f"{x:.1f},{y:.1f}" for x, y, _, _ in coords)
        + f'" fill="none" stroke="{YEL_D}" stroke-width="2.5"/>'
    )
    for x, y, v, lab in coords:
        o.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="{INK}"/>')
        o.append(
            f'<text x="{x:.1f}" y="{y-8:.1f}" font-size="9" font-weight="800" '
            f'fill="{INK}" text-anchor="middle">{v}</text>'
        )
        o.append(
            f'<text x="{x:.1f}" y="{bot+13:.1f}" font-size="7.8" fill="{MUTED}" '
            f'text-anchor="middle">{_t(lab)}</text>'
        )
    o.append("</svg>")
    return "".join(o)


def heatmatrix(
    cols: list[str],
    rows: list[str],
    data: list[list[int]],
    w: int = 470,
    cell_h: int = 26,
) -> str:
    """Row x column intensity grid, white to yellow by relative intensity."""
    if not cols or not rows:
        return ""
    labelw = 90
    cw = (w - labelw) / len(cols)
    h = len(rows) * cell_h + 30
    maxv = max((max(r) if r else 0) for r in data) or 1
    o = [f'<svg viewBox="0 0 {w} {h}" width="100%" xmlns="http://www.w3.org/2000/svg" font-family="{_FONT}">']
    for c, cl in enumerate(cols):
        o.append(
            f'<text x="{labelw+cw*c+cw/2:.0f}" y="18" font-size="8" fill="{MUTED}" '
            f'text-anchor="middle" font-weight="700">{_t(cl)}</text>'
        )
    for r, rl in enumerate(rows):
        y = 26 + r * cell_h
        o.append(
            f'<text x="0" y="{y+cell_h/2+3:.0f}" font-size="9" fill="{INK2}" '
            f'font-weight="600">{_t(rl)}</text>'
        )
        for c in range(len(cols)):
            v = data[r][c]
            inten = v / maxv
            fill = f"rgba(255,207,66,{0.15+inten*0.85:.2f})" if v > 0 else PANEL2
            o.append(
                f'<rect x="{labelw+cw*c:.0f}" y="{y}" width="{cw-2:.0f}" '
                f'height="{cell_h-3}" rx="2" fill="{fill}"/>'
            )
            if v > 0:
                tc = INK if inten > 0.35 else MUTED
                o.append(
                    f'<text x="{labelw+cw*c+cw/2:.0f}" y="{y+cell_h/2+2:.0f}" font-size="8.5" '
                    f'fill="{tc}" text-anchor="middle" font-weight="700">{v}</text>'
                )
    o.append("</svg>")
    return "".join(o)


def donut(segs: list[tuple[str, int, str]], size: int = 132, thick: int = 22) -> str:
    """Concentration donut. segs: (label, value, colour)."""
    segs = [s for s in segs if s[1] > 0]
    if not segs:
        return ""
    cx = cy = size / 2
    r = (size - thick) / 2 - 2
    total = sum(v for _, v, _ in segs) or 1
    start = -90.0
    o = [f'<svg viewBox="0 0 {size} {size}" width="{size}" height="{size}" xmlns="http://www.w3.org/2000/svg">']
    for _, val, color in segs:
        ang = val / total * 360
        # A full circle cannot be drawn as a single arc: both endpoints coincide.
        if ang >= 359.99:
            o.append(
                f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{r:.2f}" fill="none" '
                f'stroke="{color}" stroke-width="{thick}"/>'
            )
            break
        end = start + ang
        large = 1 if ang > 180 else 0
        x1 = cx + r * math.cos(math.radians(start))
        y1 = cy + r * math.sin(math.radians(start))
        x2 = cx + r * math.cos(math.radians(end))
        y2 = cy + r * math.sin(math.radians(end))
        o.append(
            f'<path d="M {x1:.2f} {y1:.2f} A {r:.2f} {r:.2f} 0 {large} 1 {x2:.2f} {y2:.2f}" '
            f'fill="none" stroke="{color}" stroke-width="{thick}"/>'
        )
        start = end
    o.append("</svg>")
    return "".join(o)
