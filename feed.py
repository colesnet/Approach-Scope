"""Live aircraft feed.

Polls the adsb.fi open-data API (or any readsb / tar1090 style endpoint) on a
background thread and turns each raw record into a clean Report.  Real feeds
contain airport ground vehicles, towers, stale positions and the occasional
1,800-knot Cessna, so everything is sanity-checked here before the tracker
ever sees it.
"""
import math
import threading
import time

import net


def _num(v):
    """A finite float, or None. (Booleans and strings are rejected.)"""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _clean(v, upper=True, limit=24):
    if not isinstance(v, str):
        return ""
    s = v.strip()
    if upper:
        s = s.upper()
    # keep to characters the bitmap font can draw
    s = "".join(ch for ch in s if 32 <= ord(ch) < 127)
    return s[:limit]


class Report(object):
    """One aircraft in one snapshot, normalised."""
    __slots__ = ("hex", "flight", "reg", "type", "desc", "own_op", "category", "src", "military",
                 "lat", "lon", "seen_pos", "seen", "on_ground", "alt_baro", "alt_geom",
                 "gs", "track", "baro_rate", "geom_rate", "ias", "tas", "mach",
                 "mag_heading", "true_heading", "roll", "track_rate",
                 "wd", "ws", "oat", "squawk", "emergency",
                 "nav_qnh", "nav_alt", "nav_heading", "nav_modes", "rssi")

    def __init__(self):
        for name in self.__slots__:
            setattr(self, name, None)


_VEHICLE_TYPES = frozenset(("GND", "TWR", "SERV", "CAR", "VEH"))


def parse_record(d, mil_prefixes=()):
    """dict from the feed -> Report, or None if it should be ignored."""
    if not isinstance(d, dict):
        return None
    hexid = d.get("hex")
    if not isinstance(hexid, str) or not hexid or hexid.startswith("~"):
        return None
    lat, lon = _num(d.get("lat")), _num(d.get("lon"))
    if lat is None or lon is None or not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        return None
    category = _clean(d.get("category"), limit=2)
    typ = _clean(d.get("t") or d.get("type_code"), limit=4)
    # airport vehicles, towers and fixed obstacles broadcast too - not aircraft
    if category.startswith("C") or typ in _VEHICLE_TYPES:
        return None
    seen_pos = _num(d.get("seen_pos"))
    if seen_pos is None:
        seen_pos = _num(d.get("seen")) or 0.0
    if seen_pos < 0 or seen_pos > 60.0:
        return None

    r = Report()
    r.hex = hexid.strip().lower()[:6]
    r.flight = _clean(d.get("flight") or d.get("callsign"), limit=8)
    r.reg = _clean(d.get("r"), limit=10)
    r.type = typ
    r.desc = _clean(d.get("desc"), upper=False, limit=48)
    r.own_op = _clean(d.get("ownOp"), upper=False, limit=40)
    r.category = category
    r.lat, r.lon = lat, lon
    r.seen_pos = seen_pos
    r.seen = _num(d.get("seen")) or 0.0

    src = d.get("type") if isinstance(d.get("type"), str) else ""
    mlat_fields = d.get("mlat") if isinstance(d.get("mlat"), list) else []
    if src.startswith("mlat") or "lat" in mlat_fields:
        r.src = "MLAT"
    elif src.startswith("adsb") or src.startswith("adsr"):
        r.src = "ADS-B"
    elif src.startswith("tisb"):
        r.src = "TIS-B"
    elif src.startswith("adsc"):
        r.src = "ADS-C"
    elif src.startswith("mode_s"):
        r.src = "MODE-S"
    else:
        r.src = "ADS-B" if not src else "OTHER"

    alt = d.get("alt_baro", d.get("altitude"))
    r.on_ground = isinstance(alt, str) and alt.strip().lower() == "ground"
    a = _num(alt)
    r.alt_baro = a if a is not None and -1500.0 <= a <= 60000.0 else None
    a = _num(d.get("alt_geom"))
    r.alt_geom = a if a is not None and -1500.0 <= a <= 60000.0 else None

    gs = _num(d.get("gs", d.get("speed")))
    r.gs = gs if gs is not None and 0.0 <= gs <= 750.0 else None
    trk = _num(d.get("track"))
    if trk is None:
        trk = _num(d.get("calc_track"))
    if trk is None and r.on_ground:
        trk = _num(d.get("true_heading"))
    r.track = trk % 360.0 if trk is not None else None

    for attr, key, lo, hi in (("baro_rate", "baro_rate", -12000, 12000), ("geom_rate", "geom_rate", -12000, 12000),
                              ("ias", "ias", 0, 600), ("tas", "tas", 0, 750), ("mach", "mach", 0, 3),
                              ("mag_heading", "mag_heading", 0, 360), ("true_heading", "true_heading", 0, 360),
                              ("roll", "roll", -90, 90), ("track_rate", "track_rate", -10, 10),
                              ("wd", "wd", 0, 360), ("ws", "ws", 0, 300), ("oat", "oat", -90, 60),
                              ("nav_qnh", "nav_qnh", 900, 1070), ("nav_heading", "nav_heading", 0, 360),
                              ("rssi", "rssi", -80, 5)):
        v = _num(d.get(key))
        setattr(r, attr, v if v is not None and lo <= v <= hi else None)
    nav_alt = _num(d.get("nav_altitude_mcp"))
    if nav_alt is None:
        nav_alt = _num(d.get("nav_altitude_fms"))
    r.nav_alt = nav_alt if nav_alt is not None and 0 <= nav_alt <= 60000 else None
    modes = d.get("nav_modes")
    r.nav_modes = tuple(m for m in modes if isinstance(m, str)) if isinstance(modes, list) else None

    sq = d.get("squawk")
    r.squawk = sq.strip()[:4] if isinstance(sq, str) and sq.strip().isdigit() else ""
    em = d.get("emergency")
    r.emergency = em.strip().lower() if isinstance(em, str) and em.strip().lower() not in ("", "none") else ""

    flags = d.get("dbFlags")
    mil = isinstance(flags, int) and not isinstance(flags, bool) and bool(flags & 1)
    if not mil and mil_prefixes:
        up = r.hex.upper()
        mil = any(up.startswith(p) for p in mil_prefixes)
    r.military = mil
    return r


