"""Everything around the map: header, flight card, window view, queue, log, status bar."""
import math
import time

import pygame

import geo
import tracker as trk
from . import gfx, icons, theme

EMERGENCY_SQUAWKS = {"7700": "EMERGENCY", "7600": "RADIO FAILURE", "7500": "HIJACK CODE"}


def fit(th, text, roles, max_w):
    """Largest of `roles` in which the text fits; otherwise truncate in the smallest."""
    for role in roles:
        if th.width(role, text) <= max_w:
            return role, text
    role = roles[-1]
    n = max(1, int(max_w // th.cw(role)) - 1)
    return role, text[:n].rstrip() + "\u2026"


def mmss(seconds):
    s = max(0, int(math.ceil(seconds)))
    if s >= 3600:
        return "%d:%02d:%02d" % (s // 3600, (s % 3600) // 60, s % 60)
    return "%02d:%02d" % (s // 60, s % 60)


def clock_text(wall, seconds=False, h24=True):
    lt = time.localtime(wall)
    if h24:
        return time.strftime("%H:%M:%S" if seconds else "%H:%M", lt)
    return time.strftime("%I:%M:%S" if seconds else "%I:%M", lt).lstrip("0") + time.strftime("%p", lt)


def ordinal(n):
    if 10 <= n % 100 <= 20:
        return "%dTH" % n
    return "%d%s" % (n, {1: "ST", 2: "ND", 3: "RD"}.get(n % 10, "TH"))


# ----------------------------------------------------------------------------
class Header(object):
    def __init__(self, rect, th):
        self.rect = pygame.Rect(rect)
        self.th = th

    def draw(self, surf, ctx):
        th, r = self.th, self.rect
        ap, t = ctx.airport, ctx.tracker
        y = r.y + 4
        x = r.x + 7
        x = th.text(surf, "med", ap.name, x, y - 1, theme.A_BRIGHT, glow=True).right + 7
        x = th.text(surf, "small", ap.icao, x, y + 1, theme.A_DIM).right + 14
        x = th.text(surf, "smallb", "ARRIVALS RUNWAY %s" % t.active_id, x, y + 1, theme.A_BRIGHT).right + 7
        basis = {"WIND": "BY WIND", "DEFAULT": "ASSUMED"}.get(t.active_basis, "")
        if basis:
            x = th.text(surf, "micro", basis, x, y + 3, theme.A_DIM).right + 7
        clk = clock_text(ctx.wall, True, ctx.cfg.clock_24h)
        right = th.text(surf, "med", clk, r.right - 7, y - 1, theme.A_BRIGHT, "r").left - 16

        parts = []
        m = ctx.metar
        if m is not None:
            age = m.age_min(ctx.wall)
            old = age is not None and age > 100.0
            col = theme.A_DIM if old else theme.A_MID
            if m.wind_kt is not None:
                parts.append(("WIND " + m.wind_text(), col))
            if m.qnh is not None:
                parts.append(("QNH %d" % int(round(m.qnh)), col))
            if m.temp is not None:
                parts.append(("%d\u00b0C" % int(round(m.temp)), col))
            if m.sky:
                parts.append((m.sky, col))
            if old:
                parts.append(("%dH OLD" % int(age // 60), theme.A_DIM))
        elif t.qnh is not None:
            parts.append(("QNH %d" % int(round(t.qnh)), theme.A_MID))
        gap = 14
        while parts and sum(th.width("small", p[0]) for p in parts) + gap * (len(parts) - 1) > right - x - 8:
            parts.pop()
        px = right
        for text, col in reversed(parts):
            px = th.text(surf, "small", text, px, y + 1, col, "r").left - gap
        pygame.draw.line(surf, theme.A_FAINT, (r.x, r.bottom - 1), (r.right - 1, r.bottom - 1))


# ----------------------------------------------------------------------------
class FlightCard(object):
    """The big panel: everything known about one arriving aircraft."""

    ART_W, ART_H = 92, 100

    def __init__(self, rect, th, ref):
        self.rect = pygame.Rect(rect)
        self.th = th
        self.ref = ref
        self._art = {}

    # --- small drawing helpers ------------------------------------------
    def _artwork(self, shape, span_ratio):
        key = (shape, span_ratio)
        if key not in self._art:
            if len(self._art) > 40:
                self._art.clear()
            self._art[key] = icons.drawing(shape, self.ART_W, self.ART_H, theme.G_BRIGHT, span_ratio,
                                           fill=theme.G_GHOST)[0]
        return self._art[key]

    def _badge(self, surf, text, right, y, color):
        th = self.th
        w = th.width("microb", text) + 10
        box = pygame.Rect(right - w, y, w, th.ch("microb") + 2)
        pygame.draw.rect(surf, color, box)
        th.text(surf, "microb", text, box.x + 5, box.y + 1, theme.BG)
        return box

    def _big(self, surf, x, y, text, color, unit="", glow=True, role="mega"):
        th = self.th
        r = th.text(surf, role, text, x, y, color, glow=glow)
        if unit:
            th.text(surf, "med", unit, r.right + 5, r.bottom - th.ch("med") - 7, color)
        return r

    def _stat(self, surf, x, y, label, value, unit, color=theme.G_BRIGHT):
        th = self.th
        th.text(surf, "micro", label, x, y, theme.G_DIM)
        r = th.text(surf, "big", value, x, y + 12, color)
        if unit:
            th.text(surf, "micro", unit, r.right + 3, r.bottom - th.ch("micro") - 3, theme.G_MID)

    def _bar(self, surf, x, y, w, frac, live=True):
        """Deviation bar: centre tick is 'spot on', the diamond is the aircraft."""
        cy = y + 7
        pygame.draw.line(surf, theme.G_FAINT, (x, cy), (x + w, cy))
        for f, hgt, col in ((-1, 3, theme.G_DIM), (-0.5, 2, theme.G_FAINT), (0.5, 2, theme.G_FAINT),
                            (1, 3, theme.G_DIM)):
            tx = int(x + w / 2.0 + f * w / 2.0)
            pygame.draw.line(surf, col, (tx, cy - hgt), (tx, cy + hgt))
        mx = int(x + w / 2.0)
        pygame.draw.line(surf, theme.A_MID, (mx, cy - 5), (mx, cy + 5))
        if frac is not None:
            f = max(-1.0, min(1.0, frac))
            gfx.diamond(surf, theme.G_HOT if live else theme.G_DIM, (x + w / 2.0 + f * w / 2.0, cy), 4)

    def _rule(self, surf, y):
        r = self.rect
        pygame.draw.line(surf, theme.G_FAINT, (r.x + 9, y), (r.right - 10, y))

    # --- entry point ------------------------------------------------------
    def draw(self, surf, ctx):
        surf.fill(theme.BG, self.rect)
        if ctx.feat is None:
            self._idle(surf, ctx)
        else:
            self._flight(surf, ctx, ctx.feat)
        gfx.frame(surf, self.rect)

    # --- a flight ---------------------------------------------------------
    def _flight(self, surf, ctx, t):
        th, r, now, ref = self.th, self.rect, ctx.now, self.ref
        L, R = r.x + 9, r.right - 9
        W = R - L
        y0 = r.y
        x, y, alt, heading = t.state_at(now)
        on_rwy = t.rwy is not None and t.phase in (trk.FINAL, trk.LANDED, trk.GO_AROUND)
        rw = t.rwy if on_rwy else ctx.rw
        dtg = xtk = None
        if on_rwy:
            dtg, xtk = rw.along_cross(x, y)
        stale = t.is_stale(now)

        # caption and status badge
        pos = ctx.arrivals.index(t) if t in ctx.arrivals else -1
        if t.phase == trk.LANDED:
            cap = "JUST LANDED"
        elif pos == 0:
            cap = "NEXT TO LAND"
        elif pos > 0:
            cap = "%s IN THE QUEUE" % ordinal(pos + 1)
        else:
            cap = "ARRIVAL"
        alert = EMERGENCY_SQUAWKS.get(t.squawk)
        if alert:
            th.text(surf, "microb", "SQUAWK %s %s" % (t.squawk, alert), L, y0 + 6, theme.R_BRIGHT)
        else:
            th.text(surf, "microb", cap, L, y0 + 6, theme.A_MID)
        if t.phase == trk.FINAL:
            self._badge(surf, "ON FINAL RWY %s" % rw.id, R, y0 + 4, theme.G_BRIGHT)
        elif t.phase == trk.GO_AROUND:
            self._badge(surf, "GO-AROUND", R, y0 + 4, theme.R_BRIGHT)
        elif t.phase == trk.LANDED:
            self._badge(surf, "LANDED RWY %s" % rw.id, R, y0 + 4, theme.G_MID)
        else:
            self._badge(surf, "INBOUND", R, y0 + 4, theme.A_BRIGHT)

        # who it is
        text_w = W - self.ART_W - 6
        th.text(surf, "hero", t.label, L, y0 + 19, theme.G_HOT, glow=True)
        al = ref.airline(t.flight, t.reg)
        yy = y0 + 55
        if al:
            flt = al["flight_iata"]
            room = text_w - (th.width("small", flt) + 8 if flt else 0)
            role, txt = fit(th, al["name"], ("med", "microb"), room)
            rr = th.text(surf, role, txt, L, yy + (0 if role == "med" else 3), theme.G_BRIGHT)
            if flt:
                th.text(surf, "small", flt, rr.right + 8, yy + 2, theme.G_MID)
        else:
            who = "MILITARY" if t.military else (t.own_op.upper() if t.own_op else "PRIVATE / CHARTER")
            role, txt = fit(th, who, ("med", "microb"), text_w)
            th.text(surf, role, txt, L, yy + (0 if role == "med" else 3), theme.G_BRIGHT)
        tname = ref.type_name(t.type, t.desc) or "TYPE NOT REPORTED"
        role, txt = fit(th, tname, ("small", "micro"), text_w)
        th.text(surf, role, txt, L, y0 + 75, theme.G_BRIGHT)
        bits = [b for b in (t.reg, ref.wake_label(t.type)) if b]
        dims = ref.dimensions(t.type)
        if dims:
            bits.append("%d M SPAN" % int(round(dims[0])))
        role, txt = fit(th, "   ".join(bits), ("micro",), text_w)
        th.text(surf, role, txt, L, y0 + 91, theme.G_MID)
        route = ctx.routes.get(t.flight) if ctx.routes is not None else None
        if route is not None and route.origin_city:
            role, txt = fit(th, "FROM " + route.origin_city, ("smallb", "microb"), text_w - 34)
            rr = th.text(surf, role, txt, L, y0 + 106, theme.G_BRIGHT)
            code = route.origin_iata or route.origin_icao
            if code:
                th.text(surf, "small", code, rr.right + 8, y0 + 106, theme.G_DIM)
        elif t.hist:
            fx, fy = t.hist[0][1], t.hist[0][2]
            if math.hypot(fx, fy) > 14.0:
                th.text(surf, "small", "CAME IN FROM THE %s" % geo.compass_words(geo.bearing_xy(fx, fy)),
                        L, y0 + 106, theme.G_MID)
        shape = ref.shape_class(t.type, t.category)
        ratio = round(dims[0] / dims[1], 2) if dims else None
        surf.blit(self._artwork(shape, ratio), (R - self.ART_W, y0 + 21), special_flags=pygame.BLEND_RGB_ADD)
        self._rule(surf, y0 + 125)

        # the two clocks
        yl, yv, ys = y0 + 130, y0 + 142, y0 + 191
        xa, xb = L, L + W // 2 + 6
        h24 = ctx.cfg.clock_24h
        if t.phase == trk.FINAL:
            if t.t_td is not None:
                left = t.t_td - now
                th.text(surf, "microb", "TOUCHDOWN IN", xa, yl, theme.G_BRIGHT)
                self._big(surf, xa, yv, mmss(left), theme.G_HOT)
                th.text(surf, "micro", "AT " + clock_text(ctx.wall + left, True, h24), xa, ys, theme.G_MID)
            self._range_block(surf, ctx, t, xb, yl, yv, ys)
        elif t.phase == trk.INBOUND:
            if t.t_td is not None:
                left = max(0.0, t.t_td - now)
                th.text(surf, "microb", "TOUCHDOWN IN ABOUT", xa, yl, theme.G_BRIGHT)
                self._big(surf, xa, yv, "%d" % max(1, int(round(left / 60.0))), theme.G_HOT, "MIN")
                th.text(surf, "micro", "AROUND " + clock_text(ctx.wall + left, False, h24), xa, ys, theme.G_MID)
            self._range_block(surf, ctx, t, xb, yl, yv, ys)
        elif t.phase == trk.GO_AROUND:
            blink = int(now * 2) % 2 == 0
            th.text(surf, "microb", "APPROACH ABANDONED", xa, yl, theme.R_BRIGHT)
            self._big(surf, xa, yv + 6, "GOING AROUND", theme.R_BRIGHT if blink else theme.R_DIM, role="hero")
            th.text(surf, "micro", "CLIMBING AWAY TO COME BACK FOR ANOTHER APPROACH", xa, ys, theme.A_MID)
        elif t.phase == trk.LANDED and t.landed_at is not None:
            th.text(surf, "microb", "TOUCHED DOWN AT", xa, yl, theme.G_BRIGHT)
            self._big(surf, xa, yv, clock_text(t.landed_wall or ctx.wall, False, h24), theme.G_HOT)
            note = "SIGNAL LOST LOW DOWN - TIME ESTIMATED" if t.landed_assumed else "RUNWAY %s" % rw.id
            th.text(surf, "micro", note, xa, ys, theme.G_MID)
            th.text(surf, "microb", "ON THE GROUND", xb, yl, theme.G_MID)
            self._big(surf, xb, yv, mmss(now - t.landed_at), theme.G_MID, glow=False)
        self._rule(surf, y0 + 208)

        # the numbers
        col = theme.G_MID if stale else theme.G_BRIGHT
        cw4 = W // 4
        ys0 = y0 + 213
        if t.phase == trk.FINAL and dtg is not None:
            self._stat(surf, L, ys0, "TO RUN", "%.1f" % max(0.0, dtg), "NM", col)
        elif t.phase == trk.INBOUND and t.track_miles is not None:
            self._stat(surf, L, ys0, "TO RUN", "~%d" % int(round(t.track_miles)), "NM", col)
        else:
            self._stat(surf, L, ys0, "TO RUN", "--", "", theme.G_DIM)
        if t.phase == trk.LANDED:
            self._stat(surf, L + cw4, ys0, "ALTITUDE", "GND", "", col)
        elif alt is not None:
            self._stat(surf, L + cw4, ys0, "ALTITUDE", "%d" % (int(round(alt / 10.0)) * 10), "FT", col)
        else:
            self._stat(surf, L + cw4, ys0, "ALTITUDE", "--", "", theme.G_DIM)
        self._stat(surf, L + 2 * cw4, ys0, "GROUND SPEED", "%d" % int(round(t.gs)) if t.gs is not None else "--",
                   "KT" if t.gs is not None else "", col)
        vr = t.vrate
        if vr is None or t.phase == trk.LANDED:
            self._stat(surf, L + 3 * cw4 + 6, ys0, "DESCENT", "--", "", theme.G_DIM)
        elif vr > 100:
            self._stat(surf, L + 3 * cw4 + 6, ys0, "CLIMB", "%d" % (int(round(vr / 10.0)) * 10), "FPM", col)
        else:
            self._stat(surf, L + 3 * cw4 + 6, ys0, "DESCENT", "%d" % (int(round(-min(vr, 0.0) / 10.0)) * 10),
                       "FPM", col)

        # where it is right now
        prefix, place = self._place(ctx, t, x, y)
        yo = y0 + 251
        if place:
            rr = th.text(surf, "micro", prefix, L, yo + 2, theme.G_DIM)
            role, txt = fit(th, place, ("smallb", "microb"), W - 150)
            th.text(surf, role, txt, rr.right + 7, yo, theme.G_BRIGHT)
        if t.dist_ap is not None and t.phase != trk.LANDED:
            brg = geo.bearing_xy(x - 0.0, y - 0.0)
            far = int(round(math.hypot(x, y)))
            th.text(surf, "micro", "%d NM %s OF THE FIELD" % (far, geo.compass16(brg)) if far >= 1 else
                    "AT THE FIELD", R, yo + 2, theme.G_DIM, "r")
        self._rule(surf, y0 + 269)

        # how well it is flying the approach
        bx, bw = L + 70, 108
        vx = bx + bw + 10
        y1, y2 = y0 + 274, y0 + 292
        if t.phase == trk.FINAL and dtg is not None and dtg > 0.1 and alt is not None:
            dev = alt - rw.gp_alt_ft(dtg)
            th.text(surf, "micro", "GLIDEPATH", L, y1 + 1, theme.G_DIM)
            self._bar(surf, bx, y1, bw, dev / 300.0, not stale)
            txt = "ON GLIDEPATH" if abs(dev) < 40 else "%d FT %s" % (int(round(abs(dev) / 10.0)) * 10,
                                                                    "HIGH" if dev > 0 else "LOW")
            th.text(surf, "smallb", txt, vx, y1, theme.G_HOT if abs(dev) < 40 else col)
            metres = xtk * 1852.0
            th.text(surf, "micro", "CENTRELINE", L, y2 + 1, theme.G_DIM)
            self._bar(surf, bx, y2, bw, metres / 400.0, not stale)
            txt = "ON CENTRELINE" if abs(metres) < 30 else "%d M %s" % (int(round(abs(metres) / 10.0)) * 10,
                                                                       "RIGHT" if metres > 0 else "LEFT")
            th.text(surf, "smallb", txt, vx, y2, theme.G_HOT if abs(metres) < 30 else col)
        elif t.phase == trk.INBOUND:
            th.text(surf, "micro", "HEADING FOR", L, y1 + 1, theme.G_DIM)
            th.text(surf, "smallb", "RUNWAY %s FINAL APPROACH" % rw.id, bx + 6, y1, col)
            th.text(surf, "micro", "TIMES ARE", L, y2 + 1, theme.G_DIM)
            th.text(surf, "small", "ESTIMATES UNTIL IT LINES UP", bx + 6, y2, theme.G_MID)
        elif t.phase == trk.GO_AROUND:
            th.text(surf, "micro", "CAME DOWN TO", L, y1 + 1, theme.G_DIM)
            if t.min_alt is not None and t.min_dtg is not None:
                th.text(surf, "smallb", "%d FT, %.1f NM OUT" % (
                    int(round(t.min_alt / 10.0)) * 10, max(0.0, t.min_dtg)), bx + 12, y1, col)
            th.text(surf, "micro", "ATTEMPT", L, y2 + 1, theme.G_DIM)
            th.text(surf, "small", "NUMBER %d COMING UP" % (t.ga_count + 1), bx + 12, y2, theme.G_MID)
        elif t.phase == trk.LANDED:
            th.text(surf, "micro", "ROLL-OUT", L, y1 + 1, theme.G_DIM)
            txt = "%d KT ON RUNWAY %s" % (int(round(t.gs)), rw.id) if t.gs is not None else "RUNWAY %s" % rw.id
            th.text(surf, "smallb", txt, bx + 6, y1, col)
            if ctx.arrivals:
                nxt = ctx.arrivals[0]
                th.text(surf, "micro", "NEXT IN", L, y2 + 1, theme.G_DIM)
                th.text(surf, "small", nxt.label, bx + 6, y2, theme.G_MID)
        self._rule(surf, y0 + 311)

        # the fine print
        facts = self._facts(ctx, t)
        for i, (label, value) in enumerate(facts[:9]):
            fx = L + (i % 3) * 104
            fy = y0 + 316 + (i // 3) * 14
            rr = th.text(surf, "micro", label, fx, fy, theme.G_DIM)
            th.text(surf, "micro", value, rr.right + 6, fy, theme.G_MID if stale else theme.G_BRIGHT)

    def _range_block(self, surf, ctx, t, xa, yl, yv, ys):
        """Used when the approach in use does not fly past your place."""
        th = self.th
        x, y, alt, _hd = t.state_at(ctx.now)
        if alt is None:
            return
        az, _el, slant, _g = ctx.home.look(x, y, alt)
        th.text(surf, "microb", "DISTANCE FROM YOU", xa, yl, theme.C_BRIGHT)
        self._big(surf, xa, yv, "%.1f" % slant if slant < 10 else "%d" % int(round(slant)), theme.C_BRIGHT, "KM",
                  glow=False)
        th.text(surf, "micro", "TO YOUR %s" % geo.compass_words(az), xa, ys, theme.C_MID)

    def _place(self, ctx, t, x, y):
        cached = getattr(t, "ui_place", None)
        if cached is not None and ctx.now - cached[0] < 2.0:
            return cached[1]
        name = ctx.data.place_name(x, y) if ctx.data is not None else ("", "")
        t.ui_place = (ctx.now, name)
        return name

    def _facts(self, ctx, t):
        out = []
        ap = ctx.airport
        if t.mag_heading is not None:
            out.append(("HEADING", "%03d\u00b0M" % (int(round(t.mag_heading)) % 360)))
        elif t.track is not None:
            out.append(("TRACK", "%03d\u00b0M" % (int(round(ap.to_mag(t.track))) % 360)))
        if t.ias is not None:
            out.append(("AIRSPEED", "%d KT" % int(round(t.ias))))
        if t.nav_alt is not None:
            out.append(("SEL ALT", "%d FT" % int(round(t.nav_alt))))
        if t.wd is not None and t.ws is not None:
            out.append(("WIND", "%03d\u00b0/%dKT" % (int(round(t.wd)) % 360, int(round(t.ws)))))
        if t.true_heading is not None and t.track is not None and (t.gs or 0) > 60 and not t.on_ground:
            crab = geo.ang_diff(t.true_heading, t.track)
            if abs(crab) >= 1.0:
                out.append(("CRAB", "%d\u00b0 %s" % (int(round(abs(crab))), "R" if crab > 0 else "L")))
        if t.vrate is not None and (t.gs or 0) > 60 and t.phase in (trk.FINAL, trk.INBOUND) and t.vrate < -100:
            ang = math.degrees(math.atan2(-t.vrate, t.gs * 101.269))
            out.append(("ANGLE", "%.1f\u00b0" % ang))
        if t.squawk:
            out.append(("SQUAWK", t.squawk))
        modes = t.nav_modes or ()
        if "approach" in modes:
            out.append(("MODE", "APPROACH"))
        elif "lnav" in modes or "vnav" in modes:
            out.append(("MODE", "LNAV/VNAV"))
        elif "althold" in modes:
            out.append(("MODE", "ALT HOLD"))
        elif "autopilot" in modes:
            out.append(("MODE", "AUTOPILOT"))
        if t.oat is not None:
            out.append(("OAT", "%d\u00b0C" % int(round(t.oat))))
        if t.src:
            out.append(("SOURCE", t.src))
        out.append(("HEX", t.hex.upper()))
        if t.alt_src:
            out.append(("ALT BY", {"GPS": "GPS", "BARO": "BARO+QNH", "STD": "BARO 1013", "GND": "GROUND"}.get(
                t.alt_src, t.alt_src)))
        return out

    # --- nothing inbound --------------------------------------------------
    def _idle(self, surf, ctx):
        th, r = self.th, self.rect
        L, R = r.x + 9, r.right - 9
        y0 = r.y
        t = ctx.tracker
        fd = ctx.feed
        th.text(surf, "microb", "ARRIVALS", L, y0 + 6, theme.A_MID)
        rows = []
        if fd.last_ok is None:
            th.text(surf, "hero", "CONNECTING", L, y0 + 22, theme.A_BRIGHT, glow=True)
            th.text(surf, "small", "WAITING FOR THE FIRST AIRCRAFT DATA", L, y0 + 60, theme.A_MID)
            if fd.last_error:
                rows.append(("PROBLEM", fd.last_error.upper()[:34]))
                rows.append(("TRYING", "AGAIN AUTOMATICALLY"))
        else:
            th.text(surf, "hero", "NO ARRIVALS", L, y0 + 22, theme.G_BRIGHT, glow=True)
            th.text(surf, "small", "NOTHING INBOUND TO %s RIGHT NOW" % ctx.airport.name, L, y0 + 60,
                    theme.G_MID)
        basis = {"TRAFFIC": "LAST USED BY ARRIVALS", "WIND": "PICKED FROM THE WIND",
                 "DEFAULT": "ASSUMED UNTIL TRAFFIC SHOWS"}.get(t.active_basis, "")
        rows.append(("RUNWAY", "%s  %s" % (t.active_id, basis)))
        if not ctx.home.passes(t.active_id):
            rows.append(("NOTE", "THIS APPROACH DOES NOT PASS YOU"))
        if t.landings:
            last = t.landings[0]
            ago = max(0, int((ctx.wall - last.wall) / 60.0))
            when = "%d MIN AGO" % ago if ago < 120 else clock_text(last.wall, False, ctx.cfg.clock_24h)
            rows.append(("LAST IN", "%s %s  %s" % (last.flight, last.type, when)))
        ds = day_start(ctx.wall, ctx.airport.curfew)
        n_ga = sum(1 for g in t.go_arounds if g.wall >= ds)
        rows.append(("TODAY", "%d LANDED%s" % (landed_today(t.landings, ctx.wall, ctx.airport.curfew),
                                               ", %d GO-AROUND%s" % (n_ga, "" if n_ga == 1 else "S") if n_ga else "")))
        n_all, n_air = t.counts()
        rows.append(("IN RANGE", "%d AIRCRAFT, %d AIRBORNE" % (n_all, n_air)))
        m = ctx.metar
        if m is not None and m.wind_kt is not None:
            txt = m.wind_text()
            if m.wind_dir is not None and m.wind_kt >= 1:
                head, cross = ctx.airport.runways[t.active_id].headwind_crosswind(m.wind_dir, m.wind_kt)
                txt += "  %s %d  CROSS %d" % ("HEAD" if head >= 0 else "TAIL", int(round(abs(head))),
                                              int(round(abs(cross))))
            rows.append(("WIND", txt))
        cf = ctx.airport.curfew
        if cf:
            rows.append(("CURFEW", "%s-%s%s" % (cf.get("start", ""), cf.get("end", ""),
                                                "  IN FORCE NOW" if in_curfew(ctx.wall, cf) else "")))
        yy = y0 + 92
        self._rule(surf, yy - 8)
        for label, value in rows:
            th.text(surf, "micro", label, L, yy + 2, theme.G_DIM)
            role, txt = fit(th, value, ("small", "micro"), R - L - 70)
            th.text(surf, role, txt, L + 70, yy + (0 if role == "small" else 2), theme.G_BRIGHT)
            yy += 21
        self._rule(surf, yy + 2)
        yy += 9
        others = sorted(t.others(), key=lambda o: o.dist_ap)
        room = (r.bottom - 34 - yy) // 14 - 1
        if others and room >= 1:
            th.text(surf, "microb", "ALSO FLYING NEARBY", L, yy, theme.A_MID)
            yy += 15
            for o in others[:room]:
                ox, oy, oalt, _hd = o.state_at(ctx.now)
                if o.phase == trk.DEPARTING:
                    doing = "DEPARTING"
                elif oalt is not None and oalt > 18000:
                    doing = "PASSING OVER"
                elif (o.vrate or 0) > 400:
                    doing = "CLIMBING"
                elif (o.vrate or 0) < -400:
                    doing = "DESCENDING"
                else:
                    doing = "LOCAL"
                th.text(surf, "micro", o.label[:8], L, yy, theme.G_BRIGHT)
                th.text(surf, "micro", o.type or "----", L + 56, yy, theme.G_MID)
                th.text(surf, "micro", doing, L + 90, yy, theme.G_MID)
                if oalt is not None:
                    th.text(surf, "micro", "%d FT" % (int(round(oalt / 100.0)) * 100), L + 212, yy, theme.G_MID, "r")
                th.text(surf, "micro", "%d NM %s" % (int(round(math.hypot(ox, oy))),
                                                    geo.compass16(geo.bearing_xy(ox, oy))), R, yy, theme.G_DIM, "r")
                yy += 14
        yy = r.bottom - 32
        th.text(surf, "micro", "WATCHING %d NM AROUND THE FIELD. THE NEXT ARRIVAL" % ctx.cfg.feed_radius_nm, L,
                yy, theme.G_DIM)
        th.text(surf, "micro", "APPEARS HERE AS SOON AS IT STARTS ITS DESCENT.", L, yy + 13, theme.G_DIM)


def _hm(text, default):
    try:
        h, m = str(text).split(":")
        return int(h) % 24, int(m) % 60
    except (ValueError, AttributeError):
        return default


def day_start(wall, curfew=None):
    """Start of the airport's operating day containing `wall`.

    With a curfew the day runs from the end of one curfew to the end of the next
    (06:00 to 06:00 at Adelaide), so the count does not reset at midnight in the
    middle of the night.  Without one it is midnight.
    """
    h, m = _hm(curfew.get("end"), (0, 0)) if curfew else (0, 0)
    lt = time.localtime(wall)
    start = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, h, m, 0, 0, 0, -1))
    if start > wall:
        start = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday - 1, h, m, 0, 0, 0, -1))
    return start


def in_curfew(wall, curfew):
    if not curfew:
        return False
    lt = time.localtime(wall)
    now = lt.tm_hour * 60 + lt.tm_min
    sh, sm = _hm(curfew.get("start"), (23, 0))
    eh, em = _hm(curfew.get("end"), (6, 0))
    s, e = sh * 60 + sm, eh * 60 + em
    return (now >= s or now < e) if s > e else (s <= now < e)


def landed_today(landings, wall, curfew=None):
    ds = day_start(wall, curfew)
    return sum(1 for l in landings if ds <= l.wall <= wall + 60)


# ----------------------------------------------------------------------------
class SkyPanel(object):
    """Where to look from your window: compass bearing across, height above the horizon up."""

    def __init__(self, rect, th, home, ref):
        self.rect = pygame.Rect(rect)
        self.th = th
        self.home = home
        self.ref = ref
        self._geom = {}
        self._static = {}

    def _geometry(self, rw):
        g = self._geom.get(rw.id)
        if g is None:
            path = self.home.sky_path[rw.id]
            if self.home.passes(rw.id):
                d_ab = self.home.abeam[rw.id][0]
                px, py = rw.point_at(d_ab)
            else:
                px, py = rw.point_at(3.0)
            centre = geo.bearing_xy(px - self.home.x, py - self.home.y)
            rel = [abs(geo.ang_diff(az, centre)) for d, az, el in path if d <= 14.0]
            half = min(175.0, max(55.0, max(rel) + 14.0))
            el_max = min(60.0, max(8.0, 1.75 * max(el for _d, _az, el in path)))
            g = (centre, half, el_max)
            self._geom[rw.id] = g
        return g

    def _scales(self, rw):
        centre, half, el_max = self._geometry(rw)
        w, h = self.rect.size
        x0, x1 = 8, w - 8
        y_h, y_t = h - 15, 19

        def X(az):
            return (x0 + x1) / 2.0 + geo.ang_diff(az, centre) / half * (x1 - x0) / 2.0

        def Y(el):
            return y_h - max(-1.0, el) / el_max * (y_h - y_t)
        return X, Y, (x0, x1, y_h, y_t), half, centre, el_max

    def _render_static(self, rw):
        th = self.th
        w, h = self.rect.size
        s = pygame.Surface((w, h))
        s.fill(theme.BG)
        X, Y, (x0, x1, y_h, y_t), half, centre, el_max = self._scales(rw)
        # height lines
        for el in (5, 10, 20, 40):
            if el < el_max * 0.95 and el >= el_max * 0.3:
                gfx.dotted(s, theme.C_FAINT, (x0 + 22, Y(el)), (x1, Y(el)), 4.0)
                th.text(s, "micro", "%d\u00b0" % el, x0, Y(el) - 6, theme.C_DIM)
        # horizon and compass
        pygame.draw.line(s, theme.C_DIM, (x0, y_h), (x1, y_h))
        names = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
        for az in range(0, 360, 15):
            if abs(geo.ang_diff(az, centre)) > half:
                continue
            x = int(X(az))
            if az % 45 == 0:
                pygame.draw.line(s, theme.C_MID, (x, y_h), (x, y_h + 3))
                th.text(s, "micro", names[az // 45], x, y_h + 3, theme.C_MID, "c")
            else:
                pygame.draw.line(s, theme.C_DIM, (x, y_h), (x, y_h + 1))
        # the path a perfect approach traces across your sky
        pts = [(X(az), Y(el)) for d, az, el in self.home.sky_path[rw.id]
               if abs(geo.ang_diff(az, centre)) <= half and el > -0.8]
        for i in range(len(pts) - 1):
            gfx.line(s, theme.A_DIM, pts[i], pts[i + 1])
        used = []
        for d, az, el in self.home.sky_path[rw.id]:
            if abs(d - round(d)) < 1e-6 and int(round(d)) in (0, 5, 10) and abs(geo.ang_diff(az, centre)) <= half:
                x, y = X(az), Y(el)
                label = "RWY " + rw.id if d < 0.5 else "%d NM" % int(round(d))
                role = "microb" if d < 0.5 else "micro"
                tw = th.width(role, label)
                lx = min(max(x, x0 + 24 + tw / 2.0), x1 - tw / 2.0)
                box = pygame.Rect(int(lx - tw / 2.0), int(y + 4 if d >= 0.5 else y - 15), tw, th.ch(role))
                if box.inflate(8, 2).collidelist(used) != -1:
                    continue
                used.append(box)
                gfx.circle(s, theme.A_MID, (x, y), 2)
                th.text(s, role, label, box.x, box.y, theme.A_BRIGHT if d < 0.5 else theme.A_MID)
        return s

    def draw(self, surf, ctx):
        th, r, now = self.th, self.rect, ctx.now
        rw = ctx.rw
        static = self._static.get(rw.id)
        if static is None:
            static = self._render_static(rw)
            self._static[rw.id] = static
        surf.blit(static, r.topleft)
        X, Y, (x0, x1, y_h, y_t), half, centre, el_max = self._scales(rw)
        feat = ctx.feat
        prev_clip = surf.get_clip()
        surf.set_clip(r)
        for t in ctx.arrivals:
            if t is feat or t.phase != trk.FINAL:
                continue
            x, y, alt, _hd = t.state_at(now)
            if alt is None:
                continue
            az, el, slant, _g = self.home.look(x, y, alt)
            if abs(geo.ang_diff(az, centre)) <= half and el > 0:
                surf.fill(theme.G_MID, (int(r.x + X(az)) - 1, int(r.y + Y(el)) - 1, 3, 3))
        caption, cap_col = "FROM YOUR WINDOW", theme.C_MID
        readout, read_col = "", theme.C_MID
        look_now = False
        if feat is not None and feat.phase in trk.ARRIVAL_PHASES:
            x, y, alt, _hd = feat.state_at(now)
            if alt is not None:
                az, el, slant, _g = self.home.look(x, y, alt)
                rel = geo.ang_diff(az, centre)
                in_sight = slant <= 15.0 and el >= 0.4
                look_now = feat.phase == trk.FINAL and slant <= 5.0 and el >= 1.8
                px, py = r.x + X(az), r.y + Y(el)
                if abs(rel) <= half:
                    col = theme.G_HOT if in_sight else theme.G_MID
                    gfx.diamond(surf, col, (px, py), 4)
                    if in_sight:
                        b = 8
                        for sx, sy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
                            gfx.line(surf, theme.G_BRIGHT, (px + sx * b, py + sy * b), (px + sx * (b - 3), py + sy * b))
                            gfx.line(surf, theme.G_BRIGHT, (px + sx * b, py + sy * b), (px + sx * b, py + sy * (b - 3)))
                else:
                    edge = r.right - 14 if rel > 0 else r.x + 8
                    th.text(surf, "micro", "\u25ba" if rel > 0 else "\u25c4", edge, r.y + y_h - 14, theme.G_MID)
                if in_sight:
                    readout = "%s %03d\u00b0   %.1f\u00b0 UP   %.1f KM" % (geo.compass16(az), int(round(az)) % 360,
                                                                        max(0.0, el), slant)
                    dims = self.ref.dimensions(feat.type)
                    if dims and look_now:
                        moons = math.degrees(math.atan2(dims[1], slant * 1000.0)) / 0.52
                        readout += "   %.1f\u00d7 MOON" % moons
                    read_col = theme.C_BRIGHT
                else:
                    why = "TOO LOW TO SEE" if slant <= 15.0 else "NOT IN SIGHT YET"
                    readout = "%s   %d KM %s" % (why, int(round(slant)), geo.compass16(az))
                    read_col = theme.C_DIM
        else:
            readout, read_col = "NOTHING TO WATCH", theme.C_DIM
        surf.set_clip(prev_clip)
        if look_now:
            on = int(now * 2) % 2 == 0
            box = pygame.Rect(r.x + 6, r.y + 3, th.width("microb", "LOOK NOW") + 10, th.ch("microb") + 2)
            pygame.draw.rect(surf, theme.C_BRIGHT if on else theme.C_DIM, box)
            th.text(surf, "microb", "LOOK NOW", box.x + 5, box.y + 1, theme.BG)
            cap_right = box.right
        else:
            cap_right = th.text(surf, "microb", caption, r.x + 7, r.y + 4, cap_col).right
        while readout and th.width("micro", readout) > r.right - 8 - cap_right - 10 and "   " in readout:
            readout = readout.rsplit("   ", 1)[0]
        if readout:
            th.text(surf, "micro", readout, r.right - 8, r.y + 4, read_col, "r")
        gfx.frame(surf, r, theme.C_FAINT, theme.C_DIM)


# ----------------------------------------------------------------------------
class QueuePanel(object):
    ROWS = 4

    def __init__(self, rect, th):
        self.rect = pygame.Rect(rect)
        self.th = th
        self.hits = []      # (rect, hex) for mouse selection

    def draw(self, surf, ctx):
        th, r, now = self.th, self.rect, ctx.now
        surf.fill(theme.BG, r)
        x0 = r.x + 8
        c_type, c_from = x0 + 70, x0 + 102
        c_togo, c_alt, c_eta = x0 + 176, x0 + 216, r.right - 8
        y = r.y + 4
        n = len(ctx.arrivals)
        th.text(surf, "microb", "QUEUE" if n <= self.ROWS else "QUEUE %d" % n, x0, y, theme.A_MID)
        th.text(surf, "micro", "TYPE", c_type, y, theme.A_DIM)
        th.text(surf, "micro", "FROM", c_from, y, theme.A_DIM)
        th.text(surf, "micro", "TO RUN", c_togo, y, theme.A_DIM, "r")
        th.text(surf, "micro", "ALT", c_alt, y, theme.A_DIM, "r")
        th.text(surf, "micro", "LANDS", c_eta, y, theme.A_DIM, "r")
        self.hits = []
        if not ctx.arrivals:
            th.text(surf, "micro", "NOTHING INBOUND", x0 + 12, y + 16, theme.G_DIM)
        for i, t in enumerate(ctx.arrivals[:self.ROWS]):
            ry = r.y + 19 + i * 14
            is_feat = t is ctx.feat
            if t.phase == trk.GO_AROUND:
                col = theme.R_BRIGHT
            elif is_feat:
                col = theme.G_HOT
            elif t.phase == trk.FINAL:
                col = theme.G_BRIGHT
            else:
                col = theme.G_MID
            if is_feat:
                th.text(surf, "micro", "\u25ba", x0, ry, col)
            th.text(surf, "microb" if is_feat else "micro", t.label, x0 + 12, ry, col)
            th.text(surf, "micro", t.type or "----", c_type, ry, col)
            route = ctx.routes.get(t.flight) if ctx.routes is not None else None
            if route is not None:
                th.text(surf, "micro", route.origin_iata or route.origin_icao, c_from, ry, col)
            _x, _y, alt, _hd = t.state_at(now)
            if t.phase == trk.FINAL and t.dtg is not None:
                dtg, _ = t.rwy.along_cross(_x, _y)
                th.text(surf, "micro", "%.1fNM" % max(0.0, dtg), c_togo, ry, col, "r")
            elif t.track_miles is not None and t.phase == trk.INBOUND:
                th.text(surf, "micro", "~%dNM" % int(round(t.track_miles)), c_togo, ry, col, "r")
            if alt is not None:
                th.text(surf, "micro", "%d" % (int(round(alt / 100.0)) * 100), c_alt, ry, col, "r")
            if t.phase == trk.GO_AROUND:
                th.text(surf, "micro", "GO-ARND", c_eta, ry, col, "r")
            elif t.t_td is not None:
                left = t.t_td - now
                txt = mmss(left) if t.phase == trk.FINAL else "~%dMIN" % max(1, int(round(left / 60.0)))
                th.text(surf, "micro", txt, c_eta, ry, col, "r")
            self.hits.append((pygame.Rect(r.x + 2, ry - 1, r.w - 4, 14), t.hex))
        gfx.frame(surf, r)

    def hit(self, pos):
        for rect, hexid in self.hits:
            if rect.collidepoint(pos):
                return hexid
        return None


# ----------------------------------------------------------------------------
class LandedPanel(object):
    """Go-around counter for the day, with the flight numbers (to look up later)."""
    ROWS = 4

    def __init__(self, rect, th):
        self.rect = pygame.Rect(rect)
        self.th = th

    def draw(self, surf, ctx):
        th, r = self.th, self.rect
        surf.fill(theme.BG, r)
        x0 = r.x + 8
        t = ctx.tracker
        curfew = ctx.airport.curfew
        ds = day_start(ctx.wall, curfew)
        gas = [g for g in t.go_arounds if g.wall >= ds]
        n = len(gas)
        th.text(surf, "microb", "GO-AROUNDS", x0, r.y + 4, theme.R_BRIGHT if n else theme.A_MID)
        th.text(surf, "micro", "LANDED %d" % landed_today(t.landings, ctx.wall, curfew), r.right - 8, r.y + 4,
                theme.A_MID, "r")
        th.text(surf, "hero", "%d" % n, x0, r.y + 21, theme.R_BRIGHT if n else theme.G_MID, glow=bool(n))
        th.text(surf, "micro", "TODAY", x0, r.y + 56, theme.G_DIM)
        lx = x0 + 40
        if not gas:
            th.text(surf, "micro", "NONE TODAY", lx, r.y + 22, theme.G_MID)
            if t.landings:
                last = t.landings[0]
                th.text(surf, "micro", "LAST LANDED", lx, r.y + 42, theme.G_DIM)
                th.text(surf, "micro", "%s %s" % (clock_text(last.wall, False, ctx.cfg.clock_24h), last.flight[:8]),
                        lx, r.y + 56, theme.G_MID)
        else:
            show = gas[:self.ROWS] if n <= self.ROWS else gas[:self.ROWS - 1]
            for i, g in enumerate(show):
                ry = r.y + 19 + i * 14
                fresh = ctx.wall - g.wall < 600.0
                col = theme.R_BRIGHT if fresh else theme.G_BRIGHT
                th.text(surf, "micro", clock_text(g.wall, False, ctx.cfg.clock_24h), lx, ry, col)
                th.text(surf, "microb", g.flight[:8], lx + 36, ry, col)
                th.text(surf, "micro", g.rwy, r.right - 8, ry, theme.G_MID, "r")
            if n > self.ROWS:
                th.text(surf, "micro", "+%d MORE  (R: REVIEW)" % (n - len(show)), lx, r.y + 19 + 3 * 14,
                        theme.G_MID)
        gfx.frame(surf, r)


# ----------------------------------------------------------------------------
class StatusBar(object):
    def __init__(self, rect, th):
        self.rect = pygame.Rect(rect)
        self.th = th

    def draw(self, surf, ctx):
        th, r = self.th, self.rect
        fd = ctx.feed
        y = r.y + 1
        age = fd.age()
        n_all, _n_air = ctx.tracker.counts()
        if ctx.demo:
            dot, col = theme.A_BRIGHT, theme.A_MID
            text = "DEMO   SIMULATED TRAFFIC, NOT REAL AIRCRAFT"
        elif fd.last_ok is None:
            dot, col = theme.A_BRIGHT, theme.A_MID
            text = "CONNECTING TO %s" % fd.label
            if fd.last_error:
                text += "   %s   RETRYING" % fd.last_error.upper()[:40]
        elif fd.state == "LIVE" and age < 20.0:
            dot, col = theme.G_BRIGHT, theme.G_MID
            text = "LIVE   %s   UPDATED %dS AGO   %d AIRCRAFT WITHIN %d NM" % (
                fd.label, int(age), n_all, ctx.cfg.feed_radius_nm)
        else:
            dot = theme.R_BRIGHT if age > 45.0 else theme.A_BRIGHT
            col = theme.A_MID
            text = "NO NEW DATA FOR %s   %s   RETRYING" % (mmss(age), (fd.last_error or "waiting").upper()[:40])
        blink = dot is not theme.G_BRIGHT and int(ctx.now * 2) % 2 == 0
        gfx.circle(surf, theme.dim(dot, 0.35) if blink else dot, (r.x + 11, r.y + 7), 3)
        th.text(surf, "micro", text, r.x + 20, y, col)
        if ctx.now - ctx.started < 25.0:
            right = "Z ZOOM   TAB NEXT FLIGHT   R REVIEW   %sQ QUIT" % (
                "A ATC AUDIO   " if ctx.audio is not None else "")
        else:
            right = ""
            if ctx.audio is not None and ctx.audio.is_playing():
                right = "ATC AUDIO ON"
            if ctx.notice and ctx.now < ctx.notice_until:
                right = ctx.notice
        if right and th.width("micro", right) < r.right - 8 - (r.x + 20 + th.width("micro", text)) - 12:
            th.text(surf, "micro", right, r.right - 8, y, theme.G_DIM, "r")


# ----------------------------------------------------------------------------
class WeatherPanel(object):
    """Airport weather, large enough to read from across the room."""

    def __init__(self, rect, th):
        self.rect = pygame.Rect(rect)
        self.th = th

    def hit(self, pos):
        return None

    def draw(self, surf, ctx):
        th, r = self.th, self.rect
        surf.fill(theme.BG, r)
        x0, R = r.x + 8, r.right - 8
        th.text(surf, "microb", "WEATHER AT %s" % ctx.airport.name, x0, r.y + 4, theme.A_MID)
        m = ctx.metar
        if m is None:
            if ctx.tracker.qnh is not None:
                th.text(surf, "big", "QNH %d" % int(round(ctx.tracker.qnh)), x0, r.y + 20, theme.A_BRIGHT)
                th.text(surf, "small", "FROM AIRCRAFT; NO WEATHER REPORT YET", x0, r.y + 46, theme.A_MID)
            else:
                th.text(surf, "small", "WAITING FOR THE WEATHER REPORT", x0, r.y + 26, theme.A_MID)
            gfx.frame(surf, r)
            return
        age = m.age_min(ctx.wall)
        if age is not None:
            th.text(surf, "micro", "%d MIN AGO" % int(age) if age < 100 else "%d H OLD" % int(age // 60), R, r.y + 4,
                    theme.A_MID, "r")
        y = r.y + 18
        wind = m.wind_text() or "NO WIND DATA"
        if m.wind_dir is not None and (m.wind_kt or 0) >= 1:
            wind = "%s %03d\u00b0 %dKT" % (geo.compass16(m.wind_dir), int(round(m.wind_dir)) % 360 or 360,
                                          int(round(m.wind_kt)))
            if m.gust_kt:
                wind += " G%d" % int(round(m.gust_kt))
        th.text(surf, "big", wind, x0, y, theme.A_BRIGHT, glow=True)
        if m.temp is not None:
            th.text(surf, "big", "%d\u00b0C" % int(round(m.temp)), R, y, theme.A_BRIGHT, "r")
        y += 23
        bits = []
        if m.qnh is not None:
            bits.append("QNH %d" % int(round(m.qnh)))
        if m.sky:
            bits.append(m.sky)
        role, txt = fit(th, "   ".join(bits), ("med", "small", "micro"), R - x0)
        th.text(surf, role, txt, x0, y, theme.A_BRIGHT)
        y += 18
        if m.wind_dir is not None and (m.wind_kt or 0) >= 1:
            rw = ctx.rw
            head, cross = rw.headwind_crosswind(m.wind_dir, m.wind_kt)
            txt = "RWY %s  %s %d  CROSSWIND %d" % (rw.id, "HEADWIND" if head >= 0 else "TAILWIND",
                                                  int(round(abs(head))), int(round(abs(cross))))
            th.text(surf, "small", txt, x0, y, theme.A_MID)
        gfx.frame(surf, r)
