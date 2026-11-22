#!/usr/bin/env python3
"""Approach Scope - start here.

    python3 main.py --setup             choose your location, airport and runway (first time)
    python3 main.py                     live traffic, full screen
    python3 main.py --demo              simulated traffic (no internet needed)
    python3 main.py --window            in a window instead of full screen
    python3 main.py --check             test fonts, data files and the internet feeds, then exit
    python3 main.py --autostart on      start by itself whenever the desktop starts

Keys:  Q / Esc quit    Z map zoom    Tab next flight    A ATC audio    S screenshot
"""
import argparse
import os
import sys

# Leave the sound device alone so VLC can use it for ATC audio (pygame only draws here).
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

if sys.version_info < (3, 7):
    sys.exit("Python 3.7 or newer is needed (this is %s)." % sys.version.split()[0])

try:
    import pygame
except ImportError:
    sys.exit("pygame is not installed in this Python.\n"
             "Start it with:  bash run.sh      (it finds or builds the right environment)\n"
             "or install it:  pip install -r requirements.txt")

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import config  # noqa: E402


def check(cfg):
    """Pre-flight: everything the display depends on, reported in plain words."""
    import airport
    import enrich
    import feed
    import net
    import refdata
    import tracker
    ok = True

    def line(good, what, detail=""):
        print("  [%s] %s%s" % (" OK " if good else "FAIL", what, ("  - " + detail) if detail else ""))
        return good

    print("Approach Scope %s - pre-flight check" % config.VERSION)
    line(True, "Python %s, pygame %s (SDL %s)" % (sys.version.split()[0], pygame.version.ver,
                                                 ".".join(str(v) for v in pygame.get_sdl_version())))
    for w in cfg.warnings:
        line(False, "config.ini", w)
    try:
        ap = airport.Airport(cfg.airport_file)
        line(True, "airport data", "%s, runways %s" % (ap.icao, " ".join(ap.runway_order)))
    except Exception as exc:
        line(False, "airport data", str(exc))
        return False
    ref = refdata.RefData(os.path.join(BASE, "data"))
    ok &= line(not ref.warnings, "aircraft and airline tables",
               "; ".join(ref.warnings) or "%d types, %d operators" % (len(ref.types), len(ref.operators)))
    if not cfg.has_home:
        ok &= line(False, "your place", "not set up yet - run:  bash run.sh --setup")
        home = None
    else:
        home = tracker.HomeGeometry(ap, cfg.home_lat, cfg.home_lon, cfg.home_eye_m or 0.0, cfg.home_name)
    rid = cfg.home_runway if cfg.home_runway in ap.runways else ap.runway_order[0]
    d_ab, x_ab = home.abeam[rid] if home else (0.0, 0.0)
    if home is None:
        pass
    elif home.passes(rid):
        line(True, "your place", "runway %s arrivals pass %.1f km away, %.1f NM before touchdown, at about %d ft" % (
            rid, abs(x_ab) * 1.852, d_ab, round(ap.runways[rid].gp_alt_ft(d_ab), -1)))
    else:
        line(True, "your place", "runway %s arrivals do not fly past it" % rid)
    import time as _time
    line(True, "clock", "%s (time zone %s) - curfew, the day's stats and night mode use this" % (
        _time.strftime("%Y-%m-%d %H:%M"), _time.strftime("%Z")))
    pygame.font.init()
    for path in (cfg.font_regular, cfg.font_bold):
        try:
            pygame.font.Font(path, 14)
            line(True, "font " + os.path.basename(path))
        except Exception as exc:
            ok &= line(False, "font " + os.path.basename(path), str(exc))
    url = (cfg.feed_url.replace("{lat}", "%.5f" % ap.lat).replace("{lon}", "%.5f" % ap.lon)
           .replace("{radius}", "%d" % cfg.feed_radius_nm))
    try:
        reports, raw = feed.HttpSource(url, cfg.feed_timeout, cfg.mil_prefixes).fetch()
        line(True, "aircraft feed", "%d aircraft within %d NM right now" % (len(reports), cfg.feed_radius_nm))
    except Exception as exc:
        ok &= line(False, "aircraft feed", "%s  (%s)" % (exc, url))
    client = net.Client()
    if cfg.metar_enabled:
        try:
            m = enrich.WeatherService(ap.icao).fetch_once(client)
            line(True, "weather", "%s  QNH %s" % (m.wind_text() or "no wind group", int(m.qnh) if m.qnh else "?"))
        except Exception as exc:
            line(False, "weather (optional)", str(exc))
    if cfg.routes_enabled:
        try:
            _st, obj = client.get_json(enrich.ROUTE_URL % "QFA681", timeout=10.0)
            line(True, "route look-ups", "adsbdb.com answered")
        except Exception as exc:
            line(False, "route look-ups (optional)", str(exc))
    print("Ready." if ok else "Something above needs attention - the display may still run with less detail.")
    return ok


