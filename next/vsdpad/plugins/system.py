"""Built-in: media keys, keyboard shortcuts, typing text, launching apps, opening links, webhooks."""
import ctypes, subprocess, sys, urllib.request, webbrowser

from .base import Plugin

VK = {"vol_up": 0xAF, "vol_down": 0xAE, "mute": 0xAD, "play": 0xB3, "next": 0xB0, "prev": 0xB1, "stop": 0xB2,
      "ctrl": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B, "esc": 0x1B, "tab": 0x09, "enter": 0x0D,
      "delete": 0x2E, "printscreen": 0x2C, "space": 0x20, "backspace": 0x08, "up": 0x26, "down": 0x28,
      "left": 0x25, "right": 0x27, "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22, "insert": 0x2D}
EXTENDED = {0x2E, 0x2D, 0x24, 0x23, 0x21, 0x22, 0x25, 0x26, 0x27, 0x28, 0x5B}


def vk_code(name):
    name = name.strip().lower()
    if name in VK:
        return VK[name]
    if len(name) == 1 and name.isalnum():
        return ord(name.upper())
    if name.startswith("f") and name[1:].isdigit() and 1 <= int(name[1:]) <= 24:
        return 0x70 + int(name[1:]) - 1
    raise ValueError(f"unknown key {name!r}")


def _user32():
    if sys.platform != "win32":
        raise OSError("keyboard actions only work on Windows")
    return ctypes.windll.user32


def tap(codes):
    u = _user32()
    for c in codes:
        u.keybd_event(c, 0, 1 if c in EXTENDED else 0, 0)
    for c in reversed(codes):
        u.keybd_event(c, 0, (1 if c in EXTENDED else 0) | 2, 0)


def type_text(text):
    """Type any text (Unicode) with SendInput, like pasting without touching the clipboard."""
    u = _user32()
    from ctypes import wintypes

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class INPUT(ctypes.Structure):
        class _U(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT), ("pad", ctypes.c_byte * 32)]
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _U)]

    seq = []
    for ch in text.replace("\r\n", "\n"):
        if ch == "\n":
            seq += [(0x0D, 0, 0), (0x0D, 0, 2)]
            continue
        data = ch.encode("utf-16-le")
        for i in range(0, len(data), 2):
            unit = int.from_bytes(data[i:i + 2], "little")
            seq += [(0, unit, 4), (0, unit, 4 | 2)]  # KEYEVENTF_UNICODE (+ KEYUP)
    arr = (INPUT * len(seq))()
    for i, (vk, scan, flags) in enumerate(seq):
        arr[i].type = 1
        arr[i].ki = KEYBDINPUT(vk, scan, flags, 0, 0)
    if seq and u.SendInput(len(seq), arr, ctypes.sizeof(INPUT)) != len(seq):
        raise OSError("Windows blocked the typing (is an admin window focused?)")


class System(Plugin):
    id = "system"
    name = "System"
    description = "Media keys, keyboard shortcuts, typing text, launching apps, opening links and webhooks."
    actions = {
        "media": {"label": "Media / volume key", "params": [
            {"key": "key", "label": "Key", "kind": "select", "options": [
                {"value": "vol_up", "label": "Volume up"}, {"value": "vol_down", "label": "Volume down"},
                {"value": "mute", "label": "Mute"}, {"value": "play", "label": "Play / pause"},
                {"value": "next", "label": "Next track"}, {"value": "prev", "label": "Previous track"},
                {"value": "stop", "label": "Stop"}]}]},
        "hotkey": {"label": "Keyboard shortcut", "params": [
            {"key": "keys", "label": "Keys", "kind": "text", "placeholder": "ctrl+shift+esc",
             "help": "Join keys with +, e.g. win+shift+s, ctrl+c, f5"}]},
        "type": {"label": "Type text", "params": [
            {"key": "text", "label": "Text", "kind": "text", "placeholder": "Hello!",
             "help": "Types this into the focused window. Use \\n for Enter."}]},
        "launch": {"label": "Launch app or command", "params": [
            {"key": "cmd", "label": "Command", "kind": "text", "placeholder": "notepad"}]},
        "url": {"label": "Open link", "params": [
            {"key": "url", "label": "URL", "kind": "text", "placeholder": "https://example.com"}]},
        "webhook": {"label": "Call a webhook", "params": [
            {"key": "url", "label": "URL", "kind": "text"},
            {"key": "method", "label": "Method", "kind": "select", "options": ["POST", "GET"], "default": "POST"},
            {"key": "body", "label": "Body (optional)", "kind": "text"}]},
    }

    def run(self, action, params, ctx):
        if action == "media":
            tap([vk_code(params.get("key", ""))])
        elif action == "hotkey":
            keys = params.get("keys", "")
            keys = keys.split("+") if isinstance(keys, str) else keys
            tap([vk_code(k) for k in keys if k.strip()])
        elif action == "type":
            type_text(str(params.get("text", "")).replace("\\n", "\n"))
        elif action == "launch":
            cmd = str(params.get("cmd", "")).strip()
            if not cmd:
                raise ValueError("Enter a command to launch.")
            subprocess.Popen(cmd, shell=True)
        elif action == "url":
            url = str(params.get("url", "")).strip()
            if not url:
                raise ValueError("Enter a link to open.")
            webbrowser.open(url)
        elif action == "webhook":
            url = str(params.get("url", "")).strip()
            if not url.lower().startswith(("http://", "https://")):
                raise ValueError("The webhook URL must start with http:// or https://")
            body = params.get("body", "")
            req = urllib.request.Request(url, data=body.encode() if body else None, method=params.get("method", "POST"))
            urllib.request.urlopen(req, timeout=5).read()
        else:
            raise ValueError(f"unknown system action {action!r}")


PLUGIN = System()
