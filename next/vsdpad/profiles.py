"""Switch pages by the app in front: e.g. show the OBS page while OBS is focused, and go back afterwards.

Only reacts when the foreground app *changes*, so it never fights the user: if you pick another page by hand
while the app is focused, it leaves you there and won't send you back either.
"""
import ctypes, os, sys, threading

from .log import LOG


def foreground_app():
    """(exe file name, window title) of the focused window, or None if unknown (non-Windows)."""
    if sys.platform != "win32":
        return None
    from ctypes import wintypes
    u, k = ctypes.windll.user32, ctypes.windll.kernel32
    hwnd = u.GetForegroundWindow()
    if not hwnd:
        return None
    n = u.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    u.GetWindowTextW(hwnd, buf, n + 1)
    pid = wintypes.DWORD()
    u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    exe = ""
    h = k.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if h:
        try:
            size = wintypes.DWORD(1024)
            pbuf = ctypes.create_unicode_buffer(1024)
            if k.QueryFullProcessImageNameW(h, 0, pbuf, ctypes.byref(size)):
                exe = os.path.basename(pbuf.value)
        finally:
            k.CloseHandle(h)
    return exe, buf.value


def match(rules, exe, title):
    exe, title = (exe or "").lower(), (title or "").lower()
    for r in rules:
        a, t = (r.get("app") or "").strip().lower(), (r.get("title") or "").strip().lower()
        if not (a or t):
            continue
        if a and exe != a and exe != a + ".exe":
            continue
        if t and t not in title:
            continue
        return r
    return None


class Profiles:
    def __init__(self, app, probe=foreground_app):
        self.app, self.probe = app, probe
        self._last = None
        self._active = {}  # device id -> (rule page, page to return to, nav version we set)
        self._stop = threading.Event()

    def start(self):
        threading.Thread(target=self._loop, name="profiles", daemon=True).start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.wait(1.0):
            try:
                self.tick()
            except Exception:
                LOG.exception("app rules")

    def tick(self):
        cfg = self.app.store.get()
        prof = cfg.get("profiles", {})
        if not prof.get("enabled", True) or not prof.get("rules"):
            return
        fg = self.probe()
        if fg is None or fg == self._last:
            return
        self._last = fg
        rule = match(prof["rules"], *fg)
        for dev_id, runner in self.app.runners_snapshot():
            nav = runner.nav
            if rule and rule["page"] in nav.ids:
                active = self._active.get(dev_id)
                back_to = active[1] if active else nav.current_id
                if nav.current_id != rule["page"]:
                    nav.goto_id(rule["page"], manual=False)
                self._active[dev_id] = (rule["page"], back_to if rule.get("revert", True) else None, nav.manual_version)
            else:
                active = self._active.pop(dev_id, None)
                # go back only if the user didn't navigate since we switched
                if active and active[1] and nav.manual_version == active[2] and active[1] in nav.ids:
                    nav.goto_id(active[1], manual=False)
