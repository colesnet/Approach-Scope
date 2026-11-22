"""The tracker: from raw position reports to "QFA741 lands on 23 in 2:35".

For every aircraft in the feed this keeps a Track that
  * smooths the 5-second position updates into continuous motion,
  * decides whether the aircraft is arriving, and on which runway,
  * estimates when it will touch down and when it passes your window,
  * notices landings and go-arounds.

All times are monotonic seconds supplied by the caller, so the whole thing can
be driven by a simulated clock in tests.
"""
import collections
import math

import geo

# phases
OTHER = "OTHER"
GROUND = "GROUND"
DEPARTING = "DEPARTING"
INBOUND = "INBOUND"
FINAL = "FINAL"
LANDED = "LANDED"
GO_AROUND = "GO-AROUND"

ARRIVAL_PHASES = (INBOUND, FINAL, GO_AROUND)

MAX_EXTRAP_S = 12.0          # never dead-reckon further than this past the last fix
BLEND_TAU_S = 1.2            # how quickly a correction is eased in
STALE_S = 15.0               # position older than this is drawn dim
DROP_S = 60.0                # gone from the feed this long -> forgotten
INBOUND_MAX_NM = 42.0
FINAL_MAX_NM = 18.0
EVIDENCE_WINDOW_S = 45 * 60  # how long a landing counts as proof of the runway in use


def qnh_offset_ft(qnh_hpa):
    """Feet to add to a pressure altitude (1013 hPa datum) to get altitude on QNH."""
    return -145366.45 * (1.0 - (qnh_hpa / 1013.25) ** 0.190284)


def approach_speed_kt(descriptor, wtc, type_code=""):
    """Typical ground speed on short final in still air, by class of aircraft."""
    d = (descriptor or "").upper()
    if d.startswith("H") or d.startswith("G"):
        return 70.0
    eng = d[2:3]
    if eng == "J":
        if wtc == "H" or type_code in ("A388",):
            return 150.0
        if wtc == "L":
            return 118.0
        return 138.0
    if eng == "T":
        if d[1:2] in ("2", "4") and wtc != "L":
            return 120.0
        return 100.0
    if eng in ("P", "E"):
        return 80.0 if d[1:2] == "1" else 95.0
    return 135.0


def time_to_go_s(d_from, d_to, gs_now, v_final, decel_until=3.0):
    """Seconds to fly along the final from d_from to d_to (NM to threshold).

    Aircraft slow down on the way in, so a plain distance/speed underestimates
    from far out.  The model: ground speed eases linearly from what it is now
    to the final-approach speed by `decel_until` NM (or sooner if already
    close in), then holds that to the threshold.
    """
    if d_from <= d_to:
        return 0.0
    gs_now = max(40.0, gs_now)
    v_final = max(40.0, v_final)
    if gs_now <= v_final + 0.5:
        return (d_from - d_to) / gs_now * 3600.0
    d_end = min(decel_until, 0.6 * d_from)
    k = (gs_now - v_final) / (d_from - d_end)        # knots shed per NM
    d_a = max(d_to, d_end)
    v_a = v_final + k * (d_a - d_end)
    hours = math.log(gs_now / v_a) / k
    if d_to < d_end:
        hours += (d_end - d_to) / v_final
    return hours * 3600.0


