"""Rebuilds the static map files in data/ (you do not need to run this).

It is kept for provenance: it shows exactly where the coastline and suburb
shapes on the display come from.

Input : suburb-10-sa.geojson - South Australian suburb / locality boundaries
        (PSMA / Geoscape Administrative Boundaries, CC BY 4.0, as published on
        data.gov.au; mirrored at github.com/tonywr71/GeoJson-Data)
Output: data/map_adelaide.json  land outline, city outline, place labels
        data/localities.json    suburb polygons for the "OVER <suburb>" readout

Usage : python tools/build_geo.py suburb-10-sa.geojson data/
Needs : shapely
"""
import json
import os
import sys

from shapely.geometry import MultiPolygon, Point, Polygon, box, shape
from shapely.ops import unary_union

SRC, OUT = sys.argv[1], sys.argv[2]
NOTE = "Derived from PSMA / Geoscape Administrative Boundaries (CC BY 4.0)"

MAP_BOX = box(137.50, -35.85, 139.60, -34.05)   # ~50 NM around Adelaide Airport
LOC_BOX = box(138.10, -35.32, 139.02, -34.55)   # suburb look-ups: ~30 NM around
WATER = ("LAKE ALEXANDRINA", "LAKE ALBERT")      # "localities" that are really water

features = json.load(open(SRC))["features"]
land_parts, suburbs = [], []
for f in features:
    g = shape(f["geometry"])
    if not g.is_valid:
        g = g.buffer(0)
    p = f["properties"]
    name = p["sa_local_2"]
    if g.intersects(MAP_BOX) and name not in WATER:
        land_parts.append(g)
    if p.get("sa_local_5") == "G" and g.intersects(LOC_BOX) and name not in WATER:
        suburbs.append((name, g))

land = unary_union(land_parts).intersection(MAP_BOX)


def r5(coords):
    return [[round(x, 5), round(y, 5)] for x, y in coords]


def rings_of(geom, min_area=3e-6, tol=0.00016):
    out = []
    for poly in (list(geom.geoms) if isinstance(geom, MultiPolygon) else [geom]):
        if poly.area < min_area:
            continue
        s = poly.simplify(tol, preserve_topology=True)
        holes = [r5(h.coords) for h in s.interiors if Polygon(h).area > 2e-5]
        out.append({"outer": r5(s.exterior.coords), "holes": holes})
    out.sort(key=lambda r: -len(r["outer"]))
    return out


def nearest(name, near):
    """The locality polygon with this name closest to a (lat, lon) hint."""
    cands = [g for n, g in suburbs if n == name]
    if not cands:
        return None
    return min(cands, key=lambda g: g.centroid.distance(Point(near[1], near[0])))


city = nearest("ADELAIDE", (-34.9285, 138.6007))
north = nearest("NORTH ADELAIDE", (-34.9084, 138.5953))
outlines = {"CITY": r5(city.exterior.coords), "NORTH ADELAIDE": r5(north.exterior.coords)}

