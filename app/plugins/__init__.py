"""Lightweight plugin registry.

Plugins extend the app in a pluggable way. Each plugin has a *kind* (the part of
the app it plugs into) and a *key* (unique within that kind). Today the only kind
is ``discovery`` -- new-release sources for the Discover page -- but the registry
is deliberately generic so other kinds (importers, notifiers, ...) can be added
later without touching this module.

A plugin registers itself at import time via ``register``. Importing a kind's
sub-package (e.g. ``app.plugins.discovery``) pulls in and registers every plugin
bundled under it.
"""


class Plugin:
    """Base class for all plugins. Subclasses set ``kind``, ``key`` and ``label``."""

    kind = None
    key = None
    label = None
    description = ""
    # Which service mark to show for this plugin, as a file in
    # app/static/icons (see ServiceIcon in the frontend). None = no icon,
    # which is right for anything that isn't a service: a folder on disk, a
    # cover pulled from your own releases.
    icon = None

    def icon_name(self):
        """The icon to show. Overridden where it depends on what answered."""
        return self.icon

    def display_label(self):
        """The name to show. Overridden where it depends on configuration --
        a renamed tracker, or a server that reports which software it runs."""
        return self.label

    def describe(self):
        """Serialisable metadata for the API / settings UI."""
        return {
            "kind": self.kind,
            "key": self.key,
            "label": self.display_label(),
            "description": self.description,
            "icon": self.icon_name(),
        }


# kind -> {key -> plugin instance}, in registration order.
_registry = {}


def register(plugin):
    """Add *plugin* to the registry (returns it, so it reads well as a decorator)."""
    _registry.setdefault(plugin.kind, {})[plugin.key] = plugin
    return plugin


def kinds():
    """Every registered plugin kind."""
    return list(_registry)


def get_plugins(kind):
    """All registered plugins of *kind*, in registration order."""
    return list(_registry.get(kind, {}).values())


def get_plugin(kind, key):
    """One plugin by kind + key, or ``None``."""
    return _registry.get(kind, {}).get(key)
