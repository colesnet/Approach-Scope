"""Self-tests.  Run from the project folder:   python3 -m unittest discover -s tests -v

Nothing here touches the network or needs a screen: live traffic is replaced by
the simulator (which emits records in the exact adsb.fi format) and by a real
adsb.fi response captured from the API.
"""
import copy
import json
import math
import os
import sys
import unittest

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import airport      # noqa: E402
import config       # noqa: E402
import enrich       # noqa: E402
import feed         # noqa: E402
import refdata      # noqa: E402
import sim          # noqa: E402
import tracker      # noqa: E402

YPAD = os.path.join(ROOT, "data", "airport_ypad.json")
HOME = (-34.871, 138.61)            # the built-in demo viewpoint (a public example, not anyone's home)
NO_CONFIG = os.path.join(HERE, "fixtures", "no-such-config.ini")   # tests never read a real config.ini


class Clock(object):
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def fixture(name):
    with open(os.path.join(HERE, "fixtures", name), "r", encoding="utf-8") as fh:
        return json.load(fh)


class World(object):
    """Simulator + feed + tracker on a hand-driven clock."""

    def __init__(self, runway="23", seed=1, wind=(240.0, 12.0), auto=False, populate=False, ap=None, home=HOME):
        self.ap = ap or airport.Airport(YPAD)
        self.ref = refdata.RefData(os.path.join(ROOT, "data"))
        self.home = tracker.HomeGeometry(self.ap, home[0], home[1], 55.0, "HOME")
        self.clock = Clock()
        self.trk = tracker.Tracker(self.ap, self.home, runway, self.ref, wall_clock=lambda: 1.7e9 + self.clock.t)
        self.sim = sim.Sim(self.ap, runway, seed=seed, wind=wind)
        self.sim.auto_traffic = auto
        if populate:
            self.sim.populate()
        self.feed = feed.Feed(sim.SimSource(self.sim, self.clock), 5.0, clock=self.clock)
        self.seq = 0
        self.seen = {}        # hex -> set of phases

    def run(self, seconds, mangle=None):
        end = self.clock.t + seconds
        while self.clock.t < end:
            self.clock.t += 1.0
            if int(self.clock.t) % 5 == 0:
                if mangle is not None:
                    original = self.sim.snapshot
                    self.sim.snapshot = lambda t: mangle(original(t))
                    ok = self.feed.poll_once()
                    self.sim.snapshot = original
                else:
                    ok = self.feed.poll_once()
                assert ok, self.feed.last_error
                snap = self.feed.take(self.seq)
                self.seq = snap.seq
                self.trk.ingest(snap)
            self.trk.tick(self.clock.t)
            self.trk.featured(self.clock.t)
            for hexid, t in self.trk.tracks.items():
                self.seen.setdefault(hexid, set()).add(t.phase)


class GeometryTests(unittest.TestCase):
    def setUp(self):
        self.ap = airport.Airport(YPAD)

    def test_runway_directions(self):
        want = {"23": 230.0, "05": 50.0, "12": 123.6, "30": 303.6}
        for rid, course in want.items():
            self.assertAlmostEqual(self.ap.runways[rid].course, course, delta=0.3)
        # 3100 m of runway; runway 05's landing threshold is displaced 150 m along it
        self.assertAlmostEqual(self.ap.runways["23"].length_nm * 1852, 3100, delta=15)
        self.assertAlmostEqual(self.ap.runways["05"].length_nm * 1852, 3100 - 150, delta=15)
        # 230 true is the published 222 magnetic
        self.assertAlmostEqual(self.ap.to_mag(self.ap.runways["23"].course), 222, delta=1.0)

    def test_glidepath_heights(self):
        rw = self.ap.runways["23"]
        self.assertAlmostEqual(rw.gp_alt_ft(0.0), 67, delta=1)
        self.assertAlmostEqual(rw.gp_alt_ft(10.0), 67 + 3184, delta=15)       # 318 ft per NM
        self.assertAlmostEqual(rw.path_angle_deg(5.0, rw.gp_alt_ft(5.0)), 3.0, delta=0.02)

    def test_frame_round_trip_and_range(self):
        fr = self.ap.frame
        for lat, lon in (HOME, (-34.40, 139.20), (-35.6, 137.9)):
            x, y = fr.to_xy(lat, lon)
            la, lo = fr.to_ll(x, y)
            self.assertAlmostEqual(la, lat, places=6)
            self.assertAlmostEqual(lo, lon, places=6)
        # 45 NM NE of the field, against an independent ellipsoidal solution (pyproj)
        x, y = fr.to_xy(-34.40, 139.20)
        self.assertAlmostEqual(math.hypot(x, y), 46.42, delta=0.1)

    def test_where_home_sits(self):
        home = tracker.HomeGeometry(self.ap, HOME[0], HOME[1], 55.0, "HOME")
        dtg, xtk = home.abeam["23"]
        self.assertAlmostEqual(dtg, 5.20, delta=0.05)
        self.assertAlmostEqual(abs(xtk) * 1.852, 2.02, delta=0.05)
        self.assertTrue(home.passes("23"))
        self.assertFalse(home.passes("05"))
        rw = self.ap.runways["23"]
        px, py = rw.point_at(dtg)
        az, el, slant, _g = home.look(px, py, rw.gp_alt_ft(dtg))
        self.assertAlmostEqual(az, 140, delta=2)       # south-east
        self.assertAlmostEqual(el, 13.1, delta=0.4)
        self.assertAlmostEqual(slant, 2.08, delta=0.1)


