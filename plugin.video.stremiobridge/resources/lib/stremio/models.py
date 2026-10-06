"""Typed views over the JSON that Stremio addons return."""

import re
from dataclasses import dataclass, field

from . import ManifestError


def _str_tuple(value):
    if not isinstance(value, list):
        return ()
    return tuple(v for v in value if isinstance(v, str))


@dataclass(frozen=True)
class ExtraProp:
    name: str
    is_required: bool = False
    options: tuple = ()
    options_limit: int = 1

    @classmethod
    def from_dict(cls, data):
        return cls(
            name=data["name"],
            is_required=bool(data.get("isRequired", False)),
            options=_str_tuple(data.get("options")),
            options_limit=int(data.get("optionsLimit", 1) or 1),
        )


@dataclass(frozen=True)
class CatalogDef:
    type: str
    id: str
    name: str
    extra: tuple = ()
    page_size: int = None      # "pageSize": the step for "skip", when the addon states it
    show_in_home: bool = None  # "showInHome": None when the addon doesn't say

    @property
    def key(self):
        return f"{self.type}/{self.id}"

    def extra_prop(self, name):
        return next((e for e in self.extra if e.name == name), None)

    def supports_extra(self, name):
        return self.extra_prop(name) is not None

    @property
    def is_searchable(self):
        return self.supports_extra("search")

    @property
    def is_searchable_alone(self):
        """Searchable with nothing but a query (no other required input)."""
        return self.is_searchable and all(e.name == "search" or not e.is_required for e in self.extra)

    @property
    def is_browsable(self):
        """True unless the catalog needs free-text input (e.g. search-only catalogs).

        Required extras with options (like Cinemeta's "by year" genre) are fine:
        the user picks one of the options.
        """
        return all(e.options for e in self.extra if e.is_required)

    @property
    def is_people_search(self):
        """A catalog that searches people and returns their filmography
        (AIOMetadata's ``people_search.*``)."""
        return self.id.startswith("people_search")

    @property
    def is_search_only(self):
        extra = self.extra_prop("search")
        return extra is not None and extra.is_required

    @property
    def default_front_page(self):
        """Whether the addon wants this catalog on the home screen (Nuvio's rule:
        yes unless search-only or it explicitly says ``showInHome: false``)."""
        return not self.is_search_only and self.show_in_home is not False

    @property
    def supports_paging(self):
        return self.supports_extra("skip")

    @property
    def filters(self):
        """Extras the user can pick a value for, e.g. genre."""
        return tuple(e for e in self.extra if e.options and e.name not in ("search", "skip"))

    @classmethod
    def from_dict(cls, data):
        if "extra" in data and isinstance(data["extra"], list):
            extra = tuple(
                ExtraProp.from_dict(e)
                for e in data["extra"]
                if isinstance(e, dict) and isinstance(e.get("name"), str)
            )
        else:
            # Legacy manifests: extraSupported / extraRequired / genres.
            required = set(_str_tuple(data.get("extraRequired")))
            genres = _str_tuple(data.get("genres"))
            extra = tuple(
                ExtraProp(
                    name=name,
                    is_required=name in required,
                    options=genres if name == "genre" else (),
                )
                for name in _str_tuple(data.get("extraSupported"))
            )
        # Non-standard flag some addons use to mark a search catalog. Treat it
        # like a required "search" extra unless the catalog declares one itself.
        if data.get("isSearch") is True and not any(e.name == "search" for e in extra):
            extra += (ExtraProp(name="search", is_required=True),)
        try:
            page_size = int(data["pageSize"]) if data.get("pageSize") else None
        except (TypeError, ValueError):
            page_size = None
        show_in_home = data.get("showInHome")
        return cls(
            type=data["type"],
            id=data["id"],
            name=data.get("name") or data["id"],
            extra=extra,
            page_size=page_size if page_size and page_size > 0 else None,
            show_in_home=show_in_home if isinstance(show_in_home, bool) else None,
        )


_YEAR = re.compile(r"(\d{4})")


