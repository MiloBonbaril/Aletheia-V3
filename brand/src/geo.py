"""Tiny geometry kit on top of skia-pathops: primitives -> real booleans -> clean SVG path data."""
import math
import pathops

K = 0.5522847498


def _r(v):
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def new():
    p = pathops.Path()
    return p, p.getPen()


def circle(cx, cy, r):
    p, pen = new()
    k = K * r
    pen.moveTo((cx + r, cy))
    pen.curveTo((cx + r, cy + k), (cx + k, cy + r), (cx, cy + r))
    pen.curveTo((cx - k, cy + r), (cx - r, cy + k), (cx - r, cy))
    pen.curveTo((cx - r, cy - k), (cx - k, cy - r), (cx, cy - r))
    pen.curveTo((cx + k, cy - r), (cx + r, cy - k), (cx + r, cy))
    pen.closePath()
    return p


def poly(pts):
    p, pen = new()
    pen.moveTo(pts[0])
    for q in pts[1:]:
        pen.lineTo(q)
    pen.closePath()
    return p


def rect(x0, y0, x1, y1):
    return poly([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])


def _arc(pen, c, r, a0, a1, first):
    """Append arc a0->a1 (degrees, screen), split into <=90 deg cubics."""
    n = max(1, math.ceil(abs(a1 - a0) / 90 - 1e-9))
    step = (a1 - a0) / n
    for i in range(n):
        t0, t1 = math.radians(a0 + i * step), math.radians(a0 + (i + 1) * step)
        h = 4 / 3 * math.tan((t1 - t0) / 4) * r
        p0 = (c[0] + r * math.cos(t0), c[1] + r * math.sin(t0))
        p1 = (c[0] + r * math.cos(t1), c[1] + r * math.sin(t1))
        c1 = (p0[0] - h * math.sin(t0), p0[1] + h * math.cos(t0))
        c2 = (p1[0] + h * math.sin(t1), p1[1] - h * math.cos(t1))
        if i == 0 and first:
            pen.moveTo(p0)
        elif i == 0:
            pen.lineTo(p0)
        pen.curveTo(c1, c2, p1)


def band(c, R, r, a0, a1):
    p, pen = new()
    _arc(pen, c, R, a0, a1, True)
    _arc(pen, c, r, a1, a0, False)
    pen.closePath()
    return p


def ring(cx, cy, R, r):
    return diff(circle(cx, cy, R), circle(cx, cy, r))


def ear(base, angle, L, b, w, lean=0.0, sharp=0.45, asym=0.0, waist=0.30):
    """Horse ear. base = centre of the base, angle = tilt from vertical (deg, + = right),
    L = length, b = base half width, w = max half width, lean = tip shift sideways (fraction of L),
    asym > 0 makes the outer (left, before mirroring) edge fuller than the inner one."""
    a = math.radians(angle)
    u = (math.sin(a), -math.cos(a))           # axis, pointing to the tip
    n = (math.cos(a), math.sin(a))            # to the right of the axis

    def T(x, y):                              # local (x right, y along axis) -> screen
        return (base[0] + n[0] * x + u[0] * y, base[1] + n[1] * x + u[1] * y)

    tipx = lean * L
    wl, wr = w * (1 + asym), w * (1 - asym)
    p, pen = new()
    pen.moveTo(T(-b, 0))
    pen.curveTo(T(-wl * 1.30, waist * L), T(-wl * sharp + tipx * 0.6, 0.80 * L), T(tipx, L))
    pen.curveTo(T(wr * sharp + tipx * 0.6, 0.80 * L), T(wr * 1.30, waist * L), T(b, 0))
    pen.closePath()
    return p


def moved(p, a=1, b=0, c=0, d=1, e=0, f=0):
    return p.transform(a, b, c, d, e, f)


def rot(p, deg, cx, cy):
    t = math.radians(deg)
    co, si = math.cos(t), math.sin(t)
    return moved(p, co, si, -si, co, cx - co * cx + si * cy, cy - si * cx - co * cy)


def grow(p, g, n=24):
    """Crude dilation (union of shifted copies); good enough for knock-out halos."""
    out = pathops.Path(p)
    for i in range(n):
        t = 2 * math.pi * i / n
        out = pathops.op(out, moved(p, e=g * math.cos(t), f=g * math.sin(t)), pathops.PathOp.UNION)
    return out


def _fold(ps, kind):
    out = pathops.Path(ps[0])
    for q in ps[1:]:
        out = pathops.op(out, q, kind)
    return out


def union(*ps):
    return _fold(list(ps), pathops.PathOp.UNION)


def diff(a, *bs):
    return _fold([a, *bs], pathops.PathOp.DIFFERENCE)


def inter(a, b):
    return pathops.op(a, b, pathops.PathOp.INTERSECTION)


def to_d(p):
    out = []
    for verb, pts in p.segments:
        if verb == "moveTo":
            out.append("M" + " ".join(f"{_r(x)} {_r(y)}" for x, y in pts))
        elif verb == "lineTo":
            out.append("L" + " ".join(f"{_r(x)} {_r(y)}" for x, y in pts))
        elif verb == "curveTo":
            out.append("C" + " ".join(f"{_r(x)} {_r(y)}" for x, y in pts))
        elif verb == "qCurveTo":
            out.append("Q" + " ".join(f"{_r(x)} {_r(y)}" for x, y in pts))
        elif verb in ("closePath", "endPath"):
            out.append("Z")
    return "".join(out)


def svg(p, w=256, h=256, title="Aletheia", fill="#111111"):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_r(w)} {_r(h)}" width="{_r(w)}" '
            f'height="{_r(h)}" role="img" aria-labelledby="t"><title id="t">{title}</title>\n'
            f'<path fill="{fill}" d="{to_d(p)}"/>\n</svg>\n')
