"""How fast the app may talk to each outside service, in one place for everyone.

Every request to someone else's service goes through here: the background
jobs (artist refresh, the Discover scrapes, the audio pre-load, similar-artist
scans), every plugin, and whatever a page asks for. Each service has a budget
-- so many calls per so many seconds, taken from its published limit with
some headroom -- and all callers draw on the same one, so a job and a page
running at once still can't go over it.

When a service says slow down anyway (429, 503, a Retry-After, a rate-limit
header, a quota error in the body, or iTunes' 403), it is left alone for as
long as it asked -- or for a while that doubles each time it says so again --
and callers get :class:`Throttled`, which is a failure to answer, never "not
found": nothing learned from it may be stored as a miss.

Your own servers (the music libraries, slskd, FlareSolverr itself) don't go
through here: they aren't anyone else's service to overload.
"""

import threading
import time
from collections import deque
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import requests

from . import db


class Throttled(requests.RequestException):
    """A service can't be asked right now: its budget is spent or it said no."""

    def __init__(self, host, wait):
        super().__init__(f"{host} is rate limited for another {wait:.0f}s")
        self.host = host
        self.wait = wait


@dataclass(frozen=True)
class Rule:
    calls: int                 # this many requests...
    per: float                 # ...in any window this many seconds long
    cooldown: float = 15       # first pause after a "slow down"; doubles each time
    forbidden_is_limit: bool = False  # the service says 403 when it means 429


# The most specific entry wins: "audio-ssl.itunes.apple.com" is iTunes' audio
# CDN, not the search API under "itunes.apple.com". Hosts are matched with and
# without their subdomains, so "www.last.fm" falls under "last.fm".
_RULES = {
    # Apple: "approximately 20 calls per minute", then 403 for a while.
    "itunes.apple.com": Rule(18, 60, cooldown=60, forbidden_is_limit=True),
    "audio-ssl.itunes.apple.com": Rule(10, 1),
    "mzstatic.com": Rule(10, 1),
    # Deezer: 50 requests per 5 seconds; its images and samples are a CDN.
    "api.deezer.com": Rule(40, 5),
    "dzcdn.net": Rule(10, 1),
    # MusicBrainz: one a second (see _musicbrainz_rule), 503 past it.
    "coverartarchive.org": Rule(5, 1),
    "archive.org": Rule(5, 1),
    # Last.fm: five a second averaged over five minutes, error 29 past it.
    "ws.audioscrobbler.com": Rule(4, 1),
    # The website itself (track pages, the login-only lists) is scraped, so
    # it gets a much gentler pace than the API.
    "last.fm": Rule(1, 2),
    "lastfm.freetls.fastly.net": Rule(10, 1),
    # ListenBrainz also says what's left in X-RateLimit-* headers.
    "listenbrainz.org": Rule(5, 1),
    "kexp.org": Rule(2, 1),
    "hypem.com": Rule(2, 1),
    "bandcamp.com": Rule(5, 1),
    "bcbits.com": Rule(5, 1),
    # yt-dlp makes several requests per video; one video every few seconds.
    "youtube.com": Rule(1, 3),
    # Discord: five messages per two seconds per webhook.
    "discord.com": Rule(5, 2),
}
# Anything else: review sites, blogs, image hosts, notification services.
_DEFAULT = Rule(2, 1)
# Doubling stops here.
_MAX_COOLDOWN = 600
# How long a caller waits for a slot unless it says otherwise. Background jobs
# can wait; a page that can't should pass something shorter.
DEFAULT_MAX_WAIT = 120

_lock = threading.Lock()
_buckets = {}


class _Bucket:
    def __init__(self):
        self.sent = deque()    # times of recent (and reserved) requests
        self.cool_until = 0.0
        self.cool_len = 0.0


def _musicbrainz_rule():
    """One request a second at most; the setting can only make it slower."""
    try:
        ms = float(db.get_setting("musicbrainz_rate_limit_ms") or 1100)
    except (TypeError, ValueError):
        ms = 1100
    return Rule(1, max(ms / 1000.0, 1.0), cooldown=5)


def _host(url):
    return (urlsplit(url).hostname or "").lower()


def _rule_for(host):
    """(bucket name, rule): the most specific rule that covers *host*."""
    parts = host.split(".")
    for i in range(len(parts) - 1):
        domain = ".".join(parts[i:])
        if domain == "musicbrainz.org":
            return domain, _musicbrainz_rule()
        if domain in _RULES:
            return domain, _RULES[domain]
    return host, _DEFAULT


def _bucket(name):
    found = _buckets.get(name)
    if found is None:
        found = _buckets[name] = _Bucket()
    return found