def parse_snapshot(obj, mil_prefixes=()):
    """Whole API response -> (list of Reports, raw aircraft count)."""
    if not isinstance(obj, dict):
        raise ValueError("feed did not return a JSON object")
    items = obj.get("ac")
    if items is None:
        items = obj.get("aircraft")
    if items is None:
        raise ValueError("feed response has no 'ac' or 'aircraft' list")
    if not isinstance(items, list):
        raise ValueError("feed aircraft list is malformed")
    out, seen = [], set()
    for d in items:
        try:
            rep = parse_record(d, mil_prefixes)
        except Exception:
            rep = None
        if rep is not None and rep.hex not in seen:
            seen.add(rep.hex)
            out.append(rep)
    return out, len(items)


class Snapshot(object):
    __slots__ = ("seq", "t_recv", "reports", "raw_count")

    def __init__(self, seq, t_recv, reports, raw_count):
        self.seq = seq
        self.t_recv = t_recv
        self.reports = reports
        self.raw_count = raw_count


class Feed(object):
    """Background poller. `source` is anything with fetch() -> (reports, raw_count)."""

    def __init__(self, source, interval=5.0, clock=time.monotonic):
        self.source = source
        self.interval = float(interval)
        self.clock = clock
        self.label = getattr(source, "label", "FEED")
        self._lock = threading.Lock()
        self._latest = None
        self._seq = 0
        self._stop = threading.Event()
        self._thread = None
        self.state = "CONNECTING"       # CONNECTING | LIVE | RETRYING
        self.last_ok = None             # monotonic time of last good poll
        self.last_error = ""
        self.failures = 0
        self.polls = 0

    def start(self):
        self._thread = threading.Thread(target=self._run, name="feed", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def poll_once(self):
        """One fetch. Returns True on success. Never raises."""
        try:
            reports, raw_count = self.source.fetch()
        except Exception as exc:
            self.failures += 1
            self.last_error = str(exc) or type(exc).__name__
            self.state = "RETRYING"
            return False
        now = self.clock()
        with self._lock:
            self._seq += 1
            self._latest = Snapshot(self._seq, now, reports, raw_count)
        self.failures = 0
        self.last_error = ""
        self.last_ok = now
        self.state = "LIVE"
        self.polls += 1
        return True

    def take(self, after_seq):
        """Latest snapshot if it is newer than `after_seq`, else None."""
        with self._lock:
            snap = self._latest
        if snap is not None and snap.seq > after_seq:
            return snap
        return None

    def age(self):
        """Seconds since the last good poll (None before the first)."""
        return None if self.last_ok is None else max(0.0, self.clock() - self.last_ok)

    def _run(self):
        while not self._stop.is_set():
            started = time.monotonic()
            ok = self.poll_once()
            if ok:
                wait = self.interval
            else:
                # back off gently so a rate limit or outage is never made worse
                wait = min(60.0, self.interval * (2 ** min(self.failures, 4)))
                if "429" in self.last_error:
                    wait = max(wait, 30.0)
            wait -= (time.monotonic() - started)
            self._stop.wait(max(1.0, wait))


class HttpSource(object):
    """adsb.fi v3 / ADSBexchange-v2 style API, or a local tar1090 aircraft.json."""

    def __init__(self, url, timeout=8.0, mil_prefixes=()):
        self.url = url
        self.timeout = timeout
        self.mil_prefixes = tuple(mil_prefixes)
        self._client = None
        host = url.split("//", 1)[-1].split("/", 1)[0].lower()
        if host.startswith("opendata."):
            host = host[len("opendata."):]
        self.label = host.upper()[:22] or "FEED"

    def fetch(self):
        if self._client is None:
            self._client = net.Client()
        status, obj = self._client.get_json(self.url, self.timeout)
        if obj is None:
            raise net.HttpError("empty response (HTTP %s)" % status, status)
        return parse_snapshot(obj, self.mil_prefixes)
