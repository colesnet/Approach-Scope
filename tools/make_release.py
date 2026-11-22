"""Builds a public release zip, and refuses to if anything personal would go in it.

    python3 tools/make_release.py [--private-words FILE] [--out release]

What it does:
  1. Retakes the screenshots in docs/ from the built-in demo viewpoint, so no
     picture in the release can show where you live.
  2. Collects the program, leaving out everything personal or not ours to share:
     config.ini, local/, arrivals_log.json, error.log, screenshots/, venvs, and the
     aircraft/airline tables (setup downloads those on each user's own machine).
  3. Scans every file that would be shipped for
       - your coordinates from config.ini (to ~10 m, rounded or cut short),
       - your place name from config.ini,
       - every word or phrase in the private-words file (default:
         ~/.approach-scope-private - one per line: street name, surname, home IP...).
     Any hit stops the build and says where it is.
  4. Writes release/approach-scope-<version>.zip
"""
import argparse
import configparser
import gzip
import os
import re
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

SKIP_DIRS = {".git", "__pycache__", "local", "release", "screenshots", ".pytest_cache"}
SKIP_FILES = {"config.ini", "arrivals_log.json", "arrivals_log.json.tmp", "error.log",
              os.path.join("data", "aircraft_types.json"), os.path.join("data", "operators.json")}
IMAGES = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")


def private_patterns(words_file):
    """(coordinate pairs, words) to look for."""
    pairs, words = [], []
    cp = configparser.ConfigParser(inline_comment_prefixes=("#", ";"), interpolation=None)
    cp.read(os.path.join(ROOT, "config.ini"), encoding="utf-8")
    try:
        lat, lon = float(cp.get("Home", "LAT")), float(cp.get("Home", "LON"))

        def forms(v):
            out = set()
            for dp in (3, 4, 5):
                out.add("%.*f" % (dp, v))                        # rounded
                s = repr(v)
                if "." in s and len(s.split(".")[1]) >= dp:
                    out.add(s[:s.index(".") + dp + 1])          # cut short
            return sorted(out)
        pairs.append((forms(lat), forms(lon)))
    except (configparser.Error, ValueError):
        pass
    try:
        name = cp.get("Home", "NAME").strip()
        if name and name.upper() not in ("HOME", "DEMO HOME"):
            words.append(name)
    except configparser.Error:
        pass
    if words_file and os.path.exists(words_file):
        with open(words_file, "r", encoding="utf-8") as fh:
            words += [w.strip() for w in fh if w.strip() and not w.startswith("#")]
    return pairs, words


def text_of(path):
    with open(path, "rb") as fh:
        raw = fh.read()
    if path.endswith(".gz"):
        raw = gzip.decompress(raw)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def scan(files, pairs, words):
    problems = []
    for rel in files:
        path = os.path.join(ROOT, rel)
        if rel.lower().endswith(IMAGES) and not rel.startswith("docs" + os.sep):
            problems.append("%s: a picture outside docs/ (only the regenerated screenshots may ship)" % rel)
            continue
        text = text_of(path)
        if text is None:
            continue
        for lats, lons in pairs:
            for la in lats:
                for m in re.finditer(re.escape(la), text):
                    window = text[max(0, m.start() - 80):m.end() + 80]
                    if any(lo in window for lo in lons):
                        problems.append("%s: contains your coordinates (%s ...)" % (rel, la))
                        break
                else:
                    continue
                break
        if rel.startswith("data" + os.sep):
            continue        # public map data: place names in it are not about you
        low = text.lower()
        for w in words:
            if w.lower() in low:
                problems.append("%s: contains the private word %r" % (rel, w))
    return problems


def screenshots():
    """Retake docs/ pictures with the demo viewpoint (never your own place)."""
    os.environ["SDL_VIDEODRIVER"] = "dummy"
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    import time
    import pygame
    import config
    import app

    class Clock(object):
        t = 1000.0

        def __call__(self):
            return self.t
    clk = Clock()
    cfg = config.Config(os.path.join(ROOT, "tools", "no-such-config.ini"))
    wall0 = time.mktime((2026, 11, 21, 13, 0, 0, 0, 0, -1))
    a = app.App(cfg, demo=True, headless=True, clock=clk, wall=lambda: wall0 + clk.t - 1000.0, seed=4,
                strict=True, log=lambda *_: None)
    a.keep_log = False
    a.alert.seconds = 0.0           # no alert in the main screenshots

    def run_to(t):
        while clk.t - 1000.0 < t:
            clk.t += 1.0
            a.update()
            a.tracker.featured(clk.t)
        for _ in range(8):
            clk.t += 0.25
            a.frame()
    os.makedirs(os.path.join(ROOT, "docs"), exist_ok=True)
    for old in os.listdir(os.path.join(ROOT, "docs")):
        if old.lower().endswith(IMAGES):
            os.remove(os.path.join(ROOT, "docs", old))
    run_to(20)
    pygame.image.save(a.screen, os.path.join(ROOT, "docs", "screenshot.png"))
    run_to(100)
    pygame.image.save(a.screen, os.path.join(ROOT, "docs", "screenshot-short-final.png"))
    run_to(1500)
    a.review_pinned = True
    run_to(1502)
    pygame.image.save(a.screen, os.path.join(ROOT, "docs", "day-in-review.png"))
    a.review_pinned = False
    a.alert.seconds = 30.0
    if a.tracker.go_arounds:
        a.alert.trigger(a.tracker.go_arounds[0], None, clk.t)
    else:
        import tracker
        a.alert.trigger(tracker.Landing(a.wall(), "QFA741", "B738", "VH-VZT", "23", "x", low=420.0), None, clk.t)
    clk.t += 3.0
    a.frame()
    pygame.image.save(a.screen, os.path.join(ROOT, "docs", "go-around-alert.png"))
    pygame.quit()


def collect():
    files = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        rel_dir = os.path.relpath(dirpath, ROOT)
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("venv")
                             and not d.startswith("."))
        for f in sorted(filenames):
            rel = os.path.normpath(os.path.join(rel_dir, f))
            if rel in SKIP_FILES or f.endswith(".pyc") or f.startswith("."):
                if f != ".gitignore":
                    continue
            files.append(rel)
    return files


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--private-words", default=os.path.expanduser("~/.approach-scope-private"))
    ap.add_argument("--out", default=os.path.join(ROOT, "release"))
    ap.add_argument("--no-screenshots", action="store_true", help="keep the pictures in docs/ as they are")
    args = ap.parse_args()
    import config
    if not args.no_screenshots:
        print("Retaking the screenshots from the demo viewpoint...")
        screenshots()
    pairs, words = private_patterns(args.private_words)
    print("Checking for: your coordinates%s, %d private word(s)" % ("" if pairs else " (none set)", len(words)))
    files = collect()
    problems = scan(files, pairs, words)
    if problems:
        print("\nNOT BUILT - personal details found:")
        for p in problems:
            print("  " + p)
        return 1
    os.makedirs(args.out, exist_ok=True)
    out = os.path.join(args.out, "approach-scope-%s.zip" % config.VERSION)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in files:
            z.write(os.path.join(ROOT, rel), os.path.join("approach-scope", rel))
    print("Checked %d files - nothing personal found.\nBuilt %s (%d KB)" % (len(files), out,
                                                                          os.path.getsize(out) // 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())
