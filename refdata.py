"""Offline reference data: who operates a flight and what the aircraft is.

Two layers:
  * a hand-checked table for the airlines and types that actually turn up at
    Adelaide (clean names, IATA flight prefixes, real dimensions), and
  * the complete ICAO type / operator designator lists (from the tar1090
    database) as a fallback for everything else.
"""
import json
import os
import re

# ICAO airline code -> (display name, IATA code, radiotelephony callsign)
AIRLINES = {
    # --- Australian ---------------------------------------------------------
    "QFA": ("QANTAS", "QF", "QANTAS"),
    "QLK": ("QANTASLINK", "QF", "QLINK"),
    "QJE": ("QANTASLINK", "QF", "QJET"),
    "NWK": ("NETWORK AVIATION", "QF", "NETLINK"),
    "JST": ("JETSTAR", "JQ", "JETSTAR"),
    "VOZ": ("VIRGIN AUSTRALIA", "VA", "VELOCITY"),
    "RXA": ("REX", "ZL", "REX"),
    "UTY": ("ALLIANCE AIRLINES", "QQ", "UNITY"),
    "ANO": ("AIRNORTH", "TL", "TOPEND"),
    "SHA": ("SHARP AIRLINES", "SH", "SHARP"),
    "PFY": ("PEL-AIR", "", "PELFLIGHT"),
    "EFA": ("QANTAS FREIGHT", "", "EXPRESS FREIGHT"),
    "TMN": ("TASMAN CARGO", "HJ", "TASMAN"),
    "ASY": ("ROYAL AUSTRALIAN AIR FORCE", "", "AUSSIE"),
    # --- international carriers seen at Adelaide --------------------------
    "SIA": ("SINGAPORE AIRLINES", "SQ", "SINGAPORE"),
    "QTR": ("QATAR AIRWAYS", "QR", "QATARI"),
    "UAE": ("EMIRATES", "EK", "EMIRATES"),
    "MAS": ("MALAYSIA AIRLINES", "MH", "MALAYSIAN"),
    "ANZ": ("AIR NEW ZEALAND", "NZ", "NEW ZEALAND"),
    "FJI": ("FIJI AIRWAYS", "FJ", "FIJI"),
    "CSN": ("CHINA SOUTHERN", "CZ", "CHINA SOUTHERN"),
    "CES": ("CHINA EASTERN", "MU", "CHINA EASTERN"),
    "CPA": ("CATHAY PACIFIC", "CX", "CATHAY"),
    "MXD": ("BATIK AIR MALAYSIA", "OD", "MALINDO"),
    "VJC": ("VIETJET AIR", "VJ", "VIETJETAIR"),
    "AWQ": ("INDONESIA AIRASIA", "QZ", "WAGON AIR"),
    "UAL": ("UNITED AIRLINES", "UA", "UNITED"),
    "HVN": ("VIETNAM AIRLINES", "VN", "VIETNAM AIRLINES"),
    "XAX": ("AIRASIA X", "D7", "XANADU"),
    "TGW": ("SCOOT", "TR", "SCOOTER"),
    "GIA": ("GARUDA INDONESIA", "GA", "INDONESIA"),
    "THA": ("THAI AIRWAYS", "TG", "THAI"),
    "JAL": ("JAPAN AIRLINES", "JL", "JAPANAIR"),
    "ANA": ("ALL NIPPON AIRWAYS", "NH", "ALL NIPPON"),
    "KAL": ("KOREAN AIR", "KE", "KOREANAIR"),
    "CCA": ("AIR CHINA", "CA", "AIR CHINA"),
    "ETD": ("ETIHAD AIRWAYS", "EY", "ETIHAD"),
    "BAW": ("BRITISH AIRWAYS", "BA", "SPEEDBIRD"),
    "DAL": ("DELTA AIR LINES", "DL", "DELTA"),
    "AAL": ("AMERICAN AIRLINES", "AA", "AMERICAN"),
    "ACA": ("AIR CANADA", "AC", "AIR CANADA"),
    "HAL": ("HAWAIIAN AIRLINES", "HA", "HAWAIIAN"),
    "THY": ("TURKISH AIRLINES", "TK", "TURKISH"),
    "ALK": ("SRILANKAN AIRLINES", "UL", "SRILANKAN"),
    "AIC": ("AIR INDIA", "AI", "AIR INDIA"),
    "CEB": ("CEBU PACIFIC", "5J", "CEBU AIR"),
    "PAL": ("PHILIPPINE AIRLINES", "PR", "PHILIPPINE"),
    # --- freight ------------------------------------------------------------
    "FDX": ("FEDEX", "FX", "FEDEX"),
    "UPS": ("UPS", "5X", "UPS"),
    "GTI": ("ATLAS AIR", "5Y", "GIANT"),
}