class FeedTests(unittest.TestCase):
    def test_real_adsbfi_response(self):
        reports, raw = feed.parse_snapshot(fixture("adsbfi_v3_sample.json"), ("7CF",))
        self.assertEqual(raw, 21)
        by = {r.flight: r for r in reports if r.flight}
        self.assertNotIn("PTACNIK2", by)                       # airport ground vehicle
        self.assertFalse(any(r.type in ("TWR", "GND") for r in reports))
        self.assertEqual(by["ICE536"].type, "B38M")
        self.assertEqual(by["ICE536"].alt_baro, 4075)
        self.assertEqual(by["ICE536"].src, "ADS-B")
        self.assertTrue(by["UAE42X"].on_ground)
        self.assertIsNone(by["UAE42X"].alt_baro)
        self.assertEqual(by["OKAQK"].src, "MLAT")
        self.assertIsNone(by["OKMAF"].gs)                      # an 1820 kt Cessna is a glitch, not a speed
        self.assertEqual(by["DFC27M"].nav_modes, ("approach",))
        self.assertFalse(any(r.seen_pos > 60 for r in reports))

    def test_rubbish_is_survivable(self):
        for bad in (None, [], "x", {"ac": "nope"}, {"msg": "hi"}):
            with self.assertRaises(ValueError):
                feed.parse_snapshot(bad)
        junk = {"ac": [None, 5, {}, {"hex": "~abc123", "lat": 1, "lon": 1}, {"hex": "7c0001"},
                       {"hex": "7c0002", "lat": "x", "lon": 3}, {"hex": "7c0003", "lat": 95.0, "lon": 3},
                       {"hex": "7c0004", "lat": -34.9, "lon": 138.5, "alt_baro": "ground", "gs": None,
                        "flight": 17, "squawk": 7700, "nav_modes": "approach", "seen_pos": "soon"},
                       {"hex": "7c0005", "lat": -34.9, "lon": 138.5, "alt_baro": 1e9, "gs": -4, "track": 725,
                        "baro_rate": float("nan"), "flight": "QFéA1  "}]}
        reports, raw = feed.parse_snapshot(junk)
        self.assertEqual(raw, 9)
        self.assertEqual([r.hex for r in reports], ["7c0004", "7c0005"])
        self.assertIsNone(reports[1].alt_baro)
        self.assertIsNone(reports[1].gs)
        self.assertEqual(reports[1].track, 5.0)
        self.assertEqual(reports[1].flight, "QFA1")

    def test_tar1090_style_list_is_accepted(self):
        reports, raw = feed.parse_snapshot({"now": 1.0, "aircraft": [
            {"hex": "7c6b2d", "flight": "QFA741  ", "lat": -34.9, "lon": 138.6, "alt_baro": 2000, "gs": 150,
             "track": 230, "seen_pos": 1.0}]})
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0].flight, "QFA741")

    def test_feed_reports_failures_without_raising(self):
        class Broken(object):
            label = "X"

            def fetch(self):
                raise RuntimeError("boom")
        f = feed.Feed(Broken(), 5.0, clock=Clock())
        self.assertFalse(f.poll_once())
        self.assertEqual(f.state, "RETRYING")
        self.assertEqual(f.last_error, "boom")
        self.assertIsNone(f.take(0))


