"""Setup wizard: the MDBList key and Stremio addons Stremio Bridge needs, in a
few dialogs. Arctic Zephyr Stremio runs it on its first start (Home.xml, while
its ``stremio.setup_done`` setting is off); it can be run again from the
skin's Settings menu or Stremio Bridge's settings.

    1. an MDBList API key              Skip / Enter / Cancel setup
    2. a Stremio addon manifest        Enter / Cancel setup
    3. another addon manifest          Skip / Enter / Cancel setup, until Skip

Cancel setup (or Back) stops there; anything already added stays.
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
SKIP, ENTER, CANCEL = "skip", "enter", "cancel"


def providers(resource):
    """Names of the enabled addons that provide `resource`."""
    return [a.name for a in get_registry().all()
            if a.enabled and resource in a.manifest.resource_names]


def is_set_up():
    """Nothing to ask: streams and metadata addons and an MDBList key are there."""
    return bool(providers(STREAM)) and bool(providers(META)) and get_mdblist() is not None


def _choice(message, can_skip=True):
    """SKIP, ENTER or CANCEL (Back cancels too). Kodi lays the buttons out as
    custom, yes, no: Skip, Enter, Cancel setup, with Enter focused."""
    dialog = xbmcgui.Dialog()
    if can_skip:
        answer = dialog.yesnocustom(L(30380), message, customlabel=L(30396), nolabel=L(30416), yeslabel=L(30417),
                                    defaultbutton=xbmcgui.DLG_YESNO_YES_BTN)
        return {1: ENTER, 2: SKIP}.get(answer, CANCEL)
    return ENTER if dialog.yesno(L(30380), message, nolabel=L(30416), yeslabel=L(30417),
                                 defaultbutton=xbmcgui.DLG_YESNO_YES_BTN) else CANCEL


@route("setup_wizard")
def setup_wizard(plugin, auto=""):
    """`auto`: started by the skin on its first run (quietly done if there's
    nothing to set up)."""
    if skin_active():
        xbmc.executebuiltin(f"Skin.SetBool({DONE_SETTING})")  # offered once, whatever happens next
    if auto and is_set_up():
        return
    added, mdblist_synced, finished = 0, False, False
    choice = _choice(L(30418))
    if choice == ENTER:
        mdblist_synced = bool(_mdblist_step())
    if choice != CANCEL:
        # The first addon is required: Enter or Cancel setup
        while not added:
            if _choice(L(30419), can_skip=False) == CANCEL:
                break
            added += _ask_addon(L(30390), None)
        while added:
            choice = _choice(L(30420))
            if choice != ENTER:
                finished = choice == SKIP
                break
            added += _ask_addon(L(30390), None)

    if added and skin_active():
        from .skinhelper import update_skin_hubs

        update_skin_hubs(force=True)
    xbmcgui.Dialog().ok(L(30380), L(30391) if finished else L(30384))
    if (added or mdblist_synced) and skin_active():
        xbmc.executebuiltin("ReloadSkin()")  # menus and widgets pick up new catalogs and MDBList data


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
