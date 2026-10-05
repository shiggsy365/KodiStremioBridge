"""Podcasts without Kodi: Apple directory, RSS feeds, local state and gPodder sync."""


class PodcastError(Exception):
    """A request or feed that couldn't be used; the message is shown to the user."""