TABLES = refdata.RefData(os.path.join(ROOT, "data")).complete


class ReferenceTests(unittest.TestCase):
    """The full type/airline tables are downloaded by setup (not shipped); these run once they are there."""

    def setUp(self):
        self.ref = refdata.RefData(os.path.join(ROOT, "data"))
        if not self.ref.complete:
            self.skipTest("aircraft tables not downloaded (run setup)")

    def test_tables_loaded(self):
        self.assertEqual(self.ref.warnings, [])
        self.assertGreater(len(self.ref.types), 2000)

    def test_callsigns(self):
        a = self.ref.airline("QFA741")
        self.assertEqual((a["name"], a["flight_iata"], a["telephony"]), ("QANTAS", "QF741", "QANTAS 741"))
        self.assertEqual(self.ref.airline("VOZ412")["telephony"], "VELOCITY 412")
        self.assertEqual(self.ref.airline("FD521")["name"], "ROYAL FLYING DOCTOR SERVICE")
        self.assertEqual(self.ref.airline("QLK442D")["flight_iata"], "")     # suffix: not a ticketed number
        for not_airline in ("VHABC", "", "N123AB", "PTACNIK2"):
            self.assertIsNone(self.ref.airline(not_airline))

    def test_types(self):
        self.assertEqual(self.ref.type_name("B738"), "BOEING 737-800")
        self.assertEqual(self.ref.wake_label("A388"), "SUPER")
        self.assertEqual(self.ref.wake_label("B789"), "HEAVY")
        shapes = {"B738": "jet", "A359": "jet_wide", "A388": "jet_quad", "F100": "jet_rear", "DH8D": "prop2",
                  "PC12": "prop1", "C172": "piston1", "A139": "heli", "": "jet", "ZZZZ": "jet"}
        for code, shape in shapes.items():
            self.assertEqual(self.ref.shape_class(code), shape, code)


class EnrichTests(unittest.TestCase):
    def test_metar_json(self):
        m = enrich.parse_metar_json(fixture("metar_sample.json"))
        self.assertEqual((m.wind_dir, m.wind_kt), (320.0, 6.0))
        self.assertAlmostEqual(m.qnh, 1021.1)
        self.assertEqual(m.wind_text(), "320/6KT")

    def test_metar_text(self):
        m = enrich.parse_metar_text("SPECI YPAD 130530Z 24018G30KT 6000 -SHRA FEW012 BKN025 OVC040 14/11 Q1009 RMK RF00.4")
        self.assertEqual((m.wind_dir, m.wind_kt, m.gust_kt, m.qnh, m.temp), (240.0, 18.0, 30.0, 1009.0, 14.0))
        self.assertEqual(m.sky, "VIS 6.0KM -SHRA BKN025")
        self.assertEqual(enrich.parse_metar_text("METAR YPAD 131100Z 23012KT CAVOK 18/09 Q1016").sky, "CAVOK")
        calm = enrich.parse_metar_text("METAR YPAD 132000Z 00000KT 9999 FEW040 12/10 Q1021")
        self.assertEqual(calm.wind_text(), "CALM")
        self.assertIsNone(enrich.parse_metar_text("METAR YPAD 130100Z VRB03KT CAVOK 31/M02 Q1012").wind_dir)
        self.assertIsNone(enrich.parse_metar_json([]))
        self.assertIsNone(enrich.parse_metar_json({"x": 1}))

    def test_routes_only_trusted_when_they_end_here(self):
        def reply(origin, dest, mid=None):
            def a(code):
                return {"icao_code": code, "iata_code": code[1:], "municipality": code, "name": code}
            fr = {"callsign": "QFA1", "callsign_iata": "QF1", "airline": {"name": "Qantas"},
                  "origin": a(origin), "destination": a(dest)}
            if mid:
                fr["midpoint"] = a(mid)
            return {"response": {"flightroute": fr}}
        self.assertEqual(enrich.parse_route(reply("YMML", "YPAD"), "YPAD").origin_city, "MELBOURNE")
        self.assertIsNone(enrich.parse_route(reply("YPAD", "YMML"), "YPAD"))       # the return leg
        self.assertIsNone(enrich.parse_route(reply("YMML", "YSSY"), "YPAD"))       # a different route entirely
        self.assertEqual(enrich.parse_route(reply("WSSS", "YSSY", "YPAD"), "YPAD").origin_icao, "WSSS")
        for junk in (None, {}, {"response": "unknown callsign"}, {"response": {"flightroute": None}}):
            self.assertIsNone(enrich.parse_route(junk, "YPAD"))


