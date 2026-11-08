"""Record a macro by doing it: keys and mouse clicks are captured on this PC and turned into macro steps.

Only runs while the user has pressed Record in the settings page, stops by itself after MAX_S seconds, and
keeps nothing: the result goes straight into the key editor for the user to check and save. Windows only.
"""
import ctypes, sys, threading, time

MAX_S = 60
MAX_EVENTS = 2000
MIN_WAIT = 0.25          # pauses shorter than this aren't kept as Wait steps
MAX_STEPS = 100

# virtual-key code -> name used by the Keyboard shortcut step (see plugins/system.py)
NAMES = {0x11: "ctrl", 0xA2: "ctrl", 0xA3: "ctrl", 0x10: "shift", 0xA0: "shift", 0xA1: "shift", 0x12: "alt",
         0xA4: "alt", 0xA5: "alt", 0x5B: "win", 0x5C: "win", 0x1B: "esc", 0x09: "tab", 0x0D: "enter", 0x2E: "delete",
         0x2C: "printscreen", 0x20: "space", 0x08: "backspace", 0x26: "up", 0x28: "down", 0x25: "left",
         0x27: "right", 0x24: "home", 0x23: "end", 0x21: "pageup", 0x22: "pagedown", 0x2D: "insert",
         0xAF: "vol_up", 0xAE: "vol_down", 0xAD: "mute", 0xB3: "play", 0xB0: "next", 0xB1: "prev", 0xB2: "stop"}
MODS = ("ctrl", "shift", "alt", "win")


def key_name(vk):
    if vk in NAMES:
        return NAMES[vk]
    if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
        return chr(vk).lower()
    if 0x70 <= vk <= 0x87:
        return f"f{vk - 0x6F}"
    return None


def to_steps(events, end_t=None):
    """Turn raw events into macro steps.

    events: ("key", t, vk, down, char) | ("click", t, button, x, y) | ("scroll", t, notches)
    char is the text the key would type (None for non-text keys). Typing becomes one "Type text" step,
    shortcuts become "Keyboard shortcut" steps, gaps become waits (capped at 10 s each).
    """
    steps, text, held, last_t = [], [], [], None

    def flush_text():
        if text:
            steps.append({"plugin": "system", "action": "type", "params": {"text": "".join(text)}})
            text.clear()

    def gap(t):
        nonlocal last_t
        if last_t is not None and t - last_t >= MIN_WAIT:
            flush_text()
            steps.append({"wait": round(min(10.0, t - last_t), 1)})
        last_t = t

    for ev in events:
        kind, t = ev[0], ev[1]
        if kind == "key":
            _, _, vk, down, char = ev
            name = key_name(vk)
            if not down:
                if name in held:
                    held.remove(name)
                continue
            if name in MODS:
                if name not in held:
                    held.append(name)
                continue
            gap(t)
            mods = [m for m in MODS if m in held]
            plain = not [m for m in mods if m != "shift"]
            if plain and char and char.isprintable():
                text.append(char)
            elif plain and name == "enter":
                text.append("\\n")
            elif name:
                flush_text()
                steps.append({"plugin": "system", "action": "hotkey", "params": {"keys": "+".join(mods + [name])}})
        elif kind == "click":
            _, _, button, x, y = ev
            gap(t)
            flush_text()
            last = steps[-1] if steps else None
            if (last and last.get("action") == "click" and last["params"].get("button") == button
                    and abs(last["params"]["x"] - x) <= 4 and abs(last["params"]["y"] - y) <= 4
                    and last["params"].get("count") == "1" and t - last.get("_t", 0) < 0.45):
                last["params"]["count"] = "2"
                continue
            steps.append({"plugin": "macro", "action": "click", "_t": t,
                          "params": {"button": button, "x": int(x), "y": int(y), "count": "1"}})
        elif kind == "scroll":
            gap(t)
            flush_text()
            n = int(ev[2])
            last = steps[-1] if steps else None
            if last and last.get("action") == "scroll" and (last["params"]["amount"] > 0) == (n > 0):
                last["params"]["amount"] += n
            else:
                steps.append({"plugin": "macro", "action": "scroll", "params": {"amount": n}})
    flush_text()
    for s in steps:
        s.pop("_t", None)
    return steps[:MAX_STEPS]


