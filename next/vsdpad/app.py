"""The running app: finds devices, keeps each one drawn and responsive, runs actions, live keys and app rules."""
import collections, os, re, threading, time

from . import devices as devmod
from .actions import Context, Gestures
from .live import LiveEngine
from .log import LOG
from .navigator import Navigator
from .plugins import load_plugins
from .profiles import Profiles
from .render import Renderer, black
from .schema import ID_RE, layout as find_layout
from .store import Store

KEEPALIVE_S = 5
SCAN_S = 2
STATUS_GRACE_S = 5     # a quick reconnect doesn't show "disconnected" in the settings page
IMAGE_RE_LOCAL = re.compile(r"^[0-9a-f]{16,64}\.(png|jpg|jpeg|gif|webp)$")


class DeviceRunner(threading.Thread):
    """Keeps one device drawn and turns its presses into actions. Reconnects by itself."""

    def __init__(self, app, info, factory):
        super().__init__(name=f"device-{info.id}", daemon=True)
        self.app, self.info, self.factory = app, info, factory
        self.nav = Navigator()
        self.gestures = Gestures(app, info.id, self.nav, self.lookup)
        self.stop_event = threading.Event()
        self.connected = False
        self.disconnected_at = time.monotonic()
        self.sent = {}            # slot -> signature of what the device shows
        self.seen = None          # versions at the last draw
        self.cfg = None
        self.last_press, self.press_seq = None, 0
        self.last_input = time.monotonic()
        self.dimmed = False
        self.brightness = None
        self.dev = None
        self.ever_connected = False
        self._wake_swallow = None
        self.doing, self.connected_at, self.last_ping, self.last_draw = "opening", 0, 0, 0

    # ---- config views ---------------------------------------------------------------------
    def dcfg(self, cfg=None):
        cfg = cfg or self.cfg or self.app.store.get()
        return cfg["devices"].get(self.info.id) or {"layout": cfg["layouts"][0]["id"]}

    def layout(self, cfg=None):
        cfg = cfg or self.cfg or self.app.store.get()
        return find_layout(cfg, self.dcfg(cfg)["layout"])

    def page(self):
        lay = self.layout()
        return lay["pages"][min(self.nav.current, len(lay["pages"]) - 1)]

    def lookup(self, button):
        """The key spec for a press on this device: screen keys from the current page, plain buttons global."""
        lay = self.layout()
        if button <= self.info.key_count:
            return self.page()["keys"].get(str(button))
        return lay.get("buttons", {}).get(str(button))

    # ---- thread ---------------------------------------------------------------------------
    def run(self):
        backoff = 2
        while not self.stop_event.is_set():
            try:
                self.doing = "opening"
                self.dev = self.factory(self.info)
                self.dev.open()
                self.session()
                backoff = 2
            except Exception as e:
                if self.connected or backoff == 2:
                    now = time.monotonic()
                    ago = lambda t: f"{now - t:.0f}s" if t else "never"  # noqa: E731
                    LOG.warning("%s: %s: %s during %s (connected %s, last press %s ago, last keep-alive %s ago, "
                                "last key drawn %s ago) - retrying", self.info.model, type(e).__name__, e, self.doing,
                                ago(self.connected_at), ago(self.last_input), ago(self.last_ping), ago(self.last_draw))
            finally:
                self.gestures.cancel_all()  # never leave a repeat key running on a dead connection
                if self.connected:
                    self.disconnected_at = time.monotonic()
                self.connected = False
                if self.dev:
                    self.dev.close()
            if self.stop_event.wait(backoff):
                break
            backoff = min(10, backoff + 2)
            if not self.app.device_present(self.info):
                LOG.info("%s unplugged", self.info.model)
                break
        self.app.runner_finished(self)

    def stop(self):
        self.stop_event.set()

    def session(self):
        dev = self.dev
        self.doing = "first draw"
        if getattr(dev, "rotation", None) is not None:
            dev.rotation = self.dcfg(self.app.store.get()).get("rotation", 0)
        if not self.ever_connected:
            dev.clear()  # first connect after start: wipe the vendor/old images once
        self.sent, self.seen, self.brightness = {}, None, None
        self.connected, self.ever_connected = True, True
        self.last_input = time.monotonic()
        self.dimmed = False
        self.connected_at = time.monotonic()
        LOG.info("%s connected", self.info.model)
        self.draw()
        self.last_ping = time.monotonic()
        while not self.stop_event.is_set():
            self.doing = "reading presses"
            for button, pressed in dev.read_events():
                self.on_event(button, pressed)
            now = time.monotonic()
            if now - self.last_ping > KEEPALIVE_S:
                self.doing = "keep-alive"
                dev.keepalive()
                self.last_ping = now
            self.check_idle(now)
            self.doing = "drawing"
            self.draw()

    # ---- input ----------------------------------------------------------------------------
    def on_event(self, button, pressed):
        self.last_input = time.monotonic()
        if pressed:
            self.last_press, self.press_seq = button, self.press_seq + 1
            if self.dimmed:  # the first press only wakes the pad
                self.dimmed = False
                self._wake_swallow = button
                return
        elif self._wake_swallow == button:
            self._wake_swallow = None
            return
        if pressed:
            self.gestures.press(button)
        else:
            self.gestures.release(button)

    def check_idle(self, now):
        idle = (self.cfg or {}).get("idle", {})
        mins = idle.get("dim_after_min", 0)
        if mins and not self.dimmed and now - self.last_input > mins * 60 and not self.gestures.held_count():
            self.dimmed = True

    # ---- drawing --------------------------------------------------------------------------
    def draw(self, force=False):
        store, live = self.app.store, self.app.live
        versions = (store.version, self.nav.version, live.version, self.dimmed)
        if versions == self.seen and not force:
            return False
        if self.seen is None or versions[0] != self.seen[0] or self.cfg is None:
            self.cfg = store.get()
            self.app.ensure_device(self.info)
            self.cfg = store.get()
            self.nav.configure(self.layout()["pages"])
            versions = (store.version, self.nav.version, live.version, self.dimmed)
        self.seen = versions
        cfg, dcfg, lay = self.cfg, self.dcfg(), self.layout()
        bright = dcfg.get("brightness")
        bright = cfg.get("brightness", 80) if bright is None else bright
        if self.dimmed:
            bright = min(bright, cfg.get("idle", {}).get("dim_to", 10))
        if bright != self.brightness:
            self.dev.set_brightness(bright)
            self.brightness = bright
        if getattr(self.dev, "rotation", None) is not None:
            self.dev.rotation = dcfg.get("rotation", 0)
        page = self.page()
        offsets = dcfg.get("row_offset", [])
        changed = False
        for slot in range(1, self.info.key_count + 1):
            key = page["keys"].get(str(slot))
            row = (slot - 1) // self.info.cols
            dy = offsets[row] if row < len(offsets) else 0
            lv = self.app.live_content(key)
            sig, img = self.app.renderer.render(cfg.get("theme", "modern"), key, lv, slot, self.info.cols, self.info.rows, dy)
            sig = f"{sig}|{dcfg.get('rotation', 0)}"
            if self.sent.get(slot) != sig:
                self.dev.set_key(slot, img if img is not None else black())
                self.sent[slot] = sig
                changed = True
        if changed:
            self.dev.flush()
            self.last_draw = time.monotonic()
        return changed

    def status(self):
        lay = self.layout()
        shown = self.connected or (time.monotonic() - self.disconnected_at) < STATUS_GRACE_S
        return {"id": self.info.id, "model": self.info.model, "name": self.dcfg().get("name", self.info.model),
                "connected": self.connected, "status": "connected" if self.connected else ("reconnecting" if shown else "disconnected"),
                "cols": self.info.cols, "rows": self.info.rows, "buttons": self.info.buttons, "layout": lay["id"],
                "page": self.nav.current, "page_id": self.nav.current_id, "last_press": self.last_press,
                "press_seq": self.press_seq, "dimmed": self.dimmed}


