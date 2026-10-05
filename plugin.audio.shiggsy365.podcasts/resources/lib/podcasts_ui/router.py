"""Maps ``plugin://plugin.audio.shiggsy365.podcasts/?action=…`` URLs to handlers."""

import inspect
from urllib.parse import parse_qsl, urlencode

import xbmc

ROUTES = {}


def route(action):
    def decorator(func):
        ROUTES[action] = func
        return func

    return decorator


class Plugin:
    """One plugin invocation: Kodi passes ``[base_url, handle, '?query']``."""

    def __init__(self, argv):
        self.base_url = argv[0]
        self.handle = int(argv[1]) if len(argv) > 1 and argv[1] else -1
        query = argv[2][1:] if len(argv) > 2 and argv[2].startswith("?") else ""
        self.params = dict(parse_qsl(query))
        # Kodi 19+ adds "resume:true"/"resume:false"; None when it doesn't say.
        flag = argv[3] if len(argv) > 3 else ""
        self.resume = {"resume:true": True, "resume:false": False}.get(flag)

    def url_for(self, action, **params):
        params = {k: v for k, v in params.items() if v is not None}
        return f"{self.base_url}?{urlencode({'action': action, **params})}"

    def run_url(self, action, **params):
        """A ``RunPlugin(...)`` builtin for context menus."""
        return f"RunPlugin({self.url_for(action, **params)})"


def run(argv):
    from . import menus  # noqa: F401 - registers the routes
    from .common import log

    plugin = Plugin(argv)
    params = dict(plugin.params)
    action = params.pop("action", "root")
    params.pop("reload", None)  # widget reload token (common.RELOAD_TOKEN): only there to change the path
    handler = ROUTES.get(action)
    if handler is None:
        log(f"Unknown action '{action}'", xbmc.LOGERROR)
        return
    # Drop parameters the handler doesn't take (e.g. from old favourites).
    accepted = inspect.signature(handler).parameters
    unknown = set(params) - set(accepted)
    if unknown:
        log(f"Ignoring unknown parameters for '{action}': {sorted(unknown)}")
        params = {k: v for k, v in params.items() if k not in unknown}
    handler(plugin, **params)