AUTOSTART = os.path.expanduser("~/.config/autostart/approach-scope.desktop")


def autostart(on):
    """Start (or stop starting) the display when the Pi's desktop starts."""
    if on:
        os.makedirs(os.path.dirname(AUTOSTART), exist_ok=True)
        with open(AUTOSTART, "w", encoding="utf-8") as fh:
            fh.write("[Desktop Entry]\nType=Application\nName=Approach Scope\n"
                     "Exec=bash \"%s\"\n" % os.path.join(BASE, "run.sh"))
        print("The display will now start by itself whenever the desktop starts (%s)." % AUTOSTART)
    else:
        if os.path.exists(AUTOSTART):
            os.remove(AUTOSTART)
        print("The display will no longer start by itself.")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Live display of the aircraft arriving at your local airport.")
    ap.add_argument("--demo", action="store_true", help="simulated traffic instead of the live feed")
    ap.add_argument("--window", action="store_true", help="run in a window instead of full screen")
    ap.add_argument("--check", action="store_true", help="test the set-up and exit")
    ap.add_argument("--setup", action="store_true", help="choose your location, airport and runway")
    ap.add_argument("--autostart", choices=("on", "off"), help="start automatically with the desktop")
    ap.add_argument("--config", default=None, help="path to your config file (default: config.ini beside main.py)")
    g = ap.add_argument_group("answers for --setup without questions")
    g.add_argument("--location", help="coordinates or a town name")
    g.add_argument("--airport", help="airport code, e.g. KLAX")
    g.add_argument("--runway", help="runway whose arrivals you watch, e.g. 24R")
    g.add_argument("--name", help="name for your place on the map")
    g.add_argument("--eye", help="eye height above sea level, metres")
    g.add_argument("--yes", action="store_true", help="accept the suggested answer to every other question")
    argv = list(sys.argv[1:] if argv is None else argv)
    for i in range(len(argv) - 1):          # allow --location -33.9,151.2 (a leading minus sign)
        if argv[i] == "--location" and argv[i + 1].startswith("-"):
            argv[i:i + 2] = ["--location=" + argv[i + 1]]
            break
    args = ap.parse_args(argv)

    if args.autostart:
        return autostart(args.autostart == "on")
    if args.setup:
        import setup_wizard
        setup_wizard.run(setup_wizard.Answers(args.location, args.airport, args.runway, args.name, args.eye,
                                              args.yes), args.config)
        return 0

    cfg = config.Config(args.config)
    if args.check:
        return 0 if check(cfg) else 1
    if not cfg.has_home and not args.demo:
        if sys.stdin.isatty():
            print("First run: let's set up where you watch from.\n")
            import setup_wizard
            setup_wizard.run(config_path=args.config)
            cfg = config.Config(args.config)
        else:
            print("Not set up yet. Run:  bash run.sh --setup     (or try:  bash run.sh --demo)")
            return 1

    import app
    try:
        a = app.App(cfg, demo=args.demo, windowed=args.window)
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        print("Could not start: %s" % exc)
        return 1
    home = a.home
    rid = a.tracker.home_runway
    print("%s approach display - watching from %s" % (a.airport.name.title(), home.name))
    if home.passes(rid):
        d_ab, x_ab = home.abeam[rid]
        print("Runway %s arrivals pass %.1f km from you, about %d ft up, %.1f NM before touchdown." % (
            rid, abs(x_ab) * 1.852, round(a.airport.runways[rid].gp_alt_ft(d_ab), -1), d_ab))
    print("Data: %s.  Keys: Q quit, Z zoom, Tab next flight, R day in review, S screenshot." % (
        "simulated traffic (demo)" if args.demo else a.feed.label.lower()))
    a.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
