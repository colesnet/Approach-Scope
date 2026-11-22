"""Airport and runway geometry.

A Runway knows where its landing threshold is, which way aircraft land on it,
and how high the glidepath is at any distance out.  All the "how far to go /
how far off the centreline / how far above the glidepath" numbers on the
display come from here.
"""
import json
import math
import os

import geo


class Fix(object):
    __slots__ = ("name", "lat", "lon", "x", "y", "dtg")

    def __init__(self, name, lat, lon):
        self.name = name
        self.lat = lat
        self.lon = lon
        self.x = self.y = 0.0
        self.dtg = 0.0


class Runway(object):
    """One landing direction (e.g. "23")."""

    def __init__(self, spec, frame):
        self.id = str(spec["id"])
        self.thr_lat, self.thr_lon = float(spec["thr"][0]), float(spec["thr"][1])
        self.elev_ft = float(spec.get("elev_ft", 0.0))
        self.tch_ft = float(spec.get("tch_ft", 50.0))
        self.gp_deg = float(spec.get("gp_deg", 3.0))
        self.aid = spec.get("aid", "") or ""
        self.tx, self.ty = frame.to_xy(self.thr_lat, self.thr_lon)
        ox, oy = frame.to_xy(float(spec["opp"][0]), float(spec["opp"][1]))
        dx, dy = ox - self.tx, oy - self.ty
        self.length_nm = math.hypot(dx, dy)          # threshold to far threshold
        self.course = geo.bearing_xy(dx, dy)          # true landing direction
        self.ux, self.uy = dx / self.length_nm, dy / self.length_nm
        # unit vector to the pilot's right
        self.rx, self.ry = self.uy, -self.ux
        self.far_x, self.far_y = ox, oy
        # where the pavement starts (behind a displaced threshold)
        back = float(spec.get("displaced_ft", 0.0)) / geo.FT_PER_NM
        self.start_x, self.start_y = self.tx - self.ux * back, self.ty - self.uy * back
        self._tan_gp = math.tan(math.radians(self.gp_deg))
        self.ft_per_nm = geo.FT_PER_NM * self._tan_gp
        # the glidepath meets the runway this far beyond the threshold
        self.gp_origin_nm = self.tch_ft / self.ft_per_nm
        self.fixes = []
        for f in spec.get("fixes", []):
            fx = Fix(f["name"], float(f["pos"][0]), float(f["pos"][1]))
            fx.x, fx.y = frame.to_xy(fx.lat, fx.lon)
            fx.dtg, _ = self.along_cross(fx.x, fx.y)
            self.fixes.append(fx)
        self.fixes.sort(key=lambda f: f.dtg)

    # --- geometry -----------------------------------------------------
    def along_cross(self, x, y):
        """(distance to go to the threshold, offset right of centreline), NM.

        Distance is positive while the aircraft still has to reach the
        threshold; the offset is positive when it is to the pilot's right.
        """
        dx, dy = x - self.tx, y - self.ty
        return -(dx * self.ux + dy * self.uy), dx * self.rx + dy * self.ry

    def point_at(self, dtg, xtk=0.0):
        return (self.tx - self.ux * dtg + self.rx * xtk,
                self.ty - self.uy * dtg + self.ry * xtk)

    def gp_alt_ft(self, dtg):
        """Glidepath altitude (ft AMSL) at a distance out from the threshold."""
        return self.elev_ft + self.tch_ft + dtg * self.ft_per_nm

    def path_angle_deg(self, dtg, alt_ft):
        """Angle from the glidepath origin up to the aircraft."""
        run_ft = (dtg + self.gp_origin_nm) * geo.FT_PER_NM
        if run_ft <= 1.0:
            return self.gp_deg
        return math.degrees(math.atan2(alt_ft - self.elev_ft, run_ft))

    def loc_angle_deg(self, dtg, xtk):
        """Angle off the centreline as a localiser at the far end would see it."""
        run = dtg + self.length_nm + 0.16
        if run <= 0.05:
            return 0.0
        return math.degrees(math.atan2(xtk, run))

    def headwind_crosswind(self, wind_dir_true, wind_kt):
        """(headwind, crosswind from the right) for a landing on this runway."""
        rel = math.radians(geo.ang_diff(wind_dir_true, self.course))
        return wind_kt * math.cos(rel), wind_kt * math.sin(rel)


class Airport(object):
    def __init__(self, path, spec=None):
        if spec is None:
            with open(path, "r", encoding="utf-8") as fh:
                spec = json.load(fh)
        self.spec = spec
        self.dir = os.path.dirname(os.path.abspath(path))
        self.icao = spec["icao"]
        self.iata = spec.get("iata", "")
        self.name = spec.get("name", self.icao)
        self.lat, self.lon = float(spec["arp"][0]), float(spec["arp"][1])
        self.elev_ft = float(spec.get("elev_ft", 0.0))
        self.mag_var = float(spec.get("mag_var_deg", 0.0))
        self.geoid_sep_ft = float(spec.get("geoid_sep_ft", 0.0))
        self.transition_alt_ft = float(spec.get("transition_alt_ft", 10000.0))
        self.curfew = spec.get("curfew")
        self.sea_name = spec.get("sea_name", "")
        self.frame = geo.LocalFrame(self.lat, self.lon)
        self.runways = {}
        self.runway_order = []
        for rs in spec["runways"]:
            rw = Runway(rs, self.frame)
            self.runways[rw.id] = rw
            self.runway_order.append(rw.id)
        # strips of pavement for drawing: pair each landing direction with the one that lands the
        # other way from its far end (for parallel runways the nearest such threshold is the partner)
        self.strips = []
        done = set()
        for rw in self.runways.values():
            if rw.id in done:
                continue
            best, best_d = None, 0.5
            for other in self.runways.values():
                if other is rw or other.id in done or abs(geo.ang_diff(other.course, rw.course + 180.0)) > 8.0:
                    continue
                d = math.hypot(other.tx - rw.far_x, other.ty - rw.far_y)
                if d < best_d:
                    best, best_d = other, d
            done.add(rw.id)
            if best is not None:
                done.add(best.id)
                self.strips.append(((rw.start_x, rw.start_y), (best.start_x, best.start_y)))
            else:
                self.strips.append(((rw.start_x, rw.start_y), (rw.far_x, rw.far_y)))
        self.nearby = []
        for n in spec.get("nearby", []):
            x, y = self.frame.to_xy(float(n["pos"][0]), float(n["pos"][1]))
            strips = []
            for s in n.get("strips", []):
                strips.append((self.frame.to_xy(s[0][0], s[0][1]), self.frame.to_xy(s[1][0], s[1][1])))
            self.nearby.append({"icao": n.get("icao", ""), "name": n.get("name", ""), "x": x, "y": y,
                                "elev_ft": float(n.get("elev_ft", 0.0)), "strips": strips})

    @classmethod
    def from_spec(cls, spec, folder="."):
        return cls(os.path.join(folder, "airport.json"), spec)

    def demo_home(self):
        """(name, lat, lon) of the example viewpoint used by --demo before setup."""
        d = self.spec.get("demo_home")
        if d:
            return str(d.get("name", "DEMO HOME")), float(d["lat"]), float(d["lon"])
        rw = self.runways[self.runway_order[0]]
        lat, lon = self.frame.to_ll(*rw.point_at(4.0, 1.2))
        return "DEMO HOME", lat, lon

    def to_mag(self, true_deg):
        return geo.norm360(true_deg - self.mag_var)

    def data_path(self, key):
        name = self.spec.get(key)
        return os.path.join(self.dir, name) if name else None
