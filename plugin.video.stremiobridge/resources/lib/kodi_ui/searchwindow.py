"""Search results as rows: one row per search catalog that found something.

Layout: resources/skins/Default/1080i/stremiobridge-search.xml (fixed row
slots; unused ones are hidden). The window only records what was picked; the
route acts on it after the window closes, and reopens it (same results, same
position) when the user comes back from Extended info.
"""

import xbmcgui

from .browse import type_label
from stremio import StremioError
from stremio.catalog import fetch_catalog, next_page

from .common import ADDON, L, busy, get_client, get_watchstate, log
from .details import play_trailer
from .infodialog import extended_info, open_folder, play_with_resume_choice
from .listitems import PLAYABLE_TYPES
from .router import route
from .search import find_results
from .watching import mark_watched

XML = "stremiobridge-search.xml"
MAX_ROWS = 30
LOAD_MORE_WITHIN = 5   # load a row's next page when this close to its end
ACTION_MOVE_RIGHT = 2
LIST_BASE, TITLE_BASE = 1001, 2001
ACTION_PREVIOUS_MENU, ACTION_NAV_BACK = 10, 92
ACTION_SHOW_INFO, ACTION_CONTEXT_MENU = 11, 117


def _list_item(preview, watched):
    item = xbmcgui.ListItem(preview.name)
    art = {"poster": preview.poster, "thumb": preview.poster, "fanart": preview.background}
    item.setArt({k: v for k, v in art.items() if v})
    tag = item.getVideoInfoTag()
    tag.setPlot(preview.description)
    if preview.year:
        tag.setYear(preview.year)
    if preview.genres:
        tag.setGenres(list(preview.genres))
    tag.setPlaycount(1 if watched else 0)
    return item


class SearchRow:
    """One catalog's results in the window, with paging."""

    def __init__(self, addon, catalog, previews, query, only_type=None):
        self.addon, self.catalog, self.query, self.only_type = addon, catalog, query, only_type
        self.previews = list(previews)
        self.next = next_page(catalog, 0, len(previews))

    @property
    def title(self):
        name = f"{type_label(self.catalog.type)} · {self.addon.search_title(self.catalog)}"
        count = f"{len(self.previews)}{'+' if self.next else ''}"
        return f"{name}   [COLOR FF999999]{self.addon.name} ({count})[/COLOR]"

    def load_more(self, client):
        """Fetch the next page; returns the previews added (may be empty)."""
        if not self.next:
            return []
        skip, size = self.next
        found = fetch_catalog(client, self.addon, self.catalog, {"search": self.query}, skip)
        self.next = next_page(self.catalog, skip, len(found), size)
        known = {p.id for p in self.previews}
        added = [p for p in found if p.id not in known and (not self.only_type or p.type == self.only_type)]
        self.previews += added
        return added


