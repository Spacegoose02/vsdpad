"""Bring any older vsdpad config (v1-v4, from the 1.x app) up to v5. Never modifies the input."""
import copy, secrets

from .schema import CONFIG_VERSION


def new_id(existing=(), prefix="p"):
    while True:
        pid = prefix + secrets.token_hex(3)
        if pid not in existing:
            return pid


def _v1_action(a):
    if not isinstance(a, dict) or "plugin" in a:
        return a
    t = a.get("type")
    if t == "media":
        return {"plugin": "system", "action": "media", "params": {"key": a.get("key", "")}}
    if t == "hotkey":
        keys = a.get("keys", [])
        return {"plugin": "system", "action": "hotkey", "params": {"keys": "+".join(keys) if isinstance(keys, list) else str(keys)}}
    if t == "launch":
        return {"plugin": "system", "action": "launch", "params": {"cmd": a.get("cmd", "")}}
    if t == "url":
        return {"plugin": "system", "action": "url", "params": {"url": a.get("url", "")}}
    if t == "http":
        return {"plugin": "system", "action": "webhook",
                "params": {"url": a.get("url", ""), "method": a.get("method", "POST"), "body": a.get("body", "")}}
    return a


def _to_v4(cfg):
    ver = cfg.get("version", 1)
    if ver < 2:
        for spec in cfg.get("keys", {}).values():
            if "action" in spec:
                spec["action"] = _v1_action(spec["action"])
        cfg.setdefault("icon_pack", "modern")
        cfg.setdefault("plugins", {})
    if ver < 3:
        keys = cfg.pop("keys", {})
        cfg["pages"] = [{"name": "Main", "keys": {k: v for k, v in keys.items() if k.isdigit() and 1 <= int(k) <= 15}}]
        cfg["round"] = {k: v for k, v in keys.items() if k.isdigit() and int(k) > 15}
        cfg.setdefault("round_buttons", [37, 48, 49])
    if ver < 4:
        seen = set()
        for pg in cfg["pages"]:
            pg["id"] = new_id(seen)
            seen.add(pg["id"])
            pg.setdefault("folder", False)
    return cfg


def _key_v5(spec):
    """1.x key {icon, label, color, repeat, action} -> v5 key with a tap list."""
    if not isinstance(spec, dict):
        return {}
    out = {k: spec[k] for k in ("icon", "label", "color") if spec.get(k) not in (None, "")}
    if isinstance(out.get("label"), str):
        out["label"] = out["label"][:32]
    if isinstance(out.get("icon"), str):
        out["icon"] = out["icon"][:8]
    if spec.get("repeat"):
        out["repeat"] = True
    a = spec.get("action")
    if isinstance(a, dict) and a.get("plugin"):
        out["tap"] = [{"plugin": a["plugin"], "action": a.get("action", ""), "params": a.get("params", {}) or {}}]
    return out


def migrate(cfg):
    """Return (v5 config, changed, from_version)."""
    cfg = copy.deepcopy(cfg)
    ver = cfg.get("version", 1)
    if ver >= CONFIG_VERSION:
        return cfg, False, ver
    cfg = _to_v4(cfg)
    pages = []
    for pg in cfg.get("pages", []):
        pages.append({"id": pg["id"], "name": str(pg.get("name", "Page"))[:24], "folder": bool(pg.get("folder")),
                      "keys": {k: _key_v5(v) for k, v in pg.get("keys", {}).items()}})
    if pages:
        pages[0]["folder"] = False
    rb = cfg.get("round_buttons", [37, 48, 49])
    out = {
        "version": CONFIG_VERSION,
        "theme": cfg.get("icon_pack", "modern"),
        "brightness": cfg.get("brightness", 80),
        "idle": {"dim_after_min": 0, "dim_to": 10},
        "layouts": [{"id": "main", "name": "Main", "cols": 5, "rows": 3, "pages": pages or [
            {"id": "pmain01", "name": "Main", "folder": False, "keys": {}}],
            "buttons": {k: _key_v5(v) for k, v in cfg.get("round", {}).items()}}],
        "devices": {"m18": {"name": "VSD M18", "layout": "main", "rotation": cfg.get("rotation", 0),
                            "row_offset": list(cfg.get("row_offset", [0, 0, 0])), "button_order": list(rb)}},
        "profiles": {"enabled": True, "rules": []},
        "plugins": cfg.get("plugins", {}),
    }
    return out, True, ver


def default_config():
    return {
        "version": CONFIG_VERSION, "theme": "modern", "brightness": 80, "idle": {"dim_after_min": 0, "dim_to": 10},
        "layouts": [{"id": "main", "name": "Main", "cols": 5, "rows": 3, "buttons": {
            "37": {"label": "Prev", "tap": [{"plugin": "pages", "action": "previous", "params": {}}]},
            "49": {"label": "Next", "tap": [{"plugin": "pages", "action": "next", "params": {}}]}},
            "pages": [{"id": "pmain01", "name": "Main", "folder": False, "keys": {
                "1": {"icon": "◐", "label": "Vol -", "color": [90, 160, 255], "repeat": True,
                      "tap": [{"plugin": "system", "action": "media", "params": {"key": "vol_down"}}]},
                "2": {"icon": "◉", "label": "Vol +", "color": [90, 160, 255], "repeat": True,
                      "tap": [{"plugin": "system", "action": "media", "params": {"key": "vol_up"}}]},
                "3": {"icon": "✕", "label": "Mute", "color": [255, 110, 110],
                      "tap": [{"plugin": "system", "action": "media", "params": {"key": "mute"}}]},
                "4": {"icon": "▶", "label": "Play", "color": [90, 220, 140],
                      "tap": [{"plugin": "system", "action": "media", "params": {"key": "play"}}]},
                "5": {"label": "Time", "color": [255, 200, 90], "live": {"plugin": "clock", "widget": "time", "params": {}}},
            }}]}],
        "devices": {"m18": {"name": "VSD M18", "layout": "main", "rotation": 0, "row_offset": [0, 0, 0],
                            "button_order": [37, 48, 49]}},
        "profiles": {"enabled": True, "rules": []},
        "plugins": {},
    }
