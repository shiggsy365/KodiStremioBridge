# Stremio Bridge for Kodi

<img src="plugin.video.stremiobridge/resources/icon.png" width="128" align="right" alt="">

**Stremio Bridge** (`plugin.video.stremiobridge`) brings Stremio addons into Kodi. Paste in the
manifest URLs of the Stremio addons you already use, as many as you like, and browse their
catalogs, read their metadata, play their streams and load their subtitles. It runs as a normal
Kodi video add-on, with your skin, your remote and your home-screen widgets.

It's a bridge, not a source: the add-on provides no content of its own. Everything you see comes
from the Stremio addons you install.

Needs **Kodi 21 Omega or later**.

## What it does

- **Any number of Stremio addons.** Add them by manifest URL (configured URLs keep their
  settings), then reorder, enable or disable them. Every addon is asked for what it supports:
  catalogs, metadata, streams and subtitles.
- **Catalogs, organised.** The front page shows Continue Watching, Next Up and your Watchlist, then
  **Movies, TV Shows, Anime and More** hubs, each with Search and Genres. Organise catalogs lets
  you reorder, rename, hide, pin catalogs to the front page, move them between hubs, and create
  hubs of your own.
- **Proper show pages.** Shows, seasons and episodes, with artwork, cast photos, trailers and
  unaired episodes greyed out. Shows open on the next episode you haven't watched.
- **Extended info.** An info page of its own with clickable cast and director (to search for
  their other work), Play/Resume, Trailer, Similar titles, an Overall Rating, Mark watched, and
  Add to library or watchlist.
- **Streams your way.** Everything your stream addons find, fetched in parallel and sorted, on a
  stream page of cards with badges (resolution, release, Dolby Vision/HDR, audio, channels,
  streaming service, cached) and filters by addon, resolution and cached. You can also turn on autoplay: it checks streams in
  order ("Trying stream 3 of 25") until one works, and if a stream fails after it starts it
  moves on to the next.
- **Search.** One search across every search catalog, with a row of results per catalog. Choose
  which catalogs take part and in what order. Search for people too.
- **Watch state.** Resume points, watched ticks, Continue Watching and Next Up, with time left and
  "5 of 10 watched" progress. Mark a movie, episode, season or whole show watched.
- **Home-screen widgets.** A Widgets folder of ready-made sources that refresh after you watch
  something.
- **Kodi's library (optional).** Add movies and shows to Kodi's own Movies and TV Shows library.
  Followed shows pick up new episodes daily.
- **Fits your skin.** Choose a default view for movie, show, season and episode lists from the
  views your skin offers, with previews.

