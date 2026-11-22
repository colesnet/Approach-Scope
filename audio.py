"""Optional live ATC audio (uses VLC if it is installed; silently absent if not)."""


class AudioManager(object):
    def __init__(self, stream_url):
        self.stream_url = stream_url
        self.player = None
        self.instance = None
        self.error = ""

    def initialise(self):
        if not self.stream_url:
            return False
        try:
            import vlc
            self.instance = vlc.Instance("--quiet")
            self.player = self.instance.media_player_new()
            media = self.instance.media_new(self.stream_url)
            media.add_option(":network-caching=10000")
            self.player.set_media(media)
            return True
        except Exception as exc:        # python-vlc or VLC itself missing
            self.error = "%s: %s" % (type(exc).__name__, exc)
            self.player = self.instance = None
            return False

    def toggle(self):
        if self.player is None:
            return
        try:
            if self.player.is_playing():
                self.player.stop()
            else:
                self.player.play()
        except Exception:
            pass

    def is_playing(self):
        try:
            return bool(self.player is not None and self.player.is_playing())
        except Exception:
            return False

    def shutdown(self):
        try:
            if self.player is not None:
                self.player.stop()
            if self.instance is not None:
                self.instance.release()
        except Exception:
            pass
        self.player = self.instance = None
