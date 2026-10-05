"""A podcast is identified by its feed URL (what gPodder syncs); an episode by
its feed URL plus its RSS guid, or its audio URL when the feed has no guid."""

from dataclasses import asdict, dataclass, fields


def _from_dict(cls, data):
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in (data or {}).items() if k in names})


@dataclass
class Podcast:
    title: str
    feed_url: str = ""
    author: str = ""
    image: str = ""
    apple_id: str = ""
    genre: str = ""
    description: str = ""

    to_dict = asdict

    @classmethod
    def from_dict(cls, data):
        return _from_dict(cls, data)


@dataclass
class Episode:
    feed_url: str
    guid: str
    title: str
    url: str
    description: str = ""
    image: str = ""
    published: int = 0      # epoch seconds, 0 when the feed doesn't say
    duration: int = 0       # seconds, 0 when the feed doesn't say
    podcast_title: str = ""
    mime: str = ""          # the enclosure's type, e.g. audio/mpeg

    @property
    def key(self):
        return self.guid or self.url

    to_dict = asdict

    @classmethod
    def from_dict(cls, data):
        return _from_dict(cls, data)
