"""Tiny HTTP helper shared by the feed, weather and route lookups.

Uses `requests` when it is installed (it is in your existing venv) and falls
back to the standard library otherwise, so a missing package can never be the
reason the display has no data.
"""
import json

import config

try:
    import requests
    _HAVE_REQUESTS = True
except Exception:  # pragma: no cover - only when requests is absent
    requests = None
    _HAVE_REQUESTS = False

import urllib.error
import urllib.request


class HttpError(Exception):
    def __init__(self, message, status=None):
        Exception.__init__(self, message)
        self.status = status


class Client(object):
    """One per thread. Keeps the connection open between polls."""

    def __init__(self):
        self._session = None
        if _HAVE_REQUESTS:
            self._session = requests.Session()
            self._session.headers.update({"User-Agent": config.USER_AGENT, "Accept": "application/json"})

    def get_json(self, url, timeout=8.0):
        """Returns (status_code, parsed_json_or_None). Raises HttpError on failure."""
        if self._session is not None:
            try:
                resp = self._session.get(url, timeout=timeout)
            except requests.RequestException as exc:
                raise HttpError(_short(exc))
            status = resp.status_code
            if status == 204:
                return status, None
            if status != 200:
                raise HttpError("HTTP %d" % status, status)
            try:
                return status, resp.json()
            except ValueError:
                raise HttpError("response was not JSON", status)
        req = urllib.request.Request(url, headers={"User-Agent": config.USER_AGENT, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = resp.getcode()
                body = resp.read()
        except urllib.error.HTTPError as exc:
            raise HttpError("HTTP %d" % exc.code, exc.code)
        except Exception as exc:
            raise HttpError(_short(exc))
        if status == 204 or not body:
            return status, None
        try:
            return status, json.loads(body.decode("utf-8", "replace"))
        except ValueError:
            raise HttpError("response was not JSON", status)

    def close(self):
        if self._session is not None:
            try:
                self._session.close()
            except Exception:
                pass


def _short(exc):
    """A one-line reason suitable for the status bar."""
    name = type(exc).__name__
    text = str(exc)
    low = text.lower()
    if "timed out" in low or "timeout" in name.lower():
        return "timed out"
    if "name or service not known" in low or "temporary failure in name resolution" in low \
            or "getaddrinfo" in low or "nodename nor servname" in low or "name resolution" in low:
        return "no DNS (is the network up?)"
    if "connection refused" in low:
        return "connection refused"
    if "network is unreachable" in low or "no route to host" in low:
        return "network unreachable"
    if "ssl" in low or "certificate" in low:
        return "TLS error (check the Pi's clock)"
    return name