class Track(object):
    def __init__(self, hexid, now):
        self.hex = hexid
        self.flight = ""
        self.reg = ""
        self.type = ""
        self.desc = ""
        self.own_op = ""
        self.category = ""
        self.src = ""
        self.military = False
        self.first_seen = now
        self.last_seen = now
        self.t_pos = None
        self.lat = self.lon = None
        self.x = self.y = 0.0
        self.gs = None
        self.track = None
        self.vrate = None
        self.alt = None              # best estimate, ft above sea level
        self.alt_src = ""
        self.alt_baro = None
        self.on_ground = False
        self.ias = self.tas = self.mach = None
        self.mag_heading = self.true_heading = None
        self.roll = self.track_rate = None
        self.wd = self.ws = self.oat = None
        self.squawk = ""
        self.emergency = ""
        self.nav_qnh = self.nav_alt = self.nav_heading = None
        self.nav_modes = None
        self.rssi = None
        self.hist = collections.deque(maxlen=120)
        # easing of corrections
        self._ox = self._oy = self._oalt = 0.0
        self._t_blend = now
        # classification
        self.phase = OTHER
        self.rwy = None
        self.dtg = None
        self.xtk = None
        self.dist_ap = 0.0
        self.final_hits = 0
        self.final_misses = 0
        self.est_since = None
        self.min_dtg = None
        self.min_alt = None
        self.climb_hits = 0
        self.leave_hits = 0
        self.was_ground = False
        self.dep_time = None
        self.inbound_since = None
        self.t_td = None
        self.t_abeam = None
        self.passed_home_at = None
        self.eta_approx = True
        self.track_miles = None
        self.landed_at = None
        self.landed_wall = None
        self.landed_assumed = False
        self.ga_at = None
        self.ga_count = 0
        self.outliers = 0
        self.touched = False
        self.v_app = 135.0
        self.descriptor = ""
        self.wtc = ""

    # --- smoothed state for drawing ------------------------------------
    def _predict(self, now):
        """Dead-reckoned (x, y, alt, track) at `now`, before easing."""
        if self.t_pos is None:
            return self.x, self.y, self.alt, self.track
        limit = 3.0 if (self.on_ground or self.phase in (LANDED, GROUND)) else MAX_EXTRAP_S
        dt = min(max(0.0, now - self.t_pos), limit)
        gs = self.gs or 0.0
        trk = self.track
        if trk is None or gs < 1.0 or dt <= 0.0:
            return self.x, self.y, self.alt, trk
        v = gs / 3600.0  # NM per second
        th0 = math.radians(trk)
        rate = self.track_rate
        if rate is not None and abs(rate) >= 0.25 and not self.on_ground:
            w = math.radians(max(-4.0, min(4.0, rate)))
            th1 = th0 + w * dt
            x = self.x + (v / w) * (math.cos(th0) - math.cos(th1))
            y = self.y + (v / w) * (math.sin(th1) - math.sin(th0))
            trk = math.degrees(th1) % 360.0
        else:
            x = self.x + v * dt * math.sin(th0)
            y = self.y + v * dt * math.cos(th0)
        alt = self.alt
        if alt is not None and self.vrate is not None and not self.on_ground:
            alt = alt + self.vrate / 60.0 * dt
            floor = self.rwy.elev_ft if self.rwy is not None else 0.0
            if self.phase in (FINAL, LANDED) and alt < floor:
                alt = floor
        return x, y, alt, trk

    def state_at(self, now):
        """Smoothed (x, y, alt, track) for drawing."""
        x, y, alt, trk = self._predict(now)
        k = math.exp(-max(0.0, now - self._t_blend) / BLEND_TAU_S)
        if k > 0.01:
            x += self._ox * k
            y += self._oy * k
            if alt is not None:
                alt += self._oalt * k
        return x, y, alt, trk

    def age(self, now):
        return 1e9 if self.t_pos is None else now - self.t_pos

    def is_stale(self, now):
        return self.age(now) > STALE_S

    @property
    def label(self):
        return self.flight or self.reg or self.hex.upper()

    @property
    def is_arrival(self):
        return self.phase in ARRIVAL_PHASES


class Landing(object):
    """One landing (or, in Tracker.go_arounds, one abandoned approach)."""
    __slots__ = ("wall", "flight", "type", "reg", "rwy", "hex", "origin", "low")

    def __init__(self, wall, flight, typ, reg, rwy, hexid, origin="", low=0.0):
        self.low = low          # go-arounds: lowest altitude reached, ft
        self.wall = wall
        self.flight = flight
        self.type = typ
        self.reg = reg
        self.rwy = rwy
        self.hex = hexid
        self.origin = origin

    def to_dict(self):
        d = {"t": self.wall, "flight": self.flight, "type": self.type, "reg": self.reg,
             "rwy": self.rwy, "hex": self.hex, "origin": self.origin}
        if self.low:
            d["low"] = round(self.low)
        return d

    @staticmethod
    def from_dict(d):
        return Landing(float(d.get("t", 0)), str(d.get("flight", "")), str(d.get("type", "")),
                       str(d.get("reg", "")), str(d.get("rwy", "")), str(d.get("hex", "")),
                       str(d.get("origin", "")), float(d.get("low", 0) or 0))


class HomeGeometry(object):
    """Everything about where you are relative to each approach."""

    def __init__(self, airport, lat, lon, eye_m, name):
        self.name = name
        self.lat, self.lon = lat, lon
        self.x, self.y = airport.frame.to_xy(lat, lon)
        self.eye_ft = eye_m * geo.FT_PER_M
        self.abeam = {}      # runway id -> (dtg, xtk) of the closest point on that final
        self.sky_path = {}   # runway id -> [(dtg, az, el)] along the ideal approach
        for rid, rw in airport.runways.items():
            dtg, xtk = rw.along_cross(self.x, self.y)
            self.abeam[rid] = (dtg, xtk)
            pts = []
            d = 0.0
            while d <= 16.001:
                px, py = rw.point_at(d)
                az, el, _slant, _g = geo.look_angles(px - self.x, py - self.y, rw.gp_alt_ft(d), self.eye_ft)
                pts.append((d, az, el))
                d += 0.25
            self.sky_path[rid] = pts

    def look(self, x, y, alt_ft):
        return geo.look_angles(x - self.x, y - self.y, alt_ft, self.eye_ft)

    def passes(self, rid):
        """True if aircraft on this final actually fly past you (rather than towards/away)."""
        dtg, _ = self.abeam[rid]
        return 0.3 <= dtg <= 15.0


