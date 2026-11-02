"""Build the Aletheia logo masters (filled paths only, no fonts).

Needs skia-pathops:  pip install skia-pathops
Run from this folder:  python build.py [logo_dir] [export_dir]   (default: ../logo ../export)
"""
import math
import os
import sys

from geo import circle, diff, ear, moved, poly, rect, ring, band, union, grow, svg, to_d

# ---------------------------------------------------------------- symbol: the A
# Two horse ears lean in and make the apex. The round glasses are the crossbar.
# All values are on a 256 x 256 grid.
REGULAR = dict(foot=18, foot_w=60, tip_gap=22, gy=150, R=34, r=21, gap=6, bridge=8, ear_L=126, ear_b=24, ear_w=25)
SMALL = dict(foot=14, foot_w=70, tip_gap=30, gy=150, R=40, r=21, gap=10, bridge=16, ear_L=118, ear_b=28, ear_w=30)


def horse_ear(base, s, L, b, w, tilt):
    """s = -1 for the left ear, +1 for the right one; tilt in degrees, + leans towards the right."""
    return ear(base, tilt, L, b, w, lean=0.0, sharp=0.30, asym=-s * 0.10, waist=0.36)


def symbol(p=REGULAR, thin=0.0):
    """thin > 0 makes the strokes lighter, for the reversed (light on dark) version."""
    R, r, g = p["R"] - thin / 2, p["r"] + thin / 2, p["gap"] + thin
    cxL, cxR, gy = 128 - p["R"] - 4, 128 + p["R"] + 4, p["gy"]
    tipL = 128 - p["tip_gap"] / 2
    # lower legs: outer and inner edges parallel, on the line from the foot to the ear tip
    slope = (tipL - p["foot"]) / 216
    x0, x1 = p["foot"] + thin, p["foot"] + p["foot_w"] - thin
    top = 236 - gy
    left = poly([(x0, 236), (x1, 236), (x1 + slope * top, gy), (x0 + slope * top, gy)])
    right = moved(left, -1, 0, 0, 1, 256, 0)
    # ears rise from behind the lenses
    L = p["ear_L"]
    base = (cxL + 4, gy - 10)
    tilt = math.degrees(math.asin((tipL - base[0]) / L))
    eL = horse_ear(base, -1, L, p["ear_b"] - thin, p["ear_w"] - thin, tilt)
    ink = union(left, right, eL, moved(eL, -1, 0, 0, 1, 256, 0))
    glasses = union(ring(cxL, gy, R, r), ring(cxR, gy, R, r),
                    rect(cxL + R - 2, gy - 3 - p["bridge"] + thin / 2, cxR - R + 2, gy - 3 - thin / 2))
    ink = diff(ink, grow(union(circle(cxL, gy, p["R"]), circle(cxR, gy, p["R"])), g))
    return union(ink, glasses)


# ---------------------------------------------------------------- wordmark: geometric lowercase
BASE, XH, ASC = 200, 80, 28          # baseline, x-height line, ascender line
CY = 140                             # centre of the round letters


def letters(W=26, RT=28, Rb=62):
    """Return the glyph functions for one weight. W = stem, RT = round stroke, Rb = bowl radius (with overshoot)."""
    rb = Rb - RT

    def a(x):
        cx = x + Rb
        return union(ring(cx, CY, Rb, rb), rect(cx + Rb - W, XH - 2, cx + Rb, BASE)), 2 * Rb

    def l(x):
        return rect(x, ASC, x + W, BASE), W

    def e(x):
        cx = x + Rb
        return union(band((cx, CY), Rb, rb, 40, 360), rect(cx - rb - 1, CY - 11, cx + Rb, CY + 11)), 2 * Rb

    def t(x):
        return union(rect(x, 40, x + W, BASE), rect(x - 22, XH, x + W + 26, XH + 22)), W + 26

    def h(x):
        wd = 112
        hc = (x + wd / 2, XH - 2 + wd / 2)
        return union(rect(x, ASC, x + W, BASE), band(hc, wd / 2, wd / 2 - W, 180, 360),
                     rect(x + wd - W, hc[1], x + wd, BASE)), wd

    def i(x):
        return union(rect(x, XH, x + W, BASE), circle(x + W / 2, 46, 16)), W

    return dict(a=a, l=l, e=e, t=t, h=h, i=i)


# space before each letter, in grid units (optical spacing, set by eye)
SPACING = [("a", 0), ("l", 22), ("e", 18), ("t", 30), ("h", 14), ("e", 18), ("i", 20), ("a", 20)]


