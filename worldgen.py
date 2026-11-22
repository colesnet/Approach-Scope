"""Builds the airport and map files for any airport in the world.

Used by `main.py --setup`.  Everything comes from the files in data/world
(see tools/build_world.py for their sources); no internet is needed.

    nearest_airports(lat, lon)        airports near a place
    find_airport("KLAX" / "LAX")      one airport by code
    airport_spec(row)                 runway geometry ready for airport.Airport
    runway_choices(spec, lat, lon)    which approaches pass the viewer, best first
    build_map(spec, home)             coastline, lakes, towns and airfields around it
    declination(lat, lon, year)       magnetic variation (World Magnetic Model 2025)
    geoid_m(lat, lon)                 EGM96 geoid height
"""
import gzip
import json
import math
import os
import re
import time

import geo

BASE = os.path.dirname(os.path.abspath(__file__))
WORLD = os.path.join(BASE, "data", "world")
_cache = {}

# Transition altitude (ft): where crews switch between local pressure and standard.
# Only used to judge which aircraft are flying on local pressure settings.
TRANSITION_ALT = {"US": 18000, "CA": 18000, "AU": 10000, "NZ": 13000, "BR": 7000, "MX": 18500,
                  "JP": 14000, "CN": 9850, "IN": 4000, "ZA": 7500}


def _load(name):
    if name not in _cache:
        with gzip.open(os.path.join(WORLD, name), "rt", encoding="utf-8") as fh:
            _cache[name] = json.load(fh)
    return _cache[name]


def dist_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0088 * math.asin(min(1.0, math.sqrt(h)))


# ---------------------------------------------------------------------------
# geoid and magnetic variation
# ---------------------------------------------------------------------------
def geoid_m(lat, lon):
    g = _load("geoid.json.gz")
    nlon = g["nlon"]
    r = min(max(lat - g["lat0"], 0.0), g["nlat"] - 1.0001)
    c = ((lon - g["lon0"]) % 360.0)
    r0, c0 = int(r), int(c)
    fr, fc = r - r0, c - c0
    d = g["dm"]

    def v(rr, cc):
        return d[rr * nlon + min(cc, nlon - 1)] / 10.0
    return (v(r0, c0) * (1 - fr) * (1 - fc) + v(r0, c0 + 1) * (1 - fr) * fc
            + v(r0 + 1, c0) * fr * (1 - fc) + v(r0 + 1, c0 + 1) * fr * fc)


def _wmm():
    if "wmm" not in _cache:
        g, h, gd, hd = {}, {}, {}, {}
        epoch = None
        with open(os.path.join(WORLD, "WMM_2025.COF"), "r", encoding="ascii") as fh:
            for line in fh:
                parts = line.split()
                if not parts or parts[0].startswith("9999"):
                    continue
                if epoch is None:
                    epoch = float(parts[0])
                    continue
                n, m = int(parts[0]), int(parts[1])
                g[n, m], h[n, m], gd[n, m], hd[n, m] = (float(x) for x in parts[2:6])
        _cache["wmm"] = (epoch, g, h, gd, hd)
    return _cache["wmm"]


