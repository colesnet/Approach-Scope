"""The application: wires the feed, the tracker and the display together."""
import glob
import json
import os
import sys
import time
import traceback
import warnings

import pygame

import airport as airport_mod
import audio as audio_mod
import config
import enrich
import feed as feed_mod
import refdata
import sim as sim_mod
import tracker as trk
from ui import alert, gfx, icons, mapview, panels, profile, review, theme

W, H = 800, 480          # the canvas; SDL scales it to whatever screen is attached


class Ctx(object):
    """Everything a panel needs to draw one frame."""
    pass


def open_display(fullscreen, log):
    """Open the window, trying the sensible fall-backs before giving up.

    Started from the Pi's own desktop this works first time.  Started over SSH
    there is no display in the environment, so the desktop session (X11, then
    Wayland) and finally the bare console (KMS) are tried in turn.
    """
    attempts = []
    linux = sys.platform.startswith("linux") and os.environ.get("SDL_VIDEODRIVER") != "dummy"
    if linux and not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        # Started over SSH or from a console while the desktop is showing on the screen: use that
        # desktop first, so the picture does not go to an invisible console instead.
        run_dir = os.environ.get("XDG_RUNTIME_DIR") or "/run/user/%d" % os.getuid()
        for sock in sorted(glob.glob(os.path.join(run_dir, "wayland-[0-9]"))):
            attempts.append(({"XDG_RUNTIME_DIR": run_dir, "WAYLAND_DISPLAY": os.path.basename(sock),
                              "SDL_VIDEODRIVER": "wayland"}, "desktop (Wayland)"))
        if os.path.exists("/tmp/.X11-unix/X0"):
            env = {"DISPLAY": ":0"}
            xauth = os.path.expanduser("~/.Xauthority")
            if not os.environ.get("XAUTHORITY") and os.path.exists(xauth):
                env["XAUTHORITY"] = xauth
            attempts.append((env, "desktop on :0"))
    attempts.append(({}, "default"))
    if linux and not os.environ.get("SDL_VIDEODRIVER"):
        attempts.append(({"SDL_VIDEODRIVER": "kmsdrm"}, "console (KMS)"))
    if fullscreen:
        flag_sets = [pygame.FULLSCREEN | pygame.SCALED, pygame.FULLSCREEN, 0]
    else:
        flag_sets = [pygame.SCALED | pygame.RESIZABLE, 0]
    errors = []
    for env, label in attempts:
        saved = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            pygame.display.quit()
            pygame.display.init()
            for flags in flag_sets:
                try:
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore")
                        screen = pygame.display.set_mode((W, H), flags)
                    if label != "default":
                        log("Display opened via the %s." % label)
                    return screen
                except pygame.error as exc:
                    errors.append("%s: %s" % (label, exc))
        except pygame.error as exc:
            errors.append("%s: %s" % (label, exc))
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    raise RuntimeError("could not open a display.\n  " + "\n  ".join(errors) +
                       "\n  If you are starting this over SSH, try:  DISPLAY=:0 bash run.sh")


