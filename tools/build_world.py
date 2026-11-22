"""Builds the worldwide data in data/world/ (you do not need to run this).

Kept for provenance: it shows exactly where the airport, coastline and place
data used by `--setup` comes from.  Everything it reads is public domain:

  OurAirports        airports.csv, runways.csv          https://ourairports.com/data/
  Natural Earth      ne_10m_land, ne_10m_minor_islands,  https://www.naturalearthdata.com
                     ne_10m_lakes, ne_10m_populated_places_simple
  EGM96 geoid        egm96_15.gtx (NGA, via PROJ datumgrid)
  World Magnetic     WMM_2025.COF (NOAA / BGS)
  Model

Usage : python tools/build_world.py <folder with the downloads> data/world
Needs : shapely
"""
import csv
import gzip
import json
import os
import shutil
import struct
import sys
import unicodedata
from collections import defaultdict

from shapely.geometry import Polygon, box, shape
from shapely.ops import unary_union

SRC, OUT = sys.argv[1], sys.argv[2]
TILE = 5          # degrees per map tile
Q = 10000         # coordinates stored as integers of 1/10000 degree (about 11 m)
os.makedirs(OUT, exist_ok=True)


def save(name, obj):
    path = os.path.join(OUT, name)
    with gzip.open(path, "wt", encoding="utf-8", compresslevel=9) as fh:
        json.dump(obj, fh, separators=(",", ":"))
    print("  %-22s %8d bytes" % (name, os.path.getsize(path)))


# --------------------------------------------------------------------------- airports
def num(v, cast=float):
    try:
        return cast(v)
    except (TypeError, ValueError):
        return None


airports = {r["ident"]: r for r in csv.DictReader(open(os.path.join(SRC, "airports.csv"), encoding="utf-8"))}
runways = defaultdict(list)
for r in csv.DictReader(open(os.path.join(SRC, "runways.csv"), encoding="utf-8")):
    runways[r["airport_ident"]].append(r)


def usable(r, min_len):
    keys = ("le_latitude_deg", "le_longitude_deg", "he_latitude_deg", "he_longitude_deg")
    if r["closed"] == "1" or not all(r[k] for k in keys):
        return False
    if (num(r["length_ft"], int) or 0) < min_len:
        return False
    surf = (r["surface"] or "").upper()
    return not surf.startswith("WATER")


def ascii_(s, n=48):
    s = unicodedata.normalize("NFKD", s or "")         # "Sao Paulo", not "So Paulo"
    s = s.replace("\u00df", "ss").replace("\u00f8", "o").replace("\u00d8", "O").replace("\u0142", "l")
    s = s.encode("ascii", "ignore").decode("ascii")
    return " ".join(s.split())[:n]


main, fields = [], []
for a in airports.values():
    t = a["type"]
    selectable = t in ("large_airport", "medium_airport") or (t == "small_airport" and a["scheduled_service"] == "yes")
    lat, lon = num(a["latitude_deg"]), num(a["longitude_deg"])
    if lat is None or lon is None:
        continue
    if selectable:
        rws = []
        for r in runways[a["ident"]]:
            if usable(r, 2500):
                rws.append([r["le_ident"].strip(), r["he_ident"].strip(),
                            round(float(r["le_latitude_deg"]), 6), round(float(r["le_longitude_deg"]), 6),
                            num(r["le_elevation_ft"], int), num(r["le_displaced_threshold_ft"], int) or 0,
                            round(float(r["he_latitude_deg"]), 6), round(float(r["he_longitude_deg"]), 6),
                            num(r["he_elevation_ft"], int), num(r["he_displaced_threshold_ft"], int) or 0,
                            num(r["length_ft"], int) or 0])
        if rws:
            icao = (a["icao_code"] or a["gps_code"] or a["ident"]).strip().upper()
            main.append([a["ident"], icao, (a["iata_code"] or "").strip().upper(), ascii_(a["name"]),
                         ascii_(a["municipality"], 30), a["iso_country"], round(lat, 6), round(lon, 6),
                         num(a["elevation_ft"], int) or 0, t, rws])
            continue
    if t in ("small_airport", "medium_airport", "large_airport"):
        strips = [[round(float(r["le_latitude_deg"]), 5), round(float(r["le_longitude_deg"]), 5),
                   round(float(r["he_latitude_deg"]), 5), round(float(r["he_longitude_deg"]), 5)]
                  for r in runways[a["ident"]] if usable(r, 800)]
        if strips:
            fields.append([(a["gps_code"] or a["ident"]).strip().upper()[:6], round(lat, 5), round(lon, 5),
                           num(a["elevation_ft"], int) or 0, strips])