def declination(lat, lon, year=None, height_km=0.0):
    """Magnetic variation in degrees (east positive) from the World Magnetic Model."""
    epoch, g0, h0, gd, hd = _wmm()
    if year is None:
        lt = time.gmtime()
        year = lt.tm_year + (lt.tm_yday - 0.5) / 365.25
    dt = year - epoch
    nmax = 12
    # geodetic -> geocentric spherical
    a_e, f = 6378.137, 1 / 298.257223563
    e2 = f * (2 - f)
    phi, lam = math.radians(lat), math.radians(lon)
    rc = a_e / math.sqrt(1 - e2 * math.sin(phi) ** 2)
    p = (rc + height_km) * math.cos(phi)
    z = (rc * (1 - e2) + height_km) * math.sin(phi)
    r = math.hypot(p, z)
    phic = math.asin(z / r)
    ct, st = math.sin(phic), math.cos(phic)       # cos and sin of the geocentric colatitude
    st = max(st, 1e-10)
    # Schmidt semi-normalised associated Legendre functions and their colatitude derivatives
    P = {(0, 0): 1.0}
    dP = {(0, 0): 0.0}
    for n in range(1, nmax + 1):
        for m in range(0, n + 1):
            if n == m:
                P[n, m] = st * P[n - 1, m - 1]
                dP[n, m] = st * dP[n - 1, m - 1] + ct * P[n - 1, m - 1]
            else:
                k = ((n - 1) ** 2 - m * m) / ((2 * n - 1) * (2 * n - 3)) if n > 1 else 0.0
                p2 = P.get((n - 2, m), 0.0)
                dp2 = dP.get((n - 2, m), 0.0)
                P[n, m] = ct * P[n - 1, m] - k * p2
                dP[n, m] = ct * dP[n - 1, m] - st * P[n - 1, m] - k * dp2
    S = {(0, 0): 1.0}
    for n in range(1, nmax + 1):
        S[n, 0] = S[n - 1, 0] * (2 * n - 1) / n
        for m in range(1, n + 1):
            S[n, m] = S[n, m - 1] * math.sqrt((n - m + 1) * (2 if m == 1 else 1) / float(n + m))
    a = 6371.2
    bx = by = 0.0
    for n in range(1, nmax + 1):
        ar = (a / r) ** (n + 2)
        for m in range(0, n + 1):
            gnm = g0[n, m] + dt * gd[n, m]
            hnm = h0[n, m] + dt * hd[n, m]
            cm, sm = math.cos(m * lam), math.sin(m * lam)
            pnm, dpnm = S[n, m] * P[n, m], S[n, m] * dP[n, m]
            bx += ar * (gnm * cm + hnm * sm) * dpnm                 # northward (geocentric)
            by += ar * m * (gnm * sm - hnm * cm) * pnm / st         # eastward
    # rotate the northward component into the geodetic frame (Z is not needed for declination)
    bz = 0.0
    for n in range(1, nmax + 1):
        ar = (a / r) ** (n + 2)
        for m in range(0, n + 1):
            gnm = g0[n, m] + dt * gd[n, m]
            hnm = h0[n, m] + dt * hd[n, m]
            bz -= (n + 1) * ar * (gnm * math.cos(m * lam) + hnm * math.sin(m * lam)) * S[n, m] * P[n, m]
    psi = phic - phi
    north = bx * math.cos(psi) - bz * math.sin(psi)
    return math.degrees(math.atan2(by, north))


# ---------------------------------------------------------------------------
# airports
# ---------------------------------------------------------------------------
FIELDS = ("ident", "icao", "iata", "name", "city", "country", "lat", "lon", "elev", "type", "runways")


def _row(r):
    return dict(zip(FIELDS, r))


def find_airport(code):
    code = (code or "").strip().upper()
    if not code:
        return None
    rows = _load("airports.json.gz")["airports"]
    for key in (1, 0, 2):           # ICAO first, then ident, then IATA
        for r in rows:
            if r[key] == code:
                return _row(r)
    return None


def nearest_airports(lat, lon, limit=6, max_km=150.0):
    out = []
    for r in _load("airports.json.gz")["airports"]:
        if abs(r[6] - lat) > 2.0:
            continue
        d = dist_km(lat, lon, r[6], r[7])
        if d <= max_km:
            out.append((d, r))
    out.sort(key=lambda t: t[0])
    return [(d, _row(r)) for d, r in out[:limit]]