class TrackerTests(unittest.TestCase):
    def test_arrival_followed_all_the_way_down(self):
        w = World(seed=7)
        ac = w.sim.add_arrival(sim.DEMO_ARRIVALS[0], dist_nm=36.0)          # QFA741 from the south-east
        phases = []
        eta_at = {}
        t_down = None
        for _ in range(1300):
            w.run(1)
            t = w.trk.tracks.get(ac.hex)
            if t is None:
                continue
            if not phases or phases[-1] != t.phase:
                phases.append(t.phase)
            if t.phase == tracker.FINAL:
                self.assertEqual(t.rwy.id, "23")
                for mark in (8, 4, 2):
                    if mark not in eta_at and t.dtg <= mark:
                        eta_at[mark] = t.t_td
            if t.phase == tracker.LANDED and t_down is None:
                t_down = t.landed_at
                break
        self.assertEqual(phases, [tracker.OTHER, tracker.INBOUND, tracker.FINAL, tracker.LANDED])
        self.assertEqual([(l.flight, l.rwy) for l in w.trk.landings], [("QFA741", "23")])
        self.assertLess(abs(eta_at[8] - t_down), 25)
        self.assertLess(abs(eta_at[4] - t_down), 12)
        self.assertLess(abs(eta_at[2] - t_down), 6)
        self.assertEqual((w.trk.active_id, w.trk.active_basis), ("23", "TRAFFIC"))

    def test_only_arrivals_are_called_arrivals(self):
        """An hour of mixed traffic on each runway: departures, overflights and
        circuit trainers at Parafield must never be shown as inbound."""
        arrival_phases = {tracker.INBOUND, tracker.FINAL, tracker.LANDED, tracker.GO_AROUND}
        for runway, seed in (("23", 3), ("05", 11), ("12", 5), ("30", 8)):
            w = World(runway=runway, seed=seed, auto=True, populate=True)
            w.sim.go_around_every = 4
            kinds = {}
            for _ in range(720):
                for a in w.sim.aircraft:
                    kinds[a.hex] = a.kind
                w.run(5)
            wrong = [(kinds[h], sorted(p)) for h, p in w.seen.items()
                     if kinds.get(h) in ("departure", "overflight", "circuit") and p & arrival_phases]
            self.assertEqual(wrong, [], "runway %s" % runway)
            self.assertGreaterEqual(len(w.trk.landings), 8, "runway %s" % runway)
            self.assertTrue(all(l.rwy == runway for l in w.trk.landings))
            self.assertEqual(len(set((l.hex, round(l.wall / 600)) for l in w.trk.landings)), len(w.trk.landings))
            self.assertEqual(w.trk.active_id, runway)
            self.assertTrue(any(k == "GO-AROUND" for _t, k, _x in w.trk.events), "runway %s" % runway)

    def test_go_around_is_not_logged_as_a_landing(self):
        w = World(seed=3)
        ac = w.sim.add_arrival(sim.DEMO_ARRIVALS[2], on_final_dtg=3.0, go_around=True)
        w.run(150)
        self.assertIn(tracker.GO_AROUND, w.seen[ac.hex])
        self.assertEqual(w.trk.landings, [])
        w.run(900)                                    # ... and it lands off the second approach
        self.assertEqual([l.flight for l in w.trk.landings], ["JST773"])
        self.assertEqual(w.trk.tracks[ac.hex].ga_count if ac.hex in w.trk.tracks else 1, 1)

    def test_landing_assumed_when_signal_is_lost_low_down(self):
        def drop_low(obj):          # no receiver hears anything below 350 ft
            obj["ac"] = [a for a in obj["ac"]
                         if isinstance(a.get("alt_baro"), (int, float)) and a["alt_baro"] > 350]
            return obj
        w = World(seed=21)
        w.sim.add_arrival(sim.DEMO_ARRIVALS[1], on_final_dtg=5.0)
        w.run(300, mangle=drop_low)
        self.assertEqual([(l.flight, l.rwy) for l in w.trk.landings], [("VOZ412", "23")])

    def test_real_traffic_snapshot(self):
        """Genuine adsb.fi data (Prague): two aircraft were on final for runway 24."""
        ap = airport.Airport(os.path.join(HERE, "fixtures", "airport_lkpr.json"))
        home = tracker.HomeGeometry(ap, 50.08, 14.42, 300.0, "HOME")
        trk = tracker.Tracker(ap, home, "24", refdata.RefData(os.path.join(ROOT, "data")), wall_clock=lambda: 0.0)
        first = fixture("adsbfi_v3_sample.json")
        second = copy.deepcopy(first)
        for a in second["ac"]:      # the same aircraft five seconds later
            gs, trk_deg = a.get("gs"), a.get("track")
            if isinstance(gs, (int, float)) and isinstance(trk_deg, (int, float)) and a.get("alt_baro") != "ground" \
                    and gs < 700:
                d = gs * 5 / 3600.0
                a["lat"] += d * math.cos(math.radians(trk_deg)) / 60.0
                a["lon"] += d * math.sin(math.radians(trk_deg)) / (60.0 * math.cos(math.radians(a["lat"])))
        for seq, (obj, t) in enumerate(((first, 100.0), (second, 105.0)), 1):
            reports, raw = feed.parse_snapshot(obj)
            trk.ingest(feed.Snapshot(seq, t, reports, raw))
        trk.tick(105.0)
        by = {t.label: t for t in trk.tracks.values()}
        for cs, dtg in (("DFC27M", 1.96), ("ICE536", 8.0)):
            self.assertEqual(by[cs].phase, tracker.FINAL, cs)
            self.assertEqual(by[cs].rwy.id, "24")
            self.assertAlmostEqual(by[cs].dtg, dtg, delta=0.25)
            self.assertLess(abs(by[cs].xtk), 0.1)
        self.assertEqual(by["LOT3FG"].phase, tracker.INBOUND)
        self.assertEqual(by["UAE42X"].phase, tracker.GROUND)
        for climbing_out in ("BEL47P", "EXS9C", "QTR193"):
            self.assertEqual(by[climbing_out].phase, tracker.OTHER, climbing_out)
        self.assertAlmostEqual(trk.qnh, 1007.2, delta=0.1)       # worked out from what the crews have set
        feat, arr = trk.featured(105.0)
        self.assertEqual(feat.label, "DFC27M")
        self.assertEqual([a.label for a in arr][:2], ["DFC27M", "ICE536"])

    def test_time_to_go_model(self):
        self.assertAlmostEqual(tracker.time_to_go_s(2.0, 0.0, 138, 138), 2.0 / 138 * 3600, delta=0.5)
        self.assertEqual(tracker.time_to_go_s(1.0, 3.0, 150, 135), 0.0)
        slowing = tracker.time_to_go_s(10.0, 0.0, 180, 135)
        self.assertGreater(slowing, 10.0 / 180 * 3600)
        self.assertLess(slowing, 10.0 / 135 * 3600)


