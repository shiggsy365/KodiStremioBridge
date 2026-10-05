"""One gPodder sync pass: push local changes first, then merge everyone else's.

Pushing first means a pull never undoes a change this device hasn't sent yet.
Play positions merge by timestamp: whichever device changed an episode last
wins (store.apply_remote).
"""

from .gpodder import from_iso
from .models import Podcast

SUBS_SINCE, ACTIONS_SINCE = "sync.subscriptions_since", "sync.actions_since"


def sync(store, client, log=lambda msg: None):
    """Returns a summary: counts of what was sent and received."""
    summary = {"subscriptions_sent": 0, "subscriptions_received": 0, "positions_sent": 0, "positions_received": 0}

    added, removed = store.pending_subscriptions()
    if added or removed:
        _, renames = client.put_subscriptions(added, removed)
        store.subscriptions_pushed(added, removed)
        store.rename_feeds(renames)
        summary["subscriptions_sent"] = len(added) + len(removed)

    remote_added, remote_removed, timestamp = client.get_subscriptions(store.get_state(SUBS_SINCE, 0))
    for url in remote_added:
        if url and not store.is_subscribed(url):
            store.subscribe(Podcast(title="", feed_url=url), dirty=False)
            summary["subscriptions_received"] += 1
    for url in remote_removed:
        if url and store.is_subscribed(url):
            store.unsubscribe(url, dirty=False)
            store.subscriptions_pushed([], [url])  # nothing to push: drop the row
            summary["subscriptions_received"] += 1
    store.set_state(SUBS_SINCE, timestamp)

    rows = store.dirty_progress()
    actions = [{"podcast": r["feed_url"], "episode": r["url"], "guid": r["guid"], "position": r["position"],
                "total": max(r["duration"], r["position"]), "timestamp": r["updated"]}
               for r in rows if r["url"]]
    if actions:
        client.put_actions(actions)
        summary["positions_sent"] = len(actions)
    store.progress_pushed(rows)  # rows with no audio URL can't be synced at all

    remote, timestamp = client.get_actions(store.get_state(ACTIONS_SINCE, 0))
    for action in remote:
        kind = str(action.get("action") or "").lower()
        if kind not in ("play", "new") or not action.get("podcast") or not action.get("episode"):
            continue
        position = int(action.get("position") or 0) if kind == "play" else 0
        total = max(0, int(action.get("total") or 0))  # some clients send -1 for unknown
        when = from_iso(action.get("timestamp"))
        if when and store.apply_remote(action["podcast"], action["episode"], action.get("guid") or "",
                                       position, total, when):
            summary["positions_received"] += 1
    store.set_state(ACTIONS_SINCE, timestamp)
    log(f"Sync: {summary}")
    return summary
