import os
import sys

import xbmc

# BingeTV: Home shows the rows it kept from last time straight away (homewidgets.remember_row),
# so the service can wait a moment before its imports and start-up work, leaving the device
# to Home's first rows. Elsewhere, or before the rows are kept, it starts at once.
if xbmc.getSkinDir() == "skin.bingetv" and xbmc.getInfoLabel("Skin.String(sbhome.10000.widgetPath)"):
    if xbmc.Monitor().waitForAbort(6):
        sys.exit()

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", "lib"))

from kodi_ui.service import run  # noqa: E402

run()
