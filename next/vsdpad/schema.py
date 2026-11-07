"""Config format v5 and validation.

{
  "version": 5,
  "theme": "modern",                       # theme id (themes/<id>/theme.json, or the built-in "modern")
  "brightness": 80,
  "idle": {"dim_after_min": 0, "dim_to": 10},
  "layouts": [                              # a set of pages for one grid size; devices pick a layout
    {"id": "main", "name": "Main", "cols": 5, "rows": 3,
     "pages": [{"id": "pmain01", "name": "Main", "folder": false, "keys": {"1": KEY, ...}}],
     "buttons": {"37": KEY}}                # plain buttons (no screen), the same on every page
  ],
  "devices": {"m18": {"name": "VSD M18", "layout": "main", "rotation": 0, "row_offset": [0, 0, 8],
                      "button_order": [37, 48, 49]}},
  "profiles": {"enabled": true, "rules": [{"app": "obs64.exe", "title": "", "page": "<page id>", "revert": true}]},
  "plugins": {"<plugin id>": {...settings}}
}
KEY  = {"icon": glyph | "<pack>:<icon>" | "theme:<icon>", "label", "color": [r, g, b], "image": "img:<file>" | "steam:<appid>",
        "live": {"plugin", "widget", "params"}, "repeat": bool,
        "tap": [STEP], "hold": [STEP], "double": [STEP]}
STEP = {"plugin", "action", "params"} | {"wait": seconds}
"""
import re

CONFIG_VERSION = 5
MAX_PAGES = 30
MAX_STEPS = 20
ID_RE = re.compile(r"^[a-z0-9]{2,16}$")
DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
THEME_RE = re.compile(r"^[a-z0-9_-]{1,32}$")
ICON_REF_RE = re.compile(r"^[a-z0-9_-]{1,32}:[a-z0-9][a-z0-9_.-]{0,63}$")
IMAGE_RE = re.compile(r"^[a-z]{2,10}:[A-Za-z0-9_.-]{1,96}$")
GESTURES = ("tap", "hold", "double")


class ConfigError(ValueError):
    pass


def _need(cond, msg):
    if not cond:
        raise ConfigError(msg)


def _is_int(v, lo, hi):
    return isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi


def validate_step(step, where):
    _need(isinstance(step, dict), f"{where}: each step must be an object")
    if "wait" in step:
        w = step["wait"]
        _need(isinstance(w, (int, float)) and not isinstance(w, bool) and 0 <= w <= 60,
              f"{where}: a wait must be 0-60 seconds")
        return
    _need(isinstance(step.get("plugin"), str) and isinstance(step.get("action"), str)
          and isinstance(step.get("params", {}), dict), f"{where}: a step needs plugin, action and params")


def validate_key(key, where):
    _need(isinstance(key, dict), f"{where}: bad key entry")
    for f, n in (("icon", 8), ("label", 32)):
        if f == "icon" and isinstance(key.get(f), str) and ICON_REF_RE.match(key[f]):
            continue  # an icon from a pack, e.g. "outline:house" or "theme:house"
        _need(key.get(f) is None or (isinstance(key[f], str) and len(key[f]) <= n), f"{where}: {f} is too long (max {n})")
    c = key.get("color")
    _need(c is None or (isinstance(c, list) and len(c) == 3 and all(_is_int(x, 0, 255) for x in c)),
          f"{where}: color must be [r, g, b]")
    img = key.get("image")
    _need(img is None or (isinstance(img, str) and IMAGE_RE.match(img)), f"{where}: bad image reference")
    live = key.get("live")
    _need(live is None or (isinstance(live, dict) and isinstance(live.get("plugin"), str)
                           and isinstance(live.get("widget"), str) and isinstance(live.get("params", {}), dict)),
          f"{where}: live needs plugin, widget and params")
    _need(isinstance(key.get("repeat", False), bool), f"{where}: repeat must be true or false")
    for g in GESTURES:
        steps = key.get(g)
        if steps is None:
            continue
        _need(isinstance(steps, list) and len(steps) <= MAX_STEPS, f"{where}: {g} must be a list of up to {MAX_STEPS} steps")
        for i, s in enumerate(steps, 1):
            validate_step(s, f"{where} {g} step {i}")