def acquire(url, max_wait=DEFAULT_MAX_WAIT):
    """Wait for this request's turn. Raises Throttled if that's too far off.

    For a caller that sends the request itself (yt-dlp, a challenge solver):
    everyone else uses :func:`request`, which calls this.
    """
    host = _host(url)
    name, rule = _rule_for(host)
    with _lock:
        bucket = _bucket(name)
        now = time.time()
        start = max(now, bucket.cool_until)
        while len(bucket.sent) > rule.calls:
            bucket.sent.popleft()
        # Sliding window: the earliest time with fewer than `calls` requests
        # in the `per` seconds before it, counting the ones already reserved.
        if len(bucket.sent) >= rule.calls:
            start = max(start, bucket.sent[-rule.calls] + rule.per)
        if start - now > max_wait:
            raise Throttled(host, start - now)
        bucket.sent.append(start)
    wait = start - time.time()
    if wait > 0:
        time.sleep(wait)


def _seconds(value):
    """A header's wait as seconds from now: a number, an epoch, or an HTTP date."""
    if not value:
        return None
    try:
        number = float(value)
    except ValueError:
        try:
            return max(parsedate_to_datetime(value).timestamp() - time.time(), 0)
        except (TypeError, ValueError, IndexError):
            return None
    # A reset given as an epoch rather than a delay.
    return max(number - time.time(), 0) if number > 1e9 else max(number, 0)


def back_off(url, seconds=None):
    """The service said slow down (in its own words): leave it alone a while."""
    host = _host(url)
    name, rule = _rule_for(host)
    with _lock:
        bucket = _bucket(name)
        if seconds is None:
            bucket.cool_len = (min(bucket.cool_len * 2, _MAX_COOLDOWN)
                               if bucket.cool_len else rule.cooldown)
            seconds = bucket.cool_len
        bucket.cool_until = max(bucket.cool_until, time.time() + min(seconds, _MAX_COOLDOWN))


def note(url, status, headers=None):
    """Learn from an answer. True when it was the service saying slow down."""
    headers = headers or {}
    name, rule = _rule_for(_host(url))
    if status in (429, 503) or (status == 403 and rule.forbidden_is_limit):
        back_off(url, _seconds(headers.get("Retry-After")))
        return True
    remaining = headers.get("X-RateLimit-Remaining") or headers.get("RateLimit-Remaining")
    if remaining is not None and str(remaining).strip() == "0":
        reset = (headers.get("X-RateLimit-Reset-In") or headers.get("X-RateLimit-Reset")
                 or headers.get("RateLimit-Reset"))
        back_off(url, _seconds(reset) or 1)
    elif status < 400:
        # It answered normally: the next "slow down" starts the pause afresh.
        with _lock:
            _bucket(name).cool_len = 0
    return False


def request(method, url, *, max_wait=DEFAULT_MAX_WAIT, send=None, **kwargs):
    """Send one request within the service's budget; a drop-in for requests.

    Waits for a slot (up to *max_wait* seconds, all told) and, if the service
    says slow down, waits out the pause and asks again while that still fits.
    Raises Throttled when it doesn't. *send* replaces requests.request for
    callers with their own client (curl_cffi).
    """
    send = send or requests.request
    deadline = time.time() + max_wait
    host = _host(url)
    for _attempt in range(3):
        acquire(url, max(deadline - time.time(), 0))
        resp = send(method, url, **kwargs)
        if not note(url, resp.status_code, resp.headers):
            return resp
        resp.close()
    raise Throttled(host, cooling(url))


def get(url, **kwargs):
    return request("GET", url, **kwargs)


def head(url, **kwargs):
    return request("HEAD", url, **kwargs)


def post(url, **kwargs):
    return request("POST", url, **kwargs)


def cooling(url):
    """Seconds this service is still being left alone for (0 when it isn't)."""
    name, _rule = _rule_for(_host(url))
    with _lock:
        return max(_bucket(name).cool_until - time.time(), 0)


def strained(url):
    """Would a request to this service have to wait right now?"""
    name, rule = _rule_for(_host(url))
    with _lock:
        bucket = _bucket(name)
        now = time.time()
        if bucket.cool_until > now:
            return True
        recent = [t for t in bucket.sent if t > now - rule.per]
        return len(recent) >= rule.calls


def status():
    """The services being left alone right now: [{service, seconds}]."""
    now = time.time()
    with _lock:
        return [{"service": name, "seconds": round(b.cool_until - now)}
                for name, b in sorted(_buckets.items()) if b.cool_until > now]
