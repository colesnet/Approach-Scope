"""Aircraft silhouettes, drawn from geometry rather than image files.

Each class of aircraft is described as a handful of polygons in a unit box
(nose at the top, fuselage length = 1.0).  The same description is used small
and filled for the map, and large as an outline drawing on the flight card.
"""
import math

import pygame

from . import gfx, theme


def _mirror(pts):
    """Right-hand outline (x >= 0, nose to tail) -> closed symmetric polygon."""
    left = [(-x, y) for x, y in reversed(pts) if x > 1e-9]
    return list(pts) + left


def _fuselage(half_w, nose=0.13, taper_from=0.70):
    w = half_w
    return _mirror([(0.0, 0.0), (0.32 * w, 0.012), (0.62 * w, 0.04), (0.86 * w, 0.08), (w, nose),
                    (w, taper_from), (0.72 * w, taper_from + 0.14), (0.34 * w, 0.955), (0.0, 1.0)])


def _wing(root_x, hs, y_root_le, sweep_le, root_chord, tip_chord, kink=None):
    """One wing as a polygon (right side); mirrored by the caller."""
    pts = [(root_x * 0.2, y_root_le), (hs, y_root_le + sweep_le), (hs, y_root_le + sweep_le + tip_chord)]
    if kink:
        kx, kc = kink
        pts.append((kx * hs, y_root_le + sweep_le * kx + kc))
    pts.append((root_x * 0.2, y_root_le + root_chord))
    return pts


def _both(poly):
    return [poly, [(-x, y) for x, y in poly]]


def _nacelle(cx, y_front, length, width):
    hw = width / 2.0
    return [(cx - hw * 0.7, y_front), (cx + hw * 0.7, y_front), (cx + hw, y_front + length * 0.25),
            (cx + hw, y_front + length), (cx - hw, y_front + length), (cx - hw, y_front + length * 0.25)]


def _wing_le_y(y_root_le, sweep_le, frac):
    return y_root_le + sweep_le * frac