class App:
    def __init__(self, data_dir, fake_devices=None, hid_module=None):
        self.data_dir = data_dir
        self.images_dir = os.path.join(data_dir, "images")
        os.makedirs(self.images_dir, exist_ok=True)
        self.plugins = load_plugins()
        secret_fields = {pid: p.secret_keys() for pid, p in self.plugins.items() if p.secret_keys()}
        self.store = Store(data_dir, secret_fields)
        self.renderer = Renderer(self.image_path)
        self.live = LiveEngine(self)
        self.profiles = Profiles(self)
        self.hid = hid_module
        self.fake = fake_devices          # list of FakeDevice to use instead of USB
        self._runners, self._lock = {}, threading.RLock()
        self._stop = threading.Event()
        self.notes = []
        self.errors = collections.deque(maxlen=20)
        self.preview_nav = {}             # layout id -> Navigator for page actions run from the settings page
        self._lost = {}                   # device id -> when it went missing (for the log)

    # ---- lifecycle ------------------------------------------------------------------------
    def start(self):
        self.live.start()
        self.profiles.start()
        threading.Thread(target=self._scan_loop, name="scan", daemon=True).start()

    def stop(self):
        self._stop.set()
        self.live.stop()
        self.profiles.stop()
        for r in self.runners():
            r.stop()
        for r in self.runners():
            r.join(timeout=2)

    def _scan_loop(self):
        while not self._stop.is_set():
            try:
                self.scan()
            except Exception:
                LOG.exception("device scan")
            self._stop.wait(SCAN_S)

    def scan(self):
        if self.fake is not None:
            infos = [d.info for d in self.fake]
            self.notes = []
        else:
            infos, self.notes = devmod.discover(self.hid)
        for info in infos:
            with self._lock:
                if info.id in self._runners:
                    continue
                r = DeviceRunner(self, info, self._factory)
                self._runners[info.id] = r
            lost = self._lost.pop(info.id, None)
            LOG.info("found %s%s", info.model, f" (back after {time.monotonic() - lost:.0f}s)" if lost else "")
            r.start()

    def _factory(self, info):
        if self.fake is not None:
            return next(d for d in self.fake if d.info.id == info.id)
        return devmod.open_device(info, self.hid)

    def device_present(self, info):
        if self.fake is not None:
            return any(d.info.id == info.id for d in self.fake)
        try:
            infos, _ = devmod.discover(self.hid)
        except Exception:
            return True
        return any(i.id == info.id for i in infos)

    def runner_finished(self, runner):
        with self._lock:
            if self._runners.get(runner.info.id) is runner:
                del self._runners[runner.info.id]
                self._lost[runner.info.id] = runner.disconnected_at

    def runners(self):
        with self._lock:
            return list(self._runners.values())

    def runners_snapshot(self):
        with self._lock:
            return list(self._runners.items())

    def runner(self, device_id):
        with self._lock:
            return self._runners.get(device_id)

    # ---- config helpers -------------------------------------------------------------------
    def ensure_device(self, info):
        """Give a newly seen device a config entry. A 15-key 5x3 deck shares the main pages; other sizes get their own."""
        cfg = self.store.get()
        if info.id in cfg["devices"]:
            return

        def add(cfg):
            if info.id in cfg["devices"]:
                return
            lay = next((l for l in cfg["layouts"] if l["cols"] == info.cols and l["rows"] == info.rows), None)
            if lay is None:
                existing = {l["id"] for l in cfg["layouts"]}
                lid = "".join(c for c in info.model.lower() if c.isalnum())[:12] or "deck"
                while lid in existing or not ID_RE.match(lid):
                    lid = (lid[:10] + os.urandom(2).hex())[:16]
                pids = {p["id"] for l in cfg["layouts"] for p in l["pages"]}
                from .migrate import new_id
                lay = {"id": lid, "name": info.model[:32], "cols": info.cols, "rows": info.rows, "buttons": {},
                       "pages": [{"id": new_id(pids), "name": "Main", "folder": False, "keys": {}}]}
                cfg["layouts"].append(lay)
            entry = {"name": info.model, "layout": lay["id"], "rotation": 0, "row_offset": [0] * info.rows}
            if info.buttons:
                entry["button_order"] = list(info.buttons)
            cfg["devices"][info.id] = entry
        self.store.update(add, snapshot=False)

    def image_path(self, ref):
        if not ref or ":" not in ref:
            return None
        scheme, rest = ref.split(":", 1)
        if scheme == "img":
            if not IMAGE_RE_LOCAL.match(rest):
                return None
            p = os.path.join(self.images_dir, rest)
            return p if os.path.exists(p) else None
        plugin = self.plugins.get(scheme)
        if plugin is None:
            return None
        try:
            return plugin.image(rest)
        except Exception:
            return None

    # ---- live keys ------------------------------------------------------------------------
    def visible_live_specs(self):
        out = []
        for r in self.runners():
            if not r.connected:
                continue
            try:
                keys = r.page()["keys"].values()
            except Exception:
                continue
            out.extend(k["live"] for k in keys if isinstance(k, dict) and k.get("live"))
        out.extend(getattr(self, "_preview_live", []))
        return out

    def watch_preview(self, specs):
        """Live specs the settings page is looking at (so previews update even with no pad connected)."""
        self._preview_live = list(specs)[:40]

    def live_content(self, key, sample_fallback=False):
        if not key or not key.get("live"):
            return None
        got = self.live.get(key["live"])
        if got is None and sample_fallback:
            return self.live.sample(key["live"])
        return got or {"text": "…"}

    # ---- actions --------------------------------------------------------------------------
    def run_action(self, step, device_id=None, nav=None):
        p = self.plugins.get(step.get("plugin"))
        if p is None:
            raise ValueError(f"plugin {step.get('plugin')!r} is not installed")
        name = step.get("action")
        if name not in p.actions:
            raise ValueError(f"{p.name} has no action {name!r}")
        ctx = Context(self, device_id, nav, self.store.plugin_settings(p.id))
        return p.run(name, step.get("params", {}) or {}, ctx)

    def report_error(self, device_id, message):
        self.errors.append({"time": time.time(), "device": device_id, "message": str(message)[:300]})

    def status(self):
        return {"devices": [r.status() for r in self.runners()], "notes": list(self.notes),
                "errors": list(self.errors)[-5:]}
