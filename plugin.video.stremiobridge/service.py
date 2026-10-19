import os
import sys

import xbmc

# BingeTV: Home shows the rows it kept from last time straight away (homewidgets.remember_row),
# so when Kodi starts the service can wait a moment before its imports and start-up work,
# leaving the device to Home's first rows. Elsewhere, before the rows are kept, or on a profile
# switch (Kodi is already up: the home window still has the property set below), it starts at once.
STARTED = "stremiobridge.service_started"
if xbmc.getSkinDir() == "skin.bingetv" and xbmc.getInfoLabel("Skin.String(sbhome.10000.widgetPath)") \
        and not xbmc.getInfoLabel(f"Window(Home).Property({STARTED})"):
    if xbmc.Monitor().waitForAbort(6):
        sys.exit()
xbmc.executebuiltin(f"SetProperty({STARTED},1,home)")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", "lib"))

from kodi_ui.service import run  # noqa: E402

run()
