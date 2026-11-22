"""Day in review: the airport's operating day summed up, shown overnight during curfew."""
import collections
import time

import pygame

from . import gfx, panels, theme

WAKE_ORDER = {"SUPER": 4, "HEAVY": 3, "MEDIUM": 2, "LIGHT": 1}


class Stats(object):
    pass


class ReviewPanel(object):
    def __init__(self, rect, th, ref, airport):
        self.rect = pygame.Rect(rect)
        self.th = th
        self.ref = ref
        self.airport = airport
        self._key = None
        self._surf = None

    # ------------------------------------------------------------------
    def compute(self, landings, go_arounds, ds, until):
        ref = self.ref
        st = Stats()
        st.day_start = ds
        L = [l for l in landings if ds <= l.wall < until]
        G = [g for g in go_arounds if ds <= g.wall < until]
        G.sort(key=lambda g: g.wall)
        st.landings, st.go_arounds = L, G
        st.n = len(L)
        st.hours = [0] * 24
        for l in L:
            st.hours[time.localtime(l.wall).tm_hour] += 1
        st.busiest = max(range(24), key=lambda h: (st.hours[h], -h)) if L else None
        st.first = min(l.wall for l in L) if L else None
        st.last = max(l.wall for l in L) if L else None

        airlines = collections.Counter()
        for l in L:
            al = ref.airline(l.flight, l.reg)
            airlines[al["name"] if al else "PRIVATE / OTHER"] += 1
        st.airlines = airlines.most_common(5)
        st.types = collections.Counter(l.type or "----" for l in L).most_common(5)
        st.airframes = len(set(l.hex for l in L))
        regs = collections.Counter(l.reg or l.hex.upper() for l in L).most_common(1)
        st.top_airframe = regs[0] if regs and regs[0][1] >= 2 else None
        origins = collections.Counter(l.origin for l in L if l.origin).most_common(1)
        st.top_origin = origins[0] if origins else None
        st.runways = collections.Counter(l.rwy for l in L).most_common()

        def size(l):
            dims = ref.dimensions(l.type)
            return (dims[0] if dims else 0.0, WAKE_ORDER.get(ref.wake_label(l.type), 0))
        st.biggest = max(L, key=size) if L else None
        return st

    # ------------------------------------------------------------------
    def draw(self, surf, ctx):
        curfew = ctx.airport.curfew
        ds = panels.day_start(ctx.wall, curfew)
        t = ctx.tracker
        counting_from = ctx.started_wall if ctx.started_wall > ds + 600 else None
        if counting_from is not None and any(l.wall < counting_from for l in t.landings if l.wall >= ds):
            counting_from = None
        key = (ds, len(t.landings), t.landings[0].wall if t.landings else 0, len(t.go_arounds),
               t.go_arounds[0].wall if t.go_arounds else 0, counting_from, int(ctx.wall // 60))
        if key != self._key:
            self._key = key
            st = self.compute(t.landings, t.go_arounds, ds, ds + 86400.0 + 3600.0)
            self._surf = self._render(st, ctx, counting_from)
        surf.blit(self._surf, self.rect.topleft)
        gfx.frame(surf, self.rect, theme.A_FAINT, theme.A_DIM)

    # ------------------------------------------------------------------
    def _render(self, st, ctx, counting_from):
        th, ref = self.th, self.ref
        w, h = self.rect.size
        s = pygame.Surface((w, h))
        s.fill(theme.BG)
        h24 = ctx.cfg.clock_24h
        L, R = 9, w - 9
        lt = time.localtime(st.day_start + 3600)
        th.text(s, "microb", "DAY IN REVIEW", L, 6, theme.A_MID)
        th.text(s, "smallb", time.strftime("%A %d %B", lt).upper().replace(" 0", " "), R, 5, theme.A_BRIGHT, "r")
        if counting_from is not None:
            th.text(s, "micro", "COUNTING FROM %s, WHEN THE DISPLAY STARTED" % panels.clock_text(counting_from, False, h24),
                    L, 20, theme.A_DIM)

        # --- headline numbers ---------------------------------------------
        cw = (R - L) // 4
        y_lab, y_val, y_sub = 36, 49, 83
        th.text(s, "micro", "LANDED", L, y_lab, theme.G_DIM)
        th.text(s, "hero", "%d" % st.n, L, y_val, theme.G_HOT, glow=True)
        th.text(s, "micro", "%d AIRCRAFT" % st.airframes if st.n else "", L, y_sub, theme.G_MID)
        x = L + cw
        n_ga = len(st.go_arounds)
        th.text(s, "micro", "GO-AROUNDS", x, y_lab, theme.G_DIM)
        th.text(s, "hero", "%d" % n_ga, x, y_val, theme.R_BRIGHT if n_ga else theme.G_MID, glow=bool(n_ga))
        if st.n:
            th.text(s, "micro", "1 IN %d ARRIVALS" % max(1, round(st.n / float(n_ga))) if n_ga else "ALL STRAIGHT IN",
                    x, y_sub, theme.G_MID)
        x = L + 2 * cw
        th.text(s, "micro", "BUSIEST HOUR", x, y_lab, theme.G_DIM)
        if st.busiest is not None:
            th.text(s, "title", "%02d:00" % st.busiest, x, y_val + 4, theme.G_BRIGHT)
            th.text(s, "micro", "%d LANDINGS" % st.hours[st.busiest], x, y_sub, theme.G_MID)
        else:
            th.text(s, "title", "--", x, y_val + 4, theme.G_DIM)
        x = L + 3 * cw
        th.text(s, "micro", "FIRST / LAST", x, y_lab, theme.G_DIM)
        if st.first is not None:
            th.text(s, "med", panels.clock_text(st.first, False, h24), x, y_val + 1, theme.G_BRIGHT)
            th.text(s, "med", panels.clock_text(st.last, False, h24), x, y_val + 18, theme.G_BRIGHT)
        else:
            th.text(s, "title", "--", x, y_val + 4, theme.G_DIM)
        pygame.draw.line(s, theme.G_FAINT, (L, 100), (R, 100))

        # --- landings by hour, 06:00 through to 05:00 ------------------------
        curfew = ctx.airport.curfew
        first_h = panels._hm(curfew.get("end"), (6, 0))[0] if curfew else 0
        th.text(s, "microb", "LANDINGS BY HOUR", L, 104, theme.A_MID)
        top, base = 122, 196
        slot = (R - L) / 24.0
        peak = max(max(st.hours), 1)
        for i in range(24):
            hr = (first_h + i) % 24
            x0 = L + i * slot
            noon_ts = st.day_start + i * 3600 + 60
            if panels.in_curfew(noon_ts, curfew):
                pygame.draw.rect(s, (34, 24, 4), (int(x0), top - 2, int(slot + 1), base - top + 2))
            c = st.hours[hr]
            if c:
                bh = max(2, int((base - top - 14) * c / float(peak)))
                col = theme.G_BRIGHT if hr == st.busiest else theme.G_MID
                pygame.draw.rect(s, col, (int(x0 + 2), base - bh, max(2, int(slot - 4)), bh))
                th.text(s, "micro", "%d" % c, x0 + slot / 2.0, base - bh - 13, col, "c")
            pygame.draw.line(s, theme.G_DIM, (int(x0), base), (int(x0 + slot), base))
            if i % 3 == 0:
                pygame.draw.line(s, theme.G_DIM, (int(x0), base), (int(x0), base + 3))
                th.text(s, "micro", "%02d" % hr, x0, base + 3, theme.G_MID)
        if curfew:
            cx = L + ((24 - (first_h - panels._hm(curfew.get("start"), (23, 0))[0])) % 24) * slot
            th.text(s, "micro", "CURFEW", min(cx + 3, R - 40), top + 1, theme.A_MID)
        pygame.draw.line(s, theme.G_FAINT, (L, 216), (R, 216))

        # --- who and what ----------------------------------------------------
        col_w = (R - L - 14) // 2
        self._ranked(s, "AIRLINES", [(name, n) for name, n in st.airlines], L, 220, col_w, st.n)
        self._ranked(s, "AIRCRAFT TYPES", [("%-4s %s" % (code, ref.type_name(code) if code != "----" else ""), n)
                                           for code, n in st.types], L + col_w + 14, 220, col_w, st.n)
        pygame.draw.line(s, theme.G_FAINT, (L, 308), (R, 308))

        # --- highlights and the go-around list --------------------------------
        y = 312
        if st.biggest is not None:
            b = st.biggest
            al = ref.airline(b.flight, b.reg)
            dims = ref.dimensions(b.type)
            txt = "%s  %s  %s%s" % (b.flight, ref.type_name(b.type) or b.type, b.reg,
                                    "  %d M SPAN" % round(dims[0]) if dims else "")
            if al and len(txt) < 46:
                txt = al["name"] + "  " + txt
            self._fact(s, "BIGGEST", txt, L, y, R)
        y += 14
        bits = []
        if st.top_origin:
            bits.append(("TOP ORIGIN", "%s (%d)" % st.top_origin))
        if st.runways:
            bits.append(("RUNWAYS", "  ".join("%s (%d)" % rw for rw in st.runways[:3])))
        if st.top_airframe:
            bits.append(("REPEAT VISITOR", "%s x%d" % st.top_airframe))
        x = L
        for label, value in bits:
            r = self._fact(s, label, value, x, y, R)
            x = r.right + 16
            if x > R - 60:
                break
        y += 14
        if st.go_arounds:
            parts = []
            for g in st.go_arounds:
                al = ref.airline(g.flight, g.reg)
                alt = " %dFT" % (int(round(g.low / 10.0)) * 10) if g.low else ""
                parts.append("%s %s%s%s" % (panels.clock_text(g.wall, False, h24), g.flight,
                                            " (%s)" % al["flight_iata"] if al and al["flight_iata"] else "", alt))
            r = th.text(s, "microb", "GO-AROUNDS", L, y, theme.R_BRIGHT)
            line, xx, yy = "", r.right + 6, y
            for part in parts:
                trial = (line + ",  " if line else "") + part
                if th.width("micro", trial) > R - xx and line:
                    th.text(s, "micro", line + ",", xx, yy, theme.G_BRIGHT)
                    line, yy = part, yy + 13
                    if yy > h - 14:
                        break
                else:
                    line = trial
            if yy <= h - 14:
                th.text(s, "micro", line, xx, yy, theme.G_BRIGHT)
        elif st.n:
            self._fact(s, "GO-AROUNDS", "NONE - EVERY APPROACH LANDED FIRST TIME", L, y, R)

        if not st.n:
            msg = "NO LANDINGS RECORDED FOR THIS DAY"
            th.text(s, "med", msg, w // 2, 150, theme.G_MID, "c")
        return s

    def _fact(self, s, label, value, x, y, right):
        th = self.th
        r = th.text(s, "micro", label, x, y, theme.G_DIM)
        role, txt = panels.fit(th, value, ("micro",), right - r.right - 6)
        r2 = th.text(s, role, txt, r.right + 6, y, theme.G_BRIGHT)
        return r.union(r2)

    def _ranked(self, s, title, rows, x, y, width, total):
        th = self.th
        th.text(s, "microb", title, x, y, theme.A_MID)
        if not rows:
            return
        top = max(n for _name, n in rows)
        for i, (name, n) in enumerate(rows):
            ry = y + 16 + i * 15
            bw = int((width - 30) * n / float(top))
            pygame.draw.rect(s, theme.G_GHOST, (x, ry, max(2, bw), 13))
            role, txt = panels.fit(th, name, ("micro",), width - 36)
            th.text(s, role, txt, x + 3, ry, theme.G_BRIGHT if i == 0 else theme.G_MID)
            th.text(s, "microb" if i == 0 else "micro", "%d" % n, x + width, ry,
                    theme.G_BRIGHT if i == 0 else theme.G_MID, "r")