def short_name(row):
    """'Los Angeles International Airport' -> 'LOS ANGELES' (fits the header)."""
    for cand in (row.get("city", ""), row.get("name", "")):
        s = re.sub(r"\s*\(.*?\)", "", cand or "").upper()
        for junk in (" INTERNATIONAL AIRPORT", " REGIONAL AIRPORT", " AIRPORT", " INTERNATIONAL", " AIRFIELD",
                     " AERODROME"):
            s = s.replace(junk, "")
        s = " ".join(s.replace("/", " ").split())
        if s:
            while len(s) > 16 and " " in s:
                s = s.rsplit(" ", 1)[0]
            return s[:16]
    return row.get("icao", "AIRPORT")


def _offset(lat, lon, brg, nm):
    fr = geo.LocalFrame(lat, lon)
    ux, uy = geo.unit_from_bearing(brg)
    return fr.to_ll(ux * nm, uy * nm)


def _clean_id(rid):
    rid = (rid or "").strip().upper()
    return rid if re.fullmatch(r"\d{1,2}[LRCWG]?|[NSEW]{1,2}", rid) else ""


def airport_spec(row, year=None):
    """OurAirports row -> airport definition in the same format as data/airport_ypad.json."""
    lat, lon = row["lat"], row["lon"]
    fr = geo.LocalFrame(lat, lon)
    runways = []
    for rw in row["runways"]:
        le_id, he_id, la1, lo1, el1, disp1, la2, lo2, el2, disp2, length_ft = rw
        x1, y1 = fr.to_xy(la1, lo1)
        x2, y2 = fr.to_xy(la2, lo2)
        if math.hypot(x2 - x1, y2 - y1) < 0.2:
            continue                       # bad data: the two ends are almost the same point
        for rid, (ea, eo, el, disp), (oa, oo) in ((le_id, (la1, lo1, el1, disp1), (la2, lo2)),
                                                 (he_id, (la2, lo2, el2, disp2), (la1, lo1))):
            rid = _clean_id(rid)
            if not rid:
                continue
            ex, ey = fr.to_xy(ea, eo)
            ox, oy = fr.to_xy(oa, oo)
            course = geo.bearing_xy(ox - ex, oy - ey)
            # the landing threshold sits `disp` feet in from the end of the pavement
            tx, ty = fr.to_xy(*_offset(ea, eo, course, (disp or 0) / geo.FT_PER_NM))
            thr = fr.to_ll(tx, ty)
            runways.append({"id": rid, "thr": [round(thr[0], 6), round(thr[1], 6)],
                            "opp": [round(oa, 6), round(oo, 6)],
                            "elev_ft": el if el is not None else row["elev"], "tch_ft": 50, "gp_deg": 3.0,
                            "displaced_ft": disp or 0, "aid": "", "fixes": []})
    if not runways:
        raise ValueError("no usable runway data for %s" % row["icao"])
    elev = row["elev"] or 0
    return {
        "_about": "Generated by setup from OurAirports and Natural Earth data. For interest only - not for navigation.",
        "icao": row["icao"], "iata": row["iata"], "name": short_name(row), "full_name": row["name"],
        "country": row["country"], "arp": [lat, lon], "elev_ft": elev,
        "mag_var_deg": round(declination(lat, lon, year), 1),
        "geoid_sep_ft": round(geoid_m(lat, lon) * geo.FT_PER_M),
        "transition_alt_ft": TRANSITION_ALT.get(row["country"], 6000),
        "runways": runways,
    }


def runway_choices(spec, lat, lon):
    """For each landing direction: (id, passes_you, abeam_nm_out, offset_km, height_ft), best first."""
    import airport as airport_mod
    import tracker
    ap = airport_mod.Airport.from_spec(spec)
    home = tracker.HomeGeometry(ap, lat, lon, 0.0, "")
    out = []
    for rid in ap.runway_order:
        rw = ap.runways[rid]
        dtg, xtk = home.abeam[rid]
        passes = home.passes(rid) and abs(xtk) * 1.852 <= 15.0
        out.append((rid, passes, dtg, abs(xtk) * 1.852, rw.gp_alt_ft(max(dtg, 0.0))))
    out.sort(key=lambda c: (not c[1], c[3] if c[1] else -c[2]))
    return out


