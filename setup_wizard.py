"""First-run setup:  bash run.sh --setup

Asks where you watch from, finds the airports near you, works out which
approach passes your place, and writes your config.ini.  For any airport
other than Adelaide (which has hand-checked data built in) it also generates
the runway and map files into local/.

Your location is rounded to about 100 metres before it is saved.  That is
still accurate for every number on the display, and it means no file the
program writes holds your exact address.

Can also run without questions, e.g.:
    python3 main.py --setup --location "-33.95,151.17" --airport YSSY --runway 16R --name HOME --yes
"""
import json
import os
import re
import sys

import config
import geo
import worldgen

BUILT_IN = {"YPAD": "data/airport_ypad.json"}
LOCAL_DIR = os.path.join(config.BASE_DIR, "local")
ROUND_DP = 3            # 3 decimal places of a degree: about 100 m


class Answers(object):
    def __init__(self, location=None, airport=None, runway=None, name=None, eye=None, yes=False):
        self.location, self.airport, self.runway = location, airport, runway
        self.name, self.eye, self.yes = name, eye, yes


def _ask(prompt, default=None, answers=None, preset=None):
    if preset is not None:
        print("%s %s" % (prompt, preset))
        return str(preset)
    if answers is not None and answers.yes:
        if default is None:
            raise SystemExit("Setup needs an answer for: %s" % prompt)
        print("%s %s" % (prompt, default))
        return str(default)
    suffix = " [%s]" % default if default not in (None, "") else ""
    try:
        text = input("%s%s " % (prompt, suffix)).strip()
    except EOFError:
        raise SystemExit("\nSetup cancelled.")
    return text or ("" if default is None else str(default))


def _choose(prompt, n, default, answers):
    while True:
        text = _ask(prompt, default, answers)
        if text.isdigit() and 1 <= int(text) <= n:
            return int(text) - 1
        print("  Type a number from 1 to %d." % n)


def round_location(lat, lon):
    return round(lat, ROUND_DP), round(lon, ROUND_DP)


# ---------------------------------------------------------------------------
def pick_location(answers):
    print("\n1. WHERE YOU WATCH FROM")
    print("   Paste your coordinates (in Google Maps, right-click your place and click the numbers")
    print("   to copy them), or type the name of your town or city.")
    while True:
        text = _ask("   Location:", None, answers, answers.location)
        answers.location = None
        coords = worldgen.parse_coords(text)
        if coords:
            return coords
        hits = worldgen.search_places(text)
        if not hits:
            print("   Nothing found for %r. Try coordinates like 51.47, -0.45 or a bigger town nearby." % text)
            if answers.yes:
                raise SystemExit("Setup could not find that location.")
            continue
        if len(hits) == 1 or answers.yes:
            h = hits[0]
        else:
            for i, h in enumerate(hits, 1):
                print("   %d) %s, %s %s" % (i, h["name"], h["region"].title(), h["country"]))
            h = hits[_choose("   Which one?", len(hits), 1, answers)]
        print("   Using the centre of %s. For the best results use your own coordinates instead." % h["name"])
        return h["lat"], h["lon"]


def pick_airport(lat, lon, answers):
    print("\n2. AIRPORT")
    if answers.airport:
        row = worldgen.find_airport(answers.airport)
        if row is None:
            raise SystemExit("No airport with the code %s in the built-in list." % answers.airport)
        print("   %s  %s" % (row["icao"], row["name"]))
        return row
    near = worldgen.nearest_airports(lat, lon, limit=8)
    if not near:
        print("   No airport with scheduled flights within 150 km of there.")
        while True:
            code = _ask("   Type an airport code instead (e.g. KLAX or LAX):", None, answers)
            row = worldgen.find_airport(code)
            if row:
                return row
            print("   Not found.")
    # suggest the nearest big airport, not just the nearest strip
    order = {"large_airport": 0, "medium_airport": 1}
    best = min(range(len(near)), key=lambda i: (order.get(near[i][1]["type"], 2) if near[i][0] < 60 else 3, near[i][0]))
    for i, (d, row) in enumerate(near, 1):
        print("   %d) %-5s %-28s %3.0f km%s" % (i, row["icao"], row["name"][:28], d, "   <- suggested" if i - 1 == best
                                                else ""))
    print("   (or type an airport code)")
    while True:
        text = _ask("   Which airport?", best + 1, answers)
        if text.isdigit() and 1 <= int(text) <= len(near):
            return near[int(text) - 1][1]
        row = worldgen.find_airport(text)
        if row:
            return row
        print("   Type a number from the list or an airport code.")


def pick_runway(spec, lat, lon, answers):
    print("\n3. THE APPROACH YOU CAN SEE")
    choices = worldgen.runway_choices(spec, lat, lon)
    if answers.runway:
        want = answers.runway.upper()
        for c in choices:
            if c[0] == want:
                print("   Runway %s" % want)
                return want
        raise SystemExit("Runway %s is not at %s (it has %s)." % (want, spec["icao"], ", ".join(c[0] for c in choices)))
    for i, (rid, passes, dtg, km, ft) in enumerate(choices, 1):
        if passes:
            what = "arrivals pass %.1f km from you, about %d ft up" % (km, int(round(ft, -1)))
        elif dtg < 0:
            what = "arrivals land heading away from you (they do not fly past)"
        else:
            what = "arrivals pass a long way from you"
        print("   %d) Runway %-4s %s" % (i, rid, what))
    return choices[_choose("   Which runway's arrivals do you want to watch?", len(choices), 1, answers)][0]


