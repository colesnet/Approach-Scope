"""Drawing primitives used by every panel."""
import math

import pygame
import pygame.gfxdraw

from . import theme


def aaline(surf, color, p1, p2):
    pygame.draw.aaline(surf, color, p1, p2)


def line(surf, color, p1, p2, width=1):
    """Anti-aliased line of any width."""
    if width <= 1:
        pygame.draw.aaline(surf, color, p1, p2)
        return
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    length = math.hypot(dx, dy)
    if length < 0.01:
        return
    nx, ny = -dy / length * width / 2.0, dx / length * width / 2.0
    pts = [(int(round(p1[0] + nx)), int(round(p1[1] + ny))), (int(round(p2[0] + nx)), int(round(p2[1] + ny))),
           (int(round(p2[0] - nx)), int(round(p2[1] - ny))), (int(round(p1[0] - nx)), int(round(p1[1] - ny)))]
    try:
        pygame.gfxdraw.filled_polygon(surf, pts, color)
        pygame.gfxdraw.aapolygon(surf, pts, color)
    except Exception:
        pygame.draw.line(surf, color, p1, p2, int(width))


def polyline(surf, color, pts, width=1, closed=False):
    n = len(pts)
    if n < 2:
        return
    for i in range(n - 1):
        line(surf, color, pts[i], pts[i + 1], width)
    if closed:
        line(surf, color, pts[-1], pts[0], width)


def dashed(surf, color, p1, p2, dash=6.0, gap=4.0, width=1, phase=0.0):
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    length = math.hypot(dx, dy)
    if length < 0.5:
        return
    ux, uy = dx / length, dy / length
    step = dash + gap
    pos = -(phase % step)
    while pos < length:
        a = max(0.0, pos)
        b = min(length, pos + dash)
        if b > a:
            line(surf, color, (p1[0] + ux * a, p1[1] + uy * a), (p1[0] + ux * b, p1[1] + uy * b), width)
        pos += step


def dotted(surf, color, p1, p2, spacing=4.0):
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    length = math.hypot(dx, dy)
    if length < 0.5:
        return
    n = max(1, int(length / spacing))
    for i in range(n + 1):
        t = i / float(n)
        x, y = int(round(p1[0] + dx * t)), int(round(p1[1] + dy * t))
        if 0 <= x < surf.get_width() and 0 <= y < surf.get_height():
            surf.set_at((x, y), color)


def poly(surf, color, pts, outline=None):
    """Filled anti-aliased polygon."""
    ipts = [(int(round(x)), int(round(y))) for x, y in pts]
    if len(ipts) < 3:
        return
    try:
        pygame.gfxdraw.filled_polygon(surf, ipts, color)
        pygame.gfxdraw.aapolygon(surf, ipts, outline or color)
    except Exception:
        pygame.draw.polygon(surf, color, ipts)


def circle(surf, color, centre, radius, width=0):
    x, y, r = int(round(centre[0])), int(round(centre[1])), int(round(radius))
    if r < 1:
        return
    try:
        if width == 0:
            pygame.gfxdraw.filled_circle(surf, x, y, r, color)
        pygame.gfxdraw.aacircle(surf, x, y, r, color)
        if width > 1:
            pygame.gfxdraw.aacircle(surf, x, y, r - 1, color)
    except Exception:
        pygame.draw.circle(surf, color, (x, y), r, width)


def diamond(surf, color, centre, r, filled=True):
    x, y = centre
    pts = [(x, y - r), (x + r, y), (x, y + r), (x - r, y)]
    if filled:
        poly(surf, color, pts)
    else:
        polyline(surf, color, pts, 1, closed=True)


def bloom(surface, strength=0.6, radius=4):
    """Returns a copy of an RGB-on-black surface with a soft glow added."""
    w, h = surface.get_size()
    small = pygame.transform.smoothscale(surface, (max(2, w // radius), max(2, h // radius)))
    blur = pygame.transform.smoothscale(small, (w, h))
    k = int(max(0, min(255, strength * 255)))
    blur.fill((k, k, k), special_flags=pygame.BLEND_RGB_MULT)
    out = surface.copy()
    out.blit(blur, (0, 0), special_flags=pygame.BLEND_RGB_ADD)
    return out


def frame(surf, rect, col_line=theme.G_FAINT, col_corner=theme.G_DIM, corner=7):
    """Panel outline: a quiet hairline with brighter corner brackets."""
    r = pygame.Rect(rect)
    pygame.draw.rect(surf, col_line, r, 1)
    x0, y0, x1, y1 = r.left, r.top, r.right - 1, r.bottom - 1
    c = corner
    for (cx, cy, sx, sy) in ((x0, y0, 1, 1), (x1, y0, -1, 1), (x0, y1, 1, -1), (x1, y1, -1, -1)):
        pygame.draw.line(surf, col_corner, (cx, cy), (cx + sx * c, cy))
        pygame.draw.line(surf, col_corner, (cx, cy), (cx, cy + sy * c))


def crt_overlay(size, scanlines=True, vignette=True):
    """Full-screen multiply layer: gentle corner darkening and (optionally) scanlines."""
    w, h = size
    ov = pygame.Surface((w, h))
    ov.fill((255, 255, 255))
    if vignette:
        # build small, scale up: smooth and cheap
        sw, sh = 64, 40
        small = pygame.Surface((sw, sh))
        for y in range(sh):
            for x in range(sw):
                dx = (x + 0.5) / sw * 2.0 - 1.0
                dy = (y + 0.5) / sh * 2.0 - 1.0
                d = (abs(dx) ** 2.6 + abs(dy) ** 2.6) ** (1 / 2.6)
                v = 1.0 - 0.42 * max(0.0, d - 0.55) ** 1.6 * 2.2
                v = int(255 * max(0.55, min(1.0, v)))
                small.set_at((x, y), (v, v, v))
        ov = pygame.transform.smoothscale(small, (w, h))
    if scanlines:
        dark = pygame.Surface((w, 1))
        dark.fill((218, 218, 218))
        for y in range(1, h, 2):
            ov.blit(dark, (0, y), special_flags=pygame.BLEND_RGB_MULT)
    return ov
