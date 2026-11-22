"""Small geodesy toolkit for the approach display.

Everything on screen is worked out in a flat "local frame" centred on the
airport: x = nautical miles east, y = nautical miles north.  Within 50 NM of
the airport that is accurate to a few metres, which is far better than the
ADS-B data itself.
"""
import math

M_PER_NM = 1852.0
FT_PER_NM = 6076.11549
FT_PER_M = 3.280839895
EARTH_R_M = 6371008.8

_COMPASS_16 = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
               "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")
_COMPASS_8_WORDS = ("NORTH", "NORTH-EAST", "EAST", "SOUTH-EAST",
                    "SOUTH", "SOUTH-WEST", "WEST", "NORTH-WEST")


def norm360(a):
    """Wrap an angle into 0..360."""
    a = math.fmod(a, 360.0)
    if a < 0:
        a += 360.0
    return a


def ang_diff(a, b):
    """Signed smallest difference a - b, in -180..+180."""
    d = math.fmod(a - b, 360.0)
    if d >= 180.0:
        d -= 360.0
    elif d < -180.0:
        d += 360.0
    return d


def compass16(bearing):
    return _COMPASS_16[int((norm360(bearing) + 11.25) // 22.5) % 16]


def compass_words(bearing):
    return _COMPASS_8_WORDS[int((norm360(bearing) + 22.5) // 45.0) % 8]


def _m_per_deg(lat_deg):
    """Metres per degree of latitude / longitude on the WGS84 ellipsoid."""
    p = math.radians(lat_deg)
    m_lat = 111132.92 - 559.82 * math.cos(2 * p) + 1.175 * math.cos(4 * p) - 0.0023 * math.cos(6 * p)
    m_lon = 111412.84 * math.cos(p) - 93.5 * math.cos(3 * p) + 0.118 * math.cos(5 * p)
    return m_lat, m_lon


class LocalFrame(object):
    """Tangent-plane frame: (lat, lon) <-> (x east NM, y north NM)."""

    def __init__(self, lat0, lon0):
        self.lat0 = float(lat0)
        self.lon0 = float(lon0)
        self._m_lat0, self._m_lon0 = _m_per_deg(self.lat0)

    def to_xy(self, lat, lon):
        # Mid-latitude scaling keeps the error to centimetres per NM.
        m_lat, m_lon = _m_per_deg((lat + self.lat0) * 0.5)
        dlon = lon - self.lon0
        if dlon > 180.0 or dlon < -180.0:       # across the 180th meridian
            dlon = (dlon + 180.0) % 360.0 - 180.0
        x = dlon * m_lon / M_PER_NM
        y = (lat - self.lat0) * m_lat / M_PER_NM
        return x, y

    def to_ll(self, x, y):
        lat = self.lat0 + y * M_PER_NM / self._m_lat0
        for _ in range(2):  # two passes are plenty at these ranges
            m_lat, m_lon = _m_per_deg((lat + self.lat0) * 0.5)
            lat = self.lat0 + y * M_PER_NM / m_lat
        m_lat, m_lon = _m_per_deg((lat + self.lat0) * 0.5)
        lon = self.lon0 + x * M_PER_NM / m_lon
        return lat, lon


def bearing_xy(dx, dy):
    """True bearing (deg) of the vector (dx east, dy north)."""
    return norm360(math.degrees(math.atan2(dx, dy)))


def unit_from_bearing(bearing):
    """Unit vector (east, north) pointing along a true bearing."""
    r = math.radians(bearing)
    return math.sin(r), math.cos(r)


def look_angles(dx_nm, dy_nm, target_alt_ft, eye_alt_ft):
    """Where to look for an aircraft from a fixed observer.

    dx/dy: target position relative to the observer (NM east / north).
    Returns (azimuth deg true, elevation deg, slant range km, ground range km).
    Earth curvature and standard refraction are included; they matter by a
    fraction of a degree for low aircraft a few kilometres away.
    """
    ground_m = math.hypot(dx_nm, dy_nm) * M_PER_NM
    az = bearing_xy(dx_nm, dy_nm)
    dh = (target_alt_ft - eye_alt_ft) / FT_PER_M
    drop = ground_m * ground_m / (2.0 * EARTH_R_M * (7.0 / 6.0))
    dh -= drop
    el = math.degrees(math.atan2(dh, max(ground_m, 1.0)))
    slant_km = math.hypot(ground_m, dh) / 1000.0
    return az, el, slant_km, ground_m / 1000.0


def point_in_ring(x, y, ring):
    """Even-odd point-in-polygon test. ring = [(x, y), ...]."""
    inside = False
    n = len(ring)
    if n < 3:
        return False
    x1, y1 = ring[-1]
    for i in range(n):
        x2, y2 = ring[i]
        if (y1 > y) != (y2 > y):
            if x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
                inside = not inside
        x1, y1 = x2, y2
    return inside