class SearchWindow(xbmcgui.WindowXMLDialog):
    """Set `heading`, `rows` (SearchRows), `watched` (set of ids), `load_more`
    (row -> new previews) and optionally `focus` ((row, position)) before
    doModal(). Afterwards `choice` is None or (action, preview) with action
    open/info/trailer, and `focus` is where the user was."""

    heading = ""
    rows = ()
    load_more = None
    watched = None  # a set, shared with the route
    focus = None
    choice = None

    def onInit(self):
        self.setProperty("heading", self.heading)
        for index in range(MAX_ROWS):
            if index < len(self.rows):
                row = self.rows[index]
                self.getControl(TITLE_BASE + index).setLabel(row.title)
                self.getControl(LIST_BASE + index).addItems(
                    [_list_item(p, p.id in self.watched) for p in row.previews])
            else:
                # Title and list are direct children of the vertical grouplist (so
                # up/down moves between rows); hiding both collapses the row.
                self.getControl(TITLE_BASE + index).setVisible(False)
                self.getControl(LIST_BASE + index).setVisible(False)
        row, position = self.focus or (0, 0)
        if self.rows:
            self.getControl(LIST_BASE + row).selectItem(position)
            self.setFocusId(LIST_BASE + row)

    def _focused(self):
        """``(row, position, preview)`` under the cursor, or None."""
        row = self.getFocusId() - LIST_BASE
        if not 0 <= row < len(self.rows):
            return None
        position = self.getControl(LIST_BASE + row).getSelectedPosition()
        previews = self.rows[row].previews
        return (row, position, previews[position]) if 0 <= position < len(previews) else None

    def onClick(self, control_id):
        self._choose("open")

    def onAction(self, action):
        action_id = action.getId()
        if action_id in (ACTION_PREVIOUS_MENU, ACTION_NAV_BACK):
            self.close()
        elif action_id == ACTION_SHOW_INFO:
            self._choose("info")
        elif action_id == ACTION_CONTEXT_MENU:
            self._menu()
        elif action_id == ACTION_MOVE_RIGHT:
            self._maybe_load_more()

    def _maybe_load_more(self):
        """Near the end of a row with more results: add its next page."""
        index = self.getFocusId() - LIST_BASE
        if not 0 <= index < len(self.rows) or not self.rows[index].next or self.load_more is None:
            return
        control = self.getControl(LIST_BASE + index)
        if control.getSelectedPosition() < control.size() - LOAD_MORE_WITHIN:
            return
        added = self.load_more(self.rows[index])
        if added:
            control.addItems([_list_item(p, p.id in self.watched) for p in added])
        self.getControl(TITLE_BASE + index).setLabel(self.rows[index].title)

    def _menu(self):
        focused = self._focused()
        if focused is None:
            return
        row, position, preview = focused
        watched = preview.id in self.watched
        options = [L(30220), L(30191) if watched else L(30190), L(30067)]
        pick = xbmcgui.Dialog().contextmenu(options)
        if pick == 0:
            self._choose("info")
        elif pick == 1 and mark_watched(preview.type, preview.id, not watched):
            (self.watched.discard if watched else self.watched.add)(preview.id)
            item = self.getControl(LIST_BASE + row).getListItem(position)
            item.getVideoInfoTag().setPlaycount(0 if watched else 1)
        elif pick == 2:
            self._choose("trailer")

    def _choose(self, action):
        focused = self._focused()
        if focused is None:
            return
        row, position, preview = focused
        self.choice, self.focus = (action, preview), (row, position)
        self.close()


@route("search_window")
def search_window(plugin, query, type=None, person=None):
    """`person=1`: `query` is an actor's/director's name (see find_results)."""
    groups, _ = find_results(query, type, person=person == "1")
    if not groups:
        return
    if len(groups) > MAX_ROWS:
        log(f"Search found results in {len(groups)} catalogs; showing the first {MAX_ROWS}")
    rows = [SearchRow(a, c, p, query, type if c.type == "all" else None) for a, c, p in groups[:MAX_ROWS]]
    state = get_watchstate()
    watched = {vid for vid, r in state.lookup([p.id for row in rows for p in row.previews]).items() if r.watched}
    client = get_client()

    def load_more(row):
        try:
            with busy():
                added = row.load_more(client)
        except StremioError as exc:
            log(f"More results from {row.addon.name} failed: {exc}")
            row.next = None
            return []
        watched.update(vid for vid, r in state.lookup([p.id for p in added]).items() if r.watched)
        return added

    focus = None
    while True:
        window = SearchWindow(XML, ADDON.getAddonInfo("path"), "Default", "1080i")
        window.heading = f"{L(30070)}: {query}"
        window.rows, window.watched, window.focus, window.load_more = rows, watched, focus, load_more
        window.doModal()
        choice, focus = window.choice, window.focus
        watched.clear()
        watched.update(window.watched)
        del window

        if choice is None:
            return
        action, preview = choice
        if action == "info":
            if extended_info(plugin, preview.type, preview.id):
                return
            continue  # back from Extended info: show the results again
        if action == "trailer":
            play_trailer(plugin, preview.type, preview.id, yt=preview.trailer or None)
        elif preview.type == "movie" and ADDON.getSettingBool("select_opens_info"):
            if not extended_info(plugin, preview.type, preview.id):
                continue  # back from Extended info: show the results again
        elif preview.type in PLAYABLE_TYPES:
            if not play_with_resume_choice(plugin, preview.type, preview.id):
                continue
        else:
            open_folder(plugin.url_for("meta", type=preview.type, id=preview.id))
        return