class LogAndReviewTests(unittest.TestCase):
    def test_log_round_trip_with_go_arounds(self):
        import tempfile
        import app
        clk = Clock()
        cfg = config.Config(NO_CONFIG)
        a = app.App(cfg, demo=False, headless=True, clock=clk, wall=lambda: 1794620000.0 + clk.t,
                    log=lambda *_: None)
        a.keep_log = True
        a.log_path = os.path.join(tempfile.mkdtemp(), "arrivals_log.json")
        a.tracker.landings = [tracker.Landing(1794620000.0, "QFA741", "B738", "VH-VZT", "23", "7c1", "MEL")]
        a.tracker.go_arounds = [tracker.Landing(1794619000.0, "JST773", "A320", "VH-VQH", "23", "7c2", low=410.0)]
        a._save_landings()
        a.tracker.landings, a.tracker.go_arounds = [], []
        a._load_landings()
        self.assertEqual([l.flight for l in a.tracker.landings], ["QFA741"])
        self.assertEqual([(g.flight, g.low) for g in a.tracker.go_arounds], [("JST773", 410.0)])
        with open(a.log_path, "w") as fh:          # the version 1 file format still loads
            json.dump([{"t": 1794620000.0, "flight": "VOZ412", "rwy": "23"}], fh)
        a._load_landings()
        self.assertEqual([l.flight for l in a.tracker.landings], ["VOZ412"])

    def test_operating_day_and_curfew(self):
        import time as _t
        from ui import panels
        cf = {"start": "23:00", "end": "06:00"}
        late = _t.mktime((2026, 11, 15, 0, 49, 0, 0, 0, -1))
        self.assertEqual(panels.day_start(late, cf), _t.mktime((2026, 11, 14, 6, 0, 0, 0, 0, -1)))
        self.assertTrue(panels.in_curfew(late, cf))
        self.assertFalse(panels.in_curfew(_t.mktime((2026, 11, 14, 12, 0, 0, 0, 0, -1)), cf))

    def test_go_around_is_recorded(self):
        w = World(seed=3)
        w.sim.add_arrival(sim.DEMO_ARRIVALS[2], on_final_dtg=3.0, go_around=True)
        w.run(150)
        self.assertEqual([g.flight for g in w.trk.go_arounds], ["JST773"])
        self.assertGreater(w.trk.go_arounds[0].low, 0)


