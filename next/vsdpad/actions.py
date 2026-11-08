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


MAX_LOOPS = 10000     # a "loop forever" with nothing to stop it (e.g. the Try button) still ends
LOOP_MIN_S = 0.05     # a forever loop never spins faster than this


def run_steps(app, steps, device_id=None, nav=None, cancel=None):
    """Run steps in order. Raises the first error (after logging it).

    A {"loop": n} step repeats the steps above it (back to the previous loop step) until they've run n times;
    {"loop": 0} repeats until the key is pressed again. Whatever ends the run, plugins get end_steps() so
    nothing (like a held-down key) is left behind."""
    try:
        _run_list(app, list(steps or []), device_id, nav, cancel)
    finally:
        for p in list((getattr(app, "plugins", None) or {}).values()):
            end = getattr(p, "end_steps", None)
            if end:
                try:
                    end()
                except Exception as e:
                    LOG.warning("%s cleanup failed: %s", getattr(p, "name", "plugin"), e)


def _pause(cancel, seconds):
    if seconds <= 0:
        return
    if cancel is not None:
        cancel.wait(seconds)
    else:
        time.sleep(seconds)


def _run_list(app, steps, device_id, nav, cancel):
    i, start, runs, round_t = 0, 0, {}, time.monotonic()
    while i < len(steps):
        if cancel is not None and cancel.is_set():
            return
        step = steps[i]
        if "loop" in step:
            n = int(step["loop"])
            done = runs.get(i, 1)
            if (n == 0 and done < MAX_LOOPS) or done < n:
                runs[i] = done + 1
                if n == 0:
                    _pause(cancel, LOOP_MIN_S - (time.monotonic() - round_t))
                round_t, i = time.monotonic(), start
            else:
                start = i = i + 1
            continue
        if "wait" in step:
            _pause(cancel, float(step["wait"]))
        else:
            app.run_action(step, device_id=device_id, nav=nav)
        i += 1


def has_loop(steps):
    return any(isinstance(s, dict) and "loop" in s for s in steps or [])


def safe_run(app, steps, device_id=None, nav=None, cancel=None, what="action"):
    try:
        run_steps(app, steps, device_id, nav, cancel)
    except Exception as e:
        LOG.warning("%s failed: %s", what, e)
        app.report_error(device_id, f"{what}: {e}")
        return False
    return True


class Gestures:
    """Turns press/release events of one device into tap / hold / double / repeat runs.

    Keys without hold or double actions fire on press (no added delay). cancel_all() stops everything that is
    still pending or repeating (used when the device disconnects, so a held repeat key can never run away)."""

    def __init__(self, app, device_id, nav, lookup, on_error=None):
        self.app, self.device_id, self.nav, self.lookup = app, device_id, nav, lookup
        self.on_error = on_error   # called with the key when its action fails (the pad shows it on the key)
        self._lock = threading.Lock()
        self._held = {}      # button -> {"key", "t", "cancel": Event, "consumed": bool, "timer"}
        self._pending = {}   # button -> Timer waiting to see if a second press comes
        self._loops = {}     # button -> Event: a looping macro that's running; pressing the key again stops it

    def _run(self, key, gesture, cancel=None, button=None):
        steps = key.get(gesture) or []
        if steps:
            if cancel is None and has_loop(steps):
                cancel = threading.Event()
                with self._lock:
                    self._loops[button] = cancel

            def go():
                try:
                    ok = safe_run(self.app, steps, self.device_id, self.nav, cancel, f"{key.get('label') or 'key'} ({gesture})")
                finally:
                    with self._lock:
                        if cancel is not None and self._loops.get(button) is cancel:
                            del self._loops[button]
                if not ok and self.on_error:
                    self.on_error(key)
            threading.Thread(target=go, daemon=True).start()

    def press(self, button):
        with self._lock:
            looping = self._loops.pop(button, None)
        if looping is not None:   # a looping macro is running on this key: this press stops it
            looping.set()
            return
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
            self._run(key, "double", button=button)
            return
        if key.get("repeat") and key.get("tap"):
            st["consumed"] = True
            self._run(key, "tap", button=button)
            threading.Thread(target=self._repeat, args=(key, cancel), daemon=True).start()
        elif key.get("hold"):
            def fire():
                with self._lock:
                    if self._held.get(button) is not st:
                        return
                    st["consumed"] = True
                self._run(key, "hold", button=button)
            st["timer"] = threading.Timer(HOLD_S, fire)
            st["timer"].daemon = True
            st["timer"].start()
        elif not key.get("double"):
            st["consumed"] = True
            self._run(key, "tap", button=button)

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
            self._run(key, "tap", button=button)

    def _single(self, button, key):
        with self._lock:
            if self._pending.pop(button, None) is None:
                return
        self._run(key, "tap", button=button)

    def _repeat(self, key, cancel):
        if cancel.wait(REPEAT_DELAY):
            return
        while not cancel.is_set():
            safe_run(self.app, key.get("tap"), self.device_id, self.nav, cancel, f"{key.get('label') or 'key'} (repeat)")
            cancel.wait(REPEAT_EVERY)

    def cancel_all(self):
        with self._lock:
            held, pending = list(self._held.values()), list(self._pending.values())
            loops = list(self._loops.values())
            self._loops.clear()
        for ev in loops:
            ev.set()
        with self._lock:
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
