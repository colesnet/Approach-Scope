"""Online extras: where a flight has come from, and the airport weather.

Both run on their own background threads, are completely optional, and fail
quietly - if either service is down the display simply shows less.

  routes   adsbdb.com   (free public API, looked up once per callsign)
  weather  aviationweather.gov METAR (free public API, every 10 minutes)
"""
import re
import threading
import time

import net
import refdata

ROUTE_URL = "https://api.adsbdb.com/v0/callsign/%s"
METAR_URL = "https://aviationweather.gov/api/data/metar?ids=%s&format=json"

_CALLSIGN_OK = re.compile(r"^[A-Z]{2,3}\d{1,4}[A-Z]{0,2}$")


class Route(object):
    __slots__ = ("callsign", "origin_icao", "origin_iata", "origin_city", "dest_icao", "airline",
                 "flight_iata", "via_icao")

    def __init__(self):
        self.callsign = ""
        self.origin_icao = self.origin_iata = self.origin_city = ""
        self.dest_icao = ""
        self.airline = ""
        self.flight_iata = ""
        self.via_icao = ""


def _airport_bits(a):
    if not isinstance(a, dict):
        return "", "", ""
    icao = str(a.get("icao_code") or "").upper()[:4]
    iata = str(a.get("iata_code") or "").upper()[:3]
    city = refdata.city_name(icao, str(a.get("municipality") or ""), str(a.get("name") or ""))
    return icao, iata, city


def parse_route(obj, here_icao):
    """adsbdb response -> Route for a flight arriving *here*, or None.

    Route databases are keyed by flight number, so the answer is only used when
    it actually ends at this airport; anything else is treated as unknown rather
    than shown wrong.
    """
    try:
        fr = obj["response"]["flightroute"]
    except (KeyError, TypeError):
        return None
    if not isinstance(fr, dict):
        return None
    o_icao, o_iata, o_city = _airport_bits(fr.get("origin"))
    d_icao, _d_iata, _d_city = _airport_bits(fr.get("destination"))
    m_icao, m_iata, m_city = _airport_bits(fr.get("midpoint"))
    r = Route()
    r.callsign = str(fr.get("callsign") or "").upper()
    if d_icao == here_icao and o_icao and o_icao != here_icao:
        if m_icao and m_icao != here_icao:
            # multi-stop service: the leg into here starts at the stopover
            r.origin_icao, r.origin_iata, r.origin_city = m_icao, m_iata, m_city
            r.via_icao = o_icao
        else:
            r.origin_icao, r.origin_iata, r.origin_city = o_icao, o_iata, o_city
    elif m_icao == here_icao and o_icao and o_icao != here_icao:
        r.origin_icao, r.origin_iata, r.origin_city = o_icao, o_iata, o_city
    else:
        return None
    r.dest_icao = here_icao
    al = fr.get("airline")
    if isinstance(al, dict):
        r.airline = str(al.get("name") or "").upper()[:26]
    fi = fr.get("callsign_iata")
    r.flight_iata = str(fi).upper()[:8] if isinstance(fi, str) else ""
    return r


