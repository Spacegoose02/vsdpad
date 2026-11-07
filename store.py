"""Thread-safe config store with migration (v1 -> v2 -> v3 -> v4) and validation. See HANDOFF.md section 5.3.

v4 layout: "pages": [{"id": str, "name": str, "folder": bool, "keys": {"1".."15": spec}}],
"round": {"<pad id>": spec} (global, shared by all pages so Previous/Next page works everywhere),
"round_buttons": [id, id, id].
A *folder* is a page that Next/Previous skip; a key opens it with pages.open and a Back key returns.
Page ids are stable (names and positions can change), so "open this folder" keeps working after reordering.
"""
import copy, json, os, re, secrets, shutil, threading

CONFIG_VERSION = 4
MAX_PAGES = 24
PAGE_ID_RE = re.compile(r"^[a-z0-9]{3,16}$")


def new_page_id(existing=()):
    while True:
        pid = "p" + secrets.token_hex(3)
        if pid not in existing:
            return pid


def migrate_action(a):
    """Convert a v1 action ({"type": "media", ...}) to v2 ({"plugin", "action", "params"})."""
    if not isinstance(a, dict) or "plugin" in a:
        return a
    t = a.get("type")
    if t == "media":
        return {"plugin": "system", "action": "media", "params": {"key": a.get("key", "")}}
    if t == "hotkey":
        keys = a.get("keys", [])
        return {"plugin": "system", "action": "hotkey",
                "params": {"keys": "+".join(keys) if isinstance(keys, list) else str(keys)}}
    if t == "launch":
        return {"plugin": "system", "action": "launch", "params": {"cmd": a.get("cmd", "")}}
    if t == "url":
        return {"plugin": "system", "action": "url", "params": {"url": a.get("url", "")}}
    if t == "http":
        return {"plugin": "system", "action": "webhook",
                "params": {"url": a.get("url", ""), "method": a.get("method", "POST"),
                           "body": a.get("body", "")}}
    return a  # unknown shape: keep as is, it will be reported at run time


def migrate(cfg):
    """Return (new_cfg, changed, from_version)."""
    cfg = copy.deepcopy(cfg)
    ver = cfg.get("version", 1)
    if ver >= CONFIG_VERSION:
        return cfg, False, ver
    if ver < 2:
        for spec in cfg.get("keys", {}).values():
            if "action" in spec:
                spec["action"] = migrate_action(spec["action"])
        cfg.setdefault("icon_pack", "modern")
        cfg.setdefault("plugins", {})
    if ver < 3:  # one flat "keys" dict -> pages + global round buttons
        keys = cfg.pop("keys", {})
        cfg["pages"] = [{"name": "Main", "keys": {k: v for k, v in keys.items() if k.isdigit() and 1 <= int(k) <= 15}}]
        cfg["round"] = {k: v for k, v in keys.items() if k.isdigit() and int(k) > 15}
        cfg.setdefault("round_buttons", [37, 48, 49])
    if ver < 4:  # stable page ids + folder flag
        seen = set()
        for pg in cfg["pages"]:
            pg["id"] = new_page_id(seen)
            seen.add(pg["id"])
            pg.setdefault("folder", False)
    cfg["version"] = CONFIG_VERSION
    return cfg, True, ver


# ---- helpers used by the app and server --------------------------------------------------
def page_keys(cfg, page):
    pages = cfg["pages"]
    return pages[page % len(pages)]["keys"]


def view(cfg, page):
    """Config as the renderers expect it: cfg['keys'] = the 15 keys of one page."""
    return {**cfg, "keys": page_keys(cfg, page)}


def spec_for_press(cfg, page, press_id):
    """The key spec for a pad press: LCD ids 1-15 come from the page, round buttons are global."""
    if press_id > 15:
        return cfg.get("round", {}).get(str(press_id))
    return page_keys(cfg, page).get(str(press_id))


# ---- validation ------------------------------------------------------------------------
def _validate_spec(slot, spec):
    if not isinstance(spec, dict):
        raise ValueError(f"bad key entry {slot!r}")
    c = spec.get("color")
    if c is not None:
        if not (isinstance(c, list) and len(c) == 3 and all(isinstance(x, int) and 0 <= x <= 255 for x in c)):
            raise ValueError(f"key {slot}: color must be [r, g, b]")
    a = spec.get("action")
    if a is not None and not (isinstance(a, dict) and isinstance(a.get("plugin"), str)
                              and isinstance(a.get("action"), str)
                              and isinstance(a.get("params", {}), dict)):
        raise ValueError(f"key {slot}: action needs plugin, action and params")