class WorldTests(unittest.TestCase):
    """Any airport in the world, from the bundled public data."""

    def test_magnetic_variation_matches_the_world_magnetic_model(self):
        import worldgen
        # reference values from NOAA's WMM2025 (pygeomag), late 2026
        for lat, lon, want in ((-34.95, 138.53, 8.10), (51.47, -0.45, 1.13), (33.94, -118.41, 11.34),
                               (64.13, -21.94, -11.01), (-37.0, 174.8, 20.44), (-75.0, 120.0, -140.05)):
            self.assertAlmostEqual(worldgen.declination(lat, lon, 2026.9), want, delta=0.02)

    def test_geoid(self):
        import worldgen
        self.assertAlmostEqual(worldgen.geoid_m(-34.95, 138.53), -1.0, delta=1.0)
        self.assertAlmostEqual(worldgen.geoid_m(50.10, 14.26), 45.3, delta=1.0)
        self.assertAlmostEqual(worldgen.geoid_m(28.5, 77.1), -52.3, delta=1.5)

    def test_generated_runways_match_the_survey(self):
        import worldgen
        import geo
        spec = worldgen.airport_spec(worldgen.find_airport("YPAD"))
        ref = json.load(open(YPAD))
        fr = geo.LocalFrame(*ref["arp"])
        for r in spec["runways"]:
            want = [x for x in ref["runways"] if x["id"] == r["id"]][0]
            a, b = fr.to_xy(*r["thr"]), fr.to_xy(*want["thr"])
            self.assertLess(math.hypot(a[0] - b[0], a[1] - b[1]) * 1852, 40, r["id"])
        self.assertEqual(spec["name"], "ADELAIDE")

    def test_airports_everywhere(self):
        import worldgen
        for code, name, n in (("KLAX", "LOS ANGELES", 8), ("EGLL", "LONDON", 4), ("SBGR", "SAO PAULO", 4),
                              ("RJTT", "TOKYO", 8), ("NFFN", "NADI", 4), ("KDEN", "DENVER", 12)):
            spec = worldgen.airport_spec(worldgen.find_airport(code))
            ap = airport.Airport.from_spec(spec)
            self.assertEqual((spec["name"], len(ap.runways)), (name, n), code)
            # every landing direction has a partner, so each strip is a real runway length
            for a, b in ap.strips:
                self.assertGreater(math.hypot(a[0] - b[0], a[1] - b[1]) * 6076, 2400, code)
            m, nearby = worldgen.build_map(spec, None)
            self.assertTrue(m["land"], code)
        self.assertEqual(worldgen.find_airport("LAX")["icao"], "KLAX")
        # across the 180th meridian
        spec = worldgen.airport_spec(worldgen.find_airport("NFFN"))
        m, _ = worldgen.build_map(spec, None)
        self.assertTrue(m["coast"])

    def test_typing_a_location(self):
        import worldgen
        self.assertEqual(worldgen.parse_coords("-33.947, 151.205"), (-33.947, 151.205))
        self.assertEqual(worldgen.parse_coords("https://www.google.com/maps/@51.4705,-0.38,15z"), (51.4705, -0.38))
        self.assertEqual(worldgen.parse_coords("33.95S 151.20E"), (-33.95, 151.2))
        self.assertIsNone(worldgen.parse_coords("adelaide"))
        self.assertIsNone(worldgen.parse_coords("200, 5"))
        self.assertEqual(worldgen.search_places("adelaide")[0]["country"], "AU")

    def test_hour_of_traffic_at_a_generated_airport(self):
        """Los Angeles: the Hawthorne circuit sits right under the 25 approaches."""
        import worldgen
        spec = worldgen.airport_spec(worldgen.find_airport("KLAX"))
        _m, spec["nearby"] = worldgen.build_map(spec, None)
        ap = airport.Airport.from_spec(spec)
        w = World(runway="24R", seed=9, auto=True, populate=True, ap=ap, home=(33.962, -118.353))
        kinds = {}
        for _ in range(720):
            for a in w.sim.aircraft:
                kinds[a.hex] = a.kind
            w.run(5)
        arrival_phases = {tracker.INBOUND, tracker.FINAL, tracker.LANDED, tracker.GO_AROUND}
        wrong = [(kinds[h], sorted(p)) for h, p in w.seen.items()
                 if kinds.get(h) in ("departure", "overflight", "circuit") and p & arrival_phases]
        self.assertEqual(wrong, [])
        self.assertGreaterEqual(len(w.trk.landings), 8)
        self.assertTrue(all(l.rwy == "24R" for l in w.trk.landings))