class RouteService(object):
    """Looks up each arriving callsign once and remembers the answer."""

    POSITIVE_TTL = 12 * 3600.0
    NEGATIVE_TTL = 2 * 3600.0
    ERROR_RETRY = 300.0
    MIN_GAP = 1.5

    def __init__(self, here_icao, enabled=True):
        self.here = here_icao
        self.enabled = enabled
        self._lock = threading.Lock()
        self._cache = {}      # callsign -> (expires monotonic, Route or None)
        self._wanted = []
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = None
        self.last_error = ""
        self.lookups = 0

    def start(self):
        if not self.enabled:
            return
        self._thread = threading.Thread(target=self._run, name="routes", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._wake.set()

    def preload(self, callsign, route):
        """Used by demo mode: answers without touching the network."""
        with self._lock:
            self._cache[callsign] = (time.monotonic() + 10 * 365 * 86400.0, route)

    def get(self, callsign):
        """Route if known, None if unknown or not looked up yet. Never blocks."""
        if not callsign:
            return None
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(callsign)
            if hit is not None and hit[0] > now:
                return hit[1]
            if self.enabled and _CALLSIGN_OK.match(callsign) and callsign not in self._wanted:
                self._wanted.append(callsign)
                del self._wanted[:-40]
                self._wake.set()
        return hit[1] if hit is not None else None

    def _run(self):
        client = net.Client()
        while not self._stop.is_set():
            self._wake.wait(30.0)
            self._wake.clear()
            while not self._stop.is_set():
                with self._lock:
                    cs = self._wanted.pop(0) if self._wanted else None
                if cs is None:
                    break
                now = time.monotonic()
                try:
                    _status, obj = client.get_json(ROUTE_URL % cs, timeout=10.0)
                    route = parse_route(obj, self.here)
                    ttl = self.POSITIVE_TTL if route is not None else self.NEGATIVE_TTL
                    self.last_error = ""
                except net.HttpError as exc:
                    route = None
                    if exc.status == 404:
                        ttl = self.NEGATIVE_TTL         # unknown callsign
                        self.last_error = ""
                    else:
                        ttl = self.ERROR_RETRY
                        self.last_error = str(exc)
                except Exception as exc:
                    route, ttl = None, self.ERROR_RETRY
                    self.last_error = type(exc).__name__
                with self._lock:
                    self._cache[cs] = (now + ttl, route)
                    if len(self._cache) > 600:
                        for key in sorted(self._cache, key=lambda k: self._cache[k][0])[:200]:
                            del self._cache[key]
                self.lookups += 1
                self._stop.wait(self.MIN_GAP if not self.last_error else 20.0)


# ----------------------------------------------------------------------------
# weather
# ----------------------------------------------------------------------------
_WIND_RE = re.compile(r"^(\d{3}|VRB)(\d{2,3})(?:G(\d{2,3}))?(KT|MPS)$")
_TEMP_RE = re.compile(r"^(M?\d{2})/(M?\d{2})?$")
_QNH_RE = re.compile(r"^Q(\d{4})$")
_ALT_RE = re.compile(r"^A(\d{4})$")
_CLOUD_RE = re.compile(r"^(FEW|SCT|BKN|OVC)(\d{3})(CB|TCU)?$")
_WX_RE = re.compile(r"^[-+]?(VC)?(MI|PR|BC|DR|BL|SH|TS|FZ)?(DZ|RA|SN|SG|IC|PL|GR|GS|UP|BR|FG|FU|VA|DU|SA|HZ|PY|PO|SQ|FC|SS|DS)+$")
_VIS_RE = re.compile(r"^\d{4}$")


class Metar(object):
    def __init__(self):
        self.raw = ""
        self.wind_dir = None     # degrees true, None when variable
        self.wind_kt = None
        self.gust_kt = None
        self.qnh = None
        self.temp = None
        self.dewpoint = None
        self.obs_epoch = None
        self.sky = ""            # short human summary: "CAVOK", "SHRA BKN025" ...
        self.fetched = time.monotonic()

    def age_min(self, wall_now):
        if self.obs_epoch is None:
            return None
        return max(0.0, (wall_now - self.obs_epoch) / 60.0)

    def wind_text(self):
        if self.wind_kt is None:
            return ""
        if self.wind_kt < 1:
            return "CALM"
        d = "VRB" if self.wind_dir is None else "%03d" % (int(round(self.wind_dir)) % 360 or 360)
        g = " G%d" % int(round(self.gust_kt)) if self.gust_kt else ""
        return "%s/%dKT%s" % (d, int(round(self.wind_kt)), g)


def _signed(tok):
    return -float(tok[1:]) if tok.startswith("M") else float(tok)


def parse_metar_text(raw, m=None):
    """Fill a Metar from the plain METAR text (fields the JSON did not give)."""
    m = m or Metar()
    m.raw = " ".join((raw or "").split())[:160]
    body = m.raw.split(" RMK ")[0].split(" TEMPO ")[0].split(" BECMG ")[0]
    toks = body.split()
    wx, clouds, vis, cavok = [], [], None, False
    for tok in toks:
        w = _WIND_RE.match(tok)
        if w and m.wind_kt is None:
            k = 1.9438 if w.group(4) == "MPS" else 1.0
            m.wind_dir = None if w.group(1) == "VRB" else float(w.group(1))
            m.wind_kt = float(w.group(2)) * k
            m.gust_kt = float(w.group(3)) * k if w.group(3) else None
            continue
        if tok == "CAVOK":
            cavok = True
            continue
        q = _QNH_RE.match(tok)
        if q and m.qnh is None:
            m.qnh = float(q.group(1))
            continue
        a = _ALT_RE.match(tok)
        if a and m.qnh is None:
            m.qnh = float(a.group(1)) / 100.0 * 33.8639
            continue
        t = _TEMP_RE.match(tok)
        if t and m.temp is None:
            m.temp = _signed(t.group(1))
            if t.group(2):
                m.dewpoint = _signed(t.group(2))
            continue
        c = _CLOUD_RE.match(tok)
        if c:
            clouds.append((c.group(1), int(c.group(2)), c.group(3) or ""))
            continue
        if _VIS_RE.match(tok) and vis is None and m.wind_kt is not None:
            vis = int(tok)
            continue
        if _WX_RE.match(tok) and len(tok) <= 8:
            wx.append(tok)
    parts = []
    if cavok:
        parts.append("CAVOK")
    else:
        if vis is not None and vis < 9999:
            parts.append("VIS %.1fKM" % (vis / 1000.0) if vis >= 1000 else "VIS %dM" % vis)
        parts.extend(wx[:2])
        ceiling = [c for c in clouds if c[0] in ("BKN", "OVC")]
        pick = ceiling[0] if ceiling else (clouds[0] if clouds else None)
        if pick is not None:
            parts.append("%s%03d%s" % pick)
        elif not wx and vis is None and "NSC" in toks:
            parts.append("NO CLOUD")
    m.sky = " ".join(parts)[:26]
    return m


def parse_metar_json(obj):
    """aviationweather.gov JSON (a list with one report) -> Metar or None."""
    if not isinstance(obj, list) or not obj or not isinstance(obj[0], dict):
        return None
    d = obj[0]
    m = Metar()
    wd, ws, wg = d.get("wdir"), d.get("wspd"), d.get("wgst")
    if isinstance(ws, (int, float)) and not isinstance(ws, bool):
        m.wind_kt = float(ws)
        m.wind_dir = float(wd) if isinstance(wd, (int, float)) and not isinstance(wd, bool) else None
        if isinstance(wg, (int, float)) and not isinstance(wg, bool) and wg > 0:
            m.gust_kt = float(wg)
    al = d.get("altim")
    if isinstance(al, (int, float)) and not isinstance(al, bool) and 900 < al < 1100:
        m.qnh = float(al)
    for attr, key in (("temp", "temp"), ("dewpoint", "dewp")):
        v = d.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and -90 < v < 70:
            setattr(m, attr, float(v))
    ot = d.get("obsTime")
    if isinstance(ot, (int, float)) and not isinstance(ot, bool):
        m.obs_epoch = float(ot) / (1000.0 if ot > 1e11 else 1.0)
    raw = d.get("rawOb")
    parse_metar_text(raw if isinstance(raw, str) else "", m)
    if m.wind_kt is None and m.qnh is None and not m.raw:
        return None
    return m


class WeatherService(object):
    REFRESH = 600.0
    RETRY = 150.0

    def __init__(self, icao, enabled=True):
        self.icao = icao
        self.enabled = enabled
        self._lock = threading.Lock()
        self._metar = None
        self._seq = 0
        self._stop = threading.Event()
        self._thread = None
        self.last_error = ""

    def start(self):
        if not self.enabled:
            return
        self._thread = threading.Thread(target=self._run, name="weather", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def set(self, metar):
        with self._lock:
            self._metar = metar
            self._seq += 1

    def get(self):
        """(sequence number, Metar or None)."""
        with self._lock:
            return self._seq, self._metar

    def fetch_once(self, client):
        status, obj = client.get_json(METAR_URL % self.icao, timeout=12.0)
        if obj is None:
            raise net.HttpError("no current report", status)
        m = parse_metar_json(obj)
        if m is None:
            raise net.HttpError("unreadable report", status)
        return m

    def _run(self):
        client = net.Client()
        while not self._stop.is_set():
            try:
                self.set(self.fetch_once(client))
                self.last_error = ""
                wait = self.REFRESH
            except Exception as exc:
                self.last_error = str(exc) or type(exc).__name__
                wait = self.RETRY
            self._stop.wait(wait)