@dataclass(frozen=True)
class MetaPreview:
    """An item in a catalog response (a subset of the full meta object)."""

    id: str
    type: str
    name: str
    poster: str = ""
    poster_shape: str = "poster"
    background: str = ""
    logo: str = ""
    description: str = ""
    release_info: str = ""
    imdb_rating: float = None
    genres: tuple = ()
    runtime: str = ""
    people: tuple = ()  # Person entries: cast, directors, writers
    trailer: str = ""   # YouTube id
    released: str = ""  # ISO date-time, when the addon gives one

    @classmethod
    def from_dict(cls, data, default_type=None):
        """Parse one catalog entry; returns None for entries that can't be shown."""
        fields_ = _preview_fields(data, default_type)
        return cls(**fields_) if fields_ else None

    @property
    def year(self):
        match = _YEAR.search(self.release_info)
        return int(match.group(1)) if match else None

    @property
    def runtime_seconds(self):
        return parse_runtime(self.runtime)

    @property
    def premiered(self):
        """``YYYY-MM-DD`` or ""."""
        match = _DATE.match(self.released)
        return match.group(1) if match else ""

    def _names(self, job):
        return tuple(p.name for p in self.people if p.job == job)

    @property
    def cast(self):
        return self._names(CAST)

    @property
    def director(self):
        return self._names(DIRECTOR)

    @property
    def writer(self):
        return self._names(WRITER)


def _poster_shape(shape):
    """"poster", "landscape" or "square". Stremio's older name for a poster is
    "regular" (some addons still send it); anything unknown is a poster too."""
    shape = (shape or "").lower()
    return shape if shape in ("landscape", "square") else "poster"


def _preview_fields(data, default_type):
    if not isinstance(data, dict):
        return None
    id_, name = data.get("id"), data.get("name")
    type_ = data.get("type") or default_type
    if not all(isinstance(v, str) and v for v in (id_, name, type_)):
        return None

    release_info = data.get("releaseInfo") or data.get("year") or data.get("released") or ""
    try:
        rating = float(data.get("imdbRating"))
    except (TypeError, ValueError):
        rating = None
    return dict(
        id=id_,
        type=type_,
        name=name,
        poster=data.get("poster") or "",
        poster_shape=_poster_shape(data.get("posterShape")),
        background=data.get("background") or "",
        logo=data.get("logo") or "",
        description=data.get("description") or "",
        release_info=str(release_info),
        imdb_rating=rating,
        genres=_str_tuple(data.get("genres") or data.get("genre")),
        runtime=str(data.get("runtime") or ""),
        people=parse_people(data),
        trailer=_trailer(data),
        released=str(data.get("released") or ""),
    )


CAST, DIRECTOR, WRITER = "cast", "director", "writer"


@dataclass(frozen=True)
class Person:
    name: str
    job: str = CAST
    role: str = ""   # the character, for cast
    photo: str = ""


def _split_names(value):
    """A name list, a single name, or "A, B" as a tuple of names."""
    if isinstance(value, str):
        return tuple(n.strip() for n in value.split(",") if n.strip())
    return tuple(n.strip() for n in _str_tuple(value) if n.strip())


def parse_people(data):
    """Cast and crew, richest source first: ``app_extras`` (with photos and
    characters, e.g. AIOMetadata), then ``cast``/``director``/``writer`` (names
    or objects), then ``links`` (Stremio's Cast/Directors/Writers links)."""
    extras = data.get("app_extras") if isinstance(data.get("app_extras"), dict) else {}
    links = data.get("links") if isinstance(data.get("links"), list) else []
    people, seen = [], set()

    def add(job, name, role="", photo=""):
        name = (name or "").strip()
        if name and (job, name) not in seen:
            seen.add((job, name))
            people.append(Person(name=name, job=job, role=role if role != name else "", photo=photo or ""))

    for job, extra_key, field_name, link_category in (
            (DIRECTOR, "directors", "director", "Directors"),
            (WRITER, "writers", "writer", "Writers"),
            (CAST, "cast", "cast", "Cast")):
        for entry in extras.get(extra_key) or []:
            if isinstance(entry, dict):
                add(job, entry.get("name"), entry.get("character") or "", entry.get("photo") or "")
        raw = data.get(field_name)
        for entry in raw if isinstance(raw, list) else [raw]:
            if isinstance(entry, dict):
                add(job, entry.get("name"), entry.get("character") or "", entry.get("photo") or "")
            else:
                for name in _split_names(entry):
                    add(job, name)
        for name in _link_names(links, link_category):
            add(job, name)
    return tuple(people)


def _certification(data):
    extras = data.get("app_extras") if isinstance(data.get("app_extras"), dict) else {}
    for value in (extras.get("certificationLocal"), extras.get("certification"), data.get("certification")):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _trailer(data):
    for trailer in data.get("trailers") or []:
        if isinstance(trailer, dict) and trailer.get("type", "Trailer") == "Trailer":
            if trailer.get("ytId") or trailer.get("source"):
                return trailer.get("ytId") or trailer.get("source")
    for trailer in data.get("trailerStreams") or []:
        if isinstance(trailer, dict) and trailer.get("ytId"):
            return trailer["ytId"]
    return ""