class SetupTests(unittest.TestCase):
    def test_setup_without_questions(self):
        import tempfile
        import setup_wizard
        tmp = tempfile.mkdtemp()
        old = setup_wizard.LOCAL_DIR
        setup_wizard.LOCAL_DIR = os.path.join(tmp, "local")
        try:
            path = os.path.join(tmp, "config.ini")
            with open(path, "w") as fh:            # an existing file keeps its comments and other settings
                fh.write("# my notes\n[Display]\nFPS = 10\n[Home]\n# NAME = HOME\n")
            setup_wizard.run(setup_wizard.Answers("33.96172, -118.35306", "KLAX", None, "my place", None, True), path,
                             fetch_tables=False)
        finally:
            setup_wizard.LOCAL_DIR = old
        text = open(path).read()
        self.assertIn("# my notes", text)
        self.assertIn("FPS = 10", text)
        cfg = config.Config(path)
        self.assertTrue(cfg.has_home)
        self.assertEqual((cfg.home_lat, cfg.home_lon), (33.962, -118.353))      # rounded to about 100 m
        self.assertEqual(cfg.home_name, "MY PLACE")
        self.assertEqual(cfg.home_runway, "24R")                                # the approach passing closest
        self.assertTrue(os.path.exists(cfg.airport_file))
        ap = airport.Airport(cfg.airport_file)
        self.assertEqual(ap.icao, "KLAX")
        self.assertTrue(ap.nearby)
        # Adelaide uses its detailed built-in data instead of generating any
        path2 = os.path.join(tmp, "config2.ini")
        setup_wizard.run(setup_wizard.Answers("-34.871,138.61", "YPAD", None, None, None, True), path2,
                         fetch_tables=False)
        cfg2 = config.Config(path2)
        self.assertTrue(cfg2.airport_file.endswith("data/airport_ypad.json"))
        self.assertEqual(cfg2.home_runway, "23")

    def test_no_home_means_setup_or_demo(self):
        cfg = config.Config(NO_CONFIG)
        self.assertFalse(cfg.has_home)
        import main
        self.assertEqual(main.main(["--config", NO_CONFIG]) if not sys.stdin.isatty() else 1, 1)