# ICAO type -> (display name, wingspan m, length m).  Dimensions are the maker's
# published figures; helicopters give rotor diameter as "span".
TYPES = {
    "A19N": ("AIRBUS A319NEO", 35.8, 33.8), "A20N": ("AIRBUS A320NEO", 35.8, 37.6),
    "A21N": ("AIRBUS A321NEO", 35.8, 44.5), "A318": ("AIRBUS A318", 34.1, 31.4),
    "A319": ("AIRBUS A319", 35.8, 33.8), "A320": ("AIRBUS A320", 35.8, 37.6),
    "A321": ("AIRBUS A321", 35.8, 44.5), "A332": ("AIRBUS A330-200", 60.3, 58.8),
    "A333": ("AIRBUS A330-300", 60.3, 63.7), "A339": ("AIRBUS A330-900", 64.0, 63.7),
    "A359": ("AIRBUS A350-900", 64.8, 66.8), "A35K": ("AIRBUS A350-1000", 64.8, 73.8),
    "A388": ("AIRBUS A380-800", 79.8, 72.7),
    "BCS1": ("AIRBUS A220-100", 35.1, 35.0), "BCS3": ("AIRBUS A220-300", 35.1, 38.7),
    "B712": ("BOEING 717-200", 28.4, 37.8), "B733": ("BOEING 737-300", 28.9, 33.4),
    "B734": ("BOEING 737-400", 28.9, 36.4), "B737": ("BOEING 737-700", 35.8, 33.6),
    "B738": ("BOEING 737-800", 35.8, 39.5), "B739": ("BOEING 737-900", 35.8, 42.1),
    "B38M": ("BOEING 737 MAX 8", 35.9, 39.5), "B39M": ("BOEING 737 MAX 9", 35.9, 42.2),
    "B744": ("BOEING 747-400", 64.4, 70.7), "B748": ("BOEING 747-8", 68.4, 76.3),
    "B752": ("BOEING 757-200", 38.0, 47.3), "B763": ("BOEING 767-300", 47.6, 54.9),
    "B772": ("BOEING 777-200", 60.9, 63.7), "B77L": ("BOEING 777-200LR", 64.8, 63.7),
    "B77W": ("BOEING 777-300ER", 64.8, 73.9), "B788": ("BOEING 787-8", 60.1, 56.7),
    "B789": ("BOEING 787-9", 60.1, 62.8), "B78X": ("BOEING 787-10", 60.1, 68.3),
    "E170": ("EMBRAER E170", 26.0, 29.9), "E75L": ("EMBRAER E175", 28.7, 31.7),
    "E75S": ("EMBRAER E175", 26.0, 31.7), "E190": ("EMBRAER E190", 28.7, 36.2),
    "E195": ("EMBRAER E195", 28.7, 38.7), "E290": ("EMBRAER E190-E2", 33.7, 36.2),
    "E295": ("EMBRAER E195-E2", 35.1, 41.5),
    "F70": ("FOKKER 70", 28.1, 30.9), "F100": ("FOKKER 100", 28.1, 35.5),
    "DH8A": ("DASH 8-100", 25.9, 22.3), "DH8B": ("DASH 8-200", 25.9, 22.3),
    "DH8C": ("DASH 8-300", 27.4, 25.7), "DH8D": ("DASH 8 Q400", 28.4, 32.8),
    "AT43": ("ATR 42-300", 24.6, 22.7), "AT45": ("ATR 42-500", 24.6, 22.7),
    "AT46": ("ATR 42-600", 24.6, 22.7), "AT72": ("ATR 72", 27.1, 27.2),
    "AT75": ("ATR 72-500", 27.1, 27.2), "AT76": ("ATR 72-600", 27.1, 27.2),
    "SF34": ("SAAB 340", 21.4, 19.7), "SB20": ("SAAB 2000", 24.8, 27.3),
    "SW4": ("FAIRCHILD METRO", 17.4, 18.1), "B190": ("BEECH 1900D", 17.6, 17.6),
    "BE20": ("KING AIR 200", 16.6, 13.3), "B350": ("KING AIR 350", 17.7, 14.2),
    "PC12": ("PILATUS PC-12", 16.3, 14.4), "PC24": ("PILATUS PC-24", 17.0, 16.9),
    "C208": ("CESSNA CARAVAN", 15.9, 11.5), "C172": ("CESSNA 172", 11.0, 8.3),
    "C182": ("CESSNA 182", 11.0, 8.8), "P28A": ("PIPER WARRIOR", 10.7, 7.3),
    "DA40": ("DIAMOND DA40", 11.9, 8.1), "DA42": ("DIAMOND DA42", 13.4, 8.6),
    "SR22": ("CIRRUS SR22", 11.7, 7.9), "PA44": ("PIPER SEMINOLE", 11.8, 8.4),
    "BE58": ("BEECH BARON", 11.5, 9.1),
    "GLEX": ("GLOBAL EXPRESS", 28.7, 30.3), "GLF5": ("GULFSTREAM G550", 28.5, 29.4),
    "GLF6": ("GULFSTREAM G650", 30.4, 30.4), "CL60": ("CHALLENGER 600", 19.6, 20.9),
    "F900": ("FALCON 900", 19.3, 20.2), "FA7X": ("FALCON 7X", 26.2, 23.4),
    "E55P": ("PHENOM 300", 15.9, 15.6),
    "P8": ("BOEING P-8A POSEIDON", 37.6, 39.5), "E737": ("E-7A WEDGETAIL", 35.8, 33.6),
    "C17": ("C-17 GLOBEMASTER", 51.8, 53.0), "C30J": ("C-130J HERCULES", 40.4, 34.4),
    "PC21": ("PILATUS PC-21", 9.1, 11.2),
    "A139": ("AW139 HELICOPTER", 13.8, 16.7), "EC35": ("H135 HELICOPTER", 10.2, 12.2),
    "EC45": ("H145 HELICOPTER", 11.0, 13.0), "B412": ("BELL 412", 14.0, 17.1),
    "AS50": ("AS350 SQUIRREL", 10.7, 12.9), "R44": ("ROBINSON R44", 10.1, 11.7),
    "R22": ("ROBINSON R22", 7.7, 8.7), "S76": ("SIKORSKY S-76", 13.4, 16.0),
}

