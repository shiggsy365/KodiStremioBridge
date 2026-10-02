import os
import sys

import xbmc

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "resources", "lib"))

from dispatcharrbridge.ui import run  # noqa: E402

path = xbmc.getInfoLabel("ListItem.FileNameAndPath")
run("context", number_label=xbmc.getInfoLabel("ListItem.ChannelNumberLabel"),
    name=xbmc.getInfoLabel("ListItem.ChannelName") or xbmc.getInfoLabel("ListItem.Label"),
    path=path if path.startswith("pvr://channels/") else "")