def wordmark(skip_a=False, thin=0.0):
    g = letters(W=26 - thin, RT=28 - thin)
    seq = [("l", 0)] + SPACING[2:] if skip_a else SPACING
    parts, x = [], 0
    for ch, gap in seq:
        x += gap
        p, w = g[ch](x)
        parts.append(p)
        x += w
    return union(*parts)


# ---------------------------------------------------------------- lockups
def place(p, k, dx, dy):
    return moved(p, k, 0, 0, k, dx, dy)


def primary(thin=0.0):
    """"Aletheia": the symbol is the capital A, then 'letheia' on the same baseline."""
    k = 0.82
    A = place(symbol(thin=thin), k, 0, 0)
    b = A.bounds
    A = place(A, 1, -b[0], BASE - b[3])
    rest = wordmark(skip_a=True, thin=thin)
    rest = place(rest, 1, A.bounds[2] + 6 - rest.bounds[0], 0)
    return union(A, rest)


def stacked(thin=0.0):
    """Symbol above the lowercase wordmark; the wordmark is as wide as 1.6 x the symbol."""
    S = symbol(thin=thin)
    Wm = wordmark(thin=thin)
    wb = Wm.bounds
    k = 1.6 * 256 / (wb[2] - wb[0])
    Wm = place(Wm, k, 0, 0)
    wb = Wm.bounds
    sb = S.bounds
    Wm = place(Wm, 1, (sb[0] + sb[2]) / 2 - (wb[0] + wb[2]) / 2, sb[3] + 40 - wb[1])
    return union(S, Wm)


def framed(p, pad=24, h=None):
    """Put the artwork on a canvas with `pad` around it (centred vertically when h is given)."""
    b = p.bounds
    w, hh = b[2] - b[0] + 2 * pad, h or (b[3] - b[1] + 2 * pad)
    return place(p, 1, pad - b[0], (hh - (b[3] - b[1])) / 2 - b[1]), w, hh


INK, PAPER, WHEAT, SILK = "#1E1B24", "#FFF7EC", "#E6A752", "#1F5A48"


def write(out, name, p, w, h, fill=INK, title="Aletheia logo"):
    with open(os.path.join(out, name), "w") as fh:
        fh.write(svg(p, w, h, title, fill))


def favicons(out):
    """favicon.svg turns wheat in dark mode; favicon-tile.svg (wheat tile) feeds the .ico and PNG sizes."""
    d = to_d(place(symbol(SMALL), 1.08, -10.24, -14))
    with open(os.path.join(out, "favicon.svg"), "w") as fh:
        fh.write('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256"><title>Aletheia</title>'
                 f'<style>path{{fill:{INK}}}@media (prefers-color-scheme:dark){{path{{fill:{WHEAT}}}}}</style>'
                 f'<path d="{d}"/></svg>\n')
    d = to_d(place(symbol(SMALL), 0.86, 17.92, 12))
    with open(os.path.join(out, "favicon-tile.svg"), "w") as fh:
        fh.write('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256"><title>Aletheia</title>'
                 f'<rect width="256" height="256" rx="56" fill="{WHEAT}"/><path fill="{INK}" d="{d}"/></svg>\n')


def main(out):
    os.makedirs(out, exist_ok=True)
    write(out, "aletheia-symbol.svg", symbol(), 256, 256, title="Aletheia symbol")
    write(out, "aletheia-symbol-small.svg", symbol(SMALL), 256, 256, title="Aletheia symbol, small sizes")
    write(out, "aletheia-symbol-reversed.svg", symbol(thin=2), 256, 256, PAPER, "Aletheia symbol, reversed")
    for name, fn in (("horizontal", primary), ("stacked", stacked)):
        p, w, h = framed(fn(), 24, 256 if name == "horizontal" else None)
        write(out, f"aletheia-{name}.svg", p, w, h)
        p, w, h = framed(fn(thin=2), 24, 256 if name == "horizontal" else None)
        write(out, f"aletheia-{name}-reversed.svg", p, w, h, PAPER, "Aletheia logo, reversed")
    p, w, h = framed(wordmark(), 24, 256)
    write(out, "aletheia-wordmark.svg", p, w, h, title="Aletheia wordmark")


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, "..", "logo"))
    favicons(sys.argv[2] if len(sys.argv) > 2 else os.path.join(here, "..", "export"))
