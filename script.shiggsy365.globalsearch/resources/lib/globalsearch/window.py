"""The results window: one row per source, like Stremio Bridge's search
results. A full window (not a dialog), so whatever opens from it (a folder,
the player) goes on top, and Back comes back here.

Layout: resources/skins/Default/1080i/globalsearch-results.xml, made by
tools/make_globalsearch_xml.py: row slot i has a title (TITLE_BASE + i), a
poster list (POSTER_BASE + i) and a square list (SQUARE_BASE + i); unused
lists are hidden, which collapses them in the grouplist.
"""

import xbmc
import xbmcgui

from .sources import SQUARE

MAX_ROWS = 30
HEADING = 100
TITLE_BASE, POSTER_BASE, SQUARE_BASE = 2001, 1001, 3001
ACTION_PREVIOUS_MENU, ACTION_NAV_BACK = 10, 92


def list_item(item):
    listitem = xbmcgui.ListItem(item.label)
    listitem.setArt({k: v for k, v in item.art.items() if v})
    tag = listitem.getVideoInfoTag()
    tag.setPlot(item.plot)
    if item.year:
        tag.setYear(item.year)
    if item.genre:
        tag.setGenres([item.genre])
    tag.setPlaycount(1 if item.watched else 0)
    return listitem


class ResultsWindow(xbmcgui.WindowXML):
    """Set `heading` and `rows` (sources.Row) before doModal()."""

    heading = ""
    rows = ()

    def onInit(self):
        if getattr(self, "_filled", False):  # back from something opened from here
            return
        self._filled = True
        self.getControl(HEADING).setLabel(self.heading)
        self._lists = {}
        for index in range(MAX_ROWS):
            title = self.getControl(TITLE_BASE + index)
            posters, squares = self.getControl(POSTER_BASE + index), self.getControl(SQUARE_BASE + index)
            if index >= len(self.rows):
                for control in (title, posters, squares):
                    control.setVisible(False)
                continue
            row = self.rows[index]
            count = f"   [COLOR FF999999]({len(row.items)})[/COLOR]" if row.counted else ""
            title.setLabel(row.title + count)
            shown, hidden = (squares, posters) if row.shape == SQUARE else (posters, squares)
            hidden.setVisible(False)
            shown.addItems([list_item(item) for item in row.items])
            self._lists[shown.getId()] = row
        if self.rows:
            first = SQUARE_BASE if self.rows[0].shape == SQUARE else POSTER_BASE
            self.setFocusId(first)

    def onClick(self, control_id):
        row = self._lists.get(control_id)
        if row is None:
            return
        position = self.getControl(control_id).getSelectedPosition()
        if 0 <= position < len(row.items):
            # Opens on top of this window; Back returns here.
            xbmc.executebuiltin(row.items[position].builtin())

    def onAction(self, action):
        if action.getId() in (ACTION_PREVIOUS_MENU, ACTION_NAV_BACK):
            self.close()
