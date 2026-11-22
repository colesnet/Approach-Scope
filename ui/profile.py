"""Side view of the final approach: height against distance to run.

The amber line is the 3-degree glidepath to the runway.  An aircraft flying a
perfect approach slides down that line; the dots it leaves behind show how
closely it actually followed it.  The cyan marker is the point where the
approach passes your place, with the height aircraft are at as they go by.
"""
import math

import pygame

import tracker as trk
from . import gfx, theme

WEDGE = (22, 15, 3)


class ProfileView(object):
    PAD_L, PAD_R, PAD_T, PAD_B = 34, 10, 17, 14

    def __init__(self, rect, th, airport, home):
        self.rect = pygame.Rect(rect)
        self.th = th
        self.airport = airport
        self.home = home
        self._key = None
        self._static = None
        self._boxes = []

    def _scales(self, rw, span):
        """Mapping functions in panel-local pixels."""
        w, h = self.rect.size
        x0, x1 = self.PAD_L, w - self.PAD_R
        y0, y1 = self.PAD_T, h - self.PAD_B
        d0, d1 = -0.045 * span, span
        top = (rw.gp_alt_ft(span) - rw.elev_ft) * 1.16
        westbound = rw.ux < 0          # landing towards the west: runway drawn on the left, as on the map
        kx = (x1 - x0) / (d1 - d0)
        ky = (y1 - y0) / top

        def X(d):
            return x0 + (d - d0) * kx if westbound else x1 - (d - d0) * kx

        def Y(alt):
            return y1 - (alt - rw.elev_ft) * ky
        return X, Y, (x0, y0, x1, y1), top, westbound

    def _render_static(self, rw, span):
        th = self.th
        w, h = self.rect.size
        s = pygame.Surface((w, h))
        s.fill(theme.BG)
        X, Y, (x0, y0, x1, y1), top, westbound = self._scales(rw, span)
        gy = Y(rw.elev_ft)

        # height grid
        step = 1000.0 if top > 3400 else 500.0
        # heights are above sea level; start at the first round figure clear of the ground line
        a = math.ceil((rw.elev_ft + step * 0.4) / step) * step
        while a - rw.elev_ft < top * 0.97:
            y = Y(a)
            gfx.dotted(s, theme.A_FAINT, (x0, y), (x1, y), 4.0)
            th.text(s, "micro", "%d" % a, x0 - 5, y - 6, theme.A_DIM, "r")
            a += step
        th.text(s, "micro", "FT", x0 - 5, gy - 6, theme.A_DIM, "r")

        # the glidepath and its tolerance wedge
        gp0 = (X(-rw.gp_origin_nm), gy)
        hi = rw.elev_ft + (span + rw.gp_origin_nm) * 6076.115 * math.tan(math.radians(rw.gp_deg + 0.7))
        lo = rw.elev_ft + (span + rw.gp_origin_nm) * 6076.115 * math.tan(math.radians(rw.gp_deg - 0.7))
        wedge = pygame.Surface((w, h))
        wedge.fill(theme.BLACK)
        pygame.draw.polygon(wedge, WEDGE, [gp0, (X(span), Y(hi)), (X(span), Y(lo))])
        s.blit(wedge, (0, 0), special_flags=pygame.BLEND_RGB_ADD)
        gfx.line(s, theme.A_MID, gp0, (X(span), Y(rw.gp_alt_ft(span))))

        # ground, distance ticks, runway
        gfx.line(s, theme.A_DIM, (x0, gy), (x1, gy))
        label_every = 1 if span <= 8.5 else 5
        you_x = None
        if self.home.passes(rw.id) and self.home.abeam[rw.id][0] < span * 0.985:
            you_x = X(self.home.abeam[rw.id][0])
        last_label = None
        for d in range(1, int(span) + 1):
            x = X(d)
            major = d % 5 == 0
            gfx.line(s, theme.A_MID if major else theme.A_DIM, (x, gy), (x, gy + (4 if major else 2)))
            if d % label_every == 0 and not (you_x is not None and abs(x - you_x) < 16):
                last_label = th.text(s, "micro", "%d" % d, x, gy + 2, theme.A_MID, "c")
        if last_label is not None:
            if westbound and last_label.right + 20 < w:
                th.text(s, "micro", "NM", last_label.right + 3, gy + 2, theme.A_DIM)
            elif not westbound and last_label.left - 16 > x0:
                th.text(s, "micro", "NM", last_label.left - 3, gy + 2, theme.A_DIM, "r")
        ra, rb = X(0.0), X(max(-rw.length_nm, -0.045 * span))
        pygame.draw.rect(s, theme.A_BRIGHT, (int(min(ra, rb)), int(gy) - 1, max(3, int(abs(ra - rb))), 3))
        th.text(s, "microb", rw.id, X(0.0), gy + 2, theme.A_BRIGHT, "c")

        # published fixes on the approach
        boxes = []
        for fx in rw.fixes:
            if fx.dtg > span * 0.985:
                continue
            x, y = X(fx.dtg), Y(rw.gp_alt_ft(fx.dtg))
            gfx.dotted(s, theme.A_DIM, (x, gy - 2), (x, y + 4), 3.0)
            gfx.polyline(s, theme.A_MID, [(x, y - 4), (x + 4, y + 3), (x - 4, y + 3)], 1, closed=True)
            ly = y - 18 if y - 18 >= 15 else y + 7
            boxes.append(th.text(s, "micro", fx.name, x, ly, theme.A_MID, "c").inflate(6, 2))

        # where the approach passes you
        note = ""
        if self.home.passes(rw.id):
            d_ab, _x_ab = self.home.abeam[rw.id]
            if d_ab < span * 0.985:
                x = X(d_ab)
                alt = rw.gp_alt_ft(d_ab)
                y = Y(alt)
                gfx.dotted(s, theme.C_MID, (x, gy - 8), (x, y + 2), 3.0)
                gfx.line(s, theme.C_BRIGHT, (x - 4, y), (x + 4, y))
                gfx.poly(s, theme.C_BRIGHT, [(x, gy - 8), (x + 5, gy - 3), (x + 3, gy - 3), (x + 3, gy),
                                              (x - 3, gy), (x - 3, gy - 3), (x - 5, gy - 3)])
                th.text(s, "microb", "YOU", x, gy + 2, theme.C_BRIGHT, "c")
                note = "PASSES YOU AT %d FT" % (int(round(alt / 10.0)) * 10)

        # caption, on the low side of the glidepath where the panel is empty
        cap = "SIDE VIEW"
        sub = "%.1f\u00b0 GLIDEPATH" % rw.gp_deg
        if westbound:
            r = th.text(s, "microb", cap, 7, 3, theme.A_MID)
            r = th.text(s, "micro", sub, r.right + 8, 3, theme.A_DIM)
            if note:
                r = th.text(s, "micro", note, r.right + 12, 3, theme.C_MID)
            boxes.append(pygame.Rect(0, 0, r.right + 6, 16))
        else:
            r = th.text(s, "micro", sub, w - 7, 3, theme.A_DIM, "r")
            r = th.text(s, "microb", cap, r.left - 8, 3, theme.A_MID, "r")
            if note:
                r = th.text(s, "micro", note, r.left - 12, 3, theme.C_MID, "r")
            boxes.append(pygame.Rect(r.left - 6, 0, w - r.left + 6, 16))
        self._boxes = boxes
        return s

    def _tag(self, surf, text, role, color, p, taken, westbound):
        """Label an aircraft: behind it and above the path if there is room, else below."""
        th = self.th
        tw, tht = th.width(role, text), th.ch(role)
        behind = 1 if westbound else -1
        px, py = int(p[0]), int(p[1])
        inner = self.rect.inflate(-6, -4)
        for dx, dy in ((behind * 12, -15), (-behind * 12, 4), (behind * 12, 4), (0, -19), (behind * 30, -15)):
            x = px + dx if dx > 0 else (px + dx - tw if dx < 0 else px - tw // 2)
            box = pygame.Rect(x, py + dy, tw, tht)
            if inner.contains(box) and box.inflate(4, 0).collidelist(taken) == -1:
                th.text(surf, role, text, box.x, box.y, color)
                taken.append(box.inflate(6, 2))
                return

    @staticmethod
    def _glyph(surf, x, y, heading_left, color, scale=1.0):
        """A tiny aircraft seen from the side."""
        pts = [(-8, 0), (-5, -2), (4, -2), (7, -6), (9, -6), (8, 0), (6, 1), (-5, 1)]
        sx = scale if heading_left else -scale
        gfx.poly(surf, color, [(x + px * sx, y + py * scale) for px, py in pts])

    def draw(self, surf, ctx, span):
        th = self.th
        rw = ctx.rw
        now = ctx.now
        r = self.rect
        key = (rw.id, round(span, 3))
        if key != self._key:
            self._static = self._render_static(rw, span)
            self._key = key
        surf.blit(self._static, r.topleft)
        X, Y, (x0, y0, x1, y1), top, westbound = self._scales(rw, span)
        ox, oy = r.x, r.y
        gy = oy + Y(rw.elev_ft)
        prev_clip = surf.get_clip()
        surf.set_clip(r)
        feat = ctx.feat
        d_lo = -0.045 * span

        def place(t):
            x, y, alt, _hd = t.state_at(now)
            if alt is None:
                return None
            dtg, _xtk = rw.along_cross(x, y)
            if dtg < d_lo or dtg > span:
                return None
            return ox + X(dtg), oy + Y(max(alt, rw.elev_ft)), alt

        shown = [t for t in ctx.arrivals if t.rwy is rw and t.phase in (trk.FINAL, trk.GO_AROUND)]
        if feat is not None and feat.rwy is rw and feat.phase == trk.LANDED and feat not in shown:
            shown.append(feat)
        taken = [b.move(ox, oy) for b in self._boxes]
        if feat is not None and feat in shown:
            for (tp, hx, hy, halt) in feat.hist:
                if halt is None or now - tp > 900.0:
                    continue
                dtg, _ = rw.along_cross(hx, hy)
                if d_lo <= dtg <= span:
                    px, py = int(ox + X(dtg)), int(oy + Y(max(halt, rw.elev_ft)))
                    if r.collidepoint(px, py):
                        surf.fill(theme.G_MID, (px, py, 2, 2))
        for t in shown:
            if t is feat:
                continue
            p = place(t)
            if p is None:
                continue
            col = theme.G_DIM if t.is_stale(now) else theme.G_MID
            self._glyph(surf, p[0], p[1], westbound, col)
            self._tag(surf, t.label, "micro", col, p, taken, westbound)
        if feat is not None and feat in shown:
            p = place(feat)
            if p is not None:
                px, py, alt = p
                gfx.dotted(surf, theme.G_DIM, (px, py + 4), (px, gy - 1), 3.0)
                self._glyph(surf, px, py, westbound, theme.G_HOT, 1.25)
                self._tag(surf, feat.label, "microb", theme.G_HOT, p, taken, westbound)
        elif feat is not None and feat.phase == trk.INBOUND:
            txt = "%s IS NOT ON FINAL YET" % feat.label
            if westbound:
                th.text(surf, "micro", txt, r.right - 8, gy - 30, theme.G_DIM, "r")
            else:
                th.text(surf, "micro", txt, r.x + self.PAD_L + 6, gy - 30, theme.G_DIM)
        surf.set_clip(prev_clip)
        gfx.frame(surf, r)