def validate(cfg):
    """Raise ValueError if cfg is structurally unusable."""
    if not isinstance(cfg, dict):
        raise ValueError("config must be an object")
    pages = cfg.get("pages")
    if not (isinstance(pages, list) and 1 <= len(pages) <= MAX_PAGES):
        raise ValueError(f"config needs 1-{MAX_PAGES} pages")
    ids = set()
    for i, pg in enumerate(pages, 1):
        if not (isinstance(pg, dict) and isinstance(pg.get("name"), str) and len(pg["name"]) <= 24
                and isinstance(pg.get("keys"), dict)):
            raise ValueError(f"page {i} needs a name (up to 24 characters) and keys")
        if not (isinstance(pg.get("id"), str) and PAGE_ID_RE.match(pg["id"])) or pg["id"] in ids:
            raise ValueError(f"page {i} needs a unique id")
        ids.add(pg["id"])
        if not isinstance(pg.get("folder", False), bool):
            raise ValueError(f"page {i}: folder must be true or false")
        if i == 1 and pg.get("folder"):
            raise ValueError("the first page can't be a folder")
        for slot, spec in pg["keys"].items():
            if not (str(slot).isdigit() and 1 <= int(slot) <= 15):
                raise ValueError(f"page {i}: key {slot!r} must be 1-15")
            _validate_spec(slot, spec)
    rnd = cfg.get("round", {})
    if not isinstance(rnd, dict):
        raise ValueError("'round' must be an object")
    for pid, spec in rnd.items():
        if not (str(pid).isdigit() and int(pid) >= 16):
            raise ValueError(f"round button id {pid!r} must be 16 or more")
        _validate_spec(pid, spec)
    b = cfg.get("brightness", 80)
    if not isinstance(b, int) or not 0 <= b <= 100:
        raise ValueError("brightness must be an integer 0-100")
    if cfg.get("rotation", 0) not in (0, 90, 180, 270):
        raise ValueError("rotation must be 0, 90, 180 or 270")
    rb = cfg.get("round_buttons", [37, 48, 49])
    if not (isinstance(rb, list) and len(rb) == 3 and len(set(rb)) == 3
            and all(isinstance(v, int) and 16 <= v <= 255 for v in rb)):
        raise ValueError("round_buttons must be three different pad IDs (16-255)")
    ro = cfg.get("row_offset", [0, 0, 0])
    if not (isinstance(ro, list) and len(ro) == 3 and all(isinstance(v, int) and -10 <= v <= 10 for v in ro)):
        raise ValueError("row_offset must be three whole numbers from -10 to 10")
    pack = cfg.get("icon_pack", "modern")
    if not isinstance(pack, str) or not re.fullmatch(r"[a-z0-9_-]{1,32}", pack):
        raise ValueError("icon_pack must be a pack id like 'modern'")
    if not isinstance(cfg.get("plugins", {}), dict):
        raise ValueError("'plugins' must be an object")
    return cfg


class Store:
    def __init__(self, path):
        self.path = path
        self._lock = threading.RLock()
        self.version = 0  # bumped on every save; the pad loop redraws when it changes
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
        cfg, changed, from_ver = migrate(cfg)
        validate(cfg)
        self._cfg = cfg
        if changed:
            bak = path + (".bak" if from_ver < 2 else f".v{from_ver}.bak")
            if not os.path.exists(bak):
                shutil.copy2(path, bak)
            self._write()

    def get(self):
        with self._lock:
            return copy.deepcopy(self._cfg)

    def replace(self, cfg):
        """Validate and save a whole new config."""
        cfg = copy.deepcopy(cfg)
        validate(cfg)
        with self._lock:
            self._cfg = cfg
            self._write()
            self.version += 1

    def update(self, fn):
        """Apply fn(cfg) to a copy, validate, save."""
        with self._lock:
            cfg = copy.deepcopy(self._cfg)
        fn(cfg)
        self.replace(cfg)

    def plugin_settings(self, plugin_id):
        with self._lock:
            return copy.deepcopy(self._cfg.get("plugins", {}).get(plugin_id, {}))

    def _write(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._cfg, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self.path)
