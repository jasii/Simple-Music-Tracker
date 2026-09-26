"""Challenge-solver plugins: fetch a page that bot protection would refuse us.

Some sources (albumoftheyear.org today) sit behind a Cloudflare managed
challenge. A browser passes it by running the challenge's JavaScript and is
handed a short-lived ``cf_clearance`` cookie; a server-side fetch has no way to
do that, so it gets a 403 "Just a moment..." page instead -- the same page,
whatever TLS fingerprint it borrows.

A solver plugin is the way out: it drives a real browser somewhere else (a
FlareSolverr container, typically), hands back the solved page, and -- more
usefully -- the clearance cookie the browser earned, which the calling scraper
can reuse directly until it is challenged again.

Scrapers don't pick a solver: they call :func:`fetch_html` and get either the
page or ``None`` when the user hasn't set one up. Importing this package
registers every bundled solver.
"""

from .. import Plugin, register, get_plugins, get_plugin  # noqa: F401 - re-exported

from ... import db

# Values that mean "off" for an enable toggle stored as a string setting.
_OFF = ("false", "0", "off", "no", "")


def setting_on(key):
    """True when the string setting *key* holds something that means "on"."""
    return (db.get_setting(key) or "").strip().lower() not in _OFF


class SolverPlugin(Plugin):
    """A service that can fetch a challenged page on our behalf.

    Subclasses set ``key`` / ``label`` and implement :meth:`solve`.
    """

    kind = "solver"
    enabled_setting = None
    config_fields = []
    has_test = False

    def enabled(self):
        """Solvers are opt-in: off unless the toggle is explicitly on."""
        if not self.enabled_setting:
            return True
        return setting_on(self.enabled_setting)

    def configured(self):
        """Ready to solve? Default: just enabled."""
        return self.enabled()

    def solve(self, url, timeout=None):
        """Fetch *url* through the solver.

        Returns ``{"html", "status", "cookie", "user_agent", "url"}``, where
        ``cookie`` is a ready-to-send Cookie header value and ``user_agent`` is
        the browser the solver used -- protections bind the cookie to it, so a
        caller reusing the cookie has to send that User-Agent too. Raises
        RuntimeError with a user-facing reason when the solve fails.
        """
        raise NotImplementedError

    def check(self):
        """Optional health check. Return ``(ok, message)`` or ``None``."""
        return None

    def describe(self):
        data = super().describe()
        data.update({
            "enabled": self.enabled(),
            "enabled_setting": self.enabled_setting,
            "configured": self.configured(),
            "config_fields": self.config_fields,
            "has_test": self.has_test,
        })
        return data


def active():
    """The first configured solver, or None when the user hasn't set one up."""
    for plugin in get_plugins("solver"):
        if plugin.configured():
            return plugin
    return None


def fetch_html(url, timeout=None):
    """Solved HTML for *url*, or None when no solver is configured.

    The solve result is returned whole (see :meth:`SolverPlugin.solve`) so the
    caller can keep the clearance cookie; use ``result["html"]`` for the page.
    """
    plugin = active()
    if plugin is None:
        return None
    return plugin.solve(url, timeout=timeout)


# Register the bundled solvers (import side effect calls register()).
from . import flaresolverr  # noqa: E402,F401
