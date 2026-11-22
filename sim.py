"""Simulated traffic for --demo and for the tests.

Produces records in exactly the shape the adsb.fi API returns (padded
callsigns, "ground" altitudes, ground vehicles, the odd broken value...) so the
demo exercises the same parsing and tracking code as live data does.
"""
import math
import random

import feed
import geo

# callsign, type, registration, origin (icao, iata, city), ADS-B category, arrival bearing from the field
DEMO_ARRIVALS = [
    ("QFA741", "B738", "VH-VZT", ("YMML", "MEL", "MELBOURNE"), "A3", 118),
    ("VOZ412", "B738", "VH-YIV", ("YMML", "MEL", "MELBOURNE"), "A3", 112),
    ("JST773", "A320", "VH-VQH", ("YSSY", "SYD", "SYDNEY"), "A3", 98),
    ("QFA882", "B738", "VH-VXM", ("YPPH", "PER", "PERTH"), "A3", 268),
    ("SIA279", "A359", "9V-SHB", ("WSSS", "SIN", "SINGAPORE"), "A5", 318),
    ("QLK2271", "DH8D", "VH-QOE", ("YPLC", "PLO", "PORT LINCOLN"), "A2", 262),
    ("RXA4381", "SF34", "VH-ZRL", ("YMTG", "MGB", "MOUNT GAMBIER"), "A2", 150),
    ("VOZ1392", "B738", "VH-YFT", ("YBBN", "BNE", "BRISBANE"), "A3", 62),
    ("UAE440", "B77W", "A6-EQP", ("OMDB", "DXB", "DUBAI"), "A5", 300),
    ("FD521", "PC12", "VH-FVD", ("YPAG", "PUG", "PORT AUGUSTA"), "A1", 340),
    ("QFA1456", "E190", "VH-UYB", ("YSCB", "CBR", "CANBERRA"), "A3", 92),
    ("QTR914", "A35K", "A7-ANP", ("OTHH", "DOH", "DOHA"), "A5", 296),
    ("UTY2901", "F100", "VH-UQG", ("YOLD", "OLP", "OLYMPIC DAM"), "A3", 338),
    ("JST587", "A21N", "VH-OFE", ("YBCG", "OOL", "GOLD COAST"), "A3", 70),
    ("ANZ191", "A20N", "ZK-NHA", ("NZAA", "AKL", "AUCKLAND"), "A3", 104),
]
DEMO_DEPARTURES = [
    ("QFA682", "B738", "VH-VZD", "A3", 105), ("VOZ233", "B738", "VH-YQR", "A3", 95),
    ("JST776", "A320", "VH-VFN", "A3", 80), ("QLK2272", "DH8D", "VH-QOP", "A2", 275),
]
DESCS = {"B738": "BOEING 737-800", "A320": "AIRBUS A-320", "A359": "AIRBUS A-350-900", "DH8D": "DE HAVILLAND DHC-8-400 Dash 8",
         "SF34": "SAAB 340", "B77W": "BOEING 777-300ER", "PC12": "PILATUS PC-12", "E190": "EMBRAER ERJ-190-100",
         "A35K": "AIRBUS A-350-1000", "F100": "FOKKER 100", "A21N": "AIRBUS A-321neo", "A20N": "AIRBUS A-320neo",
         "C172": "CESSNA 172 Skyhawk", "DA40": "DIAMOND DA-40 Diamond Star", "B789": "BOEING 787-9 Dreamliner"}
V_APP = {"B738": 142, "A320": 136, "A359": 142, "DH8D": 122, "SF34": 115, "B77W": 149, "PC12": 98, "E190": 132,
         "A35K": 147, "F100": 130, "A21N": 140, "A20N": 134, "C172": 68, "DA40": 72, "B789": 145}


def _steer(heading, want, max_step):
    d = geo.ang_diff(want, heading)
    if d > max_step:
        d = max_step
    elif d < -max_step:
        d = -max_step
    return (heading + d) % 360.0