class Recorder:
    """Low-level keyboard + mouse hooks on their own thread. start() / stop() -> steps."""

    def __init__(self):
        self.events, self.running, self.started = [], False, 0
        self._thread, self._tid = None, None
        self._lock = threading.Lock()

    def start(self):
        if sys.platform != "win32":
            raise OSError("Recording only works on Windows.")
        with self._lock:
            if self.running:
                return
            self.events, self.running, self.started = [], True, time.monotonic()
            ready = threading.Event()
            self._thread = threading.Thread(target=self._run, args=(ready,), name="recorder", daemon=True)
            self._thread.start()
            ready.wait(2)

    def stop(self, drop_last_click=True):
        with self._lock:
            if self._thread and self._tid:
                ctypes.windll.user32.PostThreadMessageW(self._tid, 0x0012, 0, 0)  # WM_QUIT
                self._thread.join(2)
            self.running, self._thread, self._tid = False, None, None
            ev = list(self.events)
        if drop_last_click:   # the click on the Stop button itself
            for i in range(len(ev) - 1, -1, -1):
                if ev[i][0] == "click":
                    ev = ev[:i]
                    break
                if ev[i][0] == "key":
                    break
        return to_steps(ev)

    def status(self):
        if self.running and time.monotonic() - self.started > MAX_S:
            self.stop(drop_last_click=False)
        return {"recording": self.running, "seconds": round(time.monotonic() - self.started) if self.running else 0,
                "events": len(self.events)}

    def _run(self, ready):
        from ctypes import wintypes
        u, k = ctypes.windll.user32, ctypes.windll.kernel32
        self._tid = k.GetCurrentThreadId()
        LRESULT = ctypes.c_ssize_t
        HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

        class KBD(ctypes.Structure):
            _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

        class MS(ctypes.Structure):
            _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD), ("flags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

        u.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        u.CallNextHookEx.restype = LRESULT
        u.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
        u.SetWindowsHookExW.restype = wintypes.HHOOK
        u.ToUnicode.argtypes = [wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_ubyte), wintypes.LPWSTR,
                                ctypes.c_int, wintypes.UINT]

        def char_for(vk, scan):
            state = (ctypes.c_ubyte * 256)()
            for m in (0x10, 0x11, 0x12, 0x14):
                state[m] = u.GetKeyState(m) & 0xFF
            buf = ctypes.create_unicode_buffer(4)
            n = u.ToUnicode(vk, scan, state, buf, 4, 4)  # flag 4: don't change keyboard state
            return buf.value[:n] if n == 1 else None

        def add(ev):
            if len(self.events) < MAX_EVENTS and time.monotonic() - self.started <= MAX_S:
                self.events.append(ev)

        def kb(code, wp, lp):
            if code == 0:
                d = ctypes.cast(lp, ctypes.POINTER(KBD)).contents
                injected = d.flags & 0x10
                if not injected:
                    down = wp in (0x100, 0x104)
                    add(("key", time.monotonic(), d.vkCode, down, char_for(d.vkCode, d.scanCode) if down else None))
            return u.CallNextHookEx(None, code, wp, lp)

        def ms(code, wp, lp):
            if code == 0:
                d = ctypes.cast(lp, ctypes.POINTER(MS)).contents
                if not d.flags & 0x01:   # not injected
                    t = time.monotonic()
                    if wp in (0x201, 0x204, 0x207):
                        add(("click", t, {0x201: "left", 0x204: "right", 0x207: "middle"}[wp], d.pt.x, d.pt.y))
                    elif wp == 0x20A:
                        add(("scroll", t, ctypes.c_short(d.mouseData >> 16).value // 120))
            return u.CallNextHookEx(None, code, wp, lp)

        kb_proc, ms_proc = HOOKPROC(kb), HOOKPROC(ms)
        h1 = u.SetWindowsHookExW(13, kb_proc, None, 0)
        h2 = u.SetWindowsHookExW(14, ms_proc, None, 0)
        ready.set()
        msg = wintypes.MSG()
        try:
            while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                u.TranslateMessage(ctypes.byref(msg))
                u.DispatchMessageW(ctypes.byref(msg))
        finally:
            if h1:
                u.UnhookWindowsHookEx(h1)
            if h2:
                u.UnhookWindowsHookEx(h2)


RECORDER = Recorder()
