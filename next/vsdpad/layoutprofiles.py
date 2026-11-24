"""Profiles: whole sets of pages (e.g. Work, Gaming, Streaming) you switch between with a key, from your phone,
or on a schedule.

The profile in use lives in the normal places (`layouts`, `profiles` (app rules) and `theme`), so the rest of the app
doesn't need to know about profiles. The others are kept in `layout_profiles`:

  "layout_profiles": {
    "active": "work",
    "list": [{"id": "work", "name": "Work"},                                # the one in use: no "saved"
             {"id": "game", "name": "Gaming",
              "saved": {"layouts": [...], "profiles": {...}, "theme": "neon",
                        "device_layouts": {"m18": "main"}}}],              # which pages each pad showed
    "schedule": [{"profile": "game", "at": "18:00", "days": [0, 1, 2, 3, 4]}]   # 0 = Monday
  }

Brightness, dimming, day/night, plugin settings and pads stay the same in every profile.
"""
import copy, datetime as dt, threading

from .log import LOG

MAX_PROFILES = 12
MAX_SCHEDULE = 40
SCHEDULE_CHECK_S = 15
SAVED_KEYS = ("layouts", "profiles", "theme")


class ProfileError(ValueError):
    pass


def _need(cond, msg):
    if not cond:
        from .schema import ConfigError
        raise ConfigError(msg)


# ---- validation (called from schema.validate) -----------------------------------------------------------------
def validate(cfg):
    lp = cfg.get("layout_profiles")
    if lp is None:
        return
    from .schema import HHMM_RE, ID_RE, validate as validate_config
    _need(isinstance(lp, dict) and isinstance(lp.get("list"), list) and 1 <= len(lp["list"]) <= MAX_PROFILES,
          f"profiles: there must be 1-{MAX_PROFILES}")
    ids = set()
    for i, p in enumerate(lp["list"], 1):
        _need(isinstance(p, dict) and isinstance(p.get("id"), str) and ID_RE.match(p["id"]) and p["id"] not in ids,
              f"profile {i} needs a unique id")
        ids.add(p["id"])
        _need(isinstance(p.get("name"), str) and 1 <= len(p["name"].strip()) <= 24, f"profile {i} needs a name (up to 24 characters)")
    _need(lp.get("active") in ids, "profiles: the one in use doesn't exist")
    for p in lp["list"]:
        saved = p.get("saved")
        if p["id"] == lp["active"]:
            _need(saved is None, f"profile {p['name']}: it's in use, so it can't have saved pages too")
            continue
        _need(isinstance(saved, dict), f"profile {p['name']} has no pages")
        dl = saved.get("device_layouts", {})
        _need(isinstance(dl, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in dl.items()),
              f"profile {p['name']}: bad pad list")
        sub = _as_config(cfg, saved)
        try:
            validate_config(sub)
        except ValueError as e:
            _need(False, f"profile {p['name']}: {e}")
    sched = lp.get("schedule", [])
    _need(isinstance(sched, list) and len(sched) <= MAX_SCHEDULE, f"profiles: up to {MAX_SCHEDULE} schedule entries")
    for i, s in enumerate(sched, 1):
        _need(isinstance(s, dict) and s.get("profile") in ids, f"schedule entry {i}: pick a profile")
        _need(isinstance(s.get("at"), str) and HHMM_RE.match(s["at"]), f"schedule entry {i}: the time must look like 18:00")
        days = s.get("days", list(range(7)))
        _need(isinstance(days, list) and days and all(isinstance(d, int) and not isinstance(d, bool) and 0 <= d <= 6 for d in days),
              f"schedule entry {i}: pick at least one day")


def _as_config(cfg, saved):
    """A stand-alone config made of the shared settings plus a saved profile's pages (for validating it)."""
    sub = {k: v for k, v in cfg.items() if k not in ("layout_profiles", "devices") + SAVED_KEYS}
    sub["layouts"] = saved.get("layouts")
    sub["profiles"] = saved.get("profiles", {"enabled": True, "rules": []})
    if saved.get("theme"):
        sub["theme"] = saved["theme"]
    sub["devices"] = {}
    return sub


# ---- reading ---------------------------------------------------------------------------------------------------
def summary(cfg):
    """[{id, name, active}] in order; one implicit "Main" profile when none were made."""
    lp = cfg.get("layout_profiles")
    if not lp:
        return [{"id": "main", "name": "Main", "active": True}]
    return [{"id": p["id"], "name": p["name"], "active": p["id"] == lp["active"]} for p in lp["list"]]


def active(cfg):
    return next(p for p in summary(cfg) if p["active"])


def find(cfg, ref):
    """A profile by id or (case-insensitive) name."""
    ref = str(ref or "").strip()
    for p in summary(cfg):
        if p["id"] == ref or p["name"].strip().lower() == ref.lower():
            return p
    raise ProfileError(f"There's no profile called {ref!r}." if ref else "Pick a profile.")