# (name shown, locality to take the position from, position hint, show from px/NM, show up to px/NM)
WANT = [
    ("GLENELG", "GLENELG", None, 12, 999), ("PORT ADELAIDE", "PORT ADELAIDE", (-34.8345, 138.5123), 12, 999),
    ("MODBURY", "MODBURY", None, 12, 999), ("TEA TREE GULLY", "TEA TREE GULLY", None, 16, 999),
    ("OUTER HARBOR", "OUTER HARBOR", None, 12, 999), ("SALISBURY", "SALISBURY", None, 12, 999),
    ("ELIZABETH", "ELIZABETH", None, 12, 999), ("BRIGHTON", "BRIGHTON", None, 16, 999),
    ("HENLEY BEACH", "HENLEY BEACH", None, 16, 999), ("NOARLUNGA", "NOARLUNGA CENTRE", None, 12, 999),
    ("STIRLING", "STIRLING", None, 12, 999), ("GOLDEN GROVE", "GOLDEN GROVE", None, 16, 999),
    ("HALLETT COVE", "HALLETT COVE", None, 16, 999), ("MARION", "MARION", None, 16, 999),
    ("SEMAPHORE", "SEMAPHORE", None, 30, 999), ("NORWOOD", "NORWOOD", None, 30, 999),
    ("WALKERVILLE", "WALKERVILLE", None, 30, 999), ("PROSPECT", "PROSPECT", None, 30, 999),
    ("UNLEY", "UNLEY", None, 30, 999), ("HINDMARSH", "HINDMARSH", None, 30, 999),
    ("MILE END", "MILE END", None, 30, 999), ("WEST BEACH", "WEST BEACH", None, 30, 999),
    ("PLYMPTON", "PLYMPTON", None, 30, 999), ("KLEMZIG", "KLEMZIG", None, 30, 999),
    ("ENFIELD", "ENFIELD", None, 30, 999), ("WOODVILLE", "WOODVILLE", None, 30, 999),
    ("BURNSIDE", "BURNSIDE", None, 30, 999), ("MAGILL", "MAGILL", None, 30, 999),
    ("FINDON", "FINDON", None, 30, 999), ("CAMPBELLTOWN", "CAMPBELLTOWN", None, 30, 999),
    ("HOLDEN HILL", "HOLDEN HILL", None, 30, 999), ("WINDSOR GARDENS", "WINDSOR GARDENS", None, 30, 999),
    # wide view only
    ("GAWLER", "GAWLER", None, 0, 999), ("MOUNT BARKER", "MOUNT BARKER", None, 0, 16),
    ("MURRAY BRIDGE", "MURRAY BRIDGE", None, 0, 16), ("VICTOR HARBOR", "VICTOR HARBOR", None, 0, 16),
    ("ALDINGA", "ALDINGA BEACH", None, 0, 16), ("ARDROSSAN", "ARDROSSAN", None, 0, 16),
    ("TANUNDA", "TANUNDA", None, 0, 16), ("PORT WAKEFIELD", "PORT WAKEFIELD", None, 0, 16),
]
by_name = {}
for f in features:
    by_name.setdefault(f["properties"]["sa_local_2"], []).append(shape(f["geometry"]))
labels = []
for shown, loc, hint, lo, hi in WANT:
    cands = by_name.get(loc)
    if not cands:
        print("  (no locality called %s - label skipped)" % loc)
        continue
    g = cands[0] if hint is None else min(cands, key=lambda g: g.centroid.distance(Point(hint[1], hint[0])))
    if hint is None and len(cands) > 1:
        g = min(cands, key=lambda g: g.area)      # the gazetted suburb rather than the cadastral "hundred"
    c = g.centroid
    labels.append({"name": shown, "lat": round(c.y, 5), "lon": round(c.x, 5), "lo": lo, "hi": hi})
labels.append({"name": "GULF ST VINCENT", "lat": -34.985, "lon": 138.395, "lo": 12, "hi": 999, "sea": True})
labels.append({"name": "GULF ST VINCENT", "lat": -34.93, "lon": 138.13, "lo": 0, "hi": 12, "sea": True})

os.makedirs(OUT, exist_ok=True)
land_rings = rings_of(land)
json.dump({"source": NOTE, "bounds": list(MAP_BOX.bounds), "land": land_rings, "outlines": outlines, "labels": labels},
          open(os.path.join(OUT, "map_adelaide.json"), "w"), separators=(",", ":"))

locs = []
for name, g in suburbs:
    for poly in (list(g.geoms) if isinstance(g, MultiPolygon) else [g]):
        if poly.area >= 1e-7:
            locs.append([name, r5(poly.simplify(0.00008, preserve_topology=True).exterior.coords)])
json.dump({"source": NOTE, "localities": locs},
          open(os.path.join(OUT, "localities.json"), "w"), separators=(",", ":"))

print("land polygons %d, outline points %d, holes %d" % (
    len(land_rings), sum(len(r["outer"]) for r in land_rings), sum(len(r["holes"]) for r in land_rings)))
print("labels %d, suburb polygons %d (%d points)" % (len(labels), len(locs), sum(len(r) for _, r in locs)))
for fn in ("map_adelaide.json", "localities.json"):
    print("  %s  %d bytes" % (fn, os.path.getsize(os.path.join(OUT, fn))))
