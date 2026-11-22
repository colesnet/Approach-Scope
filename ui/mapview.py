"""Plan view: the coast, the runway, the final approach and everything flying.

The camera frames itself around the action.  There are three views and the
map eases between them:

    TERMINAL   about 25 NM around the field - an arrival still finding the approach
    APPROACH   the runway, the last 15 NM of the final, and your place
    SHORT      the last 6 NM, zoomed in on your neighbourhood
"""
import json
import math

import pygame

import geo
import tracker as trk
from . import gfx, theme

VIEWS = ("TERMINAL", "APPROACH", "SHORT")
VIEW_TITLE = {"TERMINAL": "TERMINAL AREA", "APPROACH": "FINAL APPROACH", "SHORT": "SHORT FINAL"}
PROFILE_SPAN = {"TERMINAL": 16.0, "APPROACH": 16.0, "SHORT": 6.4}   # NM shown by the side view
ANIM_S = 1.5
DWELL_S = 6.0

COAST = (44, 190, 104)
SUBURB_LINE = (13, 52, 30)
LABEL_LAND = (34, 140, 76)
LABEL_SEA = (34, 116, 140)
FEATHER = (24, 16, 3)


class MapData(object):
    """Coastline, suburb shapes and labels, converted once into local NM coordinates."""

    def __init__(self, airport):
        self.land = []        # (outer, holes, bbox)
        self.outlines = {}    # name -> ring
        self.labels = []      # dict(name, x, y, lo, hi, sea)
        self.suburbs = []     # (name, ring, bbox)
        self.water = []       # lakes: (outer, bbox)
        self.coast = []       # shorelines as polylines (generated maps); None = outline the land polygons
        self.warnings = []
        self.sea_name = airport.sea_name
        fr = airport.frame

        def ring(pts):
            return [fr.to_xy(lat, lon) for lon, lat in pts]

        def bbox(pts):
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            return (min(xs), min(ys), max(xs), max(ys))

        path = airport.data_path("map")
        try:
            if not path:
                raise ValueError("no map file is listed for this airport")
            with open(path, "r", encoding="utf-8") as fh:
                d = json.load(fh)
            for poly in d.get("land", []):
                outer = ring(poly["outer"])
                if len(outer) >= 3:
                    self.land.append((outer, [ring(h) for h in poly.get("holes", []) if len(h) >= 3], bbox(outer)))
            for poly in d.get("water", []):
                outer = ring(poly["outer"])
                if len(outer) >= 3:
                    self.water.append((outer, bbox(outer)))
            if "coast" in d:
                self.coast = [(r, bbox(r)) for r in (ring(c) for c in d["coast"]) if len(r) >= 2]
            else:
                self.coast = None
            for name, pts in d.get("outlines", {}).items():
                if len(pts) >= 3:
                    self.outlines[name] = ring(pts)
            for lb in d.get("labels", []):
                x, y = fr.to_xy(float(lb["lat"]), float(lb["lon"]))
                self.labels.append({"name": str(lb["name"]), "x": x, "y": y, "lo": float(lb.get("lo", 0)),
                                    "hi": float(lb.get("hi", 999)), "sea": bool(lb.get("sea"))})
        except Exception as exc:
            self.warnings.append("map outline not loaded (%s) - the map will be drawn without a coastline" % exc)
        path = airport.data_path("localities")
        try:
            if not path:
                return                      # most airports: towns come from the labels instead
            with open(path, "r", encoding="utf-8") as fh:
                d = json.load(fh)
            for name, pts in d.get("localities", []):
                if len(pts) >= 3:
                    r = ring(pts)
                    self.suburbs.append((str(name), r, bbox(r)))
        except Exception as exc:
            self.warnings.append("suburb shapes not loaded (%s) - 'OVER <suburb>' will be blank" % exc)

    def on_land(self, x, y):
        for outer, (x0, y0, x1, y1) in self.water:
            if x0 <= x <= x1 and y0 <= y <= y1 and geo.point_in_ring(x, y, outer):
                return False
        for outer, holes, (x0, y0, x1, y1) in self.land:
            if x0 <= x <= x1 and y0 <= y <= y1 and geo.point_in_ring(x, y, outer):
                for h in holes:
                    if geo.point_in_ring(x, y, h):
                        return False
                return True
        return False

    def place_name(self, x, y):
        """("OVER", suburb) / ("NEAR", town) / ("OVER", water) / ("", "")."""
        for name, ring, (x0, y0, x1, y1) in self.suburbs:
            if x0 <= x <= x1 and y0 <= y <= y1 and geo.point_in_ring(x, y, ring):
                return "OVER", name
        if self.land and not self.on_land(x, y):
            if self.coast is not None:
                return "OVER", "WATER"
            if self.sea_name and x <= 2.0 and -38.0 < y < 45.0:
                return "OVER", self.sea_name
            return "OVER", "THE SEA"
        if not self.suburbs and self.labels:
            best = min(self.labels, key=lambda lb: (lb["x"] - x) ** 2 + (lb["y"] - y) ** 2)
            if (best["x"] - x) ** 2 + (best["y"] - y) ** 2 <= 8.0 ** 2:
                return "NEAR", best["name"]
        return "", ""


