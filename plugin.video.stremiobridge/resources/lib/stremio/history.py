"""Recent search queries, most recent first, persisted as JSON."""

import json
import os

DEFAULT_LIMIT = 20


class SearchHistory:
    def __init__(self, path, limit=DEFAULT_LIMIT):
        self.path = path
        self.limit = limit
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            self._queries = [q for q in data if isinstance(q, str)][:limit]
        except (OSError, ValueError, TypeError):
            self._queries = []

    def all(self):
        return list(self._queries)

    def add(self, query):
        query = query.strip()
        if not query:
            return
        self._queries = [query] + [q for q in self._queries if q.lower() != query.lower()]
        del self._queries[self.limit:]
        self._save()

    def remove(self, query):
        self._queries = [q for q in self._queries if q != query]
        self._save()

    def clear(self):
        self._queries = []
        self._save()

    def _save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._queries, f, ensure_ascii=False)
        os.replace(tmp, self.path)