[<img src="https://github.com/shiggsy365/AIOStreamsKODI/blob/main/.github/support_me_on_kofi_red.png?raw=true">](https://ko-fi.com/shiggsy365)

## The best setup

Stremio Bridge works with any Stremio addon, but it was built around one combination. Together
they give you a polished, Netflix-like Kodi with very little to configure.

| | What it brings |
|---|---|
| **[AIOMetadata](https://github.com/cedya77/aiometadata)** (Stremio addon) | Catalogs and metadata from TMDB, TVDB, MDBList, AniList and more, in one addon you configure on its website: trending, streaming-service and genre catalogs, anime, artwork, cast photos, trailers and people search. Point it at your MDBList account and its catalogs follow what you watch. |
| **[AIOStreams](https://github.com/Viren070/AIOStreams)** (Stremio addon) | Streams from many scrapers and your debrid service, combined, filtered and sorted into one clean list. Its direct links play straight away in Kodi (no torrent client needed), which suits autoplay well. |
| **[MDBList](https://mdblist.com)** (free account) | Watched history, resume points and a watchlist shared with your other devices, plus ratings and similar titles. See below. |
| **[Up Next](https://github.com/im85288/service.upnext)** (Kodi add-on, `service.upnext`) | Shows the "Next episode" pop-up near the end of an episode. Stremio Bridge hands it the next episode, and plays it from the same source (AIOStreams' `bingeGroup`) with no stream picker in between. |

How it fits together: **AIOMetadata** fills the hubs and show pages, **AIOStreams** supplies the
streams, **MDBList** keeps your watch history in step across devices, and **Up Next** takes you
from one episode to the next. In Stremio Bridge's settings, also turn on *Play the best stream
automatically* (Playback) and *Include next episodes in Continue Watching* (Watch state).

Using only some of these? Cinemeta (Stremio's own metadata addon) is used as a fallback for
metadata, and any other Stremio catalog, stream or subtitle addon works alongside them.

### Why MDBList is highly recommended

The add-on works without it, but connecting a free [MDBList](https://mdblist.com) account
(Settings → MDBList → paste your API key from *mdblist.com → Preferences → API access*)
unlocks most of what makes it feel complete:

- **Watched history everywhere.** What you watch is scrobbled to MDBList as it plays, and what you
  watched elsewhere (other Kodi devices, Stremio, Nuvio, the website) comes back. Watched ticks,
  Next Up and Continue Watching stay in step.
- **Resume on any device.** Stop a film on one device and pick it up on another at the same point.
- **Overall Rating.** IMDb, Rotten Tomatoes (critics and audience), Metacritic, Trakt, TMDB,
  Letterboxd and Roger Ebert, each scaled to 100 and averaged with MDBList's own score.
- **Similar titles,** from MDBList's recommendations.
- **A watchlist, everywhere.** *Add to watchlist* on any movie or show updates your MDBList
  watchlist. A Watchlist list on the front page (also a widget) shows it, and it can optionally
  fill Kodi's library.
- **Mark watched sticks.** Marking a movie, episode, season or show watched or unwatched is sent
  to MDBList straight away.

Without MDBList, watched state and resume points stay on one device, and the ratings, Similar
titles and watchlist features are hidden.

## Installing

Install from the repository, so updates arrive automatically:

1. In Kodi, go to **Settings → System → Add-ons** and turn on **Unknown sources**.
2. Go to **Settings → File manager → Add source**, enter
   `https://shiggsy365.github.io/KodiStremioBridge/` and name it `shiggsy365`.
3. Go to **Add-ons → Install from zip file → shiggsy365** and pick `repository.shiggsy365-x.y.z.zip`.
4. Go to **Add-ons → Install from repository → shiggsy365 Repository → Video add-ons →
   Stremio Bridge**.

Kodi installs `script.module.requests` with it. For the pop-up between episodes, also install
**Up Next** from the official Kodi repository.

### Also in the repository: Dispatcharr Bridge

**Dispatcharr Bridge** (`script.shiggsy365.dispatcharrbridge`) is for live TV in IPTV Simple served by
[Dispatcharr](https://github.com/Dispatcharr/Dispatcharr). While watching a channel, **hold
OK/Select** to see its source streams (with M3U account, resolution, codec and bitrate, the current
one marked) and switch to another, or let Dispatcharr try the next one. *Choose source (Dispatcharr)*
does the same from a channel's or guide entry's context menu.

The switch happens on Dispatcharr's server (`/proxy/ts/change_stream`), so Kodi keeps playing the
same channel with its guide and channel info. Settings: an admin user's Dispatcharr API key, and the
address (found from IPTV Simple's playlist if left empty). Switching affects everyone watching that
channel, and the channel's source order in Dispatcharr is unchanged.

### Also in the repository: Podcasts

**Podcasts** (`plugin.audio.shiggsy365.podcasts`, under *Music add-ons*) finds, follows and plays
podcasts. Its home menu has:

- **My Podcasts**: the podcasts you subscribe to (*Subscribe* is in any podcast's context menu);
- **My Unplayed Podcasts**: subscriptions you haven't played an episode of yet;
- **My Latest Episodes**: episodes you're part-way through first, then for each other subscription
  the next episode by release date after the newest one you've played, like Up Next (for one you've
  never played, its newest episode if it came out in the last 30 days);
- **Trending Podcasts**: Apple's top podcasts in each genre;
- **Top Podcasts**: Apple's overall chart;
- **Search**, with recent searches kept.

Listings come from Apple (no key needed; set the country in the settings) and episodes play straight
from each podcast's own feed. Stopping part-way saves your place, and playing again offers to resume.

**Sync between devices (optional).** Turn on *Sync with a gPodder server* to share subscriptions and
play positions between your Kodi devices, and with phone apps that support gPodder sync (AntennaPod,
for example). It works with [oPodSync](https://github.com/kd2org/opodsync) (small and easy to host
yourself), gpodder.net, or the Nextcloud *gPodder Sync* app (choose *Nextcloud* as the server type).
Use the same *Device id* on every Kodi so they share one subscription list. It syncs on start, every
15 minutes and shortly after you subscribe or stop listening; when two devices changed the same
episode, the later change wins.

### Also in the repository: Tidy Cache

**Tidy Cache** (`service.shiggsy365.tidycache`, under *Services*) keeps a low-storage device, such as a
Fire TV Stick, from filling up. By default, every 7 days it:

- removes cached artwork (posters, fanart, thumbnails) that Kodi hasn't shown in the last 7 days.
  It goes through Kodi itself, so it's safe while Kodi runs, and Kodi downloads anything it needs again;
- deletes the add-on zips Kodi keeps in `addons/packages` after installing or updating.

It waits while something is playing. Change how often it runs and how old artwork must be (0
removes all cached artwork) in its settings, which also have a *Tidy up now* button.

### Also in the repository: Arctic Zephyr Stremio (skin)

**Arctic Zephyr Stremio** (`skin.arctic.zephyr.stremio`, under *Look and feel → Skin*) is a fork of
Arctic: Zephyr - Reloaded made for Stremio Bridge, without TMDb Helper or Embuary. Out of the box:

- **Home** shows Continue Watching and your MDBList watchlist; **Movies** and **TV Shows** show
  Cinemeta's Featured and Popular lists (with your own metadata addon's details, e.g. AIOMetadata).
  Each opens a hub: Home Hub, and Movies and Series hubs listing all your Stremio Bridge catalogs
  (kept up to date by Stremio Bridge; *Settings → Update Arctic Zephyr Stremio hubs* refreshes them).
- **One info page for movies, shows, seasons and episodes**, with Play, Available streams, Mark
  watched, Watchlist, Trailer, Find similar and Cast buttons (plus "More on Netflix / Prime Video /
  …" when a streaming service has it). On a show, season or episode, Play starts the next episode to
  watch, and a **season and episode browser** fills the space under the plot, opening on that
  episode; choosing an episode opens its own page. Selecting an actor opens Stremio Bridge's person
  search. Watched titles get a *Watched* banner on the poster and episode thumbnails, and watched
  seasons are struck through.
- A context menu in a fixed order: Play, Information, Mark as watched (one entry, for the movie,
  episode, season or show it's opened on), Show Playable Streams, watchlist, Play trailer,
  favourites. Kodi's own Mark as watched also updates your history.
- **Search as you type**: an on-screen keyboard (A–Z with a space bar, or 0–9 and symbols) with rows
  of movie and TV results from Stremio Bridge's search catalogs, autofill suggestions and your last 5
  searches. Open it with the search icon by the clock, or Right on the last main menu item.
- A full-screen **TV guide** with channel groups as tabs, the programme's details above the grid and
  as many channels as fit.
- Catalogs open in *Poster Flix v2*. *Show clearlogo instead of title* (in its view settings) is one
  setting for the whole skin: views, Home widgets, the player and the info page.
- A **…** menu item: Kodi settings, with a file browser, Stremio Bridge settings, Reload Skin and Exit.

With this skin, Stremio Bridge's library integration is off (the skin's menus show your catalogs
directly). Licensed CC BY-NC-SA 3.0 like the original; credits in its `CREDITS.md`.

## Getting started

1. **Add your addons:** open Stremio Bridge's settings, then **Addons → Manage addons → Add
   addon…**, and paste each manifest URL (for example your configured AIOMetadata and AIOStreams
   URLs).
2. **Connect MDBList** (recommended): **Settings → MDBList**, then paste your API key.
3. **Arrange the catalogs:** **Settings → Addons → Organise catalogs**. Use the context menu on a
   catalog to show, hide, rename, move or pin it, or on a hub to create, rename or remove hubs.
4. **Widgets:** in your skin's widget picker, browse to **Add-ons → Stremio Bridge → Widgets** and
   pick Continue Watching, Next Up, Watchlist or any catalog. These already carry a reload token,
   so they refresh after you watch something. (If the folder isn't there, turn on *Show the
   Widgets folder on the front page* under **Settings → Metadata**.)
5. **Views** (optional): **Settings → Views**. Pick the view your skin uses for movie, show,
   season and episode lists.
6. **Library** (optional): **Settings → Library → Set up library** adds two video sources and
   restarts Kodi. Then, in **Videos → Files**, set the content of "Stremio Bridge Movies" to Movies
   and "Stremio Bridge TV" to TV shows, once. Add titles with *Add to library*.

Your addons and settings are stored in `special://profile/addon_data/plugin.video.stremiobridge/`.
Configured addon URLs often contain account tokens, so the add-on never writes them, or stream
links, to Kodi's log.

### Stream badges

The stream page's badge artwork comes from a Nuvio badge configuration shared by saif1233, with
images from [BetterFormatter](https://github.com/9mousaa/BetterFormatter) and
[Omni-Template-Bot-Bid-Raiser](https://github.com/nobnobz/Omni-Template-Bot-Bid-Raiser); see
`tools/badges/nuvio/SOURCES.md`. Dolby, DTS and IMAX logos belong to their owners.

AIOStreams users can make the badges exact with a custom formatter description that writes one
`key: value` per line (`res`, `src`, `vis`, `aud`, `ch`, `size`, `br`, `svc`, `cached`, `type`,
`prov`, `grp`, `lang`, `net`); see `stremio/streaminfo.py`.

## For developers

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
            ├── browse.py          catalogs → items, filters, paging
            ├── details.py         show → seasons → episodes
            ├── infodialog.py      Extended info window
            ├── player.py          stream fetch, picker, autoplay, subtitles
            ├── search.py, searchwindow.py   search menu and results window
            ├── watching.py        Continue Watching, Next Up, mark watched, MDBList
            ├── watchlist.py       MDBList watchlist
            ├── library.py         Kodi library export and watched-state sync
            ├── views.py           default views per list type
            ├── listitems.py       Stremio objects → Kodi ListItems
            ├── manage.py          Manage addons
            └── service.py         playback tracker, scrobbling, Up Next, background jobs
repository.shiggsy365/             the Kodi repository add-on
service.shiggsy365.tidycache/      Tidy Cache add-on
script.shiggsy365.dispatcharrbridge/ Dispatcharr Bridge add-on
plugin.audio.shiggsy365.podcasts/  Podcasts add-on: podcasts/ (Apple, RSS, SQLite state, gPodder sync;
                                   no Kodi imports) and podcasts_ui/ (menus, playback service)
docs/                              the published repository (GitHub Pages), built by tools/build_repo.py
tests/                             pytest suite with a fake addon HTTP server and Kodistubs
branding/logo.png                  the logo the icons, fanart and skin startup screen are made from
tools/                             dev install, zip/repository builds, artwork generators
```

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest                 # unit + Kodistubs smoke tests
tools/dev_install.sh                       # symlink into ~/.kodi/addons
.venv/bin/kodi-addon-checker --branch omega plugin.video.stremiobridge
```

**Releasing:** bump `version` (and `<news>`) in `plugin.video.stremiobridge/addon.xml`, run
`.venv/bin/python tools/build_repo.py`, then commit and push `docs/`. Kodi picks up the new version
on its next repository check.

### Protocol notes

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
  (never in widgets). An `f_<name>=<value>` param in a plugin URL (e.g. a hand-made widget
  path) sets a filter explicitly.
- Front page: catalogs appear on the add-on's first screen unless they are search-only or
  say `showInHome: false` (Nuvio's rule); the user can override this per catalog. Type menus
  list every browsable catalog regardless.
- Rotating catalogs: ids ending in a number (e.g. BingeCat'sKodiStremioBridge
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

## Disclaimer

This add-on provides no content. You are responsible for the Stremio addons you install
and the content they provide.

**Support the ecosystem:**

[<img src="https://github.com/shiggsy365/AIOStreamsKODI/blob/main/.github/support_me_on_kofi_red.png?raw=true">](https://ko-fi.com/shiggsy365)
