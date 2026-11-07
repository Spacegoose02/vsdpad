"""Which page the pad is showing, plus folder navigation (shared by the pad loop, the Pages plugin and the server).

Pages are identified by stable ids. Folders are pages flagged `folder`: Next/Previous skip them, `open`
enters one (remembering where you came from) and `back` returns. Thread-safe; `version` bumps on every
change so the pad loop knows to redraw.
"""
import threading


class PageState:
    def __init__(self):
        self._lock = threading.Lock()
        self.ids = ["main"]
        self.names = ["Main"]
        self.folders = [False]
        self.current = 0       # 0-based index into the pages
        self.history = []      # indexes we opened folders from (a stack)
        self.version = 0

    @property
    def count(self):
        return len(self.ids)

    # ---- configuration ----------------------------------------------------------------
    def configure(self, pages):
        """Tell the state about the pages from the config. Keeps the current page if it still exists."""
        with self._lock:
            cur_id = self.ids[self.current] if self.current < len(self.ids) else None
            hist_ids = [self.ids[i] for i in self.history if i < len(self.ids)]
            self.ids = [p["id"] for p in pages] or ["main"]
            self.names = [p.get("name", "") for p in pages] or ["Main"]
            self.folders = [bool(p.get("folder")) for p in pages] or [False]
            if cur_id in self.ids:
                new_cur = self.ids.index(cur_id)
                new_hist = [self.ids.index(i) for i in hist_ids if i in self.ids]
            else:  # the page we were on was deleted: start over from the first page, no stale trail
                new_cur, new_hist = 0, []
            if new_cur != self.current or new_hist != self.history:
                self.version += 1
            self.current, self.history = new_cur, new_hist

    def set_count(self, n):
        """Back-compat for callers that only know a page count (tests, CLI)."""
        self.configure([{"id": f"p{i}", "name": f"Page {i + 1}"} for i in range(max(1, n))])

    # ---- navigation -------------------------------------------------------------------
    def _top_levels(self):
        return [i for i, f in enumerate(self.folders) if not f] or [0]

    def _set(self, index, history):
        if index != self.current or history != self.history:
            self.version += 1
        self.current, self.history = index, history
        return index

    def goto(self, index):
        """Jump to a 0-based page (any page, folders included). Clears the folder trail."""
        with self._lock:
            return self._set(index % self.count, [])

    def step(self, delta):
        """Next/previous top-level page (folders are skipped). From inside a folder, steps relative to
        the page the folder was opened from."""
        with self._lock:
            tops = self._top_levels()
            base = self.current
            if self.folders[base]:
                base = next((i for i in self.history if not self.folders[i]), tops[0])
            pos = tops.index(base) if base in tops else 0
            self.version += 1
            self.current, self.history = tops[(pos + delta) % len(tops)], []
            return self.current

    def open(self, page_id):
        """Open a folder or page by id, remembering where we came from."""
        with self._lock:
            if page_id not in self.ids:
                raise ValueError("That folder no longer exists - re-pick it on the key.")
            target = self.ids.index(page_id)
            return self._set(target, self.history + [self.current]) if target != self.current else target

    def back(self):
        """Return to where the last folder was opened from (or the first page if there is no trail)."""
        with self._lock:
            if self.history:
                return self._set(self.history[-1], self.history[:-1])
            return self._set(self._top_levels()[0], [])

    def depth(self):
        with self._lock:
            return len(self.history)


STATE = PageState()