# Jets with engines on the rear fuselage (drawn differently from under-wing twins)
REAR_ENGINED = frozenset((
    "F70", "F100", "F28", "B712", "MD80", "MD81", "MD82", "MD83", "MD87", "MD88", "MD90", "DC9",
    "DC93", "DC95", "CRJ1", "CRJ2", "CRJ7", "CRJ9", "CRJX", "E135", "E145", "E45X", "E35L", "E50P",
    "E55P", "E545", "E550", "PC24", "GLEX", "GL5T", "GL7T", "GLF2", "GLF3", "GLF4", "GLF5", "GLF6",
    "G150", "G280", "CL30", "CL35", "CL60", "F2TH", "F900", "FA7X", "FA8X", "FA50", "FA10", "H25B",
    "H25C", "LJ31", "LJ35", "LJ40", "LJ45", "LJ55", "LJ60", "LJ75", "BE40", "PRM1", "C25A", "C25B",
    "C25C", "C25M", "C500", "C501", "C510", "C525", "C550", "C551", "C560", "C56X", "C650", "C680",
    "C68A", "C700", "C750", "ASTR", "G100", "G200", "HDJT", "EA50", "SF50", "HA4T"))

# preferred city names for common origins (overrides whatever the route service says)
CITY = {
    "YMML": "MELBOURNE", "YSSY": "SYDNEY", "YBBN": "BRISBANE", "YPPH": "PERTH", "YSCB": "CANBERRA",
    "YBCG": "GOLD COAST", "YMHB": "HOBART", "YMLT": "LAUNCESTON", "YPDN": "DARWIN", "YBAS": "ALICE SPRINGS",
    "YBCS": "CAIRNS", "YBTL": "TOWNSVILLE", "YBSU": "SUNSHINE COAST", "YMAV": "AVALON", "YWLM": "NEWCASTLE",
    "YPLC": "PORT LINCOLN", "YMTG": "MOUNT GAMBIER", "YWHA": "WHYALLA", "YKSC": "KANGAROO ISLAND",
    "YPAG": "PORT AUGUSTA", "YCDU": "CEDUNA", "YCBP": "COOBER PEDY", "YOLD": "OLYMPIC DAM",
    "YPED": "EDINBURGH", "YPPF": "PARAFIELD", "YBHI": "BROKEN HILL", "YMIA": "MILDURA",
    "YAYE": "ULURU", "YBMA": "MOUNT ISA", "YPKG": "KALGOORLIE", "YBRM": "BROOME", "YPPD": "PORT HEDLAND",
    "YMMB": "MOORABBIN", "YSBK": "BANKSTOWN", "YMEN": "ESSENDON", "YPKA": "KARRATHA", "YPMQ": "PORT MACQUARIE",
    "WSSS": "SINGAPORE", "OTHH": "DOHA", "OMDB": "DUBAI", "WMKK": "KUALA LUMPUR", "NZAA": "AUCKLAND",
    "NZCH": "CHRISTCHURCH", "NZWN": "WELLINGTON", "NFFN": "NADI", "ZGGG": "GUANGZHOU", "ZSPD": "SHANGHAI",
    "VHHH": "HONG KONG", "WADD": "BALI", "VVTS": "HO CHI MINH CITY", "VVNB": "HANOI", "KSFO": "SAN FRANCISCO",
    "KLAX": "LOS ANGELES", "WIII": "JAKARTA", "VTBS": "BANGKOK", "RJAA": "TOKYO", "RJTT": "TOKYO",
}