class Tracker(object):
    def __init__(self, airport, home, home_runway="", ref=None, wall_clock=None):
        self.airport = airport
        self.home = home
        self.ref = ref
        self.wall_clock = wall_clock
        self.tracks = {}
        self.home_runway = home_runway if home_runway in airport.runways else airport.runway_order[0]
        self.qnh = None
        self.qnh_from = ""
        self.wind = None                 # (direction true, knots)
        self.active_id = self.home_runway
        self.active_basis = "DEFAULT"    # TRAFFIC | WIND | DEFAULT
        self.evidence = {}               # runway id -> last time something landed / was on short final
        self.landings = []               # newest first
        self.go_arounds = []             # newest first
        self.on_go_around = None         # callback(Landing record, Track)
        self.events = collections.deque(maxlen=20)   # (t, kind, text)
        self.featured_hex = None
        self.manual_until = 0.0
        self.on_landing = None           # callback(Landing, Track)
        self.on_unlanding = None         # callback(Track) when a logged landing was really a go-around
        self.last_ingest = None
        self.raw_count = 0
        self.snapshots = 0

    # ------------------------------------------------------------------
    # weather in
    # ------------------------------------------------------------------
    def set_weather(self, qnh=None, wind_dir=None, wind_kt=None):
        if qnh is not None and 940.0 <= qnh <= 1060.0:
            self.qnh = float(qnh)
            self.qnh_from = "METAR"
        if wind_kt is not None:
            self.wind = (wind_dir, float(wind_kt)) if wind_dir is not None else (None, float(wind_kt))

    # ------------------------------------------------------------------
    # data in
    # ------------------------------------------------------------------
    def ingest(self, snap):
        now = snap.t_recv
        self.last_ingest = now
        self.raw_count = snap.raw_count
        self.snapshots += 1
        if self.qnh_from != "METAR":
            self._estimate_qnh(snap.reports)
        for rep in snap.reports:
            try:
                self._ingest_one(rep, now)
            except Exception as exc:   # one bad record must never take the display down
                self._log(now, "ERROR", "bad record %s: %s" % (getattr(rep, "hex", "?"), exc))
        self._update_active_runway(now)

    def _estimate_qnh(self, reports):
        vals = sorted(r.nav_qnh for r in reports
                      if r.nav_qnh is not None and r.alt_baro is not None
                      and r.alt_baro < self.airport.transition_alt_ft - 1000 and abs(r.nav_qnh - 1013.25) > 0.3)
        if len(vals) >= 2:
            self.qnh = vals[len(vals) // 2]
            self.qnh_from = "TRAFFIC"

    def _ingest_one(self, rep, now):
        ap = self.airport
        tr = self.tracks.get(rep.hex)
        if tr is None:
            tr = Track(rep.hex, now)
            self.tracks[rep.hex] = tr
        tr.last_seen = now
        if rep.flight:
            tr.flight = rep.flight
        if rep.reg:
            tr.reg = rep.reg
        if rep.type and rep.type != tr.type:
            tr.type = rep.type
            desc, wtc = "", ""
            if self.ref is not None:
                desc, wtc = self.ref.descriptor(rep.type)
            tr.descriptor, tr.wtc = desc, wtc
            tr.v_app = approach_speed_kt(desc, wtc, rep.type)
        if rep.desc:
            tr.desc = rep.desc
        if rep.own_op:
            tr.own_op = rep.own_op
        if rep.category:
            tr.category = rep.category
        tr.src = rep.src or tr.src
        tr.military = bool(rep.military)
        tr.squawk = rep.squawk or tr.squawk
        tr.emergency = rep.emergency or ""

        t_pos = now - rep.seen_pos
        if tr.t_pos is not None:
            same_spot = abs(rep.lat - tr.lat) < 1e-7 and abs(rep.lon - tr.lon) < 1e-7
            if t_pos <= tr.t_pos + 0.2 or (same_spot and t_pos <= tr.t_pos + 1.5):
                return  # same fix as last time: keep coasting on the old one

        x, y = ap.frame.to_xy(rep.lat, rep.lon)
        if tr.t_pos is not None:
            dt = t_pos - tr.t_pos
            jump = math.hypot(x - tr.x, y - tr.y)
            limit = ((tr.gs or 250.0) * 2.0 + 150.0) / 3600.0 * max(dt, 1.0) + 0.3
            if jump > limit and tr.outliers < 2:
                tr.outliers += 1     # an MLAT glitch; believe it only if it persists
                return
        tr.outliers = 0

        # where the display currently shows it, so the correction can be eased in
        had_fix = tr.t_pos is not None
        if had_fix:
            sx, sy, salt, _ = tr.state_at(now)

        prev = (tr.t_pos, tr.x, tr.y, tr.alt)
        prev_track = tr.track
        tr.lat, tr.lon = rep.lat, rep.lon
        tr.x, tr.y = x, y
        tr.t_pos = t_pos
        tr.on_ground = bool(rep.on_ground)
        tr.alt_baro = rep.alt_baro

        # --- altitude above sea level -------------------------------------
        if rep.on_ground:
            tr.alt, tr.alt_src = ap.elev_ft, "GND"
        elif rep.alt_geom is not None and rep.src != "MLAT":
            tr.alt, tr.alt_src = rep.alt_geom - ap.geoid_sep_ft, "GPS"
        elif rep.alt_baro is not None:
            qnh = self.qnh
            if qnh is None and rep.nav_qnh is not None and rep.alt_baro < ap.transition_alt_ft:
                qnh = rep.nav_qnh
            if qnh is not None and rep.alt_baro < max(ap.transition_alt_ft + 1000, 15000):
                tr.alt, tr.alt_src = rep.alt_baro + qnh_offset_ft(qnh), "BARO"
            else:
                tr.alt, tr.alt_src = rep.alt_baro, "STD"
        else:
            tr.alt, tr.alt_src = None, ""

        # --- speed, direction, climb/descent --------------------------------
        gs, trk = rep.gs, rep.track
        if (gs is None or trk is None) and prev[0] is not None:
            dt = t_pos - prev[0]
            if 3.0 <= dt <= 40.0:
                dx, dy = x - prev[1], y - prev[2]
                dist = math.hypot(dx, dy)
                if gs is None:
                    gs = min(600.0, dist / dt * 3600.0)
                if trk is None and dist > 0.03:
                    trk = geo.bearing_xy(dx, dy)
        tr.gs = gs if gs is not None else tr.gs
        tr.track = trk if trk is not None else tr.track
        vr = rep.geom_rate if (tr.alt_src == "GPS" and rep.geom_rate is not None) else rep.baro_rate
        if vr is None:
            vr = rep.geom_rate
        if vr is None and prev[0] is not None and prev[3] is not None and tr.alt is not None:
            dt = t_pos - prev[0]
            if 3.0 <= dt <= 40.0:
                raw = (tr.alt - prev[3]) / dt * 60.0
                vr = raw if tr.vrate is None else 0.5 * tr.vrate + 0.5 * raw
        tr.vrate = 0.0 if rep.on_ground else vr

        tr.ias, tr.tas, tr.mach = rep.ias, rep.tas, rep.mach
        tr.mag_heading, tr.true_heading = rep.mag_heading, rep.true_heading
        tr.roll, tr.track_rate = rep.roll, rep.track_rate
        if tr.track_rate is None and prev[0] is not None and trk is not None and prev_track is not None:
            dt = t_pos - prev[0]
            if 2.0 <= dt <= 30.0:          # MLAT and older transponders do not send a turn rate: derive it
                tr.track_rate = geo.ang_diff(trk, prev_track) / dt
        tr.wd, tr.ws, tr.oat = rep.wd, rep.ws, rep.oat
        tr.nav_qnh, tr.nav_alt, tr.nav_heading = rep.nav_qnh, rep.nav_alt, rep.nav_heading
        tr.nav_modes = rep.nav_modes
        tr.rssi = rep.rssi

        if not tr.hist or t_pos - tr.hist[-1][0] >= 4.0:
            tr.hist.append((t_pos, x, y, tr.alt))

        self._classify(tr, now)

        # ease from where it was drawn to where it now should be
        if had_fix:
            nx, ny, nalt, _ = tr._predict(now)
            ox, oy = sx - nx, sy - ny
            if math.hypot(ox, oy) > 1.0:
                ox = oy = 0.0   # too far to slide: just jump
            tr._ox, tr._oy = ox, oy
            tr._oalt = (salt - nalt) if (salt is not None and nalt is not None and abs(salt - nalt) < 600) else 0.0
            tr._t_blend = now

    # ------------------------------------------------------------------
    # classification
    # ------------------------------------------------------------------
    def _established(self, tr):
        """(runway, dtg, xtk) if the aircraft is lined up on a final approach."""
        if tr.track is None or tr.alt is None or tr.gs is None or tr.on_ground:
            return None
        if not (45.0 <= tr.gs <= 290.0):
            return None
        best = None
        for rw in self.airport.runways.values():
            held = tr.phase == FINAL and tr.rwy is rw
            dtg, xtk = rw.along_cross(tr.x, tr.y)
            if dtg > (FINAL_MAX_NM + 2.0 if held else FINAL_MAX_NM) or dtg < (-0.5 if held else 0.05):
                continue
            tol = max(0.35, 0.085 * dtg) * (1.7 if held else 1.0)
            if abs(xtk) > tol:
                continue
            if abs(geo.ang_diff(tr.track, rw.course)) > (42.0 if held else 25.0):
                continue
            hgt = tr.alt - rw.elev_ft
            gp_hgt = rw.gp_alt_ft(max(dtg, 0.0)) - rw.elev_ft
            if hgt > gp_hgt + (2600.0 if held else 2000.0):
                continue                       # far above the path: just passing over
            if hgt < 0.45 * gp_hgt - 250.0:
                continue                       # far below it: something else low and local
            if not held and tr.vrate is not None and tr.vrate > 500.0:
                continue                       # climbing: a departure or an overshoot
            score = abs(xtk) / tol
            if best is None or score < best[0]:
                best = (score, rw, dtg, xtk)
        return None if best is None else best[1:]

    def _classify(self, tr, now):
        ap = self.airport
        tr.dist_ap = math.hypot(tr.x, tr.y)
        near_field = tr.dist_ap < 2.6
        hgt_ap = None if tr.alt is None else tr.alt - ap.elev_ft

        # ---- on the ground ------------------------------------------------
        if tr.on_ground or (near_field and hgt_ap is not None and hgt_ap < 80.0
                            and tr.gs is not None and tr.gs < 55.0):
            if tr.phase in (FINAL, GO_AROUND) and tr.rwy is not None:
                self._landed(tr, now, assumed=False)
            elif tr.phase != LANDED:
                tr.phase = GROUND
                tr.rwy = None
            tr.touched = True
            if near_field:
                tr.was_ground = True
            return

        if tr.phase == LANDED:
            if tr.gs is not None and tr.gs < 95.0:
                tr.touched = True
            if hgt_ap is not None and hgt_ap > 400.0 and (tr.vrate or 0.0) > 400.0:
                if not tr.touched and tr.landed_at is not None and now - tr.landed_at < 90.0 and tr.rwy is not None:
                    # it never slowed down or reported being on the ground: that was a
                    # go-around from the flare, not a landing
                    self.landings[:] = [l for l in self.landings
                                        if not (l.hex == tr.hex and abs(l.wall - (tr.landed_wall or 0)) < 5.0)]
                    tr.landed_at = tr.landed_wall = None
                    if self.on_unlanding is not None:
                        try:
                            self.on_unlanding(tr)
                        except Exception:
                            pass
                    self._go_around(tr, now)
                else:
                    # airborne again and climbing clear: a touch-and-go, now outbound
                    tr.phase, tr.rwy, tr.dep_time = DEPARTING, None, now
            return

        est = self._established(tr)

        # ---- go-around in progress ---------------------------------------
        if tr.phase == GO_AROUND:
            if now - (tr.ga_at or now) > 150.0:
                tr.phase, tr.rwy = INBOUND, None
                tr.inbound_since = now
                tr.final_hits = 0
                tr.min_dtg = tr.min_alt = None
            else:
                if tr.rwy is not None:
                    tr.dtg, tr.xtk = tr.rwy.along_cross(tr.x, tr.y)
                return

        # ---- lined up on a final ------------------------------------------
        if est is not None:
            rw, dtg, xtk = est
            tr.final_misses = 0
            if tr.phase == FINAL and tr.rwy is rw:
                pass
            else:
                # A turning aircraft (say, in a nearby airfield's circuit) can line up with the
                # approach for a moment.  Ask for steady, repeated evidence before believing it,
                # and more of it from anything that was not already heading in to land.
                turning = tr.track_rate is not None and abs(tr.track_rate) > 1.2
                was_inbound = tr.phase in (INBOUND, GO_AROUND)
                descending = tr.vrate is not None and tr.vrate < -300.0
                # something not already arriving must at least be coming down to count
                tr.final_hits = 0 if (turning or not (was_inbound or descending)) else tr.final_hits + 1
                sure = was_inbound and dtg < 6.0 and abs(xtk) < 0.25 and not turning
                if tr.final_hits >= (2 if (was_inbound or descending) else 3) or sure:
                    tr.phase, tr.rwy = FINAL, rw
                    tr.est_since = now
                    tr.min_dtg, tr.min_alt = dtg, tr.alt
                    tr.climb_hits = 0
                    tr.t_td = tr.t_abeam = None
                    tr.passed_home_at = None
            if tr.phase == FINAL and tr.rwy is rw:
                self._update_final(tr, rw, dtg, xtk, now)
                return
        else:
            tr.final_hits = 0

        # ---- was on final, no longer passes the test ------------------------
        if tr.phase == FINAL and tr.rwy is not None:
            rw = tr.rwy
            dtg, xtk = rw.along_cross(tr.x, tr.y)
            hgt = (tr.alt - rw.elev_ft) if tr.alt is not None else 9e9
            if dtg < 0.3 and hgt < 220.0 and abs(xtk) < 0.2:
                self._landed(tr, now, assumed=False)
                return
            climbing = tr.vrate is not None and tr.vrate > 450.0
            if climbing and (tr.min_dtg or 99.0) < 6.0 and hgt > 250.0:
                self._go_around(tr, now)
                return
            tr.final_misses += 1
            if tr.final_misses <= 2:
                tr.dtg, tr.xtk = dtg, xtk      # brief wobble: keep it as it was
                return
            tr.phase, tr.rwy = OTHER, None
            tr.t_td = tr.t_abeam = None

        # ---- departures -----------------------------------------------------
        climbing = tr.vrate is not None and tr.vrate > 400.0
        if tr.phase in (GROUND,) and tr.was_ground and not tr.on_ground:
            tr.phase, tr.dep_time = DEPARTING, now
            return
        if tr.phase == OTHER and tr.inbound_since is None and climbing and hgt_ap is not None \
                and hgt_ap < 4500.0 and tr.dist_ap < 9.0 and self._closing_kt(tr) < -30.0 \
                and now - tr.first_seen < 90.0:
            tr.phase, tr.dep_time = DEPARTING, now
            return
        if tr.phase == DEPARTING:
            if tr.dist_ap > 45.0 or (tr.alt is not None and tr.alt > 18000.0) \
                    or now - (tr.dep_time or now) > 1500.0:
                tr.phase = OTHER
            return
        if tr.phase == GROUND:
            tr.phase = OTHER

        # ---- inbound but not yet lined up -----------------------------------
        if self._looks_inbound(tr, now):
            if tr.phase != INBOUND:
                tr.phase = INBOUND
                tr.inbound_since = now
                tr.t_td = None
            self._update_inbound(tr, now)
        elif tr.phase == INBOUND:
            tr.phase = OTHER
            tr.inbound_since = None
            tr.t_td = None

    def _closing_kt(self, tr):
        """Speed towards the airport (negative = moving away)."""
        if tr.gs is None or tr.track is None or tr.dist_ap < 0.05:
            return 0.0
        ux, uy = geo.unit_from_bearing(tr.track)
        return -(ux * tr.x + uy * tr.y) / tr.dist_ap * tr.gs

    def _looks_inbound(self, tr, now):
        if tr.alt is None or tr.gs is None or tr.track is None:
            return False
        if tr.dep_time is not None and now - tr.dep_time < 1500.0:
            return False
        d = tr.dist_ap
        h = tr.alt - self.airport.elev_ft          # height above the airport (matters at high airports)
        if d > INBOUND_MAX_NM or tr.category == "A7" or tr.descriptor[:1] in ("H", "G"):
            return False
        if tr.gs < 105.0 or h > 15000.0 or h > 600.0 * d + 5000.0:
            return False
        on_extended_centreline = False
        for rw in self.airport.runways.values():
            dtg, xtk = rw.along_cross(tr.x, tr.y)
            if dtg > 0 and abs(xtk) < 1.5:
                on_extended_centreline = True
        if not on_extended_centreline:
            for nb in self.airport.nearby:
                if math.hypot(tr.x - nb["x"], tr.y - nb["y"]) < 5.0 and tr.alt < nb["elev_ft"] + 2600.0:
                    return False          # in another airfield's circuit
        if h < 2800.0 and d > 9.0:
            lined_up = False
            for rw in self.airport.runways.values():
                dtg, xtk = rw.along_cross(tr.x, tr.y)
                if dtg > 0 and abs(xtk) < 3.0:
                    lined_up = True
            if not lined_up:
                return False
        closing = self._closing_kt(tr)
        vr = tr.vrate
        if tr.phase == INBOUND:
            if vr is not None and vr > 700.0 and h > 4500.0:
                tr.leave_hits += 1
            elif closing < -90.0 and d > 18.0:
                tr.leave_hits += 1
            else:
                tr.leave_hits = max(0, tr.leave_hits - 1)
            return tr.leave_hits < 4
        tr.leave_hits = 0
        descending = vr is not None and vr < -250.0
        if descending and (closing > 40.0 or d < 16.0):
            return True
        if h < 6500.0 and d < 24.0 and tr.gs > 150.0 and (closing > 60.0 or d < 12.0) \
                and (vr is None or vr < 300.0):
            return True
        return False

    # ------------------------------------------------------------------
    # per-phase bookkeeping
    # ------------------------------------------------------------------
    def _v_final(self, tr, rw):
        v = tr.v_app
        if self.wind is not None and self.wind[0] is not None:
            head, _ = rw.headwind_crosswind(self.wind[0], self.wind[1])
            v -= max(-15.0, min(35.0, head))
        return max(50.0, v)

    def _ease(self, old, new, weight=0.5, snap=25.0):
        if old is None or abs(new - old) > snap:
            return new
        return old + (new - old) * weight

    def _update_final(self, tr, rw, dtg, xtk, now):
        tr.dtg, tr.xtk = dtg, xtk
        tr.eta_approx = False
        tr.track_miles = dtg
        if tr.min_dtg is None or dtg < tr.min_dtg:
            tr.min_dtg = dtg
        if tr.alt is not None and (tr.min_alt is None or tr.alt < tr.min_alt):
            tr.min_alt = tr.alt
        gs = tr.gs or tr.v_app
        v_final = self._v_final(tr, rw)
        tr.t_td = self._ease(tr.t_td, tr.t_pos + time_to_go_s(max(dtg, 0.0), 0.0, gs, v_final))
        ab_dtg, _ = self.home.abeam[rw.id]
        if self.home.passes(rw.id):
            if dtg > ab_dtg:
                tr.t_abeam = self._ease(tr.t_abeam, tr.t_pos + time_to_go_s(dtg, ab_dtg, gs, v_final))
            elif tr.passed_home_at is None:
                over = (ab_dtg - dtg) / max(gs, 40.0) * 3600.0
                tr.passed_home_at = tr.t_abeam if tr.t_abeam is not None else tr.t_pos - over
                tr.t_abeam = None
        if dtg < 3.5:
            self.evidence[rw.id] = now

        hgt = (tr.alt - rw.elev_ft) if tr.alt is not None else 9e9
        # over the runway and nearly down: call it landed even without a "ground" flag
        if (dtg < -0.05 and hgt < 150.0) or (dtg < 0.35 and hgt < 90.0 and gs < 105.0):
            self._landed(tr, now, assumed=False)
            return
        # go-around: was close in, now climbing away
        if tr.vrate is not None and tr.vrate > 600.0 and tr.min_dtg < 5.0 and tr.alt is not None \
                and tr.min_alt is not None and tr.alt > tr.min_alt + 200.0 and hgt > 250.0:
            tr.climb_hits += 1
            if tr.climb_hits >= 2:
                self._go_around(tr, now)
        else:
            tr.climb_hits = 0

    def _update_inbound(self, tr, now):
        """Rough time to touchdown for an aircraft that is not lined up yet.

        The route is not known, so a standard arrival is assumed: straight to a
        9-13 NM final if the aircraft is out beyond that, otherwise a downwind
        leg past the airport, a base turn and then the final.  Speeds follow the
        usual pattern (250 kt, slowing to 210 by 15 NM to run and 185 by 9).
        """
        rw = self.airport.runways[self.active_id]
        dtg_p, xtk_p = rw.along_cross(tr.x, tr.y)
        if dtg_p >= 11.0:
            join = min(13.0, max(9.0, 0.6 * dtg_p))
            jx, jy = rw.point_at(join)
            leg = math.hypot(tr.x - jx, tr.y - jy)
        else:
            join = 9.0
            side = 1.0 if xtk_p >= 0 else -1.0
            bx, by = rw.point_at(join + 2.5, side * 5.0)       # end of the downwind leg
            leg = math.hypot(tr.x - bx, tr.y - by) + 5.6       # ... plus the base turn onto final
        gs = max(tr.gs or 200.0, 110.0)
        secs = 0.0
        m = leg + join
        while m > join:
            step = min(1.0, m - join)
            if m > 30.0:
                v = 250.0
            elif m > 15.0:
                v = 210.0 + (m - 15.0) / 15.0 * 40.0
            else:
                v = 185.0 + max(0.0, m - 9.0) / 6.0 * 25.0
            secs += step / min(gs, v) * 3600.0
            m -= step
        secs *= 1.06        # real routes are never quite the shortest path
        secs += time_to_go_s(join, 0.0, min(gs, 185.0), self._v_final(tr, rw))
        tr.track_miles = leg + join
        tr.eta_approx = True
        tr.dtg = tr.xtk = None
        tr.t_td = self._ease(tr.t_td, tr.t_pos + secs, weight=0.3, snap=90.0)
        tr.t_abeam = None

    def _landed(self, tr, now, assumed):
        rw = tr.rwy
        if rw is None:
            tr.phase = GROUND
            return
        t_cross = now
        if not assumed and tr.t_pos is not None:
            dtg, _ = rw.along_cross(tr.x, tr.y)
            if dtg < 0 and tr.gs:
                t_cross = tr.t_pos - (-dtg) / max(tr.gs, 40.0) * 3600.0
            elif tr.t_td is not None and abs(tr.t_td - now) < 30.0:
                t_cross = tr.t_td
        elif tr.t_td is not None:
            t_cross = tr.t_td
        t_cross = min(t_cross, now)
        tr.phase = LANDED
        tr.landed_at = t_cross
        tr.landed_assumed = assumed
        tr.t_td = None
        tr.t_abeam = None
        self.evidence[rw.id] = now
        wall = self.wall_clock() - (now - t_cross) if self.wall_clock else 0.0
        tr.landed_wall = wall
        for prev in self.landings[:12]:
            if prev.hex == tr.hex and abs(prev.wall - wall) < 600.0:
                return       # already logged this arrival
        rec = Landing(wall, tr.label, tr.type, tr.reg, rw.id, tr.hex)
        self.landings.insert(0, rec)
        del self.landings[3000:]
        self._log(now, "LANDED", "%s landed runway %s" % (tr.label, rw.id))
        if self.on_landing is not None:
            try:
                self.on_landing(rec, tr)
            except Exception:
                pass

    def _go_around(self, tr, now):
        tr.phase = GO_AROUND
        tr.ga_at = now
        tr.ga_count += 1
        tr.t_td = tr.t_abeam = None
        tr.climb_hits = 0
        self._log(now, "GO-AROUND", "%s going around runway %s" % (tr.label, tr.rwy.id if tr.rwy else "?"))
        wall = self.wall_clock() if self.wall_clock else 0.0
        for prev in self.go_arounds[:6]:
            if prev.hex == tr.hex and abs(prev.wall - wall) < 120.0:
                return
        rec = Landing(wall, tr.label, tr.type, tr.reg, tr.rwy.id if tr.rwy else "", tr.hex,
                      low=tr.min_alt or 0.0)
        self.go_arounds.insert(0, rec)
        del self.go_arounds[500:]
        if self.on_go_around is not None:
            try:
                self.on_go_around(rec, tr)
            except Exception:
                pass

    def _log(self, now, kind, text):
        self.events.append((now, kind, text))

    # ------------------------------------------------------------------
    # which runway is in use
    # ------------------------------------------------------------------
    def _update_active_runway(self, now):
        best_id, best_t = None, None
        for rid, t in self.evidence.items():
            if now - t <= EVIDENCE_WINDOW_S and (best_t is None or t > best_t):
                best_id, best_t = rid, t
        # anybody established right now is the best evidence of all
        est = [t for t in self.tracks.values() if t.phase == FINAL and t.rwy is not None]
        if est:
            est.sort(key=lambda t: t.dtg if t.dtg is not None else 99.0)
            best_id, basis = est[0].rwy.id, "TRAFFIC"
        elif best_id is not None:
            basis = "TRAFFIC"
        elif self.wind is not None and self.wind[0] is not None and self.wind[1] >= 5.0:
            scored = []
            for rid, rw in self.airport.runways.items():
                head, cross = rw.headwind_crosswind(self.wind[0], self.wind[1])
                # the long runway is preferred unless the crosswind on it is strong
                bonus = 4.0 if rw.length_nm >= 1.2 else 0.0
                scored.append((head + bonus - max(0.0, abs(cross) - 15.0), rid))
            scored.sort(reverse=True)
            best_id, basis = scored[0][1], "WIND"
        else:
            best_id, basis = self.home_runway, "DEFAULT"
        if best_id != self.active_id:
            self._log(now, "RUNWAY", "arrivals now runway %s (%s)" % (best_id, basis.lower()))
        self.active_id, self.active_basis = best_id, basis

    # ------------------------------------------------------------------
    # housekeeping, once per frame
    # ------------------------------------------------------------------
    def tick(self, now):
        dead = []
        for hexid, tr in self.tracks.items():
            gone = now - tr.last_seen
            if tr.phase == FINAL and tr.rwy is not None and tr.t_pos is not None:
                # lost it low on short final (no receiver sees the last few hundred feet):
                # assume it landed when the countdown runs out
                silent = now - tr.t_pos
                hgt = (tr.alt - tr.rwy.elev_ft) if tr.alt is not None else 9e9
                if silent > 10.0 and tr.dtg is not None and tr.dtg < 2.0 and hgt < 800.0 \
                        and (tr.vrate is None or tr.vrate < 200.0) and tr.t_td is not None and now > tr.t_td + 4.0:
                    self._landed(tr, now, assumed=True)
                elif silent > 45.0:
                    tr.phase, tr.rwy, tr.t_td, tr.t_abeam = OTHER, None, None, None
            elif tr.phase == INBOUND and tr.t_pos is not None and now - tr.t_pos > 45.0:
                tr.phase, tr.t_td = OTHER, None
            if gone > DROP_S and not (tr.phase == LANDED and tr.landed_at is not None and now - tr.landed_at < 90.0):
                dead.append(hexid)
        for hexid in dead:
            del self.tracks[hexid]
        if self.featured_hex is not None and self.featured_hex not in self.tracks:
            self.featured_hex = None

    # ------------------------------------------------------------------
    # what the display shows
    # ------------------------------------------------------------------
    def arrivals(self, now):
        """Arriving aircraft, next to land first."""
        out = [t for t in self.tracks.values() if t.phase in ARRIVAL_PHASES and t.t_pos is not None]

        def key(t):
            if t.phase == GO_AROUND:
                return (2, 0.0)
            eta = (t.t_td - now) if t.t_td is not None else 9e9
            return (0 if t.phase == FINAL else 1, eta)
        out.sort(key=key)
        # an inbound aircraft with a sooner estimate than a long-final one still queues behind it
        return out

    def select(self, hexid, now, hold_s=45.0):
        self.featured_hex = hexid
        self.manual_until = now + hold_s if hexid else 0.0

    def featured(self, now):
        """The aircraft the big card is about."""
        arr = self.arrivals(now)
        cur = self.tracks.get(self.featured_hex) if self.featured_hex else None
        if cur is not None and now < self.manual_until and (cur.is_arrival or cur.phase == LANDED):
            return cur, arr
        self.manual_until = 0.0
        # hold on a just-landed aircraft for a moment, unless the next is almost down
        if cur is not None and cur.phase == LANDED and cur.landed_at is not None:
            since = now - cur.landed_at
            nxt = arr[0] if arr else None
            nxt_eta = (nxt.t_td - now) if (nxt is not None and nxt.t_td is not None) else 9e9
            if since < 20.0 and not (nxt is not None and nxt.phase == FINAL and nxt_eta < 75.0):
                return cur, arr
        if cur is not None and cur.phase == GO_AROUND and now - (cur.ga_at or now) < 40.0:
            return cur, arr
        choice = arr[0] if arr else None
        # stick with the current one unless something is clearly ahead of it
        if cur is not None and cur.is_arrival and choice is not None and choice is not cur \
                and cur.phase == choice.phase and cur.t_td is not None and choice.t_td is not None \
                and abs(cur.t_td - choice.t_td) < 20.0:
            choice = cur
        self.featured_hex = choice.hex if choice is not None else None
        return choice, arr

    def view_runway(self, feat):
        if feat is not None and feat.rwy is not None and feat.phase in (FINAL, LANDED, GO_AROUND):
            return feat.rwy
        return self.airport.runways[self.active_id]

    def others(self):
        return [t for t in self.tracks.values()
                if t.phase not in ARRIVAL_PHASES and t.phase != LANDED and t.t_pos is not None
                and not (t.phase == GROUND)]

    def counts(self):
        n_air = sum(1 for t in self.tracks.values() if t.phase != GROUND and not t.on_ground)
        return len(self.tracks), n_air
