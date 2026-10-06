# Developing Stremio Bridge and Arctic Zephyr Stremio

How the repository is laid out, how to test and release, and how Stremio Bridge talks to Stremio
addons. For using them, see the [README](README.md).

## Layout

```
plugin.video.stremiobridge/        the add-on itself
├── addon.xml, default.py, service.py
└── resources/
    ├── settings.xml, language/, media/, skins/
    └── lib/
        ├── stremio/               pure-Python protocol code (no Kodi imports)
        │   ├── models.py          manifest / catalog / meta parsing and routing rules
        │   ├── client.py          HTTP client, URL normalisation and encoding
        │   ├── catalog.py         catalog fetching, filters, paging heuristics, search
        │   ├── registry.py        installed addons, order, catalog prefs, hubs
        │   ├── meta.py            meta resolver (addons in order, then Cinemeta)
        │   ├── streams.py         stream parsing, quality detection, filter/sort, probing
        │   ├── streaminfo.py      stream badges and card text (free text or key: value lines)
        │   ├── badgestrip.py      badge artwork strips for the stream cards
        │   ├── streaming.py       "More on Netflix / Prime Video / …" lookups
        │   ├── subtitles.py       subtitle fetching, language selection, download
        │   ├── watchstate.py      watched/resume database, Continue Watching, Next Up
        │   ├── library.py         .strm/.nfo export and library index
        │   ├── cache.py           sqlite response cache with stale fallback
        │   ├── aggregate.py       parallel requests with progress/cancel
        │   ├── history.py         recent searches
        │   └── refresh.py         daily manifest refresh
        ├── mdblist.py             MDBList scrobbling, sync, watchlist, ratings
        └── kodi_ui/               Kodi screens and routing
            ├── router.py          plugin://…?action=… dispatch
            ├── menus.py           front page, Widgets folder
            ├── hubs.py            hubs, Genres, Organise catalogs
            ├── browse.py          catalogs → items, filters, paging (in place in widgets and hub rows)
            ├── details.py         show → seasons → episodes
            ├── infodialog.py      Extended info window, and the skin's info page text
            ├── contextmenu.py     the context menu entries and their order
            ├── player.py          stream fetch, picker, autoplay, subtitles
            ├── streamwindow.py    the stream picker window
            ├── search.py, searchwindow.py   search menu, search as you type, results window
            ├── watching.py        Continue Watching, Next Up, mark watched, MDBList
            ├── watchlist.py       MDBList watchlist
            ├── library.py         Kodi library export and watched-state sync
            ├── views.py           default views per list type
            ├── listitems.py       Stremio objects → Kodi ListItems
            ├── manage.py          Manage addons
            ├── wizard.py          setup wizard
            ├── skinhelper.py      helpers for Arctic Zephyr Stremio (hub shortcuts, jump bar)
            ├── keymap.py          "Back stops playback" keymap
            └── service.py         playback tracker, scrobbling, Up Next, background jobs
skin.arctic.zephyr.stremio/        Arctic Zephyr Stremio: 1080i/ (its own pages are in
                                   Includes_StremioInfo.xml, Includes_StremioOSD.xml,
                                   View_528_Stremio.xml, Custom_1170_Search.xml), media/, shortcuts/
repository.shiggsy365/             the Kodi repository add-on
script.shiggsy365.dispatcharrbridge/ Dispatcharr Bridge add-on
plugin.audio.shiggsy365.podcasts/  Podcasts add-on: podcasts/ (no Kodi imports) and podcasts_ui/
service.shiggsy365.tidycache/      Tidy Cache add-on
docs/                              the published repository, built by tools/build_repo.py
tests/                             pytest suite with a fake addon HTTP server and Kodistubs
screenshots/                       the README's screenshots
branding/logo.png                  the logo the icons, fanart and skin startup screen are made from
tools/                             dev install, repository build, artwork generators
                                   (make_info_textures.py: the skin's pills, masks and frames;
                                   make_tiles.py: Stremio Bridge's tiles)
```