def _link_names(links, category):
    return tuple(
        link["name"] for link in links
        if isinstance(link, dict) and link.get("category") == category and isinstance(link.get("name"), str)
    )


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


@dataclass(frozen=True)
class Video:
    """An episode, or an item of a channel."""

    id: str
    title: str = ""
    season: int = None
    episode: int = None
    released: str = ""
    thumbnail: str = ""
    overview: str = ""
    rating: float = None  # the episode's own rating, when the addon gives one (Cinemeta often sends "0")

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict) or not isinstance(data.get("id"), str) or not data["id"]:
            return None
        return cls(
            id=data["id"],
            title=data.get("title") or data.get("name") or "",
            season=_int_or_none(data.get("season")),
            episode=_int_or_none(data.get("episode", data.get("number"))),
            released=str(data.get("released") or ""),
            thumbnail=data.get("thumbnail") or "",
            overview=data.get("overview") or data.get("description") or "",
            rating=_rating(data.get("imdbRating") or data.get("rating")),
        )

    @property
    def air_date(self):
        """``YYYY-MM-DD`` or ""."""
        match = _DATE.match(self.released)
        return match.group(1) if match else ""

    def is_released(self, today):
        """`today` is an ISO date string. Videos without a date count as released."""
        return not self.air_date or self.air_date <= today


def _rating(value):
    """A 0-10 rating, or None for missing, unreadable or zero ("0" means unrated)."""
    try:
        rating = float(value)
    except (TypeError, ValueError):
        return None
    return rating if 0 < rating <= 10 else None


# The video fields Video.from_dict reads; trim_meta drops the rest before caching.
VIDEO_KEYS = ("id", "title", "name", "season", "episode", "number", "released", "thumbnail", "overview",
              "rating", "imdbRating")


def trim_meta(response):
    """A ``{"meta": ...}`` response with each video cut down to VIDEO_KEYS (plus
    "description" where there's no overview): long-running shows send thousands
    of episodes with fields nothing here uses."""
    meta = response.get("meta") if isinstance(response, dict) else None
    if not isinstance(meta, dict) or not isinstance(meta.get("videos"), list):
        return response
    videos = []
    for video in meta["videos"]:
        if isinstance(video, dict):
            slim = {k: video[k] for k in VIDEO_KEYS if k in video}
            if not slim.get("overview") and video.get("description"):
                slim["description"] = video["description"]
            video = slim
        videos.append(video)
    return {**response, "meta": {**meta, "videos": videos}}


@dataclass(frozen=True)
class Meta(MetaPreview):
    """A full meta object, as returned by the ``meta`` resource."""

    country: str = ""
    certification: str = ""  # age rating, local one preferred (e.g. "15", "TV-MA")
    videos: tuple = ()
    default_video_id: str = ""
    external_ids: tuple = ()  # (("imdb", "tt…"), ("tmdb", 123), ...) from the id and any *_id fields

    @classmethod
    def from_dict(cls, data, default_type=None):
        fields_ = _preview_fields(data, default_type)
        if not fields_:
            return None
        hints = data.get("behaviorHints") if isinstance(data.get("behaviorHints"), dict) else {}
        return cls(
            **fields_,
            country=data.get("country") or "",
            certification=_certification(data),
            videos=tuple(v for v in (Video.from_dict(x) for x in data.get("videos") or []) if v),
            default_video_id=hints.get("defaultVideoId") or "",
            external_ids=tuple(sorted(external_ids(fields_["id"], data).items())),
        )

    @property
    def seasons(self):
        """Season numbers in display order; specials (season 0) last."""
        return sorted({v.season for v in self.videos if v.season is not None}, key=lambda s: (s == 0, s))

    def episodes(self, season):
        """Videos of one season (None = videos without a season) in episode order."""
        videos = [v for v in self.videos if v.season == season]
        if season is None:
            return videos  # channel videos: keep the addon's order
        return sorted(videos, key=lambda v: (v.episode is None, v.episode or 0))


_ID_PREFIXES = {"tt": "imdb", "tmdb:": "tmdb", "tvdb:": "tvdb", "kitsu:": "kitsu", "mal:": "mal"}
_ID_FIELDS = {"imdb_id": "imdb", "imdbId": "imdb", "tmdb_id": "tmdb", "moviedb_id": "tmdb", "tvdb_id": "tvdb"}


def _id_value(value):
    value = str(value).strip()
    return int(value) if value.isdigit() else value