class DisplayTests(unittest.TestCase):
    """Every panel drawn for real (off-screen), through twenty minutes of traffic."""

    @classmethod
    def setUpClass(cls):
        import app
        cls.app_mod = app

    def _app(self, demo, seed=4):
        clk = Clock()
        a = self.app_mod.App(config.Config(NO_CONFIG), demo=demo, headless=True, clock=clk,
                             wall=lambda: 1794620000.0 + clk.t, seed=seed, strict=True, log=lambda *_: None)
        return a, clk

    def test_demo_draws_every_state(self):
        a, clk = self._app(True)
        a.world.go_around_every = 3
        seen = set()
        views = set()
        for i in range(1200 * 2):
            clk.t += 0.5
            a.frame()
            f = a.ctx.feat
            seen.add(f.phase if f is not None else "IDLE")
            views.add(a.map.view)
            if i % 240 == 0:
                a.map.cycle_lock()
                a._step_selection(1)
        self.assertEqual(a.errors, 0)
        self.assertTrue({tracker.FINAL, tracker.INBOUND, tracker.LANDED} <= seen, seen)
        self.assertEqual(views, {"TERMINAL", "APPROACH", "SHORT"})
        self.assertGreaterEqual(len(a.tracker.landings), 3)
        self.assertEqual(a.screen.get_size(), (800, 480))

    def test_other_runways_and_no_traffic(self):
        for runway in ("05", "12", "30"):
            a, clk = self._app(True, seed=6)
            a.world = sim.Sim(a.airport, runway, seed=6)
            a.world.populate()
            a.feed.source.sim = a.world
            for _ in range(500):
                clk.t += 1.0
                a.frame()
            self.assertEqual(a.ctx.rw.id, runway)
        a, clk = self._app(True)
        a.world.aircraft = []
        a.world.auto_traffic = False
        for _ in range(120):
            clk.t += 1.0
            a.frame()
        self.assertIsNone(a.ctx.feat)

    def test_review_draws(self):
        a, clk = self._app(True)
        base = 1794620000.0
        a.tracker.landings = [tracker.Landing(base - i * 400.0, f, t, r, "23", "h%d" % i, "MEL")
                              for i, (f, t, r, _o, _c, _b) in enumerate(sim.DEMO_ARRIVALS * 6)]
        a.tracker.go_arounds = [tracker.Landing(base - 900.0, "QFA741", "B738", "VH-VZT", "23", "h0", low=400.0)]
        a.review_pinned = True
        for _ in range(5):
            clk.t += 1.0
            a.frame()
        self.assertTrue(a.ctx.review)
        # the go-around alert takes over the whole screen, then clears itself
        a.tracker.on_go_around(a.tracker.go_arounds[0], None)
        clk.t += 1.0
        a.frame()
        self.assertTrue(a.alert.is_active(clk.t))
        clk.t += a.cfg.alert_seconds + 1.0
        a.frame()
        self.assertFalse(a.alert.is_active(clk.t))
        a.review_pinned = False
        a.tracker.landings = []
        a.frame()

    def test_stats_when_quiet(self):
        import time as _t
        import app
        clk = Clock()
        noon = _t.mktime((2026, 11, 14, 12, 0, 0, 0, 0, -1))
        a = app.App(config.Config(NO_CONFIG), demo=True, headless=True, clock=clk, wall=lambda: noon + clk.t - 1000.0,
                    seed=4, strict=True, log=lambda *_: None)
        a.world.aircraft = []
        a.world.auto_traffic = False
        a.cfg.stats_when_quiet = False
        clk.t += 120.0
        a.frame()
        self.assertIsNone(a.ctx.feat)
        self.assertFalse(a.ctx.review)          # off: midday, nothing arriving -> the map stays
        a.cfg.stats_when_quiet = True
        clk.t += 30.0
        a.frame()
        self.assertFalse(a.ctx.review)          # on: waits a minute of quiet first ...
        clk.t += 31.0
        a.frame()
        self.assertTrue(a.ctx.review)           # ... then nothing arriving -> stats
        a.world.add_arrival(sim.DEMO_ARRIVALS[0], on_final_dtg=6.0)
        for _ in range(12):
            clk.t += 1.0
            a.frame()
        self.assertIsNotNone(a.ctx.feat)
        self.assertFalse(a.ctx.review)          # an arrival brings the map straight back

    def test_generated_airport_draws(self):
        import tempfile
        import setup_wizard
        tmp = tempfile.mkdtemp()
        old = setup_wizard.LOCAL_DIR
        setup_wizard.LOCAL_DIR = os.path.join(tmp, "local")
        try:
            path = os.path.join(tmp, "config.ini")
            setup_wizard.run(setup_wizard.Answers("51.4705,-0.38", "EGLL", "27R", None, None, True), path,
                             fetch_tables=False)
        finally:
            setup_wizard.LOCAL_DIR = old
        import app
        clk = Clock()
        a = app.App(config.Config(path), demo=True, headless=True, clock=clk, wall=lambda: 1794620000.0 + clk.t,
                    seed=2, strict=True, log=lambda *_: None)
        for _ in range(600):
            clk.t += 1.0
            a.frame()
        self.assertEqual(a.errors, 0)
        self.assertEqual(a.airport.icao, "EGLL")
        self.assertGreaterEqual(len(a.tracker.landings), 1)

    def test_no_internet_still_draws(self):
        class Dead(object):
            label = "ADSB.FI"

            def fetch(self):
                raise RuntimeError("no DNS (is the network up?)")
        a, clk = self._app(False)
        a.feed.source = Dead()
        for _ in range(60):
            clk.t += 1.0
            a.frame()
        self.assertEqual(a.feed.state, "RETRYING")
        self.assertIsNone(a.ctx.feat)


if __name__ == "__main__":
    unittest.main()
