"""Setup wizard: the addons and MDBList key Stremio Bridge needs, in a few
dialogs. Arctic Zephyr Stremio runs it on its first start (Home.xml, while
its ``stremio.setup_done`` setting is off); it can be run again from the
skin's "..." menu or Stremio Bridge's settings.

    1. a streams addon (e.g. AIOStreams)          required
    2. a metadata addon (e.g. AIOMetadata)        recommended (else Cinemeta)
    3. any other addons (catalogs, subtitles)     optional
    4. an MDBList API key                         recommended
"""

import xbmc
import xbmcgui

from mdblist import MDBListAuthError, MDBListError
from stremio import StremioError
from stremio.registry import DuplicateAddonError

from .common import ADDON, L, busy, get_client, get_mdblist, get_registry, log, skin_active
from .router import route

DONE_SETTING = "stremio.setup_done"  # the skin's: the wizard has been offered
STREAM, META = "stream", "meta"


def providers(resource):
    """Names of the enabled addons that provide `resource`."""
    return [a.name for a in get_registry().all()
            if a.enabled and resource in a.manifest.resource_names]


def is_set_up():
    """Nothing to ask: streams and metadata addons and an MDBList key are there."""
    return bool(providers(STREAM)) and bool(providers(META)) and get_mdblist() is not None


@route("setup_wizard")
def setup_wizard(plugin, auto=""):
    """`auto`: started by the skin on its first run (quietly done if there's
    nothing to set up)."""
    if skin_active():
        xbmc.executebuiltin(f"Skin.SetBool({DONE_SETTING})")  # offered once, whatever happens next
    if auto and is_set_up():
        return
    dialog = xbmcgui.Dialog()
    if not dialog.yesno(L(30380), L(30381), nolabel=L(30382), yeslabel=L(30383)):
        dialog.ok(L(30380), L(30384))
        return

    added = _addon_step(STREAM, L(30385), L(30386), required=True)
    added += _addon_step(META, L(30387), L(30388), required=False)
    while dialog.yesno(L(30380), L(30389)):
        added += _ask_addon(L(30390), None)
    mdblist_synced = _mdblist_step()

    if added and skin_active():
        from .skinhelper import update_skin_hubs

        update_skin_hubs(force=True)
    dialog.ok(L(30380), L(30391))
    if (added or mdblist_synced) and skin_active():
        xbmc.executebuiltin("ReloadSkin()")  # menus and widgets pick up new catalogs and MDBList data


def _addon_step(resource, heading, intro, required):
    """Offer to add an addon providing `resource`. Returns how many were added."""
    dialog = xbmcgui.Dialog()
    have = providers(resource)
    if have and not dialog.yesno(heading, L(30392, names=", ".join(have)), nolabel=L(30393), yeslabel=L(30394)):
        return 0
    if not have:
        dialog.ok(heading, intro)
    while True:
        added = _ask_addon(heading, resource)
        if added or providers(resource) or not required:
            return added
        if dialog.yesno(heading, L(30395), nolabel=L(30396), yeslabel=L(30397)):
            return 0  # skipped anyway


def _ask_addon(heading, resource):
    """Ask for a manifest link and add it; 1 if added, 0 if not (left empty,
    cancelled or failed)."""
    dialog = xbmcgui.Dialog()
    while True:
        url = dialog.input(heading, type=xbmcgui.INPUT_ALPHANUM).strip()
        if not url:
            return 0
        try:
            with busy():
                transport_url, manifest = get_client().fetch_manifest(url)
        except StremioError as exc:
            log(f"Wizard: adding {url} failed: {exc}")
            if not dialog.yesno(heading, L(30398, error=exc), nolabel=L(30396), yeslabel=L(30399)):
                return 0
            continue
        if manifest.configuration_required:
            dialog.ok(heading, L(30035))
            continue
        if resource and resource not in manifest.resource_names \
                and not dialog.yesno(heading, L(30400, name=manifest.name)):
            continue
        try:
            get_registry().add(transport_url, manifest)
        except DuplicateAddonError:
            dialog.ok(heading, L(30034))
            return 0
        dialog.notification(L(30380), L(30030, name=manifest.name), ADDON.getAddonInfo("icon"), 3000)
        return 1


def _mdblist_step():
    dialog = xbmcgui.Dialog()
    current = ADDON.getSettingString("mdblist_api_key").strip()
    if current and get_mdblist() is not None \
            and not dialog.yesno("MDBList", L(30401), nolabel=L(30393), yeslabel=L(30405)):
        return
    while True:
        key = dialog.input(L(30402), defaultt=current, type=xbmcgui.INPUT_ALPHANUM).strip()
        if not key:
            return
        ADDON.setSettingString("mdblist_api_key", key)
        ADDON.setSettingBool("mdblist_enabled", True)
        try:
            with busy():
                get_mdblist().last_activities()
        except MDBListAuthError:
            if not dialog.yesno("MDBList", L(30213), nolabel=L(30396), yeslabel=L(30399)):
                return
            current = key
            continue
        except MDBListError as exc:
            dialog.ok("MDBList", f"{L(30214)}\n{exc}")  # kept: probably MDBList being down
            return False
        try:
            from .watching import run_mdblist_sync

            with busy():
                run_mdblist_sync(force=True)
        except (MDBListError, StremioError) as exc:
            dialog.ok("MDBList", f"{L(30214)}\n{exc}")
            return False
        dialog.notification("MDBList", L(30215), ADDON.getAddonInfo("icon"), 3000)
        return True