class App(object):
    def __init__(self, cfg, demo=False, headless=False, windowed=False, clock=None, wall=None,
                 seed=None, log=print, strict=False):
        self.cfg = cfg
        self.demo = demo
        self.headless = headless
        self.log = log
        self.strict = strict
        self.manual = clock is not None       # tests and screenshots drive time themselves
        self.clock = clock or time.monotonic
        self.wall = wall or time.time
        self.running = True
        self.errors = 0
        self._last_error_log = 0.0

        for w in cfg.warnings:
            log("Note: " + w)
        self.airport = airport_mod.Airport(cfg.airport_file)
        self.ref = refdata.RefData(os.path.join(config.BASE_DIR, "data"))
        for w in self.ref.warnings:
            log("Note: " + w)
        if cfg.has_home:
            h_name, h_lat, h_lon = cfg.home_name, cfg.home_lat, cfg.home_lon
        else:                           # not set up yet: the demo uses an example viewpoint
            h_name, h_lat, h_lon = self.airport.demo_home()
        eye = cfg.home_eye_m if cfg.home_eye_m is not None else self.airport.elev_ft / 3.2808 + 5.0
        self.home = trk.HomeGeometry(self.airport, h_lat, h_lon, eye, h_name)
        self.tracker = trk.Tracker(self.airport, self.home, cfg.home_runway, self.ref, wall_clock=self.wall)
        if cfg.home_runway and cfg.home_runway not in self.airport.runways:
            log("Note: HOME_RUNWAY %s is not a runway at %s - using %s" % (
                cfg.home_runway, self.airport.icao, self.tracker.home_runway))

        # --- data sources ---------------------------------------------------
        self.world = None
        self.routes = enrich.RouteService(self.airport.icao, enabled=cfg.routes_enabled and not demo)
        self.weather = enrich.WeatherService(self.airport.icao, enabled=cfg.metar_enabled and not demo)
        self.metar = None
        self._wx_seq = 0
        self._demo_routes = set()
        if demo:
            rid = self.tracker.home_runway
            self.world = sim_mod.Sim(self.airport, rid, seed=seed)
            self.world.go_around_every = 5
            self.world.populate()
            source = sim_mod.SimSource(self.world, self.clock, cfg.mil_prefixes)
            m = enrich.parse_metar_text("METAR %s 140130Z 24012KT CAVOK 22/09 Q1016" % self.airport.icao)
            m.obs_epoch = self.wall()
            self.weather.set(m)
        else:
            url = (cfg.feed_url.replace("{lat}", "%.5f" % self.airport.lat)
                   .replace("{lon}", "%.5f" % self.airport.lon).replace("{radius}", "%d" % cfg.feed_radius_nm))
            source = feed_mod.HttpSource(url, cfg.feed_timeout, cfg.mil_prefixes)
            self.feed_url = url
        self.feed = feed_mod.Feed(source, cfg.feed_interval, clock=self.clock)
        self._seq = 0
        self._next_poll = None

        # --- landing log ----------------------------------------------------
        self.log_path = os.path.join(config.BASE_DIR, "arrivals_log.json")
        self.keep_log = cfg.keep_log and not demo and not self.manual
        if self.keep_log:
            self._load_landings()
        self.tracker.on_landing = self._on_landing
        self.tracker.on_unlanding = lambda _t: self._save_landings()
        self.tracker.on_go_around = self._on_go_around

        # --- audio ------------------------------------------------------------
        self.audio = None
        if cfg.atc_stream_url and not headless:
            a = audio_mod.AudioManager(cfg.atc_stream_url)
            if a.initialise():
                self.audio = a
                if cfg.atc_auto_start:
                    a.toggle()
            else:
                log("Note: ATC audio is off (%s)" % (a.error or "no stream"))

        # --- display ------------------------------------------------------------
        if headless:
            os.environ["SDL_VIDEODRIVER"] = "dummy"
        pygame.display.init()
        pygame.font.init()
        # self.screen is always the 800x480 canvas everything is drawn on; self.display is the real
        # window.  Normally they are the same surface and SDL scales it to the screen for us.
        self._scale, self._offset, self._target = 1, (0, 0), None
        if headless:
            self.display = self.screen = pygame.display.set_mode((W, H))
        else:
            fullscreen = cfg.fullscreen and not windowed
            self.display = self.screen = open_display(fullscreen, log)
            if fullscreen and not (self.display.get_flags() & pygame.SCALED):
                self._software_scaling(log)
        pygame.display.set_caption("%s approach - %s" % (self.airport.name.title(), self.home.name))
        self.th = theme.Theme(cfg.font_regular, cfg.font_bold)
        for w in self.th.warnings:
            log("Note: " + w)
        self.data = mapview.MapData(self.airport)
        for w in self.data.warnings:
            log("Note: " + w)
        self.bank = icons.SpriteBank()

        # layout (fixed 800 x 480 canvas)
        self.header = panels.Header((0, 0, W, 25), self.th)
        self.map = mapview.MapView((4, 28, 464, 262), self.th, self.data, self.airport, self.home, self.ref,
                                   self.bank, cfg.show_other_traffic)
        self.profile = profile.ProfileView((4, 293, 464, 93), self.th, self.airport, self.home)
        self.card = panels.FlightCard((472, 28, 324, 358), self.th, self.ref)
        self.sky = panels.SkyPanel((4, 389, 326, 75), self.th, self.home, self.ref)
        self.queue = panels.WeatherPanel((334, 389, 284, 75), self.th)
        self.landed = panels.LandedPanel((622, 389, 174, 75), self.th)
        self.review = review.ReviewPanel((4, 28, 464, 358), self.th, self.ref, self.airport)
        self.review_pinned = False
        self._quiet_since = None
        self.alert = alert.GoAroundAlert(self.th, self.ref, cfg.font_bold, cfg.alert_seconds)
        self.status = panels.StatusBar((0, 466, W, 14), self.th)
        self.overlay = gfx.crt_overlay((W, H), vignette=False) if cfg.crt_effect else None
        self.night = pygame.Surface((W, H))
        self.night.fill((150, 150, 150))
        self.is_night, self._night_checked = False, -1e9

        self.ctx = Ctx()
        c = self.ctx
        c.cfg, c.airport, c.home, c.ref, c.data = cfg, self.airport, self.home, self.ref, self.data
        c.tracker, c.feed, c.routes, c.audio = self.tracker, self.feed, self.routes, self.audio
        c.demo = demo
        c.started = self.clock()
        c.started_wall = self.wall()
        c.review = False
        c.notice, c.notice_until = "", 0.0
        c.metar = None
        c.feat, c.arrivals, c.rw = None, [], self.airport.runways[self.tracker.active_id]
        c.now, c.wall = self.clock(), self.wall()
        self._mouse_seen = time.monotonic()

    def _software_scaling(self, log):
        """No accelerated renderer (so SDL will not scale): fill a bigger screen ourselves."""
        try:
            ww, wh = pygame.display.get_window_size()
            if min(ww // W, wh // H) < 2:
                return
            real = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
            rw, rh = real.get_size()
            f = max(1, min(rw // W, rh // H))
            self.display = real
            self.screen = pygame.Surface((W, H))
            self._scale = f
            self._offset = ((rw - W * f) // 2, (rh - H * f) // 2)
            self._target = real.subsurface(pygame.Rect(self._offset, (W * f, H * f)))
            log("Scaling the picture %dx in software to fill the %dx%d screen." % (f, rw, rh))
        except pygame.error:
            self.display = self.screen = pygame.display.set_mode((W, H), pygame.FULLSCREEN)

    def present(self):
        if self.display is not self.screen:
            if self._scale > 1:
                pygame.transform.scale(self.screen, self._target.get_size(), self._target)
            else:
                self.display.blit(self.screen, self._offset)
        pygame.display.flip()

    def _canvas_pos(self, pos):
        """Mouse position on the real screen -> position on the canvas."""
        if self.display is self.screen:
            return pos
        return ((pos[0] - self._offset[0]) // self._scale, (pos[1] - self._offset[1]) // self._scale)

    # ------------------------------------------------------------------
    # landing log
    # ------------------------------------------------------------------
    def _load_landings(self):
        try:
            with open(self.log_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, list):          # version 1 kept only landings
                data = {"landings": data}
            cutoff = self.wall() - 8 * 86400.0

            def records(key):
                rows = data.get(key, []) if isinstance(data, dict) else []
                keep = [trk.Landing.from_dict(d) for d in rows if isinstance(d, dict)]
                return [l for l in keep if l.wall >= cutoff]
            self.tracker.landings = records("landings")[:3000]
            self.tracker.go_arounds = records("go_arounds")[:500]
        except FileNotFoundError:
            pass
        except Exception as exc:
            self.log("Note: could not read arrivals_log.json (%s) - starting a fresh one" % exc)

    def _save_landings(self):
        t = self.tracker
        if not self.keep_log or (not t.landings and not t.go_arounds and not os.path.exists(self.log_path)):
            return
        try:
            tmp = self.log_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"landings": [l.to_dict() for l in t.landings[:3000]],
                           "go_arounds": [g.to_dict() for g in t.go_arounds[:500]]}, fh)
            os.replace(tmp, self.log_path)
        except Exception:
            pass      # a read-only card must never stop the display

    def _on_go_around(self, rec, track):
        self._on_landing(rec, track)
        if self.cfg.go_around_alert:
            self.alert.trigger(rec, track, self.clock())

    def _on_landing(self, rec, track):
        route = self.routes.get(rec.flight)
        if route is not None:
            rec.origin = route.origin_iata or route.origin_icao
        self._save_landings()

    # ------------------------------------------------------------------
    # one step of the world
    # ------------------------------------------------------------------
    def update(self):
        now = self.clock()
        if self.manual:
            if self._next_poll is None or now >= self._next_poll:
                self.feed.poll_once()
                self._next_poll = now + self.cfg.feed_interval
        snap = self.feed.take(self._seq)
        if snap is not None:
            self._seq = snap.seq
            self.tracker.ingest(snap)
            if self.world is not None:
                for cs, origin in list(self.world.routes.items()):
                    if cs not in self._demo_routes:
                        r = enrich.Route()
                        r.callsign, r.dest_icao = cs, self.airport.icao
                        r.origin_icao, r.origin_iata, r.origin_city = origin
                        self.routes.preload(cs, r)
                        self._demo_routes.add(cs)
        seq, metar = self.weather.get()
        if seq != self._wx_seq:
            self._wx_seq = seq
            self.metar = metar
            if metar is not None:
                self.tracker.set_weather(metar.qnh, metar.wind_dir, metar.wind_kt)
        self.tracker.tick(now)

    def render(self):
        c = self.ctx
        now = self.clock()
        c.now, c.wall = now, self.wall()
        c.feat, c.arrivals = self.tracker.featured(now)
        c.rw = self.tracker.view_runway(c.feat)
        c.metar = self.metar
        s = self.screen
        s.fill(theme.BG)
        self.header.draw(s, c)
        # day in review: when nothing is arriving (or only during curfew, if STATS_WHEN_QUIET is off),
        # or on demand with the R key.  It waits a minute of quiet before taking over, so a brief gap
        # between arrivals does not flick the map away, and gives way the moment an arrival appears.
        quiet = c.feat is None
        if not quiet:
            self._quiet_since = None
        elif self._quiet_since is None:
            self._quiet_since = now
        settled = quiet and now - self._quiet_since >= 60.0
        c.review = self.review_pinned or (quiet and panels.in_curfew(c.wall, self.airport.curfew)) \
            or (self.cfg.stats_when_quiet and settled)
        if c.review:
            self.review.draw(s, c)
        else:
            self.map.draw(s, c)
            self.profile.draw(s, c, self.map.span_nm)
        self.card.draw(s, c)
        self.sky.draw(s, c)
        self.queue.draw(s, c)
        self.landed.draw(s, c)
        self.status.draw(s, c)
        if self.alert.is_active(now):
            self.alert.draw(s, c)
        if now - self._night_checked > 30.0:
            self._night_checked = now
            mode = self.cfg.mode
            self.is_night = mode == "night" or (mode != "day" and theme.sun_elevation(
                c.wall, self.home.lat, self.home.lon) < -2.0)
        if self.is_night:
            s.blit(self.night, (0, 0), special_flags=pygame.BLEND_RGB_MULT)
        if self.overlay is not None:
            s.blit(self.overlay, (0, 0), special_flags=pygame.BLEND_RGB_MULT)

    def frame(self):
        """update + render, never letting one bad frame take the display down."""
        try:
            self.update()
            self.render()
        except Exception:
            if self.strict:
                raise
            self.errors += 1
            now = time.monotonic()
            if now - self._last_error_log > 30.0:
                self._last_error_log = now
                try:
                    with open(os.path.join(config.BASE_DIR, "error.log"), "a", encoding="utf-8") as fh:
                        fh.write("%s\n%s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), traceback.format_exc()))
                except Exception:
                    pass
                self.log("A drawing error was logged to error.log (the display keeps running).")

    # ------------------------------------------------------------------
    # input
    # ------------------------------------------------------------------
    def notice(self, text, seconds=4.0):
        self.ctx.notice, self.ctx.notice_until = text, self.clock() + seconds

    def _step_selection(self, direction):
        arr = self.ctx.arrivals
        if not arr:
            return
        cur = self.ctx.feat
        i = arr.index(cur) if cur in arr else -1
        nxt = arr[(i + direction) % len(arr)]
        self.tracker.select(nxt.hex, self.clock(), hold_s=60.0)

    def handle_events(self):
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                self.running = False
            elif ev.type in (pygame.KEYDOWN, pygame.MOUSEBUTTONDOWN) and self.alert.active is not None \
                    and not (ev.type == pygame.KEYDOWN and ev.key == pygame.K_q):
                self.alert.dismiss()          # any key or tap clears the go-around alert
            elif ev.type == pygame.KEYDOWN:
                if ev.key in (pygame.K_q, pygame.K_ESCAPE):
                    self.running = False
                elif ev.key == pygame.K_z:
                    self.notice("MAP VIEW: %s" % self.map.cycle_lock())
                elif ev.key in (pygame.K_TAB, pygame.K_RIGHT, pygame.K_DOWN):
                    self._step_selection(1)
                elif ev.key in (pygame.K_LEFT, pygame.K_UP):
                    self._step_selection(-1)
                elif ev.key == pygame.K_a and self.audio is not None:
                    self.audio.toggle()
                elif ev.key == pygame.K_r:
                    self.review_pinned = not self.review_pinned
                    self.notice("DAY IN REVIEW: %s" % ("ON" if self.review_pinned else "AUTO"))
                elif ev.key == pygame.K_s:
                    self.notice("SAVED " + os.path.basename(self.save_screenshot()))
            elif ev.type == pygame.MOUSEBUTTONDOWN:
                pos = self._canvas_pos(ev.pos)
                hexid = self.queue.hit(pos)
                if hexid:
                    self.tracker.select(hexid, self.clock(), hold_s=60.0)
                elif self.map.rect.collidepoint(pos):
                    self.notice("MAP VIEW: %s" % self.map.cycle_lock())
                self._mouse_seen = time.monotonic()
                pygame.mouse.set_visible(True)
            elif ev.type == pygame.MOUSEMOTION:
                self._mouse_seen = time.monotonic()
                pygame.mouse.set_visible(True)
        if time.monotonic() - self._mouse_seen > 3.0:
            pygame.mouse.set_visible(False)

    def save_screenshot(self, path=None):
        if path is None:
            folder = os.path.join(config.BASE_DIR, "screenshots")
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, time.strftime("approach-%Y%m%d-%H%M%S.png"))
        pygame.image.save(self.screen, path)
        return path

    # ------------------------------------------------------------------
    def run(self):
        try:        # stop cleanly when the service manager (or kill) asks
            import signal
            signal.signal(signal.SIGTERM, lambda *_: setattr(self, "running", False))
        except Exception:
            pass
        self.feed.start()
        self.routes.start()
        self.weather.start()
        ticker = pygame.time.Clock()
        slow = 0.0
        try:
            while self.running:
                self.handle_events()
                t0 = time.monotonic()
                self.frame()
                self.present()
                # on a slow board, stop animating the map zoom rather than stutter through it
                slow = 0.95 * slow + 0.05 * (time.monotonic() - t0)
                if slow > 0.070 and not self.map.instant:
                    self.map.instant = True
                ticker.tick(self.cfg.fps)
        except KeyboardInterrupt:
            pass
        finally:
            self.shutdown()

    def shutdown(self):
        self.feed.stop()
        self.routes.stop()
        self.weather.stop()
        if self.audio is not None:
            self.audio.shutdown()
        self._save_landings()
        pygame.quit()