# ---------------------------------------------------------------------------
def set_values(path, values):
    """Write {section: {key: value}} into an ini file, keeping its comments and other settings."""
    lines = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    else:
        lines = ["# Your Approach Scope settings. Written by setup; you can edit it.",
                 "# Anything not here comes from config.default.ini.", ""]
    for section, kv in values.items():
        start = None
        for i, line in enumerate(lines):
            if line.strip().lower() == "[%s]" % section.lower():
                start = i
                break
        if start is None:
            if lines and lines[-1].strip():
                lines.append("")
            lines.append("[%s]" % section)
            start = len(lines) - 1
        end = len(lines)
        for i in range(start + 1, len(lines)):
            if lines[i].strip().startswith("["):
                end = i
                break
        for key, val in kv.items():
            pat = re.compile(r"^\s*#?\s*%s\s*=" % re.escape(key), re.I)
            for i in range(start + 1, end):
                if pat.match(lines[i]):
                    lines[i] = "%s = %s" % (key, val)
                    break
            else:
                lines.insert(end, "%s = %s" % (key, val))
                end += 1
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines).rstrip() + "\n")


def write_airport(row, home):
    """Returns the DATA_FILE path to put in the config (relative to the program folder)."""
    icao = row["icao"]
    if icao in BUILT_IN:
        return BUILT_IN[icao]
    spec = worldgen.airport_spec(row)
    mp, nearby = worldgen.build_map(spec, home)
    os.makedirs(LOCAL_DIR, exist_ok=True)
    safe = re.sub(r"[^A-Z0-9]", "", icao) or "AIRPORT"
    spec["nearby"] = nearby
    spec["map"] = "map_%s.json" % safe.lower()
    with open(os.path.join(LOCAL_DIR, spec["map"]), "w", encoding="utf-8") as fh:
        json.dump(mp, fh, separators=(",", ":"))
    path = os.path.join(LOCAL_DIR, "airport_%s.json" % safe.lower())
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(spec, fh, indent=1)
    rel = os.path.relpath(path, config.BASE_DIR)
    return path if rel.startswith("..") else rel.replace(os.sep, "/")


def run(answers=None, config_path=None, fetch_tables=True):
    answers = answers or Answers()
    config_path = config_path or os.path.join(config.BASE_DIR, "config.ini")
    print("APPROACH SCOPE SETUP")
    print("Your location is rounded to about 100 m before it is saved, so your exact address is never")
    print("stored. It only goes in config.ini on this machine, which is never part of a download.")
    lat, lon = pick_location(answers)
    lat, lon = round_location(lat, lon)
    print("   Saved as %.3f, %.3f" % (lat, lon))

    row = pick_airport(lat, lon, answers)
    spec = worldgen.airport_spec(row)
    d_km = worldgen.dist_km(lat, lon, row["lat"], row["lon"])
    if d_km > 80:
        print("   Note: that airport is %.0f km away, which is further than the display is designed for." % d_km)
    rid = pick_runway(spec, lat, lon, answers)

    print("\n4. A NAME FOR YOUR PLACE ON THE MAP")
    name = _ask("   Name (up to 16 letters):", "HOME", answers, answers.name).upper()[:16] or "HOME"

    print("\n5. EYE HEIGHT")
    print("   Your eye height above sea level, in metres. It sets how high above the horizon to look.")
    print("   The default assumes the ground at your place is level with the airport, plus a little;")
    print("   add about 3 m for each floor you are up.")
    default_eye = int(round(spec["elev_ft"] / geo.FT_PER_M + 5))
    while True:
        text = _ask("   Eye height (m):", default_eye, answers, answers.eye)
        try:
            eye = float(text)
            if -450 <= eye <= 6000:
                break
        except ValueError:
            pass
        print("   Type a number of metres.")
        answers.eye = None

    print("\nWriting settings...")
    data_file = write_airport(row, (lat, lon))
    set_values(config_path, {
        "Home": {"NAME": name, "LAT": "%.3f" % lat, "LON": "%.3f" % lon, "EYE_HEIGHT_M": "%g" % eye},
        "Airport": {"DATA_FILE": data_file, "HOME_RUNWAY": rid},
    })
    print("   %s" % config_path)
    if not data_file.startswith("data/"):
        print("   %s (runways and map for %s)" % (data_file, row["icao"]))
    tables = os.path.join(LOCAL_DIR, "aircraft_types.json")
    if fetch_tables and not os.path.exists(tables):
        print("\nDownloading the full aircraft-type and airline tables (one-off)...")
        try:
            import refdata
            print("   %s" % refdata.fetch_tables(LOCAL_DIR))
        except Exception as exc:
            print("   Could not download them (%s). The display still works, with less detailed names;"
                  % (str(exc) or type(exc).__name__))
            print("   run setup again later to retry.")
    print("\nDone. Start the display with:   bash run.sh")
    return config_path


if __name__ == "__main__":
    run()
    sys.exit(0)