def build(shape, span_ratio=None):
    """Describe a silhouette in unit coordinates.

    Returns dict(nacelles, wings, body, lines, circles, span, bounds) where
    x runs -half-span .. +half-span and y runs 0 (nose) .. 1 (tail).
    """
    nacelles, wings, body, lines, circles = [], [], [], [], []
    if shape in ("jet", "jet_wide", "jet_quad"):
        wide = shape != "jet"
        sr = span_ratio or (0.97 if wide else 0.93)
        hs = sr / 2.0
        fw = 0.050 if wide else 0.054
        y0 = 0.345 if wide else 0.36
        sweep = 0.30 * hs / 0.47 * (1.05 if wide else 0.95)
        root_c, tip_c = (0.235, 0.045) if wide else (0.215, 0.05)
        wings += _both(_wing(fw, hs, y0, sweep, root_c, tip_c, kink=(0.36, root_c * 0.50)))
        wings += _both([(fw * 0.3, 0.845), (hs * 0.40, 0.925), (hs * 0.40, 0.965), (fw * 0.3, 0.955)])
        body.append(_fuselage(fw))
        fracs = (0.30, 0.62) if shape == "jet_quad" else ((0.33,) if wide else (0.34,))
        nl, nw = (0.105, 0.062) if wide else (0.092, 0.055)
        for f in fracs:
            nacelles += _both(_nacelle(f * hs, _wing_le_y(y0, sweep, f) - nl * 0.72, nl, nw))
    elif shape == "jet_rear":
        sr = span_ratio or 0.86
        hs = sr / 2.0
        fw = 0.050
        y0 = 0.43
        sweep = 0.26 * hs / 0.43
        wings += _both(_wing(fw, hs, y0, sweep, 0.20, 0.05))
        wings += _both([(fw * 0.2, 0.90), (hs * 0.42, 0.955), (hs * 0.42, 0.99), (fw * 0.2, 0.985)])
        body.append(_fuselage(fw, taper_from=0.74))
        nacelles += _both(_nacelle(fw + 0.045, 0.705, 0.13, 0.055))
    elif shape in ("prop2", "prop4"):
        sr = span_ratio or 0.95
        hs = sr / 2.0
        fw = 0.047
        y0 = 0.37
        wings += _both(_wing(fw, hs, y0, 0.035, 0.125, 0.06))
        wings += _both([(fw * 0.2, 0.90), (hs * 0.36, 0.915), (hs * 0.36, 0.965), (fw * 0.2, 0.985)])
        body.append(_fuselage(fw, taper_from=0.72))
        for f in ((0.26, 0.55) if shape == "prop4" else (0.30,)):
            cx = f * hs
            yf = _wing_le_y(y0, 0.035, f) - 0.115
            nacelles += _both(_nacelle(cx, yf, 0.21, 0.05))
            lines += [((cx - 0.085, yf), (cx + 0.085, yf)), ((-cx - 0.085, yf), (-cx + 0.085, yf))]
    elif shape in ("prop1", "piston1", "piston2"):
        sr = span_ratio or 1.25
        hs = sr / 2.0
        fw = 0.068
        y0 = 0.26
        wings += _both(_wing(fw, hs, y0, 0.012, 0.19, 0.13))
        wings += _both([(fw * 0.2, 0.86), (hs * 0.36, 0.875), (hs * 0.36, 0.95), (fw * 0.2, 0.975)])
        body.append(_fuselage(fw, nose=0.10, taper_from=0.52))
        if shape == "piston2":
            cx = 0.30 * hs
            yf = y0 - 0.10
            nacelles += _both(_nacelle(cx, yf, 0.22, 0.075))
            lines += [((cx - 0.10, yf), (cx + 0.10, yf)), ((-cx - 0.10, yf), (-cx + 0.10, yf))]
        else:
            lines.append(((-0.14, 0.012), (0.14, 0.012)))
    elif shape == "heli":
        sr = span_ratio or 0.86
        body.append(_mirror([(0.0, 0.10), (0.07, 0.13), (0.105, 0.22), (0.105, 0.40), (0.06, 0.54),
                             (0.022, 0.62), (0.018, 0.93), (0.0, 0.95)]))
        wings.append([(-0.10, 0.90), (0.10, 0.90), (0.10, 0.925), (-0.10, 0.925)])
        r = sr / 2.0
        cy = 0.36
        circles.append(((0.0, cy), r))
        for ang in (35, 125, 215, 305):
            a = math.radians(ang)
            lines.append(((0.0, cy), (r * math.cos(a), cy + r * math.sin(a))))
    else:
        return build("jet", span_ratio)
    xs, ys = [], []
    for p in nacelles + wings + body:
        xs += [q[0] for q in p]
        ys += [q[1] for q in p]
    for a, b in lines:
        xs += [a[0], b[0]]
        ys += [a[1], b[1]]
    for c, r in circles:
        xs += [c[0] - r, c[0] + r]
        ys += [c[1] - r, c[1] + r]
    return {"nacelles": nacelles, "wings": wings, "body": body, "lines": lines, "circles": circles,
            "span": sr, "bounds": (min(xs), max(xs), min(ys), max(ys))}