print("airports %d selectable, %d other airfields" % (len(main), len(fields)))
save("airports.json.gz", {"source": "OurAirports (public domain)", "airports": main, "airfields": fields})


# --------------------------------------------------------------------------- land and lakes, tiled
def load(name):
    return json.load(open(os.path.join(SRC, name + ".geojson"), encoding="utf-8"))["features"]


def enc(ring):
    out, px, py = [], 0, 0
    for x, y in ring:
        ix, iy = int(round(x * Q)), int(round(y * Q))
        out += [ix - px, iy - py]
        px, py = ix, iy
    return out


def tiles_of(geoms, tol, min_area, label):
    tiles = {}
    merged = unary_union([g if g.is_valid else g.buffer(0) for g in geoms])
    n_pts = 0
    for la in range(-90, 90, TILE):
        for lo in range(-180, 180, TILE):
            tb = box(lo, la, lo + TILE, la + TILE)
            if not merged.intersects(tb):
                continue
            part = merged.intersection(tb)
            polys = [g for g in getattr(part, "geoms", [part]) if isinstance(g, Polygon)]
            enc_polys = []
            for p in polys:
                if p.area < min_area:
                    continue
                s = p.simplify(tol, preserve_topology=True)
                if s.is_empty or not isinstance(s, Polygon):
                    continue
                rings = [enc(s.exterior.coords)] + [enc(h.coords) for h in s.interiors
                                                    if Polygon(h).area >= min_area]
                n_pts += sum(len(r) // 2 for r in rings)
                enc_polys.append(rings)
            if enc_polys:
                tiles["%d,%d" % (la, lo)] = enc_polys
    print("%s: %d tiles, %d points" % (label, len(tiles), n_pts))
    return tiles


land = [shape(f["geometry"]) for f in load("ne_10m_land") + load("ne_10m_minor_islands")]
save("land.json.gz", {"source": "Natural Earth 10m land + minor islands (public domain)", "tile": TILE, "q": Q,
                      "tiles": tiles_of(land, 0.0006, 2e-6, "land")})
lakes = [shape(f["geometry"]) for f in load("ne_10m_lakes")]
save("lakes.json.gz", {"source": "Natural Earth 10m lakes (public domain)", "tile": TILE, "q": Q,
                       "tiles": tiles_of(lakes, 0.0006, 4e-6, "lakes")})

# --------------------------------------------------------------------------- towns and cities
places = []
for f in load("ne_10m_populated_places_simple"):
    p = f["properties"]
    name = ascii_(p.get("nameascii") or p.get("name"), 26).upper()
    if not name:
        continue
    lon, lat = f["geometry"]["coordinates"][:2]
    cc = ascii_(p.get("iso_a2") or "", 2).upper()
    if cc in ("", "-9", "-1"):
        cc = ascii_(p.get("adm0_a3") or "", 3).upper()
    places.append([name, round(lat, 4), round(lon, 4), int(p.get("scalerank") or 10), int(p.get("pop_max") or 0),
                   cc, ascii_(p.get("adm1name") or "", 24).upper()])
print("places", len(places))
save("places.json.gz", {"source": "Natural Earth 10m populated places (public domain)", "places": places})

# --------------------------------------------------------------------------- geoid, 1 degree grid
raw = open(os.path.join(SRC, "egm96_15.gtx"), "rb").read()
lat0, lon0, dlat, dlon = struct.unpack(">4d", raw[:32])
nrows, ncols = struct.unpack(">2i", raw[32:40])


def geoid(lat, lon):
    lo = (lon - lon0) % 360.0
    r, c = (lat - lat0) / dlat, lo / dlon
    r0, c0 = min(int(r), nrows - 2), int(c)
    fr, fc = r - r0, c - c0

    def v(rr, cc):
        cc %= ncols
        return struct.unpack(">f", raw[40 + (rr * ncols + cc) * 4:44 + (rr * ncols + cc) * 4])[0]
    return (v(r0, c0) * (1 - fr) * (1 - fc) + v(r0, c0 + 1) * (1 - fr) * fc
            + v(r0 + 1, c0) * fr * (1 - fc) + v(r0 + 1, c0 + 1) * fr * fc)


grid = [int(round(geoid(la, lo) * 10)) for la in range(-90, 91) for lo in range(-180, 181)]
save("geoid.json.gz", {"source": "EGM96 geoid heights (NGA), decimetres, 1 degree grid",
                       "lat0": -90, "lon0": -180, "nlat": 181, "nlon": 361, "dm": grid})
# --------------------------------------------------------------------------- magnetic model
shutil.copy(os.path.join(SRC, "WMM_2025.COF"), os.path.join(OUT, "WMM_2025.COF"))
print("  WMM_2025.COF copied")
