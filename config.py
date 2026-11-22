"""Loads the settings: config.default.ini, then your config.ini on top of it.

Every setting has a sensible default, and a bad value (a typo, a missing key,
a missing file) falls back to that default with a warning instead of stopping
the display from starting.
"""
import configparser
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VERSION = "1.0"
USER_AGENT = "approach-scope/%s (home ADS-B approach display)" % VERSION


def resolve(path):
    """Paths in the config are relative to this folder, not to wherever you launched from."""
    if not path:
        return path
    path = os.path.expanduser(path)
    return path if os.path.isabs(path) else os.path.join(BASE_DIR, path)


class Config(object):
    """config.default.ini (shipped) overlaid with config.ini (yours, made by setup)."""

    def __init__(self, path=None, defaults=None):
        self.path = path or os.path.join(BASE_DIR, "config.ini")
        self.defaults_path = defaults or os.path.join(BASE_DIR, "config.default.ini")
        self.warnings = []
        cp = configparser.ConfigParser(inline_comment_prefixes=("#", ";"), interpolation=None)
        self.found = False
        for p in (self.defaults_path, self.path):
            try:
                read = cp.read(p, encoding="utf-8")
                if p == self.path and read:
                    self.found = True
            except (configparser.Error, OSError, UnicodeError) as exc:
                self.warnings.append("could not read %s (%s) - using built-in defaults" % (p, exc))
        self._cp = cp

        # --- airport ---------------------------------------------------
        self.airport_file = resolve(self._str("Airport", "DATA_FILE", "data/airport_ypad.json"))
        self.home_runway = self._str("Airport", "HOME_RUNWAY", "").upper()

        # --- home (where you watch from); empty until setup has been run ----
        self.home_name = (self._str("Home", "NAME", "HOME").upper() or "HOME")[:16]
        self.home_lat = self._float("Home", "LAT", None)
        self.home_lon = self._float("Home", "LON", None)
        self.home_eye_m = self._float("Home", "EYE_HEIGHT_M", None)
        self.has_home = (self.home_lat is not None and self.home_lon is not None
                         and -90 <= self.home_lat <= 90 and -180 <= self.home_lon <= 180)

        # --- live aircraft feed -------------------------------------------
        self.feed_url = self._str("Feed", "URL",
                                  "https://opendata.adsb.fi/api/v3/lat/{lat}/lon/{lon}/dist/{radius}")
        self.feed_radius_nm = int(min(250, max(10, self._float("Feed", "RADIUS_NM", 45))))
        # adsb.fi allows one request per second; never go faster than every 2 s
        self.feed_interval = max(2.0, self._float("Feed", "INTERVAL", 5.0))
        self.feed_timeout = max(2.0, self._float("Feed", "TIMEOUT", 8.0))
        self.mil_prefixes = tuple(p.strip().upper() for p in
                                  self._str("Feed", "MIL_PREFIX_LIST", "7CF").split(",") if p.strip())

        # --- optional extras ----------------------------------------------
        self.routes_enabled = self._bool("Extras", "ROUTES", True)
        self.metar_enabled = self._bool("Extras", "WEATHER", True)
        self.keep_log = self._bool("Extras", "KEEP_ARRIVALS_LOG", True)

        # --- audio ----------------------------------------------------------
        self.atc_stream_url = self._str("Audio", "ATC_STREAM_URL", "")
        self.atc_auto_start = self._bool("Audio", "AUTO_START", False)

        # --- display --------------------------------------------------------
        self.fullscreen = self._bool("Display", "FULLSCREEN", True)
        self.fps = int(min(60, max(2, self._float("Display", "FPS", 15))))
        self.font_regular = resolve(self._str("Display", "FONT_REGULAR", "fonts/TerminusTTF-4.49.3.ttf"))
        self.font_bold = resolve(self._str("Display", "FONT_BOLD", "fonts/TerminusTTF-Bold-4.49.3.ttf"))
        self.crt_effect = self._bool("Display", "CRT_EFFECT", False)
        self.mode = self._str("Display", "MODE", "auto").lower()
        self.go_around_alert = self._bool("Display", "GO_AROUND_ALERT", True)
        self.stats_when_quiet = self._bool("Display", "STATS_WHEN_QUIET", True)
        self.alert_seconds = min(300.0, max(5.0, self._float("Display", "ALERT_SECONDS", 30)))
        self.show_other_traffic = self._bool("Display", "SHOW_OTHER_TRAFFIC", True)
        self.clock_24h = self._bool("Display", "CLOCK_24H", True)

    # ------------------------------------------------------------------
    def _raw(self, section, key):
        try:
            if self._cp.has_option(section, key):
                return self._cp.get(section, key)
        except configparser.Error:
            pass
        return None

    def _str(self, section, key, default):
        v = self._raw(section, key)
        return default if v is None else v.strip()

    def _float(self, section, key, default):
        v = self._raw(section, key)
        if v is None or not v.strip():
            return default
        try:
            return float(v.strip())
        except ValueError:
            self.warnings.append("[%s] %s = %r is not a number - using %s" % (section, key, v, default))
            return default

    def _bool(self, section, key, default):
        v = self._raw(section, key)
        if v is None or not v.strip():
            return default
        s = v.strip().lower()
        if s in ("1", "true", "yes", "on", "y"):
            return True
        if s in ("0", "false", "no", "off", "n"):
            return False
        self.warnings.append("[%s] %s = %r is not true/false - using %s" % (section, key, v, default))
        return default
