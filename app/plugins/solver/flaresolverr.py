"""Challenge solver: FlareSolverr.

FlareSolverr (https://github.com/FlareSolverr/FlareSolverr) runs a real browser
behind a small HTTP API. POST ``/v1`` with ``{"cmd": "request.get", "url": ...}``
and it loads the page, sits through the challenge, and returns the final HTML
along with the cookies and User-Agent its browser ended up with.

It is a separate service: run the container (``ghcr.io/flaresolverr/flaresolverr``,
port 8191) and point this plugin at it. Nothing else in the app needs to know --
scrapers ask ``app.plugins.solver.fetch_html`` and this answers when it's on.

Note on cookies: the clearance a solver earns is tied to the IP that earned it,
so FlareSolverr has to leave from the same public address as the app (the usual
case: both in the same Docker network / host).
"""

import requests

from ... import db
from . import SolverPlugin, register, setting_on

# FlareSolverr drives a browser through a challenge; that takes seconds, not
# milliseconds. Its own maxTimeout is in ms, ours in whole seconds.
_DEFAULT_TIMEOUT_S = 60
# Our HTTP wait always outlasts the browser's, so a slow solve reports itself
# rather than dying as a connection timeout.
_SLACK_S = 15


class FlareSolverrPlugin(SolverPlugin):
    """Solve Cloudflare challenges through a FlareSolverr instance."""

    key = "flaresolverr"
    label = "FlareSolverr"
    icon = "flaresolverr"
    description = (
        "Fetches pages that Cloudflare would block, using a real browser in a "
        "separate FlareSolverr container. Sources that hit a challenge (Album "
        "of the Year) use it automatically and keep the clearance cookie it "
        "brings back, so you stop pasting cookies by hand."
    )
    has_test = True

    url_setting = "solver_flaresolverr_url"
    timeout_setting = "solver_flaresolverr_timeout"
    enabled_setting = "solver_flaresolverr_enabled"

    config_fields = [
        {"key": url_setting, "label": "FlareSolverr address",
         "placeholder": "http://localhost:8191",
         "help": "Where the FlareSolverr container listens. In Docker Compose "
                 "that's usually http://flaresolverr:8191."},
        {"key": timeout_setting, "label": "Solve timeout (seconds)",
         "placeholder": str(_DEFAULT_TIMEOUT_S),
         "help": "How long the browser may spend on one page before giving up."},
    ]

    def url(self):
        value = (db.get_setting(self.url_setting) or "").strip().rstrip("/")
        if value and "://" not in value:
            value = "http://" + value
        return value

    def timeout(self):
        try:
            return max(int(db.get_setting(self.timeout_setting) or _DEFAULT_TIMEOUT_S), 5)
        except (TypeError, ValueError):
            return _DEFAULT_TIMEOUT_S

    def enabled(self):
        return setting_on(self.enabled_setting)

    def configured(self):
        return self.enabled() and bool(self.url())

    def _post(self, payload, timeout):
        endpoint = self.url()
        if not endpoint:
            raise RuntimeError("No FlareSolverr address configured.")
        try:
            resp = requests.post(endpoint + "/v1", json=payload, timeout=timeout)
        except requests.Timeout as exc:
            raise RuntimeError(f"FlareSolverr at {endpoint} timed out.") from exc
        except requests.RequestException as exc:
            raise RuntimeError(f"Could not reach FlareSolverr at {endpoint}.") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"FlareSolverr returned HTTP {resp.status_code}.")
        try:
            data = resp.json() or {}
        except ValueError as exc:
            raise RuntimeError("FlareSolverr returned a non-JSON response.") from exc
        if (data.get("status") or "").lower() != "ok":
            raise RuntimeError("FlareSolverr: " + (data.get("message") or "request failed"))
        return data

    def solve(self, url, timeout=None):
        seconds = timeout or self.timeout()
        data = self._post(
            {"cmd": "request.get", "url": url, "maxTimeout": seconds * 1000},
            timeout=seconds + _SLACK_S,
        )
        solution = data.get("solution") or {}
        cookie = "; ".join(
            f"{c.get('name')}={c.get('value')}"
            for c in (solution.get("cookies") or [])
            if c.get("name")
        )
        return {
            "html": solution.get("response") or "",
            "status": solution.get("status"),
            "cookie": cookie,
            "user_agent": solution.get("userAgent") or "",
            "url": solution.get("url") or url,
        }

    def check(self):
        """Ask the instance for its sessions -- cheap, and proves it's alive."""
        if not self.url():
            return (False, "Set the FlareSolverr address first.")
        try:
            data = self._post({"cmd": "sessions.list"}, timeout=15)
        except RuntimeError as exc:
            return (False, str(exc))
        version = data.get("version") or "unknown version"
        return (True, f"OK - FlareSolverr {version} responded.")


register(FlareSolverrPlugin())