def validate(cfg):
    """Raise ConfigError if cfg is unusable. Returns cfg."""
    _need(isinstance(cfg, dict), "config must be an object")
    _need(cfg.get("version") == CONFIG_VERSION, f"config version must be {CONFIG_VERSION}")
    _need(isinstance(cfg.get("theme", "modern"), str) and THEME_RE.match(cfg.get("theme", "modern")), "bad theme id")
    _need(_is_int(cfg.get("brightness", 80), 0, 100), "brightness must be a whole number 0-100")
    idle = cfg.get("idle", {})
    _need(isinstance(idle, dict) and _is_int(idle.get("dim_after_min", 0), 0, 240) and _is_int(idle.get("dim_to", 10), 0, 100),
          "idle: dim_after_min must be 0-240 minutes and dim_to 0-100")
    layouts = cfg.get("layouts")
    _need(isinstance(layouts, list) and 1 <= len(layouts) <= 8, "config needs 1-8 layouts")
    lids, pids = set(), set()
    for li, lay in enumerate(layouts, 1):
        _need(isinstance(lay, dict) and isinstance(lay.get("id"), str) and ID_RE.match(lay["id"]) and lay["id"] not in lids,
              f"layout {li} needs a unique id")
        lids.add(lay["id"])
        _need(isinstance(lay.get("name"), str) and len(lay["name"]) <= 32, f"layout {li} needs a name")
        _need(_is_int(lay.get("cols"), 1, 8) and _is_int(lay.get("rows"), 1, 4), f"layout {li}: cols 1-8, rows 1-4")
        nkeys = lay["cols"] * lay["rows"]
        pages = lay.get("pages")
        _need(isinstance(pages, list) and 1 <= len(pages) <= MAX_PAGES, f"layout {lay['name']}: needs 1-{MAX_PAGES} pages")
        for i, pg in enumerate(pages, 1):
            where = f"{lay['name']} page {i}"
            _need(isinstance(pg, dict) and isinstance(pg.get("name"), str) and len(pg["name"]) <= 24
                  and isinstance(pg.get("keys"), dict), f"{where} needs a name (up to 24 characters) and keys")
            _need(isinstance(pg.get("id"), str) and ID_RE.match(pg["id"]) and pg["id"] not in pids, f"{where} needs a unique id")
            pids.add(pg["id"])
            _need(isinstance(pg.get("folder", False), bool), f"{where}: folder must be true or false")
            _need(not (i == 1 and pg.get("folder")), f"{lay['name']}: the first page can't be a folder")
            for slot, key in pg["keys"].items():
                _need(str(slot).isdigit() and 1 <= int(slot) <= nkeys, f"{where}: key {slot!r} must be 1-{nkeys}")
                validate_key(key, f"{where} key {slot}")
        buttons = lay.get("buttons", {})
        _need(isinstance(buttons, dict), f"layout {lay['name']}: buttons must be an object")
        for bid, key in buttons.items():
            _need(str(bid).isdigit() and nkeys < int(bid) <= 255, f"{lay['name']}: button id {bid!r} must be {nkeys + 1}-255")
            validate_key(key, f"{lay['name']} button {bid}")
    devices = cfg.get("devices", {})
    _need(isinstance(devices, dict), "'devices' must be an object")
    for did, dev in devices.items():
        _need(DEVICE_ID_RE.match(did) and isinstance(dev, dict), f"bad device entry {did!r}")
        _need(dev.get("layout") in lids, f"device {dev.get('name', did)}: unknown layout {dev.get('layout')!r}")
        _need(dev.get("rotation", 0) in (0, 90, 180, 270), f"device {did}: rotation must be 0, 90, 180 or 270")
        b = dev.get("brightness")
        _need(b is None or _is_int(b, 0, 100), f"device {did}: brightness must be 0-100 or empty")
        ro = dev.get("row_offset", [])
        _need(isinstance(ro, list) and len(ro) <= 4 and all(_is_int(v, -10, 10) for v in ro),
              f"device {did}: row offsets must be whole numbers from -10 to 10")
        bo = dev.get("button_order", [])
        _need(isinstance(bo, list) and len(bo) <= 8 and len(set(bo)) == len(bo) and all(_is_int(v, 1, 255) for v in bo),
              f"device {did}: button_order must be different button ids")
    prof = cfg.get("profiles", {"enabled": True, "rules": []})
    _need(isinstance(prof, dict) and isinstance(prof.get("enabled", True), bool) and isinstance(prof.get("rules", []), list)
          and len(prof.get("rules", [])) <= 50, "profiles must have enabled and up to 50 rules")
    for i, r in enumerate(prof.get("rules", []), 1):
        _need(isinstance(r, dict) and isinstance(r.get("app", ""), str) and isinstance(r.get("title", ""), str)
              and (r.get("app") or r.get("title")) and r.get("page") in pids and isinstance(r.get("revert", True), bool),
              f"app rule {i}: needs an app name or window title and an existing page")
    _need(isinstance(cfg.get("plugins", {}), dict), "'plugins' must be an object")
    return cfg


# ---- helpers ---------------------------------------------------------------------------
def layout(cfg, layout_id):
    for lay in cfg["layouts"]:
        if lay["id"] == layout_id:
            return lay
    return cfg["layouts"][0]


def all_keys(cfg):
    """Yield (layout, page_or_None, slot, key) for every key and button in the config."""
    for lay in cfg["layouts"]:
        for pg in lay["pages"]:
            for slot, key in pg["keys"].items():
                yield lay, pg, slot, key
        for bid, key in lay.get("buttons", {}).items():
            yield lay, None, bid, key


def page_layout(cfg, page_id):
    for lay in cfg["layouts"]:
        if any(p["id"] == page_id for p in lay["pages"]):
            return lay
    return None