# ---- changing (each takes the config and changes it in place; use with store.update) -------------------------
def ensure(cfg):
    lp = cfg.get("layout_profiles")
    if not lp:
        lp = cfg["layout_profiles"] = {"active": "main", "list": [{"id": "main", "name": "Main"}], "schedule": []}
    lp.setdefault("schedule", [])
    return lp


def _snapshot(cfg):
    out = {k: copy.deepcopy(cfg[k]) for k in SAVED_KEYS if k in cfg}
    out["device_layouts"] = {did: d.get("layout") for did, d in cfg.get("devices", {}).items() if d.get("layout")}
    return out


def _new_id(lp, name):
    from .schema import ID_RE
    base = "".join(c for c in name.lower() if c.isalnum())[:12] or "profile"
    if len(base) < 2:
        base += "p"
    ids, n, pid = {p["id"] for p in lp["list"]}, 2, base
    while pid in ids or not ID_RE.match(pid):
        pid = f"{base[:12]}{n}"
        n += 1
    return pid


def _unique_name(lp, name):
    name = (name or "").strip()[:24] or "Profile"
    names, n, out = {p["name"].lower() for p in lp["list"]}, 2, name
    while out.lower() in names:
        out = f"{name[:20]} ({n})"
        n += 1
    return out


def _blank_like(cfg):
    """Empty pages in the same pad sizes (same layout ids, so every pad keeps its place)."""
    from .migrate import new_id
    pids, lays = set(), []
    for lay in cfg["layouts"]:
        pid = new_id(pids)
        pids.add(pid)
        lays.append({"id": lay["id"], "name": lay["name"], "cols": lay["cols"], "rows": lay["rows"], "buttons": {},
                     "pages": [{"id": pid, "name": "Main", "folder": False, "keys": {}}]})
    return {"layouts": lays, "profiles": {"enabled": True, "rules": []}}


def add(cfg, name, start="copy", saved=None):
    """Add a profile. start: "copy" (of the one in use), "empty", or "data" with saved = {layouts, profiles, theme}.
    Returns its id. The new profile isn't switched to."""
    lp = ensure(cfg)
    if len(lp["list"]) >= MAX_PROFILES:
        raise ProfileError(f"You can have up to {MAX_PROFILES} profiles.")
    if start == "data":
        if not isinstance(saved, dict) or not isinstance(saved.get("layouts"), list):
            raise ProfileError("That profile has no pages.")
        data = {k: copy.deepcopy(saved[k]) for k in SAVED_KEYS if k in saved}
        data["device_layouts"] = {}
    elif start == "empty":
        data = _blank_like(cfg)
        data["device_layouts"] = {}
    else:
        data = _snapshot(cfg)
    entry = {"id": _new_id(lp, name or "Profile"), "name": _unique_name(lp, name), "saved": data}
    lp["list"].append(entry)
    return entry["id"]


def switch(cfg, ref):
    """Make another profile the one in use. Returns its name. Does nothing if it already is."""
    lp = ensure(cfg)
    target = next((p for p in lp["list"] if p["id"] == find(cfg, ref)["id"]), None)
    if target["id"] == lp["active"]:
        return target["name"]
    current = next(p for p in lp["list"] if p["id"] == lp["active"])
    old_sizes = {lay["id"]: (lay["cols"], lay["rows"]) for lay in cfg["layouts"]}
    current["saved"] = _snapshot(cfg)
    saved = target.pop("saved")
    for k in SAVED_KEYS:
        if k in saved:
            cfg[k] = saved[k]
        elif k == "profiles":
            cfg[k] = {"enabled": True, "rules": []}
    _place_devices(cfg, saved.get("device_layouts", {}), old_sizes)
    lp["active"] = target["id"]
    return target["name"]


def _place_devices(cfg, remembered, old_sizes):
    """Point every pad at pages in the new profile: where it was last time, else the same layout id, else any layout of
    the same size, else a copy of the profile's main pages fitted to its size."""
    lays = cfg["layouts"]
    for did, dev in cfg.get("devices", {}).items():
        ids = {lay["id"] for lay in lays}
        want = remembered.get(did)
        if want in ids:
            dev["layout"] = want
            continue
        if dev.get("layout") in ids:
            continue
        size = old_sizes.get(dev.get("layout"))
        same = next((lay for lay in lays if size and (lay["cols"], lay["rows"]) == size), None)
        if same is None and size and len(lays) < 8:
            from .fitlayout import starter_layout
            from .migrate import new_id
            same = starter_layout(lays, new_id(ids), dev.get("name") or "Pad", size[0], size[1])
            lays.append(same)
        dev["layout"] = (same or lays[0])["id"]


def step(cfg, delta):
    """Switch to the next (delta=1) or previous (-1) profile. Returns its name."""
    items = summary(cfg)
    i = next(n for n, p in enumerate(items) if p["active"])
    return switch(cfg, items[(i + delta) % len(items)]["id"])


