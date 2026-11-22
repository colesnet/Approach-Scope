"""Full-screen go-around alert: flashing red, hazard stripes, the flight number in huge type."""
import pygame

RED = (235, 18, 18)
DARK = (26, 0, 0)
BLACK = (0, 0, 0)


class GoAroundAlert(object):
    def __init__(self, th, ref, font_path, seconds=30.0):
        self.th = th
        self.ref = ref
        self.font_path = font_path
        self.seconds = float(seconds)
        self.active = None          # (record, track, start time)
        self._fonts = {}
        self._text = {}

    # ------------------------------------------------------------------
    def trigger(self, rec, track, now):
        self.active = (rec, track, now)

    def dismiss(self):
        self.active = None

    def is_active(self, now):
        if self.active is not None and now - self.active[2] > self.seconds:
            self.active = None
        return self.active is not None

    # ------------------------------------------------------------------
    def _font(self, size):
        f = self._fonts.get(size)
        if f is None:
            try:
                f = pygame.font.Font(self.font_path, size)
            except Exception:
                f = pygame.font.Font(None, int(size * 1.25))
            self._fonts[size] = f
        return f

    def _render(self, size, text, color):
        key = (size, text, color)
        s = self._text.get(key)
        if s is None:
            if len(self._text) > 60:
                self._text.clear()
            s = self._font(size).render(text, True, color)
            self._text[key] = s
        return s

    def _centre(self, surf, size, text, y, color, max_w=780):
        s = self._render(size, text, color)
        while s.get_width() > max_w and size > 14:
            size -= 4
            s = self._render(size, text, color)
        surf.blit(s, ((surf.get_width() - s.get_width()) // 2, y))
        return s.get_height()

    @staticmethod
    def _stripes(surf, rect, offset, fg, bg):
        r = pygame.Rect(rect)
        surf.fill(bg, r)
        prev = surf.get_clip()
        surf.set_clip(r)
        width, h = 26, r.h
        x = r.x - h - 2 * width + (offset % (2 * width))
        while x < r.right + h:
            pygame.draw.polygon(surf, fg, [(x, r.bottom), (x + width, r.bottom), (x + width + h, r.y), (x + h, r.y)])
            x += 2 * width
        surf.set_clip(prev)

    # ------------------------------------------------------------------
    def draw(self, surf, ctx):
        rec, track, t0 = self.active
        now = ctx.now
        age = now - t0
        w, h = surf.get_size()
        on = int(age * 2.5) % 2 == 0          # 2.5 flashes a second
        bg, fg = (RED, BLACK) if on else (DARK, RED)
        surf.fill(bg)

        # hazard bands, crawling sideways
        band = 34
        crawl = age * 60.0
        self._stripes(surf, (0, 0, w, band), crawl, RED if not on else BLACK, BLACK if not on else RED)
        self._stripes(surf, (0, h - band, w, band), -crawl, RED if not on else BLACK, BLACK if not on else RED)
        pygame.draw.rect(surf, fg, (6, band + 6, w - 12, h - 2 * band - 12), 4)

        rw = " RUNWAY %s" % rec.rwy if rec.rwy else ""
        self._centre(surf, 24, "APPROACH ABANDONED" + rw, band + 18, fg)
        self._centre(surf, 112, "GO-AROUND", band + 44, fg)
        self._centre(surf, 88, rec.flight or "UNKNOWN", band + 162, fg)

        al = self.ref.airline(rec.flight, rec.reg)
        parts = []
        if al:
            parts.append(al["name"] + ("  " + al["flight_iata"] if al["flight_iata"] else ""))
        tname = self.ref.type_name(rec.type)
        if tname:
            parts.append(tname)
        if rec.reg:
            parts.append(rec.reg)
        self._centre(surf, 24, "   ".join(parts), band + 262, fg)

        info = []
        if rec.low:
            info.append("CAME DOWN TO %d FT" % (int(round(rec.low / 10.0)) * 10))
        if track is not None and ctx.tracker.tracks.get(track.hex) is track:
            _x, _y, alt, _hd = track.state_at(now)
            if alt is not None:
                verb = "CLIMBING THROUGH" if (track.vrate or 0) > 200 else "NOW AT"
                info.append("%s %d FT" % (verb, int(round(alt / 10.0)) * 10))
        if info:
            self._centre(surf, 20, "   ".join(info), band + 298, fg)

        # time left
        left = max(0.0, self.seconds - age)
        bar_w = w - 120
        y = h - band - 44
        pygame.draw.rect(surf, fg, (60, y, bar_w, 10), 1)
        pygame.draw.rect(surf, fg, (60, y, int(bar_w * left / self.seconds), 10))
        self._centre(surf, 14, "CLEARS IN %d S   -   PRESS ANY KEY OR TAP TO DISMISS" % int(left + 0.99),
                     y + 16, fg)
