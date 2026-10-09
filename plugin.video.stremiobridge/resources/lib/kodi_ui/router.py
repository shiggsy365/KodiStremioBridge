"""Maps ``plugin://plugin.video.stremiobridge/?action=…`` URLs to handlers."""

import importlib
from urllib.parse import parse_qsl, urlencode

import xbmc

ROUTES = {}
CO_VARKEYWORDS = 0x08  # a code object's flag: the function takes **kwargs
# Parameters that only make Kodi reload a list (common.notify_widgets' token on
# widget paths): dropped without a word where a route doesn't take them
QUIET_PARAMETERS = {"reload"}


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
        self.names = {name for name, _ in parse_qsl(query, keep_blank_values=True)}  # with empty ones
        # Kodi 19+ adds "resume:true"/"resume:false" when a playable item with a
        # resume point is started; None when it doesn't say.
        flag = argv[3] if len(argv) > 3 else ""
        self.resume = {"resume:true": True, "resume:false": False}.get(flag)

    def url_for(self, action, **params):
        params = {k: v for k, v in params.items() if v is not None}
        return f"{self.base_url}?{urlencode({'action': action, **params})}"

    def run_url(self, action, **params):
        """A ``RunPlugin(...)`` builtin for context menus."""
        return f"RunPlugin({self.url_for(action, **params)})"


# Each handler module's actions. A call imports only its action's module (and
# what it imports): importing every module took about 0.3 s a call on a Fire TV
# Stick, and the info page alone makes three calls at once. Kept in step with the
# @route decorators by tests/test_router.py.
HANDLERS = {
    "backup": ("backup", "restore"),
    "browse": ("catalog", "choose_filter", "row_page", "type"),
    "contextmenu": ("context_menu",),
    "details": ("info_episodes", "info_seasons", "meta", "people", "play_trailer", "season"),
    "hubs": ("hub", "hub_create", "hub_genres", "hub_remove", "hub_rename", "organise", "organise_actions",
             "organise_do", "organise_hub"),
    "infodialog": ("extended_info", "info_mark", "info_play", "info_toggle"),
    "library": ("library_add", "library_remove", "library_setup", "library_update"),
    "manage": ("add_addon", "addon_actions", "addon_catalogs", "addon_details", "manage", "move_addon",
               "refresh_addon", "remove_addon", "set_catalog_pref", "toggle_addon"),
    "menus": ("clear_cache", "open", "root", "settings", "toggle_parent_items", "widgets"),
    "player": ("play",),
    "profiles": ("switch_profile",),
    "search": ("move_search_catalog", "new_search", "rename_search_catalog", "search", "search_catalog_actions",
               "search_catalogs", "search_history_clear", "search_history_remove", "search_key", "search_live",
               "search_menu", "search_open", "search_pick", "search_recent", "search_suggest",
               "toggle_search_catalog"),
    "searchwindow": ("search_window",),
    "skinhelper": ("skin_hubs", "skin_jump", "skin_letters", "skin_select"),
    "splash": ("kodi_splash",),
    "views": ("choose_view",),
    "watching": ("clear_resume", "clear_watch_history", "continue", "dismiss_show", "mdblist_sync", "mdblist_test",
                 "next_up", "set_watched", "similar"),
    "watchlist": ("watchlist", "watchlist_add", "watchlist_remove"),
    "wizard": ("setup_wizard",),
}
ROUTE_MODULES = {action: module for module, actions in HANDLERS.items() for action in actions}


def load_handler(action):
    """The handler for `action`, importing its module; None if there's none."""
    module = ROUTE_MODULES.get(action)
    if action not in ROUTES:
        # Handler modules register themselves on import; an action missing
        # from the table still works (every module is imported).
        for name in [module] if module else HANDLERS:
            importlib.import_module(f"{__package__}.{name}")
    return ROUTES.get(action)


def run(argv):
    from .common import log

    from .views import reset_content

    plugin = Plugin(argv)
    reset_content(argv[0] + (argv[2] if len(argv) > 2 else ""))
    params = dict(plugin.params)
    action = params.pop("action", "root")
    handler = load_handler(action)
    if handler is None:
        log(f"Unknown action '{action}'", xbmc.LOGERROR)
        return
    # Drop parameters the handler doesn't take (e.g. from old widget/favourite
    # links made by an earlier version), unless it accepts **kwargs. Read from
    # its code: importing inspect for this is slow on a Fire TV Stick.
    code = handler.__code__
    if not code.co_flags & CO_VARKEYWORDS:
        accepted = code.co_varnames[:code.co_argcount + code.co_kwonlyargcount]
        unknown = set(params) - set(accepted)
        if unknown - QUIET_PARAMETERS:
            log(f"Ignoring unknown parameters for '{action}': {sorted(unknown)}")
        params = {k: v for k, v in params.items() if k not in unknown}
    handler(plugin, **params)