_AIRLINE_CS = re.compile(r"^([A-Z]{3})(\d{1,4})([A-Z]{0,2})$")
_RFDS_CS = re.compile(r"^FD(\d{2,4})$")


def _tidy_type_name(name):
    """'AIRBUS A-320' -> 'AIRBUS A320'; keep it short and capitalised."""
    s = (name or "").upper().strip()
    s = re.sub(r"\bA-(\d)", r"A\1", s)
    s = s.replace("DE HAVILLAND DHC-8", "DASH 8").replace(" DREAMLINER", "")
    s = s.replace("MCDONNELL-DOUGLAS", "MCDONNELL DOUGLAS")
    s = re.sub(r"\s+", " ", s)
    return s[:30]


TABLES_URL = "https://raw.githubusercontent.com/wiedehopf/tar1090-db/master/db/%s"


def fetch_tables(dest_dir, timeout=60):
    """Download the full ICAO aircraft-type and airline tables (tar1090-db / Mictronics) to this machine.

    They are fetched rather than shipped because their licence does not cover redistribution.
    Returns a short message; raises on failure.
    """
    import gzip
    import urllib.request
    import config
    out = {}
    for name in ("icao_aircraft_types2.js", "operators.js"):
        req = urllib.request.Request(TABLES_URL % name, headers={"User-Agent": config.USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
        try:
            raw = gzip.decompress(raw)
        except OSError:
            pass
        out[name] = json.loads(raw.decode("utf-8"))
    types = {}
    for k, v in out["icao_aircraft_types2.js"].items():
        if re.fullmatch(r"[A-Z0-9]{2,4}", k) and isinstance(v, list):
            name, desc, wtc = (list(v) + ["", "", ""])[:3]
            types[k] = [str(name).strip(), str(desc).strip(), str(wtc).strip()]
    ops = {}
    for k, v in out["operators.js"].items():
        if re.fullmatch(r"[A-Z]{3}", k) and isinstance(v, dict):
            n = " ".join(str(v.get("n") or "").encode("ascii", "ignore").decode("ascii").split())
            r = " ".join(str(v.get("r") or "").encode("ascii", "ignore").decode("ascii").split())
            if n:
                ops[k] = [n, r]
    if len(types) < 1000 or len(ops) < 1000:
        raise ValueError("the downloaded tables look incomplete")
    os.makedirs(dest_dir, exist_ok=True)
    note = "tar1090-db / Mictronics aircraft database, downloaded to this machine by setup"
    with open(os.path.join(dest_dir, "aircraft_types.json"), "w", encoding="utf-8") as fh:
        json.dump({"source": note, "types": types}, fh, separators=(",", ":"))
    with open(os.path.join(dest_dir, "operators.json"), "w", encoding="utf-8") as fh:
        json.dump({"source": note, "operators": ops}, fh, separators=(",", ":"))
    return "%d aircraft types, %d airlines" % (len(types), len(ops))


class RefData(object):
    def __init__(self, data_dir, extra_dirs=None):
        self.types = {}
        self.operators = {}
        self.warnings = []
        if extra_dirs is None:
            extra_dirs = [os.path.join(os.path.dirname(os.path.abspath(data_dir)), "local")]
        for attr, fname, key in (("types", "aircraft_types.json", "types"),
                                 ("operators", "operators.json", "operators")):
            for folder in list(extra_dirs) + [data_dir]:
                path = os.path.join(folder, fname)
                if not os.path.exists(path):
                    continue
                try:
                    with open(path, "r", encoding="utf-8") as fh:
                        setattr(self, attr, json.load(fh).get(key, {}))
                    break
                except (OSError, ValueError) as exc:
                    self.warnings.append("%s not loaded (%s)" % (fname, exc))
            else:
                self.warnings.append("%s not downloaded yet - run setup to fetch it (names are less "
                                     "detailed until then)" % fname)
        self.complete = bool(self.types) and bool(self.operators)

    # --- aircraft types -------------------------------------------------
    def descriptor(self, code):
        """('L2J', 'M') style ICAO descriptor and wake category for a type code."""
        row = self.types.get(code or "")
        if row and len(row) >= 3:
            return row[1] or "", row[2] or ""
        return "", ""

    def type_name(self, code, api_desc=""):
        if code in TYPES:
            return TYPES[code][0]
        row = self.types.get(code or "")
        if row and row[0]:
            return _tidy_type_name(row[0])
        if api_desc:
            return _tidy_type_name(api_desc)
        return code or ""

    def dimensions(self, code):
        """(wingspan m, length m) or None."""
        t = TYPES.get(code or "")
        return (t[1], t[2]) if t else None

    def wake_label(self, code):
        if code == "A388":
            return "SUPER"
        _desc, wtc = self.descriptor(code)
        return {"H": "HEAVY", "M": "MEDIUM", "L": "LIGHT"}.get(wtc, "")

    def shape_class(self, code, category=""):
        """Which silhouette to draw."""
        desc, wtc = self.descriptor(code)
        if not desc:
            if category == "A7":
                return "heli"
            if category == "A1":
                return "piston1"
            if category == "A5":
                return "jet_wide"
            return "jet"
        kind, n, eng = desc[0:1], desc[1:2], desc[2:3]
        if kind in ("H", "G"):
            return "heli"
        if eng == "J":
            if n in ("4", "6", "8"):
                return "jet_quad"
            if code in REAR_ENGINED or (wtc == "L" and n in ("1", "2")):
                return "jet_rear"
            if n == "3":
                return "jet_wide" if wtc == "H" else "jet_rear"
            if n == "1":
                return "jet_rear"
            return "jet_wide" if wtc == "H" else "jet"
        if eng == "T":
            if n in ("2", "3"):
                return "prop2"
            if n == "4":
                return "prop4"
            return "prop1"
        if eng in ("P", "E"):
            return "piston2" if n in ("2", "3", "4") else "piston1"
        return "jet"

    # --- operators --------------------------------------------------------
    def airline(self, callsign, reg=""):
        """Decode an airline-style callsign.

        Returns dict(code, name, iata, radio, number, flight_iata, telephony) or None
        when the callsign is a registration or something unrecognised.
        """
        cs = (callsign or "").strip().upper()
        if not cs:
            return None
        m = _RFDS_CS.match(cs)
        if m:
            return {"code": "FD", "name": "ROYAL FLYING DOCTOR SERVICE", "iata": "", "radio": "FLYDOC",
                    "number": m.group(1), "flight_iata": "", "telephony": "FLYDOC %s" % m.group(1)}
        m = _AIRLINE_CS.match(cs)
        if not m:
            return None
        code, num, suffix = m.group(1), m.group(2), m.group(3)
        if reg and cs == reg.replace("-", ""):
            return None
        name = iata = radio = ""
        if code in AIRLINES:
            name, iata, radio = AIRLINES[code]
        else:
            row = self.operators.get(code)
            if not row:
                return None
            name = (row[0] or "").upper()
            for junk in (" LIMITED", " LTD", " PTY", " CORPORATION", " COMPANY", " INC", " SDN BHD", " BERHAD",
                         " GROUP", " S.A.", " AG"):
                name = name.replace(junk, "")
            name = name.strip(" ,.")[:26]
            radio = (row[1] or "").upper() if len(row) > 1 else ""
        flight_iata = "%s%s" % (iata, num.lstrip("0") or "0") if (iata and not suffix) else ""
        telephony = "%s %s%s" % (radio, num.lstrip("0") or "0", suffix) if radio else ""
        return {"code": code, "name": name, "iata": iata, "radio": radio, "number": num + suffix,
                "flight_iata": flight_iata, "telephony": telephony}


def city_name(icao, municipality="", airport_name=""):
    """Best short place name for an origin airport."""
    if icao in CITY:
        return CITY[icao]
    s = (municipality or airport_name or "").upper()
    s = re.sub(r"\s*\(.*?\)", "", s)
    for junk in (" INTERNATIONAL AIRPORT", " AIRPORT", " INTERNATIONAL", " AERODROME"):
        s = s.replace(junk, "")
    s = "".join(ch for ch in s if 32 <= ord(ch) < 127).strip(" ,-")
    return s[:18]
