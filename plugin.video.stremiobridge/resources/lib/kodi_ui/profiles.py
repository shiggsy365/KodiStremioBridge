"""Arctic Zephyr Stremio's profile picker (the picture and name beside the
clock): choose another Kodi profile and load it straight away, instead of
logging off to Kodi's login screen and choosing there."""

import xbmc
import xbmcgui

from .common import L, jsonrpc, log
from .router import route

LOCKED, CURRENT = 20166, 30447  # Kodi's "Locked"; ours "Watching now"


def profile_choices(profiles, current):
    """``[(name, label2, thumbnail)]`` for Profiles.GetProfiles' `profiles`."""
    choices = []
    for profile in profiles:
        name = profile.get("label") or ""
        if not name:
            continue
        if name == current:
            note = L(CURRENT)
        elif profile.get("lockmode"):
            note = xbmc.getLocalizedString(LOCKED)
        else:
            note = ""
        choices.append((name, note, profile.get("thumbnail") or "DefaultUser.png"))
    return choices


@route("switch_profile")
def switch_profile(plugin):
    profiles = jsonrpc("Profiles.GetProfiles", properties=["thumbnail", "lockmode"]).get("profiles") or []
    current = xbmc.getInfoLabel("System.ProfileName")
    choices = profile_choices(profiles, current)
    if len(choices) < 2:  # nothing to choose from here: Kodi's own login screen
        xbmc.executebuiltin("System.LogOff")
        return
    items = []
    for name, note, thumb in choices:
        item = xbmcgui.ListItem(name, note, offscreen=True)
        item.setArt({"icon": thumb, "thumb": thumb})
        items.append(item)
    names = [name for name, _, _ in choices]
    pick = xbmcgui.Dialog().select(L(30446), items, preselect=names.index(current) if current in names else -1,
                                   useDetails=True)
    if pick < 0 or names[pick] == current:
        return
    log(f"Switching to profile {names[pick]}")
    # Kodi asks for a locked profile's code itself (prompt)
    xbmc.executebuiltin(f'LoadProfile("{names[pick]}",prompt)')
