"""Manage Addons: add, remove, reorder, enable/disable, refresh, catalog prefs."""

import xbmc
import xbmcgui
import xbmcplugin

from stremio import StremioError
from stremio.registry import DuplicateAddonError

from .common import L, add_context_menu, busy, get_client, get_registry, log, notify, refresh_container
from .router import route


@route("manage")
def manage(plugin):
    handle = plugin.handle
    registry = get_registry()

    add_item = xbmcgui.ListItem(f"[B]{L(30001)}[/B]")
    add_item.setArt({"icon": "DefaultAddSource.png"})
    xbmcplugin.addDirectoryItem(handle, plugin.url_for("add_addon"), add_item, isFolder=False)

    for addon in registry.all():
        m = addon.manifest
        label = f"{m.name}  [COLOR grey]v{m.version}[/COLOR]"
        if not addon.enabled:
            label = f"[COLOR grey]{m.name}  v{m.version}  ({L(30003)})[/COLOR]"
        item = xbmcgui.ListItem(label, label2=m.description)
        if m.logo:
            item.setArt({"icon": m.logo, "thumb": m.logo})
        item.getVideoInfoTag().setPlot(_details_text(addon))

        key = addon.key
        add_context_menu(item, [
            (L(30011) if not addon.enabled else L(30010), plugin.run_url("toggle_addon", addon=key)),
            (L(30012), plugin.run_url("move_addon", addon=key, delta=-1)),
            (L(30013), plugin.run_url("move_addon", addon=key, delta=1)),
            (L(30014), plugin.run_url("refresh_addon", addon=key)),
            (L(30017), f"Container.Update({plugin.url_for('addon_catalogs', addon=key)})"),
            (L(30016), plugin.run_url("addon_details", addon=key)),
            (L(30015), plugin.run_url("remove_addon", addon=key)),
        ])
        xbmcplugin.addDirectoryItem(
            handle, plugin.url_for("addon_actions", addon=key), item, isFolder=False
        )

    xbmcplugin.endOfDirectory(handle, cacheToDisc=False)


@route("add_addon")
def add_addon(plugin, url=None):
    if not url:
        url = xbmcgui.Dialog().input(L(30002), type=xbmcgui.INPUT_ALPHANUM)
    if not url:
        return

    try:
        with busy():
            transport_url, manifest = get_client().fetch_manifest(url)
    except StremioError as exc:
        log(f"Adding {url} failed: {exc}")
        xbmcgui.Dialog().ok(L(30033), str(exc))
        return

    if manifest.configuration_required:
        xbmcgui.Dialog().ok(L(30033), L(30035))
        return

    try:
        get_registry().add(transport_url, manifest)
    except DuplicateAddonError:
        xbmcgui.Dialog().ok(L(30033), L(30034))
        return

    notify(L(30030, name=manifest.name))
    refresh_container()


@route("addon_actions")
def addon_actions(plugin, addon):
    """Remote-friendly action menu shown when an addon is selected."""
    installed = get_registry().get(addon)
    actions = [
        (L(30011) if not installed.enabled else L(30010), toggle_addon),
        (L(30012), lambda p, addon: move_addon(p, addon, -1)),
        (L(30013), lambda p, addon: move_addon(p, addon, 1)),
        (L(30014), refresh_addon),
        (L(30017), None),
        (L(30016), addon_details),
        (L(30015), remove_addon),
    ]
    choice = xbmcgui.Dialog().select(installed.name, [label for label, _ in actions])
    if choice < 0:
        return
    handler = actions[choice][1]
    if handler is None:
        xbmc.executebuiltin(f"Container.Update({plugin.url_for('addon_catalogs', addon=addon)})")
    else:
        handler(plugin, addon=addon)


@route("toggle_addon")
def toggle_addon(plugin, addon):
    registry = get_registry()
    registry.set_enabled(addon, not registry.get(addon).enabled)
    refresh_container()


@route("move_addon")
def move_addon(plugin, addon, delta):
    get_registry().move(addon, int(delta))
    refresh_container()


