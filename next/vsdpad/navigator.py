"""Which page a device shows, plus folder navigation. One Navigator per device.

Folders are pages flagged `folder`: Next/Previous skip them, `open` enters one (remembering where you came from)
and `back` returns. Thread-safe; `version` bumps on every change so the device redraws.
"""
import threading


class Navigator:
    def __init__(self, pages=None):
        self._lock = threading.Lock()
        self.ids, self.names, self.folders = ["main"], ["Main"], [False]
        self.current = 0
        self.history = []
        self.version = 0
        self.manual_version = 0   # bumps only on navigation by the user (not by app rules)
        if pages:
            self.configure(pages)

    @property
    def count(self):
        return len(self.ids)

    @property
    def current_id(self):
        with self._lock:
            return self.ids[self.current]

    def configure(self, pages):
        with self._lock:
            cur_id = self.ids[self.current] if self.current < len(self.ids) else None
            hist_ids = [self.ids[i] for i in self.history if i < len(self.ids)]
            self.ids = [p["id"] for p in pages] or ["main"]
            self.names = [p.get("name", "") for p in pages] or ["Main"]
            self.folders = [bool(p.get("folder")) for p in pages] or [False]
            if cur_id in self.ids:
                cur, hist = self.ids.index(cur_id), [self.ids.index(i) for i in hist_ids if i in self.ids]
            else:
                cur, hist = 0, []
            self._set(cur, hist)

    def _tops(self):
        return [i for i, f in enumerate(self.folders) if not f] or [0]

    def _set(self, index, history, manual=False):
        if index != self.current or history != self.history:
            self.version += 1
            if manual:
                self.manual_version += 1
        self.current, self.history = index, history
        return index

    def goto(self, index, manual=True):
        with self._lock:
            return self._set(index % self.count, [], manual)

    def goto_id(self, page_id, manual=True):
        with self._lock:
            if page_id not in self.ids:
                raise ValueError("that page no longer exists")
            return self._set(self.ids.index(page_id), [], manual)

    def step(self, delta):
        with self._lock:
            tops, base = self._tops(), self.current
            if self.folders[base]:
                base = next((i for i in self.history if not self.folders[i]), tops[0])
            pos = tops.index(base) if base in tops else 0
            return self._set(tops[(pos + delta) % len(tops)], [], True)

    def open(self, page_id):
        with self._lock:
            if page_id not in self.ids:
                raise ValueError("That folder no longer exists - pick it again on the key.")
            target = self.ids.index(page_id)
            if target == self.current:
                return target
            return self._set(target, self.history + [self.current], True)

    def back(self):
        with self._lock:
            if self.history:
                return self._set(self.history[-1], self.history[:-1], True)
            return self._set(self._tops()[0], [], True)