class SimAircraft(object):
    def __init__(self, sim, hexid, flight, typ, reg, category):
        self.sim = sim
        self.hex = hexid
        self.flight = flight
        self.type = typ
        self.reg = reg
        self.category = category
        self.kind = "arrival"
        self.x = self.y = 0.0
        self.heading = 0.0
        self.gs = 250.0
        self.alt = 10000.0
        self.vrate = 0.0
        self.track_rate = 0.0
        self.on_ground = False
        self.done = False
        self.rw = None
        self.waypoints = []
        self.mode = "enroute"       # enroute | final | rollout | goaround | climbout | cruise | circuit
        self.join_dtg = 10.0
        self.level_alt = 3200.0
        self.v_app = float(V_APP.get(typ, 135))
        self.gp_noise = 0.0
        self.lat_bias = random.uniform(-0.02, 0.02)
        self.ground_time = 0.0
        self.will_go_around = False
        self.ga_done = False
        self.mlat = False
        self.sel_alt = 3000
        self.exit_bearing = 90.0
        self.cruise_alt = 36000.0
        self.circuit_centre = (0.0, 0.0)
        self.circuit_phase = random.uniform(0, 6.28)
        self.descent_k = 310.0
        self.age = 0.0

    # --- dynamics -------------------------------------------------------
    def step(self, dt):
        self.age += dt
        if self.mode == "circuit":
            self._step_circuit(dt)
            return
        old_heading, old_alt = self.heading, self.alt
        getattr(self, "_step_" + self.mode)(dt)
        v = self.gs / 3600.0
        r = math.radians(self.heading)
        self.x += v * dt * math.sin(r)
        self.y += v * dt * math.cos(r)
        self.track_rate = geo.ang_diff(self.heading, old_heading) / dt
        inst = (self.alt - old_alt) / dt * 60.0
        self.vrate += (inst - self.vrate) * min(1.0, dt / 4.0)      # what a real VSI reports: smoothed
        if math.hypot(self.x, self.y) > 70.0:
            self.done = True

    def _target_speed(self, miles_to_go):
        if miles_to_go > 32:
            return 285.0 if self.alt > 10500 else 250.0
        if miles_to_go > 16:
            return 250.0 - (32 - miles_to_go) / 16.0 * 40.0
        if miles_to_go > 9:
            return 210.0 - (16 - miles_to_go) / 7.0 * 30.0
        if miles_to_go > 4.5:
            return 180.0 - (9 - miles_to_go) / 4.5 * (180.0 - self.v_app - 8.0)
        return self.v_app + max(0.0, (miles_to_go - 3.0)) * 5.0

    def _headwind(self):
        w = self.sim.wind
        if w is None:
            return 0.0
        return w[1] * math.cos(math.radians(geo.ang_diff(w[0], self.heading)))

    def _step_enroute(self, dt):
        if not self.waypoints:
            self.mode = "final"
            return
        wx, wy = self.waypoints[0]
        dx, dy = wx - self.x, wy - self.y
        dist = math.hypot(dx, dy)
        if dist < max(0.7, self.gs / 250.0 * 1.3) and len(self.waypoints) > 1 or dist < 0.35:
            self.waypoints.pop(0)
            if not self.waypoints:
                self.mode = "final"
            return
        self.heading = _steer(self.heading, geo.bearing_xy(dx, dy), 3.0 * dt)
        remaining = dist
        for i in range(len(self.waypoints) - 1):
            remaining += math.hypot(self.waypoints[i + 1][0] - self.waypoints[i][0],
                                    self.waypoints[i + 1][1] - self.waypoints[i][1])
        miles = remaining + self.join_dtg
        tgt = min(self._target_speed(miles), 250.0 if self.alt < 10000 else 300.0)
        if self.type in ("PC12", "SF34", "DH8D"):
            tgt = min(tgt, 230.0 if self.type == "DH8D" else 200.0)
        tgt -= self._headwind()
        self.gs += max(-1.3 * dt, min(1.0 * dt, tgt - self.gs))
        want_alt = max(self.level_alt, self.level_alt + (miles - self.join_dtg - 2.0) * self.descent_k)
        if self.alt > want_alt:
            self.alt = max(want_alt, self.alt - 2400.0 / 60.0 * dt)
        self.sel_alt = int(round(self.level_alt / 100.0) * 100)

    def _step_final(self, dt):
        rw = self.rw
        dtg, xtk = rw.along_cross(self.x, self.y)
        want = rw.course - max(-28.0, min(28.0, (xtk - self.lat_bias) * 38.0))
        self.heading = _steer(self.heading, want, 3.0 * dt)
        tgt = self._target_speed(dtg) - self._headwind()
        self.gs += max(-1.4 * dt, min(0.8 * dt, tgt - self.gs))
        self.gp_noise += (-self.gp_noise * 0.05 + random.gauss(0, 1.6)) * dt
        gp = rw.gp_alt_ft(max(dtg, 0.0)) + self.gp_noise
        if self.alt > gp:
            self.alt = max(gp, self.alt - 1500.0 / 60.0 * dt) if self.alt - gp > 60 else gp
        elif self.alt < gp - 40 and dtg < 1.0:
            self.alt = gp
        if self.will_go_around and not self.ga_done and dtg < 0.9:
            self.mode, self.ga_done = "goaround", True
            return
        if dtg <= 0.0:
            # over the runway: flare down the last 50 ft and touch down ~300 m in
            self.alt = max(rw.elev_ft, min(self.alt, rw.elev_ft + rw.tch_ft * (1.0 + dtg / 0.17)))
        if dtg <= -0.17:
            self.mode = "rollout"
            self.on_ground = True
            self.alt = rw.elev_ft

    def _step_rollout(self, dt):
        self.heading = _steer(self.heading, self.rw.course, 2.0 * dt)
        self.gs = max(12.0, self.gs - 3.2 * dt)
        self.alt = self.rw.elev_ft
        self.ground_time += dt
        if self.ground_time > 55.0:
            self.done = True

    def _step_goaround(self, dt):
        rw = self.rw
        dtg, xtk = rw.along_cross(self.x, self.y)
        self.heading = _steer(self.heading, rw.course, 3.0 * dt)
        self.gs = min(200.0, self.gs + 1.6 * dt)
        self.alt = min(3000.0, self.alt + 2300.0 / 60.0 * dt)
        self.sel_alt = 3000
        if dtg < -(rw.length_nm + 3.0):
            side = 1.0 if self.lat_bias >= 0 else -1.0
            self.waypoints = [rw.point_at(-1.0, side * 5.5), rw.point_at(9.5, side * 5.5),
                              rw.point_at(11.5, side * 2.6), rw.point_at(9.0, 0.0)]
            self.join_dtg, self.level_alt = 9.0, 3000.0
            self.will_go_around = False
            self.mode = "enroute"

    def _step_climbout(self, dt):
        rw = self.rw
        if self.on_ground:
            self.heading = rw.course
            self.gs = min(165.0, self.gs + 4.2 * dt)
            if self.gs >= 150.0:
                self.on_ground = False
            return
        dtg, _ = rw.along_cross(self.x, self.y)
        if dtg < -(rw.length_nm + 2.5):
            self.heading = _steer(self.heading, self.exit_bearing, 3.0 * dt)
        self.alt = min(self.cruise_alt, self.alt + (2600.0 if self.alt < 10000 else 1800.0) / 60.0 * dt)
        cap = 250.0 if self.alt < 10000 else 320.0
        self.gs = min(cap, self.gs + 1.5 * dt)
        self.sel_alt = int(self.cruise_alt)

    def _step_cruise(self, dt):
        pass

    def _step_circuit(self, dt):
        cx, cy = self.circuit_centre
        self.circuit_phase += (self.gs / 3600.0) / 1.1 * dt
        self.x = cx + 1.4 * math.cos(self.circuit_phase)
        self.y = cy + 0.9 * math.sin(self.circuit_phase)
        self.heading = geo.bearing_xy(-1.4 * math.sin(self.circuit_phase), 0.9 * math.cos(self.circuit_phase))
        self.alt = 1050.0 + 150.0 * math.sin(self.circuit_phase * 0.5)
        self.vrate = 0.0
        self.track_rate = 2.0

    # --- output ---------------------------------------------------------
    def record(self):
        sim = self.sim
        seen = round(random.uniform(0.05, 1.3), 3)
        v = self.gs / 3600.0
        r = math.radians(self.heading)
        x = self.x - v * seen * math.sin(r)
        y = self.y - v * seen * math.cos(r)
        lat, lon = sim.airport.frame.to_ll(x, y)
        d = {"hex": self.hex, "type": "mlat" if self.mlat else "adsb_icao",
             "flight": (self.flight + "        ")[:8], "r": self.reg, "t": self.type,
             "lat": round(lat, 6), "lon": round(lon, 6), "nic": 8, "rc": 186, "seen_pos": seen,
             "messages": int(1000 + self.age * 7), "seen": min(seen, 0.3), "rssi": round(random.uniform(-22, -4), 1),
             "mlat": [], "tisb": [], "alert": 0, "spi": 0}
        if self.type in DESCS:
            d["desc"] = DESCS[self.type]
        if self.category:
            d["category"] = self.category
        qnh = sim.qnh
        if self.on_ground:
            d["alt_baro"] = "ground"
            d["gs"] = round(self.gs, 1)
            d["true_heading"] = round(self.heading, 2)
            d["track"] = round(self.heading, 2)
            d["squawk"] = self.squawk
            return d
        baro = self.alt - (-145366.45 * (1.0 - (qnh / 1013.25) ** 0.190284))
        d["alt_baro"] = int(round(baro / 25.0) * 25)
        hw, xw = 0.0, 0.0
        if sim.wind is not None:
            rel = math.radians(geo.ang_diff(sim.wind[0], self.heading))
            hw, xw = sim.wind[1] * math.cos(rel), sim.wind[1] * math.sin(rel)
        tas = max(50.0, self.gs + hw)
        crab = math.degrees(math.asin(max(-0.5, min(0.5, xw / tas))))
        if self.mlat:
            d["mlat"] = ["gs", "track", "baro_rate", "lat", "lon", "nic", "rc"]
            d["gs"] = round(self.gs)
            d["track"] = round(self.heading)
            d["baro_rate"] = int(round(self.vrate / 64.0) * 64)
            d["squawk"] = self.squawk
            return d
        d["alt_geom"] = int(round((self.alt + sim.airport.geoid_sep_ft) / 25.0) * 25)
        d["gs"] = round(self.gs, 1)
        d["track"] = round(self.heading, 2)
        d["track_rate"] = round(self.track_rate, 2)
        d["baro_rate"] = int(round(self.vrate / 64.0) * 64)
        d["geom_rate"] = int(round(self.vrate / 32.0) * 32)
        d["true_heading"] = round((self.heading + crab) % 360.0, 2)
        d["mag_heading"] = round((self.heading + crab - sim.airport.mag_var) % 360.0, 2)
        d["tas"] = int(round(tas))
        d["ias"] = int(round(tas / (1.0 + self.alt / 1000.0 * 0.0165)))
        d["mach"] = round(tas / 661.0 * (1.0 + self.alt / 100000.0), 3)
        d["roll"] = round(max(-30.0, min(30.0, self.track_rate * 9.0)), 2)
        d["squawk"] = self.squawk
        d["emergency"] = "none"
        d["nav_qnh"] = round(qnh / 0.8) * 0.8 if self.alt < 11000 else 1013.6
        d["nav_altitude_mcp"] = int(self.sel_alt)
        if sim.wind is not None and self.alt > 300 and self.kind != "circuit":
            d["wd"] = int(round(sim.wind[0] + random.uniform(-4, 4))) % 360
            d["ws"] = int(round(sim.wind[1] * (1.0 + self.alt / 9000.0) + random.uniform(-1, 1)))
        if self.category in ("A3", "A5", "A2"):
            if self.mode == "final":
                d["nav_modes"] = ["autopilot", "approach", "tcas"] if self.alt > 900 else ["approach", "tcas"]
            elif self.mode in ("enroute", "climbout", "cruise"):
                d["nav_modes"] = ["autopilot", "vnav", "lnav", "tcas"]
            else:
                d["nav_modes"] = ["tcas"]
        return d

    @property
    def squawk(self):
        return "%04o" % (int(self.hex[-3:], 16) % 4096)