# ---------------------------------------------------------------------------
# the map
# ---------------------------------------------------------------------------
def _clip(ring, x0, y0, x1, y1):
    """Sutherland-Hodgman clip of a closed ring to a rectangle."""
    def edge(pts, inside, cut):
        out = []
        n = len(pts)
        for i in range(n):
            a, b = pts[i - 1], pts[i]
            ia, ib = inside(a), inside(b)
            if ib:
                if not ia:
                    out.append(cut(a, b))
                out.append(b)
            elif ia:
                out.append(cut(a, b))
        return out

    def cx(xc):
        return lambda a, b: (xc, a[1] + (b[1] - a[1]) * (xc - a[0]) / (b[0] - a[0]))

    def cy(yc):
        return lambda a, b: (a[0] + (b[0] - a[0]) * (yc - a[1]) / (b[1] - a[1]), yc)
    pts = ring
    for inside, cut in ((lambda p: p[0] >= x0, cx(x0)), (lambda p: p[0] <= x1, cx(x1)),
                        (lambda p: p[1] >= y0, cy(y0)), (lambda p: p[1] <= y1, cy(y1))):
        if not pts:
            return []
        pts = edge(pts, inside, cut)
    return pts


def _decode(flat, q, dlon):
    pts, x, y = [], 0, 0
    for i in range(0, len(flat), 2):
        x += flat[i]
        y += flat[i + 1]
        pts.append((x / q + dlon, y / q))
    return pts


def _on_seam(a, b, tile, box):
    """True if a-b runs along a tile boundary or the clip box (not a real shoreline)."""
    x0, y0, x1, y1 = box
    eps = 1e-6
    for v1, v2, edges in ((a[0], b[0], (x0, x1)), (a[1], b[1], (y0, y1))):
        if abs(v1 - v2) < eps:
            if any(abs(v1 - e) < eps for e in edges):
                return True
            k = round(v1 / tile)
            if abs(v1 - k * tile) < eps:
                return True
    return False


def _area_layer(name, box):
    """Polygons of a tiled layer inside box (lon/lat), plus their real shorelines."""
    d = _load(name)
    tile, q = d["tile"], d["q"]
    x0, y0, x1, y1 = box
    polys, coast = [], []
    for key, plist in d["tiles"].items():
        la, lo = (int(v) for v in key.split(","))
        for dlon in (-360, 0, 360):
            if lo + dlon + tile < x0 or lo + dlon > x1 or la + tile < y0 or la > y1:
                continue
            for rings in plist:
                done = []
                for ring in rings:
                    pts = _clip(_decode(ring, q, dlon), x0, y0, x1, y1)
                    if len(pts) >= 3:
                        done.append(pts)
                        run = []
                        n = len(pts)
                        for i in range(n):
                            a, b = pts[i], pts[(i + 1) % n]
                            if _on_seam(a, b, tile, box):
                                if len(run) >= 2:
                                    coast.append(run)
                                run = []
                            else:
                                if not run:
                                    run = [a]
                                run.append(b)
                        if len(run) >= 2:
                            coast.append(run)
                if done and len(done[0]) >= 3:
                    polys.append(done)
    return polys, coast


def _r5(pts):
    return [[round(x, 5), round(y, 5)] for x, y in pts]


