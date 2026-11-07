"""Thread-safe config store: atomic saves, an undo history, encrypted secrets, export/import.

Files in the data folder:
  config.json              the live config (plugin secrets encrypted with DPAPI on Windows)
  history/<stamp>.json     the previous versions, newest last (keeps HISTORY_KEEP)
"""
import copy, json, os, threading, time

from . import secret
from .migrate import migrate
from .schema import ConfigError, validate

HISTORY_KEEP = 30


class Store:
    def __init__(self, data_dir, secret_fields=None):
        """secret_fields: {plugin_id: {setting keys}} that are secrets (encrypted on disk, hidden from the browser)."""
        self.dir = data_dir
        self.path = os.path.join(data_dir, "config.json")
        self.history_dir = os.path.join(data_dir, "history")
        self.secret_fields = secret_fields or {}
        self._lock = threading.RLock()
        self.version = 0
        self._listeners = []
        with open(self.path, encoding="utf-8") as f:
            raw = json.load(f)
        cfg, changed, _ = migrate(raw)
        cfg = self._decrypt(cfg)
        validate(cfg)
        self._cfg = cfg
        if changed:  # keep the old version in the history (with its secrets encrypted too)
            self._snapshot(self._encrypt(raw))
            self._write()

    # ---- secrets ------------------------------------------------------------------------
    def _map_secrets(self, cfg, fn):
        for pid, keys in self.secret_fields.items():
            settings = cfg.get("plugins", {}).get(pid)
            if isinstance(settings, dict):
                for k in keys:
                    if isinstance(settings.get(k), str) and settings[k]:
                        settings[k] = fn(settings[k])
        return cfg

    def _decrypt(self, cfg):
        return self._map_secrets(cfg, secret.unprotect)

    def _encrypt(self, cfg):
        return self._map_secrets(copy.deepcopy(cfg), secret.protect)

    def strip_secrets(self, cfg):
        """A copy of cfg with secret settings removed. Returns (cfg, {plugin_id: [keys that were set]})."""
        cfg = copy.deepcopy(cfg)
        was_set = {}
        for pid, keys in self.secret_fields.items():
            settings = cfg.get("plugins", {}).get(pid)
            if isinstance(settings, dict):
                for k in keys:
                    if settings.pop(k, None):
                        was_set.setdefault(pid, []).append(k)
        return cfg, was_set

    # ---- read / write -------------------------------------------------------------------
    def get(self):
        with self._lock:
            return copy.deepcopy(self._cfg)

    def on_change(self, fn):
        self._listeners.append(fn)

    def replace(self, cfg, snapshot=True):
        cfg = copy.deepcopy(cfg)
        validate(cfg)
        with self._lock:
            if snapshot:
                self._snapshot()
            self._cfg = cfg
            self._write()
            self.version += 1
        for fn in list(self._listeners):
            try:
                fn()
            except Exception:
                pass

    def update(self, fn, snapshot=True):
        """Apply fn(cfg) to a copy, validate and save. fn may return a value, which is passed back."""
        with self._lock:
            cfg = copy.deepcopy(self._cfg)
            result = fn(cfg)
            self.replace(cfg, snapshot)
            return result

    def plugin_settings(self, plugin_id):
        with self._lock:
            return copy.deepcopy(self._cfg.get("plugins", {}).get(plugin_id, {}))

    def _write(self, snapshot=False):
        if snapshot:
            self._snapshot()
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._encrypt(self._cfg), f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)

    # ---- history (undo) -----------------------------------------------------------------
    def _snapshot(self, data=None):
        """Copy the config file as it is on disk (or `data`) into history/ before it changes."""
        if data is None and not os.path.exists(self.path):
            return
        os.makedirs(self.history_dir, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
        dest = os.path.join(self.history_dir, stamp + ".json")
        n = 1
        while os.path.exists(dest):
            dest = os.path.join(self.history_dir, f"{stamp}-{n}.json")
            n += 1
        if data is not None:
            with open(dest, "w", encoding="utf-8") as out:
                json.dump(data, out, indent=2, ensure_ascii=False)
        else:
            with open(self.path, "rb") as src, open(dest, "wb") as out:
                out.write(src.read())
        for old in self.history()[:-HISTORY_KEEP]:
            try:
                os.remove(os.path.join(self.history_dir, old["id"]))
            except OSError:
                pass

    def history(self):
        """Saved versions, oldest first: [{id, time}]."""
        try:
            names = sorted(n for n in os.listdir(self.history_dir) if n.endswith(".json"))
        except OSError:
            return []
        out = []
        for n in names:
            try:
                out.append({"id": n, "time": os.path.getmtime(os.path.join(self.history_dir, n))})
            except OSError:
                pass
        return out

    def restore(self, history_id):
        """Bring back a saved version (the current one is kept in the history too)."""
        if os.path.basename(history_id) != history_id or not history_id.endswith(".json"):
            raise ConfigError("unknown saved version")
        path = os.path.join(self.history_dir, history_id)
        if not os.path.exists(path):
            raise ConfigError("that saved version no longer exists")
        with open(path, encoding="utf-8") as f:
            cfg = self._decrypt(migrate(json.load(f))[0])
        self.replace(cfg)

    def undo(self):
        h = self.history()
        if not h:
            raise ConfigError("nothing to undo")
        self.restore(h[-1]["id"])
        # the restore snapshotted the state we left; drop both so repeated undo walks further back
        for item in self.history()[-2:]:
            try:
                os.remove(os.path.join(self.history_dir, item["id"]))
            except OSError:
                pass

    # ---- export / import ----------------------------------------------------------------
    def export(self, include_secrets=False):
        cfg = self.get()
        if not include_secrets:
            cfg, _ = self.strip_secrets(cfg)
        return {"vsdpad_export": 1, "exported": time.strftime("%Y-%m-%d %H:%M"), "config": cfg}

    def import_(self, data):
        """Replace the config with an exported file (or a raw config of any version). Keeps saved secrets
        when the file has none, so a shared layout doesn't wipe your tokens."""
        if not isinstance(data, dict):
            raise ConfigError("that file is not a vsdpad layout")
        cfg = data.get("config", data) if "vsdpad_export" in data else data
        if not isinstance(cfg, dict) or not (cfg.get("layouts") or cfg.get("pages") or cfg.get("keys")):
            raise ConfigError("that file is not a vsdpad layout")
        cfg, _, _ = migrate(cfg)
        cfg = self._decrypt(cfg)
        mine = self.get().get("plugins", {})
        for pid, keys in self.secret_fields.items():
            for k in keys:
                if mine.get(pid, {}).get(k) and not cfg.get("plugins", {}).get(pid, {}).get(k):
                    cfg.setdefault("plugins", {}).setdefault(pid, {})[k] = mine[pid][k]
        validate(cfg)
        self.replace(cfg)