def _ease(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


class MapView(object):
    def __init__(self, rect, th, data, airport, home, ref, bank, show_others=True):
        self.rect = pygame.Rect(rect)
        self.th = th
        self.data = data
        self.airport = airport
        self.home = home
        self.ref = ref
        self.bank = bank
        self.show_others = show_others
        self.view = "APPROACH"
        self.rw_id = None
        self.locked = None
        self._cam = None
        self._cam_from = None
        self._cam_to = None
        self._t_anim = -1e9
        self._t_switch = -1e9
        self._prev_view = "APPROACH"
        self._static = {}
        self.instant = False          # set on slow machines: camera changes snap instead of gliding
        self.span_nm = PROFILE_SPAN["APPROACH"]

    # ------------------------------------------------------------------
    # camera
    # ------------------------------------------------------------------
    def camera_for(self, view, rw):
        w, h = self.rect.size
        if view == "TERMINAL":
            cx, cy = rw.point_at(4.0)
            return (cx, cy, h / 50.0)
        far, margin = (15.7, 1.3) if view == "APPROACH" else (6.3, 0.75)
        pts = [(rw.far_x, rw.far_y), (rw.tx, rw.ty), (self.home.x, self.home.y), rw.point_at(far)]
        x0 = min(p[0] for p in pts) - margin
        x1 = max(p[0] for p in pts) + margin
        y0 = min(p[1] for p in pts) - margin
        y1 = max(p[1] for p in pts) + margin
        k = min(w / (x1 - x0), (h - 14) / (y1 - y0))
        return ((x0 + x1) / 2.0, (y0 + y1) / 2.0 + 5.0 / k, k)

    def _inside(self, cam, x, y, margin_px):
        cx, cy, k = cam
        w, h = self.rect.size
        return abs((x - cx) * k) < w / 2.0 - margin_px and abs((y - cy) * k) < h / 2.0 - margin_px

    def _choose(self, feat, rw, now):
        if self.locked:
            return self.locked
        cur = self.view
        if feat is None:
            return "APPROACH"
        x, y, _alt, _trk = feat.state_at(now)
        approach = self.camera_for("APPROACH", rw)
        if feat.phase == trk.LANDED:
            return cur if cur != "TERMINAL" else "APPROACH"
        if feat.phase == trk.FINAL and feat.dtg is not None and feat.rwy is rw:
            if feat.dtg <= 5.4:
                return "SHORT"
            if cur == "SHORT" and feat.dtg < 6.2:
                return "SHORT"
        inside = self._inside(approach, x, y, 22 if cur != "TERMINAL" else 46)
        return "APPROACH" if inside else "TERMINAL"

    def cycle_lock(self):
        """Z key / tap: AUTO -> TERMINAL -> APPROACH -> SHORT -> AUTO."""
        order = [None] + list(VIEWS)
        self.locked = order[(order.index(self.locked) + 1) % len(order)]
        self._t_switch = -1e9
        return self.locked or "AUTO"

    def update(self, feat, rw, now):
        want = self._choose(feat, rw, now)
        first = self._cam is None
        changed = want != self.view or rw.id != self.rw_id
        if first or (changed and (now - self._t_switch >= DWELL_S or rw.id != self.rw_id or self.locked)):
            target = self.camera_for(want, rw)
            self._prev_view = self.view
            if first or self.instant:
                self._cam = target
                self._prev_view = want
                self._t_anim = -1e9
            else:
                self._cam_from = self._cam
                self._t_anim = now
            self._cam_to = target
            self.view, self.rw_id = want, rw.id
            self._t_switch = now
        t = (now - self._t_anim) / ANIM_S
        if t < 1.0 and self._cam_from is not None:
            e = _ease(t)
            a, b = self._cam_from, self._cam_to
            k = math.exp(math.log(a[2]) + (math.log(b[2]) - math.log(a[2])) * e)
            self._cam = (a[0] + (b[0] - a[0]) * e, a[1] + (b[1] - a[1]) * e, k)
            self.span_nm = PROFILE_SPAN[self._prev_view] + (PROFILE_SPAN[self.view] - PROFILE_SPAN[self._prev_view]) * e
            return True
        self._cam = self._cam_to
        self.span_nm = PROFILE_SPAN[self.view]
        return False

    # ------------------------------------------------------------------
    # static layer
    # ------------------------------------------------------------------
    def _render_static(self, cam, rw, title, fast=False):
        th = self.th
        w, h = self.rect.size
        s = pygame.Surface((w, h))
        s.fill(theme.BG)
        cx, cy, k = cam
        hw, hh = w / 2.0, h / 2.0

        def P(x, y):
            return (hw + (x - cx) * k, hh - (y - cy) * k)
        vx0, vx1, vy0, vy1 = cx - hw / k, cx + hw / k, cy - hh / k, cy + hh / k

        def off(b):
            return b[2] < vx0 or b[0] > vx1 or b[3] < vy0 or b[1] > vy1

        # --- land and coast ---------------------------------------------
        for outer, holes, bb in self.data.land:
            if off(bb):
                continue
            pts = [P(x, y) for x, y in outer]
            pygame.draw.polygon(s, theme.LAND, pts)
            hole_pts = [[P(x, y) for x, y in hl] for hl in holes]
            for hp in hole_pts:
                pygame.draw.polygon(s, theme.BG, hp)
            if self.data.coast is None:
                pygame.draw.aalines(s, COAST, True, pts)
                for hp in hole_pts:
                    pygame.draw.aalines(s, COAST, True, hp)
        for outer, bb in self.data.water:
            if not off(bb):
                pygame.draw.polygon(s, theme.BG, [P(x, y) for x, y in outer])
        for line, bb in (self.data.coast or []):
            if not off(bb):
                pygame.draw.aalines(s, COAST, False, [P(x, y) for x, y in line])
        if k >= 30.0 and not fast:
            for _name, ring, bb in self.data.suburbs:
                if not off(bb):
                    pygame.draw.lines(s, SUBURB_LINE, True, [P(x, y) for x, y in ring], 1)
        if k >= 9.0:
            for ring in self.data.outlines.values():
                pygame.draw.aalines(s, theme.G_FAINT, True, [P(x, y) for x, y in ring])

        # --- the approach: a faint beam, the centreline and distance ticks ----
        reach = 16.0
        thr = P(rw.tx, rw.ty)
        beam = pygame.Surface((w, h))
        beam.fill(theme.BLACK)
        half = math.tan(math.radians(2.2)) * reach
        pygame.draw.polygon(beam, FEATHER, [thr, P(*rw.point_at(reach, half)), P(*rw.point_at(reach, -half))])
        s.blit(beam, (0, 0), special_flags=pygame.BLEND_RGB_ADD)
        gfx.dashed(s, theme.A_DIM, thr, P(*rw.point_at(reach)), 5.0, 4.0)
        nx, ny = rw.rx, -rw.ry          # screen direction of "pilot's right"
        marks = []                      # label boxes that place names must keep clear of
        every = 1 if k >= 14.0 else 5
        label_every = 1 if k >= 34.0 else 5
        for d in range(1, int(reach) + 1):
            if d % every:
                continue
            px, py = P(*rw.point_at(d))
            major = d % 5 == 0
            t = 5.0 if major else 3.0
            gfx.line(s, theme.A_MID if major else theme.A_DIM, (px - nx * t, py - ny * t), (px + nx * t, py + ny * t))
            if d % label_every == 0 and k >= 5.0:
                lx, ly = px - nx * 13.0, py - ny * 13.0
                marks.append(th.text(s, "micro", "%d" % d, lx, ly - th.ch("micro") / 2.0, theme.A_MID, "c"))
        if k >= 9.0:
            for fx in rw.fixes:
                if fx.dtg > reach + 0.5:
                    continue
                px, py = P(fx.x, fx.y)
                gfx.polyline(s, theme.A_MID, [(px, py - 4), (px + 4, py + 3), (px - 4, py + 3)], 1, closed=True)
                lx, ly = px + nx * 11.0, py + ny * 11.0
                marks.append(th.text(s, "micro", fx.name, lx, ly - th.ch("micro") / 2.0, theme.A_MID,
                                     "l" if nx >= 0 else "r"))

        # --- runways -----------------------------------------------------
        rwidth = 3 if k >= 18.0 else 2
        for a, b in self.airport.strips:
            gfx.line(s, theme.A_MID, P(*a), P(*b), rwidth)
        gfx.line(s, theme.A_BRIGHT, P(rw.start_x, rw.start_y), P(rw.far_x, rw.far_y), rwidth)
        if k >= 18.0:
            gfx.line(s, theme.A_BRIGHT, (thr[0] - nx * 5, thr[1] - ny * 5), (thr[0] + nx * 5, thr[1] + ny * 5), 2)
        taken = [m.inflate(6, 4) for m in marks]
        taken.append(pygame.Rect(0, 0, th.width("microb", title) + 16, 20))      # the caption
        taken.append(pygame.Rect(w - 26, 0, 26, 32))                            # the north arrow
        if k >= 30.0:
            for other in self.airport.runways.values():
                ox, oy = P(other.tx, other.ty)
                lx, ly = ox - other.ux * 11.0, oy + other.uy * 11.0
                tw, tht = th.width("microb", other.id), th.ch("microb")
                box = pygame.Rect(int(lx - tw / 2), int(ly - tht / 2), tw, tht)
                if box.inflate(4, 2).collidelist(taken) != -1:
                    continue                      # busy airports: skip a label rather than overprint
                th.text(s, "microb", other.id, box.x, box.y, theme.A_BRIGHT if other is rw else theme.A_MID)
                taken.append(box.inflate(4, 2))
        if k >= 5.0:
            for nb in self.airport.nearby:
                for a, b in nb["strips"]:
                    gfx.line(s, theme.A_DIM, P(*a), P(*b), 1)
                px, py = P(nb["x"], nb["y"])
                if 0 <= px < w - 30 and 12 <= py < h and k >= 9.0:
                    taken.append(th.text(s, "micro", nb["icao"], px + 9, py - 14, theme.A_DIM).inflate(8, 6))
                    taken.append(pygame.Rect(int(px) - 10, int(py) - 10, 20, 20))

        # --- your place --------------------------------------------------
        hx, hy = P(self.home.x, self.home.y)
        if self.home.passes(rw.id):
            dtg_ab, xtk_ab = self.home.abeam[rw.id]
            fx, fy = P(*rw.point_at(dtg_ab))
            gfx.dotted(s, theme.C_DIM, (hx, hy), (fx, fy), 3.0)
            gfx.line(s, theme.C_MID, (fx - nx * 4, fy - ny * 4), (fx + nx * 4, fy + ny * 4))
            if k >= 30.0:
                mx, my = (hx + fx) / 2.0, (hy + fy) / 2.0
                taken.append(th.text(s, "micro", "%.1f KM" % (abs(xtk_ab) * 1.852), mx + 6, my - 2, theme.C_MID))
        gfx.poly(s, theme.C_BRIGHT, [(hx, hy - 6), (hx + 6, hy), (hx + 4, hy), (hx + 4, hy + 5),
                                     (hx - 4, hy + 5), (hx - 4, hy), (hx - 6, hy)])
        s.fill(theme.BG, (int(hx) - 1, int(hy) + 1, 2, 4))
        name = self.home.name or "HOME"
        tw = th.width("microb", name)
        to_left = hx + 16 + tw > w - 4
        taken.append(pygame.Rect(int(hx) - 14, int(hy) - 14, 28, 28))
        taken.append(th.text(s, "microb", name, hx - 16 if to_left else hx + 16, hy - 5, theme.C_BRIGHT,
                             "r" if to_left else "l").inflate(4, 2))

        # --- furniture: caption, scale bar, north --------------------------
        cap = th.text(s, "microb", title, 7, 5, theme.A_MID)
        taken.append(cap.inflate(8, 4))
        for nice in (20.0, 10.0, 5.0, 2.0, 1.0, 0.5):
            if nice * k <= 96:
                break
        bx, by = 8, h - 9
        bl = nice * k
        gfx.line(s, theme.A_MID, (bx, by), (bx + bl, by))
        gfx.line(s, theme.A_MID, (bx, by - 3), (bx, by + 1))
        gfx.line(s, theme.A_MID, (bx + bl, by - 3), (bx + bl, by + 1))
        txt = ("%g NM" % nice)
        taken.append(th.text(s, "micro", txt, bx + bl + 6, by - 9, theme.A_MID).union(
            pygame.Rect(bx, by - 6, int(bl) + 4, 10)))
        ncx = w - 14
        gfx.poly(s, theme.A_MID, [(ncx, 6), (ncx + 4, 15), (ncx, 12), (ncx - 4, 15)])
        taken.append(th.text(s, "micro", "N", ncx, 16, theme.A_MID, "c").union(pygame.Rect(ncx - 6, 4, 12, 24)))

        # --- place names (quiet; they never fight the aircraft tags) --------
        if not fast:
            if k >= 12.0 and "CITY" in self.data.outlines:
                ring = self.data.outlines["CITY"]
                ccx = sum(p[0] for p in ring) / len(ring)
                ccy = sum(p[1] for p in ring) / len(ring)
                self._label(s, "CITY", P(ccx, ccy - (0.55 if k < 30 else 0.75)), LABEL_LAND, taken)
            for lb in self.data.labels:
                if lb["lo"] <= k < lb["hi"]:
                    self._label(s, lb["name"], P(lb["x"], lb["y"]), LABEL_SEA if lb["sea"] else LABEL_LAND, taken)
        return s, taken

    def _label(self, s, text, pos, color, taken):
        th = self.th
        w, h = s.get_size()
        tw, tht = th.width("micro", text), th.ch("micro")
        r = pygame.Rect(int(pos[0] - tw / 2), int(pos[1] - tht / 2), tw, tht)
        if r.left < 3 or r.right > w - 3 or r.top < 3 or r.bottom > h - 3:
            return
        box = r.inflate(10, 6)
        if box.collidelist(taken) != -1:
            return
        th.text(s, "micro", text, r.x, r.y, color)
        taken.append(box)

    # ------------------------------------------------------------------
    # per-frame drawing
    # ------------------------------------------------------------------
    def draw(self, surf, ctx):
        th = self.th
        rw = ctx.rw
        feat = ctx.feat
        now = ctx.now
        animating = self.update(feat, rw, now)
        title = VIEW_TITLE[self.view] + (" (LOCKED)" if self.locked else "")
        if animating:
            static, boxes = self._render_static(self._cam, rw, title, fast=True)
        else:
            key = (self.view, rw.id, title)
            hit = self._static.get(key)
            if hit is None:
                if len(self._static) > 14:
                    self._static.clear()
                hit = self._render_static(self._cam, rw, title)
                self._static[key] = hit
            static, boxes = hit
        r = self.rect
        surf.blit(static, r.topleft)
        prev_clip = surf.get_clip()
        surf.set_clip(r)
        cx, cy, k = self._cam
        ox, oy = r.x + r.w / 2.0, r.y + r.h / 2.0

        def P(x, y):
            return (ox + (x - cx) * k, oy - (y - cy) * k)
        taken = [b.move(r.x, r.y) for b in boxes]
        hx, hy = P(self.home.x, self.home.y)

        # home "ping"
        period = 2.4
        passing = feat is not None and feat.t_abeam is not None and abs(feat.t_abeam - now) < 12.0
        if passing:
            period = 0.9
        ph = (now % period) / period
        gfx.circle(surf, theme.dim(theme.C_BRIGHT, (1.0 - ph) * (0.9 if passing else 0.55)), (hx, hy), 6 + ph * 7, 1)

        tr = ctx.tracker
        others = tr.others() if self.show_others else []
        arrivals = [a for a in ctx.arrivals if a is not feat]
        landed = [t for t in tr.tracks.values() if t.phase == trk.LANDED and t is not feat and t.t_pos is not None]

        # trails first, so icons sit on top of them
        for t in arrivals:
            self._trail(surf, t, P, now, 150.0, theme.G_DIM, theme.G_FAINT)
        if feat is not None:
            self._trail(surf, feat, P, now, 420.0, theme.G_MID, theme.G_DIM, big=True)

        # line of sight from your place to the featured aircraft
        if feat is not None and feat.phase in trk.ARRIVAL_PHASES:
            fx, fy, falt, _ft = feat.state_at(now)
            if falt is not None:
                _az, _el, slant, _gr = self.home.look(fx, fy, falt)
                ax, ay = P(fx, fy)
                if slant <= 12.0 and r.collidepoint(ax, ay) and r.collidepoint(hx, hy):
                    col = theme.C_MID if slant <= 6.0 else theme.C_DIM
                    gfx.dotted(surf, col, (hx, hy), (ax, ay), 4.0)
                    mx, my = (hx + ax) / 2.0, (hy + ay) / 2.0
                    txt = "%.1f KM" % slant
                    box = pygame.Rect(int(mx) + 5, int(my) - 6, th.width("micro", txt), th.ch("micro"))
                    if box.collidelist(taken) == -1 and r.contains(box):
                        th.text(surf, "micro", txt, box.x, box.y, theme.C_BRIGHT)
                        taken.append(box.inflate(4, 2))

        # reserve the icons before any tag is placed
        marks = []
        for t in others + landed + arrivals + ([feat] if feat is not None else []):
            x, y, alt, heading = t.state_at(now)
            px, py = P(x, y)
            if not r.inflate(30, 30).collidepoint(px, py):
                continue
            marks.append((t, px, py, alt, heading))
            taken.append(pygame.Rect(int(px) - 9, int(py) - 9, 18, 18))

        for t, px, py, alt, heading in marks:
            stale = t.is_stale(now)
            shape = self._shape(t)
            if t is feat:
                continue
            if t in arrivals:
                col = theme.G_MID if stale else theme.G_BRIGHT
                spr = self.bank.get(shape, t.wtc, col, heading)
                surf.blit(spr, (px - spr.get_width() / 2.0, py - spr.get_height() / 2.0))
                self._tag(surf, t, px, py, [(t.label, "microb", col), (self._alt_speed(t, alt), "micro", theme.G_MID)],
                          taken, r)
            elif t.phase == trk.LANDED:
                if k >= 18.0:
                    spr = self.bank.get(shape, t.wtc, theme.G_DIM, heading)
                    surf.blit(spr, (px - spr.get_width() / 2.0, py - spr.get_height() / 2.0))
            else:
                col = theme.G_FAINT if stale else theme.G_DIM
                spr = self.bank.get(shape, t.wtc, col, heading)
                surf.blit(spr, (px - spr.get_width() / 2.0, py - spr.get_height() / 2.0))
                if k >= 5.0 and not stale:
                    mark = "\u2191" if t.phase == trk.DEPARTING else ""
                    lvl = "" if alt is None else " %03d" % max(0, int(round(alt / 100.0)))
                    self._tag(surf, t, px, py, [(t.label + mark + lvl, "micro", theme.G_DIM)], taken, r,
                              leader=False, must_fit=True)

        for t, px, py, alt, heading in marks:
            if t is not feat:
                continue
            stale = t.is_stale(now)
            col = theme.G_BRIGHT if stale else theme.G_HOT
            if t.gs and heading is not None and t.phase != trk.LANDED:
                vx, vy = geo.unit_from_bearing(heading)
                reach = t.gs / 60.0 * k
                gfx.line(surf, theme.G_MID, (px, py), (px + vx * reach, py - vy * reach))
            spr = self.bank.get(self._shape(t), t.wtc, col, heading, halo=(92, 255, 138, 64))
            surf.blit(spr, (px - spr.get_width() / 2.0, py - spr.get_height() / 2.0))
            b = 14.0 + 1.5 * math.sin(now * 3.0)
            c = 5.0
            for sx, sy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
                gfx.line(surf, theme.G_BRIGHT, (px + sx * b, py + sy * b), (px + sx * (b - c), py + sy * b))
                gfx.line(surf, theme.G_BRIGHT, (px + sx * b, py + sy * b), (px + sx * b, py + sy * (b - c)))
            taken.append(pygame.Rect(int(px - b), int(py - b), int(2 * b), int(2 * b)))
            self._tag(surf, t, px, py, [(t.label, "smallb", col), (self._alt_speed(t, alt), "micro", theme.G_BRIGHT)],
                      taken, r, gap=17)
        surf.set_clip(prev_clip)
        gfx.frame(surf, r)

    def _shape(self, t):
        s = getattr(t, "ui_shape", None)
        if s is None or getattr(t, "ui_shape_type", None) != t.type:
            s = self.ref.shape_class(t.type, t.category)
            t.ui_shape, t.ui_shape_type = s, t.type
        return s

    @staticmethod
    def _alt_speed(t, alt):
        if t.phase == trk.LANDED:
            return "LANDED"
        a = "---" if alt is None else "%03d" % max(0, int(round(alt / 100.0)))
        arrow = ""
        if t.vrate is not None:
            arrow = "\u2193" if t.vrate < -250 else ("\u2191" if t.vrate > 250 else "")
        g = "" if t.gs is None else " %d" % int(round(t.gs))
        return a + arrow + g

    def _trail(self, surf, t, P, now, max_age, c_new, c_old, big=False):
        r = self.rect
        for (tp, x, y, _alt) in t.hist:
            age = now - tp
            if age > max_age:
                continue
            px, py = P(x, y)
            ix, iy = int(px), int(py)
            if not r.collidepoint(ix, iy):
                continue
            col = c_new if age < max_age * 0.35 else c_old
            if big and age < max_age * 0.35:
                surf.fill(col, (ix - 1, iy - 1, 2, 2))
            else:
                surf.set_at((ix, iy), col)

    def _tag(self, surf, t, px, py, lines, taken, bounds, gap=11, leader=True, must_fit=False):
        """Place a data tag next to an icon where it collides with nothing."""
        th = self.th
        w = max(th.width(role, text) for text, role, _c in lines)
        hgt = sum(th.ch(role) for _t, role, _c in lines)
        cands = [(gap, -hgt - 4), (gap, 5), (-gap - w, -hgt - 4), (-gap - w, 5),
                 (-w / 2.0, -gap - hgt - 6), (-w / 2.0, gap + 4), (gap + 8, -hgt / 2.0), (-gap - 8 - w, -hgt / 2.0)]
        last = getattr(t, "ui_tag", 0)
        order = [last] + [i for i in range(len(cands)) if i != last]
        chosen = None
        inner = bounds.inflate(-6, -6)
        for i in order:
            dx, dy = cands[i]
            box = pygame.Rect(int(px + dx), int(py + dy), int(w), int(hgt))
            if inner.contains(box) and box.inflate(4, 2).collidelist(taken) == -1:
                chosen = (i, box)
                break
        if chosen is None:
            if must_fit:
                return
            dx, dy = cands[last]
            box = pygame.Rect(int(px + dx), int(py + dy), int(w), int(hgt))
            box.clamp_ip(inner)
            chosen = (last, box)
        t.ui_tag, box = chosen
        if leader:
            ex = box.left - 2 if box.centerx > px else box.right + 2
            ey = max(box.top, min(box.bottom, int(py)))
            if abs(ex - px) > 12 or abs(ey - py) > 12:
                ang = math.atan2(ey - py, ex - px)
                gfx.line(surf, theme.G_DIM, (px + math.cos(ang) * 10, py + math.sin(ang) * 10), (ex, ey))
        y = box.y
        for text, role, col in lines:
            th.text(surf, role, text, box.x, y, col)
            y += th.ch(role)
        taken.append(box.inflate(6, 4))