def rename(cfg, ref, name):
    lp = ensure(cfg)
    pid = find(cfg, ref)["id"]
    name = (name or "").strip()[:24]
    if not name:
        raise ProfileError("Give the profile a name.")
    for p in lp["list"]:
        if p["id"] != pid and p["name"].lower() == name.lower():
            raise ProfileError(f"There's already a profile called {name}.")
    next(p for p in lp["list"] if p["id"] == pid)["name"] = name


def delete(cfg, ref):
    lp = ensure(cfg)
    pid = find(cfg, ref)["id"]
    if pid == lp["active"]:
        raise ProfileError("That profile is in use: switch to another one first.")
    lp["list"] = [p for p in lp["list"] if p["id"] != pid]
    lp["schedule"] = [s for s in lp.get("schedule", []) if s.get("profile") != pid]


def saved_data(cfg, ref=None):
    """{layouts, profiles, theme} of a profile (the one in use when ref is None)."""
    lp = cfg.get("layout_profiles")
    pid = find(cfg, ref)["id"] if ref else active(cfg)["id"]
    if not lp or pid == lp["active"]:
        return {k: copy.deepcopy(cfg[k]) for k in SAVED_KEYS if k in cfg}
    saved = next(p for p in lp["list"] if p["id"] == pid)["saved"]
    return {k: copy.deepcopy(saved[k]) for k in SAVED_KEYS if k in saved}


# ---- doing it in the running app ----------------------------------------------------------------------------
def switch_now(app, ref):
    """Switch profile in the running app (from a key, the phone, the schedule or the settings page)."""
    box = {}

    def apply(cfg):
        before = active(cfg)["id"]
        box["name"] = switch(cfg, ref)
        box["changed"] = active(cfg)["id"] != before
    app.store.update(apply, snapshot=False)
    if box.get("changed"):
        LOG.info("profile: switched to %s", box["name"])
    return box["name"]


def step_now(app, delta):
    box = {}
    app.store.update(lambda cfg: box.setdefault("name", step(cfg, delta)), snapshot=False)
    LOG.info("profile: switched to %s", box["name"])
    return box["name"]


def due(schedule, last, now):
    """The schedule entry whose time passed in (last, now], latest first; None if none did."""
    hits = []
    day = last.date()
    while day <= now.date():
        for i, s in enumerate(schedule):
            try:
                hh, mm = (int(x) for x in s["at"].split(":"))
            except (KeyError, ValueError):
                continue
            if day.weekday() not in s.get("days", range(7)):
                continue
            t = dt.datetime.combine(day, dt.time(hh, mm), tzinfo=now.tzinfo)
            if last < t <= now:
                hits.append((t, i, s))
        day += dt.timedelta(days=1)
    return max(hits, key=lambda h: (h[0], h[1]))[2] if hits else None


class Scheduler:
    """Switches profile when a scheduled time comes. Only at that moment: switching by hand in between is left alone.
    If the PC was asleep at the time, it catches up when it wakes (within a day)."""

    def __init__(self, app, clock=None):
        self.app = app
        self.clock = clock or (lambda: dt.datetime.now().astimezone())
        self.last = self.clock()
        self._stop = threading.Event()

    def start(self):
        threading.Thread(target=self._loop, name="profile-schedule", daemon=True).start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.wait(SCHEDULE_CHECK_S):
            try:
                self.tick()
            except Exception:
                LOG.exception("profile schedule")

    def tick(self):
        now = self.clock()
        last, self.last = self.last, now
        if now <= last or now - last > dt.timedelta(days=1):
            return None   # clock went back, or the PC was off for ages: don't jump around
        cfg = self.app.store.get()
        lp = cfg.get("layout_profiles") or {}
        entry = due(lp.get("schedule") or [], last, now)
        if not entry:
            return None
        try:
            return switch_now(self.app, entry["profile"])
        except ValueError as e:
            LOG.warning("profile schedule: %s", e)
            return None


def settings_api(app, body):
    """The settings page's profile buttons: {"do": "switch" | "add" | "rename" | "delete" | "move", ...}."""
    do = body.get("do")
    if do == "switch":
        return {"name": switch_now(app, body.get("profile"))}
    box = {}

    def apply(cfg):
        if do == "add":
            box["id"] = add(cfg, str(body.get("name") or ""), "empty" if body.get("start") == "empty" else "copy")
            if body.get("use_now"):
                switch(cfg, box["id"])
        elif do == "rename":
            rename(cfg, body.get("profile"), body.get("name"))
        elif do == "delete":
            delete(cfg, body.get("profile"))
        elif do == "move":
            lp = ensure(cfg)
            pid = find(cfg, body.get("profile"))["id"]
            i = next(n for n, p in enumerate(lp["list"]) if p["id"] == pid)
            j = max(0, min(len(lp["list"]) - 1, i + (1 if body.get("by", 1) > 0 else -1)))
            lp["list"].insert(j, lp["list"].pop(i))
        else:
            raise ProfileError("unknown profile action")
    app.store.update(apply)
    return box