def build_map(spec, home=None, radius_nm=60.0):
    """Map file for the airport: land, lakes, shorelines, towns and nearby airfields."""
    lat, lon = spec["arp"]
    fr = geo.LocalFrame(lat, lon)
    pts = [(0.0, 0.0)]
    if home is not None:
        pts.append(fr.to_xy(home[0], home[1]))
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    la0, lo0 = fr.to_ll(min(xs) - radius_nm, min(ys) - radius_nm)
    la1, lo1 = fr.to_ll(max(xs) + radius_nm, max(ys) + radius_nm)
    box = (lo0, la0, lo1, la1)
    land, land_coast = _area_layer("land.json.gz", box)
    lakes, lake_coast = _area_layer("lakes.json.gz", box)

    # towns: the biggest few everywhere, smaller ones only when zoomed in
    places = []
    for name, pla, plo, rank, pop, _cc, _adm in _load("places.json.gz")["places"]:
        if not (la0 <= pla <= la1):
            continue
        dl = ((plo - lon + 180.0) % 360.0) - 180.0 + lon
        if lo0 <= dl <= lo1:
            places.append((rank, -pop, name, pla, plo))
    places.sort()
    labels = []
    for rank, _p, name, pla, plo in places[:60]:
        lo = 0 if rank <= 4 else (8 if rank <= 7 else 18)
        labels.append({"name": name, "lat": pla, "lon": plo, "lo": lo, "hi": 999})

    # other airfields within 20 NM, for the map and so their circuit traffic is not taken as arrivals
    nearby = []
    seen = {spec["icao"]}
    data = _load("airports.json.gz")
    cands = [(r[1], r[6], r[7], r[8], [[w[2], w[3], w[6], w[7]] for w in r[10]]) for r in data["airports"]]
    cands += [(f[0], f[1], f[2], f[3], f[4]) for f in data["airfields"]]
    for code, pla, plo, elev, strips in cands:
        if code in seen or abs(pla - lat) > 0.5:
            continue
        d = dist_km(lat, lon, pla, plo) / 1.852
        if 1.5 < d <= 20.0:
            nearby.append((d, {"icao": code, "name": code, "pos": [pla, plo], "elev_ft": elev,
                               "strips": [[[s[0], s[1]], [s[2], s[3]]] for s in strips]}))
            seen.add(code)
    nearby.sort(key=lambda t: t[0])
    return {
        "source": "Natural Earth (public domain) and OurAirports (public domain), generated by setup",
        "bounds": [round(v, 4) for v in box],
        "land": [{"outer": _r5(p[0]), "holes": [_r5(h) for h in p[1:]]} for p in land],
        "water": [{"outer": _r5(p[0]), "holes": []} for p in lakes],
        "coast": [_r5(c) for c in land_coast + lake_coast],
        "outlines": {},
        "labels": labels,
    }, [n for _d, n in nearby[:5]]


# ---------------------------------------------------------------------------
# places by name (offline)
# ---------------------------------------------------------------------------
def search_places(text, limit=8):
    q = " ".join((text or "").upper().split())
    if len(q) < 2:
        return []
    hits = []
    for name, la, lo, rank, pop, cc, adm in _load("places.json.gz")["places"]:
        if name == q:
            score = 0
        elif name.startswith(q):
            score = 1
        elif q in name:
            score = 2
        else:
            continue
        hits.append((score, -pop, name, la, lo, cc, adm))
    hits.sort()
    return [{"name": h[2], "lat": h[3], "lon": h[4], "country": h[5], "region": h[6]} for h in hits[:limit]]


_COORD_URL = re.compile(r"@(-?\d{1,2}\.\d+),\s*(-?\d{1,3}\.\d+)")
_COORD = re.compile(r"(-?\d{1,3}(?:\.\d+)?)\s*°?\s*([NSns])?\s*[,; ]\s*(-?\d{1,3}(?:\.\d+)?)\s*°?\s*([EWew])?")


def parse_coords(text):
    """'51.47, -0.45' / '51.47N 0.45W' / a Google Maps link -> (lat, lon) or None."""
    t = (text or "").strip()
    m = _COORD_URL.search(t)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
    else:
        m = _COORD.fullmatch(t) or _COORD.search(t if re.search(r"\d+\.\d+", t) else "")
        if not m:
            return None
        lat, lon = float(m.group(1)), float(m.group(3))
        if m.group(2) and m.group(2).upper() == "S":
            lat = -abs(lat)
        if m.group(4) and m.group(4).upper() == "W":
            lon = -abs(lon)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lat, lon