@route("refresh_addon")
def refresh_addon(plugin, addon):
    registry = get_registry()
    installed = registry.get(addon)
    try:
        with busy():
            _, manifest = get_client().fetch_manifest(installed.transport_url)
    except StremioError as exc:
        xbmcgui.Dialog().ok(L(30036), str(exc))
        return
    registry.update_manifest(addon, manifest)
    notify(L(30031, name=manifest.name))
    refresh_container()


@route("remove_addon")
def remove_addon(plugin, addon):
    registry = get_registry()
    name = registry.get(addon).name
    if not xbmcgui.Dialog().yesno(name, L(30018, name=name)):
        return
    registry.remove(addon)
    notify(L(30032, name=name))
    refresh_container()


@route("addon_details")
def addon_details(plugin, addon):
    installed = get_registry().get(addon)
    xbmcgui.Dialog().textviewer(installed.name, _details_text(installed))


@route("addon_catalogs")
def addon_catalogs(plugin, addon):
    """Per-catalog toggles: in its type menu, on the front page, used for search."""
    handle = plugin.handle
    installed = get_registry().get(addon)
    for catalog in installed.manifest.catalogs:
        prefs = installed.prefs_for(catalog)
        flags, menu = [], []
        if catalog.is_browsable:
            front = prefs.on_front_page(catalog)
            flags += [_check(prefs.home) + " " + L(30020), _check(front) + " " + L(30025)]
            menu += [
                (L(30022), plugin.run_url("set_catalog_pref", addon=addon, catalog=catalog.key,
                                          field="home", value=int(not prefs.home))),
                (L(30026), plugin.run_url("set_catalog_pref", addon=addon, catalog=catalog.key,
                                          field="front", value=int(not front))),
            ]
        else:
            flags.append(f"[COLOR grey]{L(30024)}[/COLOR]")
        if catalog.is_searchable_alone:
            flags.append(_check(prefs.search) + " " + L(30021))
            menu.append((L(30023), plugin.run_url("set_catalog_pref", addon=addon, catalog=catalog.key,
                                                  field="search", value=int(not prefs.search))))

        item = xbmcgui.ListItem(f"{catalog.name}  [COLOR grey]{catalog.type}[/COLOR]   " + "   ".join(flags))
        add_context_menu(item, menu)

        # Selecting the item toggles its primary flag.
        field = "home" if catalog.is_browsable else "search"
        target = plugin.url_for(
            "set_catalog_pref", addon=addon, catalog=catalog.key, field=field,
            value=int(not getattr(prefs, field)),
        )
        xbmcplugin.addDirectoryItem(handle, target, item, isFolder=False)
    xbmcplugin.endOfDirectory(handle, cacheToDisc=False)


@route("set_catalog_pref")
def set_catalog_pref(plugin, addon, catalog, field, value):
    if field not in ("home", "search", "front"):
        return
    get_registry().set_catalog_pref(addon, catalog, **{field: bool(int(value))})
    refresh_container()


def _check(on):
    return "[COLOR lime]✔[/COLOR]" if on else "[COLOR grey]✘[/COLOR]"


def _details_text(addon):
    m = addon.manifest
    lines = [
        f"[B]{m.name}[/B] v{m.version}  ({m.id})",
        m.description,
        "",
        f"URL: {addon.transport_url}",
        f"Resources: {', '.join(m.resource_names) or '-'}",
        f"Types: {', '.join(m.types) or '-'}",
    ]
    if m.id_prefixes:
        lines.append(f"ID prefixes: {', '.join(m.id_prefixes)}")
    flags = [name for name, on in (("P2P", m.p2p), ("Adult", m.adult), ("Configurable", m.configurable)) if on]
    if flags:
        lines.append(f"Flags: {', '.join(flags)}")
    if m.catalogs:
        lines += ["", "[B]Catalogs[/B]"]
        for c in m.catalogs:
            extras = ", ".join(("*" if e.is_required else "") + e.name for e in c.extra)
            lines.append(f"  {c.name} ({c.type})" + (f"  [{extras}]" if extras else ""))
    return "\n".join(lines)