## Testing

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest                 # unit + Kodistubs smoke tests
tools/dev_install.sh                       # symlink into ~/.kodi/addons
.venv/bin/kodi-addon-checker --branch omega plugin.video.stremiobridge skin.arctic.zephyr.stremio
```

## Releasing

1. Bump `version` and `<news>` (keep it under ~1100 characters) in the add-on's `addon.xml`. If the
   skin needs the new Stremio Bridge, raise its `<import addon="plugin.video.stremiobridge">` too.
2. Run `.venv/bin/python tools/build_repo.py`. It writes the zips, `addons.xml` and its checksum to
   `docs/`. The skin's `media/` goes into its zip packed as `media/Textures.xbt`, which needs Kodi's
   TexturePacker: install `kodi-tools-texturepacker`, or set `TEXTUREPACKER` to its path.
3. Commit and push. Kodi reads the repository from
   `raw.githubusercontent.com/shiggsy365/KodiStremioBridge/main/docs/` and picks up the new version
   on its next check.

## Protocol notes

- An addon serves `meta` / `stream` / `subtitles` for a request only if the resource is
  listed for that `type` **and** the ID matches its `idPrefixes`, where resource-level
  values override manifest-level ones (`Manifest.supports`).
- Addons are keyed by transport URL, not manifest ID, so the same addon can be installed
  several times with different configurations.
- Manifests are re-fetched (in parallel) when the front page opens if they are more than a
  day old, so catalogs added or removed in an addon's own configuration show up. Unreachable
  addons keep their current manifest; per-catalog preferences are kept (except for catalogs
  that no longer exist). Setting: Network → Refresh addon manifests daily.
- Plugin URLs identify an addon by a short hash of its transport URL
  (`?action=catalog&addon=<key>&type=movie&id=top&f_genre=Action&skip=50&ps=50`), so tokens
  in configured URLs never land in Kodi's logs or widget settings, and any catalog folder
  can be used as a skin widget.
- Paging: many addons drop a few items per page (Cinemeta returns 48–50 of 50), and a declared
  `pageSize` can be wrong (seen: `pageSize: 50` while serving 20, where stepping by 50 skips
  30 items per page). A declared `pageSize` is used only if the first page is consistent with
  it; otherwise the first page's count is rounded up to a common page size. `skip` advances
  by that size, and a page counts as full if it is ≥ 80% filled.
- Required filters with options (e.g. `genre` with `"None"` first) use their first option, like
  Stremio. With *Filter by Genre* on, a genre can be picked while browsing in the Videos window
  or in an Arctic Zephyr Stremio hub row (never in home widgets). An `f_<name>=<value>` param in a
  plugin URL (e.g. a hand-made widget path) sets a filter explicitly.
- Widgets and hub rows page in place: their Next Page and Previous Page tiles are "playable" items
  that run `row_page`, which keeps the row's page (and, in hubs, filters) in a home-window property
  and bumps the widgets' reload token. Only rows whose path carries that token
  (`&reload=$INFO[Window(Home).Property(plugin.video.stremiobridge.widgets.reload)]`) can do
  this; others open the next page in the Videos window.
- Front page: catalogs appear on the add-on's first screen unless they are search-only or
  say `showInHome: false` (Nuvio's rule); the user can override this per catalog. Type menus
  list every browsable catalog regardless.
- Rotating catalogs: ids ending in a number (e.g. BingeCat's
  `aicat_because_watched_movie_seed_1435`) form a family. Links to them also carry
  `slot`/`of` (position and family size). If the exact id is gone, the catalog now in that
  slot is used, provided the family size is unchanged. A missing catalog, or an empty
  numbered one, triggers an immediate manifest refresh (at most every 10 minutes).
- Catalog `isSearch: true` (non-standard) implies a required `search` extra. Catalogs of type
  `all` take part in per-type searches, with results filtered to that type.
- The manifest URL's query string (e.g. `?bcv=48`) is kept on every resource request, as
  Stremio does. Zero-width characters are stripped from stream labels.
- Meta: every enabled addon whose manifest supports `meta` for the type and ID is asked in
  your order; `{"meta": null}` or an error moves on to the next. For IMDb (`tt…`) movies
  and series, Cinemeta is tried last even if it isn't installed (setting).
- Episodes play via `?action=play&type=series&id=tt…:S:E&meta=tt…`; metas with no
  `videos` (or with `behaviorHints.defaultVideoId`) show a single playable entry.
- Streams: all stream addons are asked at once. Duplicates (same URL or same torrent file)
  are dropped. "Quality" sorting is resolution, then debrid-cached (`[RD+]`, ⚡), then size,
  then addon order. Resolution is read from the addon's short label first, because
  descriptions often mention other resolutions.
- Request headers from `behaviorHints.proxyHeaders.request` are passed as Kodi's
  `url|Header=value` syntax, or as inputstream.adaptive properties for HLS/DASH.
- Stream URLs are never written to the log, since debrid links and configured addons carry tokens.
- Subtitles are saved as `NN.<lang>.srt` in `special://temp/stremiobridge/subtitles`, so
  Kodi can tell their language from the file name.
- Search: `?action=search&query=…[&type=movie]` is a stable URL that skins can use. Catalogs
  that need more than a query (e.g. a required genre) are skipped. Each result group opens
  the normal catalog view with `f_search=…`, so paging works there too. Search as you type
  (Arctic Zephyr Stremio's search page) uses `search_live`, `search_suggest` and `search_recent`,
  which read the query from the home window property `sbsearch.query` and wait for typing to
  settle; their URL parameters only make Kodi reload the lists.
- Watch state lives in `watch.db`: one row per video id (`tt…` or `tt…:S:E`). Watched at
  ≥ 90 %, resume point after 2 min (settings). The play route announces what it starts
  (home-window property); the service only tracks playback it was told about. Kodi's own
  "Resume from…" choice arrives as `resume:true|false` in `sys.argv[3]`; the service seeks
  if Kodi didn't.
- MDBList: `/scrobble/start|pause|stop` during playback; `/sync/watched` pushed for items
  watched locally and pulled (full list) when `/sync/last_activities` changes, every few
  hours and after playback. A pull never unwatches items pushed in the same sync, watched in
  the last day, or more than 30 % of synced items at once. Remote items map to local ids by
  IMDb id first (then TMDB/TVDB).
- Stream checks (autoplay): a 2-byte ranged GET with the stream's headers. Fails on HTTP errors,
  HTML pages, and tiny files standing in for multi-GB releases (error/placeholder videos).
  400/408/425/429/5xx and connection errors are retried twice, 2 s apart: AIOStreams (via its
  debrid proxy) often answers the first request for a fresh link with 400, then serves it.
- Resource path segments and `extra` values are encoded like JavaScript's `encodeURIComponent`.
