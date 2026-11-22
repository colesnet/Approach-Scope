"""Look and feel: palette, fonts, cached text.

Three inks, used consistently everywhere:
    green  = live aircraft and their numbers
    amber  = the chart: runway, glidepath, labels - where aircraft *should* be
    cyan   = you: your home and everything measured from it
Red is reserved for alerts.
"""
import collections

import pygame

# --- palette -----------------------------------------------------------------
BG = (2, 7, 5)
LAND = (8, 30, 18)
PANEL = (3, 11, 8)

G_HOT = (232, 255, 236)
G_BRIGHT = (92, 255, 138)
G_MID = (60, 215, 115)
G_DIM = (42, 168, 92)
G_FAINT = (22, 100, 55)
G_GHOST = (10, 52, 30)

A_BRIGHT = (255, 184, 48)
A_MID = (236, 166, 30)
A_DIM = (178, 122, 20)
A_FAINT = (92, 64, 10)

C_BRIGHT = (126, 236, 255)
C_MID = (82, 202, 232)
C_DIM = (46, 138, 164)
C_FAINT = (18, 66, 82)

R_BRIGHT = (255, 88, 72)
R_DIM = (140, 34, 26)

BLACK = (0, 0, 0)

# Terminus ships true bitmap strikes at these pixel sizes; anything else is
# scaled outlines.  Sticking to them (and whole multiples) keeps text razor sharp.
_CRISP = (12, 14, 16, 18, 20, 22, 24, 28, 32, 36, 40, 44, 48, 56, 64, 72, 80, 96)

# role -> (base pixel size at 800x480, bold?)
_ROLES = {
    "micro": (12, False),
    "microb": (12, True),
    "small": (14, False),
    "smallb": (14, True),
    "med": (16, True),
    "big": (20, True),
    "title": (24, True),
    "hero": (32, True),
    "mega": (48, True),
}


def _snap(px):
    return min(_CRISP, key=lambda s: (abs(s - px), s))


class Theme(object):
    """Fonts and a text cache, sized for the screen."""

    def __init__(self, font_regular, font_bold, scale=1.0):
        self.scale = scale
        self.warnings = []
        self._fonts = {}
        self._cache = collections.OrderedDict()
        self._glow = collections.OrderedDict()
        self._cache_max = 900
        self._glow_max = 160
        loaded = {}
        for role, (base, bold) in _ROLES.items():
            size = _snap(base * scale)
            key = (size, bold)
            if key not in loaded:
                loaded[key] = self._load(font_bold if bold else font_regular, size, bold)
            self._fonts[role] = loaded[key]
        self.metrics = {}
        for role, font in self._fonts.items():
            w, h = font.size("0")
            self.metrics[role] = (w, font.get_height())

    def _load(self, path, size, bold):
        try:
            return pygame.font.Font(path, size)
        except Exception as exc:
            msg = "font %s not usable (%s) - falling back to the built-in font" % (path, exc)
            if msg not in self.warnings:
                self.warnings.append(msg)
            f = pygame.font.Font(None, int(size * 1.25))
            try:
                f.set_bold(bold)
            except Exception:
                pass
            return f

    # --- measuring -------------------------------------------------------
    def cw(self, role):
        """Character cell width."""
        return self.metrics[role][0]

    def ch(self, role):
        """Character cell height."""
        return self.metrics[role][1]

    def width(self, role, text):
        return self._fonts[role].size(text)[0]

    # --- rendering -------------------------------------------------------
    def surf(self, role, text, color):
        key = (role, text, color)
        s = self._cache.get(key)
        if s is not None:
            self._cache.move_to_end(key)
            return s
        try:
            s = self._fonts[role].render(text, True, color)
        except Exception:
            s = self._fonts[role].render("?", True, color)
        self._cache[key] = s
        if len(self._cache) > self._cache_max:
            for _ in range(self._cache_max // 6):
                self._cache.popitem(last=False)
        return s

    def text(self, dst, role, text, x, y, color, align="l", glow=False):
        """Draw text; (x, y) is the top-left / top-right / top-centre by `align`.
        Returns the rect drawn."""
        if not text:
            return pygame.Rect(int(x), int(y), 0, 0)
        s = self.surf(role, text, color)
        w = s.get_width()
        if align == "r":
            x -= w
        elif align == "c":
            x -= w // 2
        x, y = int(x), int(y)
        if glow:
            g, pad = self.glow_surf(role, text, color)
            dst.blit(g, (x - pad, y - pad), special_flags=pygame.BLEND_RGB_ADD)
        dst.blit(s, (x, y))
        return pygame.Rect(x, y, w, s.get_height())

    def glow_surf(self, role, text, color):
        """Soft halo of the text, for additive blitting underneath it."""
        key = (role, text, color)
        hit = self._glow.get(key)
        if hit is not None:
            self._glow.move_to_end(key)
            return hit
        s = self.surf(role, text, color)
        pad = max(4, int(self.ch(role) * 0.3))
        w, h = s.get_width() + 2 * pad, s.get_height() + 2 * pad
        base = pygame.Surface((w, h))
        base.fill(BLACK)
        base.blit(s, (pad, pad))
        small = pygame.transform.smoothscale(base, (max(2, w // 4), max(2, h // 4)))
        blur = pygame.transform.smoothscale(small, (w, h))
        blur.fill((150, 150, 150), special_flags=pygame.BLEND_RGB_MULT)
        hit = (blur, pad)
        self._glow[key] = hit
        if len(self._glow) > self._glow_max:
            for _ in range(self._glow_max // 4):
                self._glow.popitem(last=False)
        return hit


def mix(c1, c2, t):
    """Blend two colours, t=0 gives c1."""
    return (int(c1[0] + (c2[0] - c1[0]) * t), int(c1[1] + (c2[1] - c1[1]) * t), int(c1[2] + (c2[2] - c1[2]) * t))


def dim(c, f):
    return (int(c[0] * f), int(c[1] * f), int(c[2] * f))


def sun_elevation(wall, lat, lon):
    """Height of the sun above the horizon in degrees (good to a fraction of a degree)."""
    import math
    n = (wall - 946728000.0) / 86400.0
    g = math.radians((357.528 + 0.9856003 * n) % 360.0)
    lam = math.radians((280.460 + 0.9856474 * n) % 360.0 + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g))
    eps = math.radians(23.439 - 0.0000004 * n)
    dec = math.asin(math.sin(eps) * math.sin(lam))
    ra = math.atan2(math.cos(eps) * math.sin(lam), math.cos(lam))
    gmst = (18.697374558 + 24.06570982441908 * n) % 24.0
    lha = math.radians(gmst * 15.0 + lon) - ra
    la = math.radians(lat)
    return math.degrees(math.asin(math.sin(la) * math.sin(dec) + math.cos(la) * math.cos(dec) * math.cos(lha)))