def external_ids(stremio_id, data=None):
    """IMDb/TMDB/TVDB/... ids for a Stremio id such as ``tt0903747`` or
    ``tmdb:1396``, plus any ``imdb_id``/``tmdb_id``/``tvdb_id`` fields in `data`."""
    ids = {}
    base = stremio_id.split(":")[0] if stremio_id.startswith("tt") else stremio_id
    for prefix, name in _ID_PREFIXES.items():
        if base.startswith(prefix):
            ids[name] = base if name == "imdb" else _id_value(base[len(prefix):].split(":")[0])
            break
    for field_name, name in _ID_FIELDS.items():
        value = (data or {}).get(field_name)
        if value not in (None, "") and name not in ids:
            value = _id_value(value)
            if name != "imdb" or str(value).startswith("tt"):
                ids[name] = value
    return ids


_HOURS = re.compile(r"(\d+)\s*h", re.I)
_MINUTES = re.compile(r"(\d+)\s*m", re.I)


def parse_runtime(text):
    """"142 min", "2h 22min" or "142" -> seconds; None if unknown."""
    text = (text or "").strip()
    if text.isdigit():
        return int(text) * 60
    hours, minutes = _HOURS.search(text), _MINUTES.search(text)
    if not hours and not minutes:
        return None
    return (int(hours.group(1)) * 3600 if hours else 0) + (int(minutes.group(1)) * 60 if minutes else 0)


@dataclass(frozen=True)
class ResourceDef:
    """A manifest resource. types/id_prefixes of None mean "inherit from manifest"."""

    name: str
    types: tuple = None
    id_prefixes: tuple = None

    @classmethod
    def from_value(cls, value):
        if isinstance(value, str):
            return cls(name=value)
        if isinstance(value, dict) and isinstance(value.get("name"), str):
            return cls(
                name=value["name"],
                types=_str_tuple(value["types"]) if "types" in value else None,
                id_prefixes=_str_tuple(value["idPrefixes"]) if "idPrefixes" in value else None,
            )
        return None


@dataclass(frozen=True)
class Manifest:
    id: str
    name: str
    version: str
    description: str = ""
    logo: str = ""
    types: tuple = ()
    id_prefixes: tuple = None
    resources: tuple = ()
    catalogs: tuple = ()
    configurable: bool = False
    configuration_required: bool = False
    adult: bool = False
    p2p: bool = False
    raw: dict = field(default_factory=dict, compare=False, repr=False)

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise ManifestError("Manifest is not a JSON object")
        for key in ("id", "name"):
            if not isinstance(data.get(key), str) or not data[key]:
                raise ManifestError(f"Manifest is missing '{key}'")
        if not isinstance(data.get("resources"), list):
            raise ManifestError("Manifest is missing 'resources'")

        resources = tuple(
            r for r in (ResourceDef.from_value(v) for v in data["resources"]) if r is not None
        )
        catalogs = []
        for c in data.get("catalogs") or []:
            try:
                catalogs.append(CatalogDef.from_dict(c))
            except (KeyError, TypeError, ValueError):
                continue  # Skip malformed catalogs rather than rejecting the addon.

        hints = data.get("behaviorHints") or {}
        return cls(
            id=data["id"],
            name=data["name"],
            version=str(data.get("version") or "0.0.0"),
            description=data.get("description") or "",
            logo=data.get("logo") or "",
            types=_str_tuple(data.get("types")),
            id_prefixes=_str_tuple(data["idPrefixes"]) if "idPrefixes" in data else None,
            resources=resources,
            catalogs=tuple(catalogs),
            configurable=bool(hints.get("configurable", False)),
            configuration_required=bool(hints.get("configurationRequired", False)),
            adult=bool(hints.get("adult", False)),
            p2p=bool(hints.get("p2p", False)),
            raw=data,
        )

    @property
    def resource_names(self):
        return tuple(r.name for r in self.resources)

    def supports(self, resource, type_, id_=None):
        """Whether this addon can answer `resource` requests for `type_` (and `id_`).

        Catalog support is per-catalog, so use `catalogs` for that instead.
        """
        for res in self.resources:
            if res.name != resource:
                continue
            types = res.types if res.types is not None else self.types
            if type_ not in types:
                continue
            prefixes = res.id_prefixes if res.id_prefixes is not None else self.id_prefixes
            if id_ is None or not prefixes or any(id_.startswith(p) for p in prefixes):
                return True
        return False


@dataclass(frozen=True)
class Subtitle:
    url: str
    lang: str = ""
    id: str = ""
    addon: str = ""

    @classmethod
    def from_dict(cls, data, addon=""):
        if not isinstance(data, dict) or not isinstance(data.get("url"), str) or not data["url"]:
            return None
        return cls(url=data["url"], lang=str(data.get("lang") or ""), id=str(data.get("id") or ""), addon=addon)