def sprite(shape, length_px, color, span_ratio=None, halo=None, supersample=4):
    """Filled silhouette pointing north, centred on a transparent square surface."""
    sd = build(shape, span_ratio)
    x0, x1, y0, y1 = sd["bounds"]
    reach = max(abs(x0), abs(x1), abs(y0 - 0.5), abs(y1 - 0.5))      # from the centre of the fuselage
    side = int(math.ceil(2 * reach * length_px)) + 8
    if side % 2:
        side += 1
    big = side * supersample
    s = pygame.Surface((big, big), pygame.SRCALPHA)
    k = length_px * supersample
    ox, oy = big / 2.0, big / 2.0 - 0.5 * k

    def tp(pt):
        return (ox + pt[0] * k, oy + pt[1] * k)
    if halo:
        r = int(big * 0.5)
        for i in range(6, 0, -1):
            a = int(halo[3] * (1.0 - i / 7.0) ** 1.6)
            pygame.draw.circle(s, (halo[0], halo[1], halo[2], a), (big // 2, big // 2), int(r * i / 6.0))
    for p in sd["nacelles"] + sd["wings"] + sd["body"]:
        pygame.draw.polygon(s, color, [tp(q) for q in p])
    lw = max(1, int(supersample * max(1.0, length_px / 16.0)))
    for a, b in sd["lines"]:
        pygame.draw.line(s, color, tp(a), tp(b), lw)
    for c, r in sd["circles"]:
        pygame.draw.circle(s, color, tp(c), int(r * k), lw)
    return pygame.transform.smoothscale(s, (side, side))


def drawing(shape, box_w, box_h, color, span_ratio=None, fill=None, supersample=3):
    """Outline drawing of the aircraft fitted into a box.

    Returns (surface, pixels-per-fuselage-length).  The surface is RGB on black,
    meant to be blitted additively.
    """
    sd = build(shape, span_ratio)
    x0, x1, y0, y1 = sd["bounds"]
    half = max(abs(x0), abs(x1))
    k = min((box_h - 8) / (y1 - y0), (box_w - 8) / (2.0 * half))
    W, H = box_w * supersample, box_h * supersample
    s = pygame.Surface((W, H))
    s.fill(theme.BLACK)
    kk = k * supersample
    ox, oy = W / 2.0, (H - (y1 - y0) * kk) / 2.0 - y0 * kk

    def tp(pt):
        return (ox + pt[0] * kk, oy + pt[1] * kk)
    lw = max(2, int(round(supersample * 1.0)))
    inside = fill or theme.BLACK
    # nacelles first, wings over them, fuselage last: each layer hides the joins beneath it
    for group in (sd["nacelles"], sd["wings"], sd["body"]):
        for p in group:
            pts = [tp(q) for q in p]
            pygame.draw.polygon(s, inside, pts)
            pygame.draw.polygon(s, color, pts, lw)
    for a, b in sd["lines"]:
        pygame.draw.line(s, color, tp(a), tp(b), lw)
    for c, r in sd["circles"]:
        pygame.draw.circle(s, color, tp(c), int(r * kk), lw)
    out = pygame.transform.smoothscale(s, (box_w, box_h))
    return gfx.bloom(out, 0.55, 5), k


class SpriteBank(object):
    """Rotated map icons, made on demand and remembered."""

    STEP = 4  # degrees between cached rotations

    def __init__(self, scale=1.0):
        self.scale = scale
        self._base = {}
        self._rot = {}

    @staticmethod
    def size_for(shape, wtc):
        if shape == "jet_quad":
            return 27
        if shape == "jet_wide":
            return 25
        if shape == "jet":
            return 20
        if shape == "jet_rear":
            return 17 if wtc != "L" else 14
        if shape in ("prop2", "prop4"):
            return 17 if wtc != "L" else 14
        if shape == "heli":
            return 14
        return 12

    def get(self, shape, wtc, color, heading, halo=None, span_ratio=None):
        step = self.STEP
        ang = int(round((heading or 0.0) / step)) * step % 360
        key = (shape, wtc, color, ang, halo, span_ratio)
        s = self._rot.get(key)
        if s is not None:
            return s
        bkey = (shape, wtc, color, halo, span_ratio)
        base = self._base.get(bkey)
        if base is None:
            length = int(round(self.size_for(shape, wtc) * self.scale))
            base = sprite(shape, length, color, span_ratio, halo)
            self._base[bkey] = base
        s = pygame.transform.rotozoom(base, -ang, 1.0) if ang else base
        if len(self._rot) > 2500:
            self._rot.clear()
        self._rot[key] = s
        return s
