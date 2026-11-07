"""Running key actions: step lists with waits, and the tap / hold / double-press / repeat gestures."""
import threading, time

from .log import LOG

HOLD_S = 0.5        # press longer than this = hold
DOUBLE_S = 0.3      # second press within this = double
REPEAT_DELAY = 0.4
REPEAT_EVERY = 0.08


class Context:
    """What an action can see: its plugin settings, the device's navigator, and the app."""

    def __init__(self, app, device_id=None, nav=None, settings=None):
        self.app, self.device_id, self.nav = app, device_id, nav
        self.settings = settings or {}


def run_steps(app, steps, device_id=None, nav=None, cancel=None):
    """Run steps in order. Raises the first error (after logging it)."""
    for step in steps or []:
        if cancel is not None and cancel.is_set():
            return
        if "wait" in step:
            if cancel is not None:
                cancel.wait(float(step["wait"]))
            else:
                time.sleep(float(step["wait"]))
            continue
        app.run_action(step, device_id=device_id, nav=nav)


def safe_run(app, steps, device_id=None, nav=None, cancel=None, what="action"):
    try:
        run_steps(app, steps, device_id, nav, cancel)
    except Exception as e:
        LOG.warning("%s failed: %s", what, e)
        app.report_error(device_id, f"{what}: {e}")


class Gestures:
    """Turns press/release events of one device into tap / hold / double / repeat runs.

    Keys without hold or double actions fire on press (no added delay). cancel_all() stops everything that is
    still pending or repeating (used when the device disconnects, so a held repeat key can never run away)."""

    def __init__(self, app, device_id, nav, lookup):
        self.app, self.device_id, self.nav, self.lookup = app, device_id, nav, lookup
        self._lock = threading.Lock()
        self._held = {}      # button -> {"key", "t", "cancel": Event, "consumed": bool, "timer"}
        self._pending = {}   # button -> Timer waiting to see if a second press comes

    def _run(self, key, gesture, cancel=None):
        steps = key.get(gesture) or []
        if steps:
            threading.Thread(target=safe_run, args=(self.app, steps, self.device_id, self.nav, cancel,
                                                    f"{key.get('label') or 'key'} ({gesture})"), daemon=True).start()

    def press(self, button):
        key = self.lookup(button)
        if not key:
            return
        cancel = threading.Event()
        st = {"key": key, "t": time.monotonic(), "cancel": cancel, "consumed": False, "timer": None}
        with self._lock:
            old = self._held.pop(button, None)
            if old:
                old["cancel"].set()
            self._held[button] = st
            pending = self._pending.pop(button, None)
        if pending:  # second press of a double
            pending.cancel()
            st["consumed"] = True
            self._run(key, "double")
            return
        if key.get("repeat") and key.get("tap"):
            st["consumed"] = True
            self._run(key, "tap")
            threading.Thread(target=self._repeat, args=(key, cancel), daemon=True).start()
        elif key.get("hold"):
            def fire():
                with self._lock:
                    if self._held.get(button) is not st:
                        return
                    st["consumed"] = True
                self._run(key, "hold")
            st["timer"] = threading.Timer(HOLD_S, fire)
            st["timer"].daemon = True
            st["timer"].start()
        elif not key.get("double"):
            st["consumed"] = True
            self._run(key, "tap")

    def release(self, button):
        with self._lock:
            st = self._held.pop(button, None)
        if not st:
            return
        st["cancel"].set()
        if st["timer"]:
            st["timer"].cancel()
        if st["consumed"]:
            return
        key = st["key"]
        if key.get("double"):
            t = threading.Timer(DOUBLE_S, self._single, args=(button, key))
            t.daemon = True
            with self._lock:
                self._pending[button] = t
            t.start()
        else:
            self._run(key, "tap")

    def _single(self, button, key):
        with self._lock:
            if self._pending.pop(button, None) is None:
                return
        self._run(key, "tap")

    def _repeat(self, key, cancel):
        if cancel.wait(REPEAT_DELAY):
            return
        while not cancel.is_set():
            safe_run(self.app, key.get("tap"), self.device_id, self.nav, cancel, f"{key.get('label') or 'key'} (repeat)")
            cancel.wait(REPEAT_EVERY)

    def cancel_all(self):
        with self._lock:
            held, pending = list(self._held.values()), list(self._pending.values())
            self._held.clear()
            self._pending.clear()
        for st in held:
            st["cancel"].set()
            if st["timer"]:
                st["timer"].cancel()
        for t in pending:
            t.cancel()

    def held_count(self):
        with self._lock:
            return len(self._held)