class Sim(object):
    """A little world of traffic around the airport."""

    def __init__(self, airport, runway_id="23", seed=None, wind=(240.0, 12.0), qnh=1016.0):
        self.airport = airport
        self.rw = airport.runways[runway_id]
        self.wind = wind
        self.qnh = qnh
        self.t = None
        self.aircraft = []
        self.rng = random.Random(seed)
        if seed is not None:
            random.seed(seed)
        self._n = 0
        self._arr_i = 0
        self._dep_i = 0
        self._next_arrival = 0.0
        self._next_departure = 0.0
        self.routes = {}
        self.auto_traffic = True
        self.go_around_every = 0     # every Nth arrival goes around (0 = never)

    def _hex(self):
        self._n += 1
        return "7c%04x" % (0x1a00 + self._n * 37)

    # --- spawning -------------------------------------------------------
    def add_arrival(self, spec=None, dist_nm=None, on_final_dtg=None, go_around=False):
        if spec is None:
            spec = DEMO_ARRIVALS[self._arr_i % len(DEMO_ARRIVALS)]
            self._arr_i += 1
        flight, typ, reg, origin, cat, brg = spec
        if any(a.flight == flight for a in self.aircraft):
            flight = "%s%d" % (flight[:3], 100 + self.rng.randint(0, 899))
        ac = SimAircraft(self, self._hex(), flight, typ, reg, cat)
        ac.kind, ac.rw = "arrival", self.rw
        ac.will_go_around = go_around
        self.routes[flight] = origin
        rw = self.rw
        if on_final_dtg is not None:
            ac.x, ac.y = rw.point_at(on_final_dtg, ac.lat_bias)
            ac.heading = rw.course
            ac.mode = "final"
            ac.gs = ac._target_speed(on_final_dtg)
            ac.alt = min(rw.gp_alt_ft(on_final_dtg), 3200.0 if on_final_dtg > 9.9 else 1e9)
            ac.join_dtg = on_final_dtg
        else:
            d = dist_nm if dist_nm is not None else 38.0
            sx, sy = geo.unit_from_bearing(brg)
            ac.x, ac.y = sx * d, sy * d
            join = self.rng.choice((9.0, 10.0, 10.0, 12.0))
            dtg0, xtk0 = rw.along_cross(ac.x, ac.y)
            side = 1.0 if xtk0 >= 0 else -1.0
            wps = []
            if dtg0 < join + 1.0:
                # coming from "behind" the approach: fly a downwind leg first
                wps.append(rw.point_at(min(dtg0, 1.0), side * 6.0))
                wps.append(rw.point_at(join + 2.5, side * 6.0))
            wps.append(rw.point_at(join + 3.2, side * 3.2))
            wps.append(rw.point_at(join, 0.0))
            ac.waypoints = wps
            ac.join_dtg = join
            ac.level_alt = 3200.0 if join >= 10 else 3000.0
            ac.heading = geo.bearing_xy(wps[0][0] - ac.x, wps[0][1] - ac.y)
            path = math.hypot(wps[0][0] - ac.x, wps[0][1] - ac.y) + sum(
                math.hypot(wps[i + 1][0] - wps[i][0], wps[i + 1][1] - wps[i][1]) for i in range(len(wps) - 1)) + join
            ac.alt = min(14500.0, ac.level_alt + max(0.0, path - join - 2.0) * 310.0)
            # descend steadily all the way in, however long the route is
            ac.descent_k = min(310.0, (ac.alt - ac.level_alt) / max(1.0, path - join - 2.0))
            ac.gs = min(ac._target_speed(path), 250.0 if ac.alt < 10000 else 290.0)
            if typ in ("PC12", "SF34", "DH8D"):
                ac.gs = min(ac.gs, 230.0 if typ == "DH8D" else 200.0)
        self.aircraft.append(ac)
        return ac

    def add_departure(self, spec=None):
        if spec is None:
            spec = DEMO_DEPARTURES[self._dep_i % len(DEMO_DEPARTURES)]
            self._dep_i += 1
        flight, typ, reg, cat, exit_brg = spec
        ac = SimAircraft(self, self._hex(), flight, typ, reg, cat)
        ac.kind, ac.rw = "departure", self.rw
        ac.x, ac.y = self.rw.start_x, self.rw.start_y
        ac.heading = self.rw.course
        ac.gs, ac.alt, ac.on_ground = 8.0, self.rw.elev_ft, True
        ac.mode = "climbout"
        ac.exit_bearing = exit_brg
        ac.cruise_alt = 36000.0
        self.aircraft.append(ac)
        return ac

    def add_overflight(self, flight="QFA9", typ="B789", reg="VH-ZNA", brg=95.0, offset=12.0, alt=38000.0):
        ac = SimAircraft(self, self._hex(), flight, typ, reg, "A5")
        ac.kind, ac.mode = "overflight", "cruise"
        ux, uy = geo.unit_from_bearing(brg)
        ac.x, ac.y = -ux * 55.0 + uy * offset, -uy * 55.0 - ux * offset
        ac.heading, ac.gs, ac.alt = brg, 470.0, alt
        ac.sel_alt = int(alt)
        self.aircraft.append(ac)
        return ac

    def add_circuit(self, flight="XKD", reg="VH-XKD", typ="C172", centre=None, mlat=False):
        ac = SimAircraft(self, self._hex(), flight, typ, reg, "A1")
        ac.kind, ac.mode = "circuit", "circuit"
        ac.gs = 88.0
        ac.mlat = mlat
        nb = self.airport.nearby[0] if self.airport.nearby else {"x": 5.0, "y": 9.0}
        ac.circuit_centre = centre or (nb["x"], nb["y"])
        ac._step_circuit(0.0)
        self.aircraft.append(ac)
        return ac

    def populate(self):
        """A busy-but-believable starting picture for demo mode."""
        self.add_arrival(on_final_dtg=6.4)
        self.add_arrival(on_final_dtg=12.5)
        self.add_arrival(dist_nm=24.0)
        self.add_arrival(dist_nm=34.0)
        self.add_departure()
        self.add_overflight()
        self.add_circuit()
        self.add_circuit(flight="YTQ", reg="VH-YTQ", typ="DA40", mlat=True)
        self._next_arrival = 150.0
        self._next_departure = 200.0

    # --- time -----------------------------------------------------------
    def step_to(self, t):
        if self.t is None:
            self.t = t
            self._t0 = t
            return
        remaining = min(t - self.t, 120.0)
        while remaining > 1e-6:
            dt = min(0.5, remaining)
            for ac in self.aircraft:
                ac.step(dt)
            remaining -= dt
        self.t = t
        self.aircraft = [a for a in self.aircraft if not a.done]
        if self.auto_traffic:
            el = t - self._t0
            if el >= self._next_arrival:
                n_arr = sum(1 for a in self.aircraft if a.kind == "arrival")
                if n_arr < 5:
                    ga = bool(self.go_around_every) and (self._arr_i + 1) % self.go_around_every == 0
                    self.add_arrival(go_around=ga)
                self._next_arrival = el + self.rng.uniform(150.0, 260.0)
            if el >= self._next_departure:
                self.add_departure()
                self._next_departure = el + self.rng.uniform(240.0, 420.0)

    def snapshot(self, t):
        self.step_to(t)
        ac = [a.record() for a in self.aircraft]
        # the junk a real feed contains: a tower beacon and a tug
        lat, lon = self.airport.lat, self.airport.lon
        ac.append({"hex": "7cf901", "type": "adsb_icao_nt", "flight": "        ", "r": "TWR", "t": "TWR",
                   "alt_baro": "ground", "category": "C1", "lat": lat + 0.001, "lon": lon + 0.002, "seen_pos": 0.5,
                   "seen": 0.5, "rssi": -5.0, "mlat": [], "tisb": [], "messages": 99})
        ac.append({"hex": "7cf902", "type": "adsb_icao_nt", "flight": "TUG7    ", "r": "GND", "t": "GND",
                   "alt_baro": "ground", "gs": 9.5, "track": 140.0, "category": "C2", "lat": lat - 0.002,
                   "lon": lon + 0.004, "seen_pos": 0.7, "seen": 0.7, "rssi": -6.0, "mlat": [], "tisb": [], "messages": 12})
        return {"ac": ac, "msg": "No error", "now": int(t * 1000), "total": len(ac), "ctime": int(t * 1000), "ptime": 0}


class SimSource(object):
    """Drop-in replacement for the HTTP source."""
    label = "SIMULATED TRAFFIC"

    def __init__(self, sim, clock, mil_prefixes=()):
        self.sim = sim
        self.clock = clock
        self.mil_prefixes = mil_prefixes

    def fetch(self):
        return feed.parse_snapshot(self.sim.snapshot(self.clock()), self.mil_prefixes)
