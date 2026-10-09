"""Arctic Zephyr Stremio's search page (Custom_1170_Search.xml): its Delete and
symbol keys, which the skin can't do itself (it can't shorten a property, and
commas and brackets would break its command). Run with RunScript, so it starts
in a moment: nothing but Kodi's own modules is imported.

Arguments: the key (``delete``, or ``type`` and the character, URL-quoted) and
the query as it was shown when the key was pressed. Keys typed since then are
kept after the edit, so a quick next key never lands before it.
"""

import sys
import time
from urllib.parse import unquote

import xbmc
import xbmcgui

QUERY = "sbsearch.query"      # what's typed (the skin's letters and space go in directly)
SETTLED = "sbsearch.settled"  # changes when typing pauses: the result lists reload then
NBSP = " "               # the skin's space (it can't append a plain one)


def edit(key, shown, current, char=""):
    """The query after `key` was pressed on `shown`, now that `current` is typed."""
    if not current.startswith(shown):  # changed some other way meanwhile: edit what's there
        shown = current
    later = current[len(shown):]
    if key == "delete":
        return shown[:-1] + later
    if key == "type":
        return shown + char + later
    return current


def settle_later():
    """As the skin does after a key: the lists reload once typing pauses."""
    xbmc.executebuiltin("CancelAlarm(sbsearch,true)")
    xbmc.executebuiltin(f"AlarmClock(sbsearch,SetProperty({SETTLED},{time.strftime('%H:%M:%S')}d,home),00:01,silent)")


def main(argv):
    key = argv[1] if len(argv) > 1 else ""
    shown = argv[2] if len(argv) > 2 else ""
    char = unquote(argv[3]) if len(argv) > 3 else ""
    home = xbmcgui.Window(10000)
    home.setProperty(QUERY, edit(key, shown, home.getProperty(QUERY), char))
    settle_later()


if __name__ == "__main__":
    main(sys.argv)
