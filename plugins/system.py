"""Built-in plugin: media keys, hotkeys, launch an app, open a URL, call a webhook."""
import ctypes, subprocess, urllib.request, webbrowser

from .base import Plugin

user32 = ctypes.windll.user32

VK = {"vol_up": 0xAF, "vol_down": 0xAE, "mute": 0xAD, "play": 0xB3,
      "next": 0xB0, "prev": 0xB1, "stop": 0xB2,
      "ctrl": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B, "esc": 0x1B,
      "tab": 0x09, "enter": 0x0D, "delete": 0x2E, "printscreen": 0x2C,
      "space": 0x20, "backspace": 0x08}


def vk_code(name):
    name = name.strip().lower()
    if name in VK:
        return VK[name]
    if len(name) == 1:
        return ord(name.upper())
    if name.startswith("f") and name[1:].isdigit():
        return 0x70 + int(name[1:]) - 1
    raise ValueError(f"unknown key {name!r}")


def tap(codes):
    for c in codes:
        user32.keybd_event(c, 0, 0, 0)
    for c in reversed(codes):
        user32.keybd_event(c, 0, 2, 0)


class System(Plugin):
    id = "system"
    name = "System"
    description = "Media keys, keyboard shortcuts, launch apps, open links, call webhooks."
    settings = []
    actions = {
        "media": {"label": "Media / volume key", "params": [
            {"key": "key", "label": "Key", "kind": "select",
             "options": [{"value": "vol_up", "label": "Volume up"},
                         {"value": "vol_down", "label": "Volume down"},
                         {"value": "mute", "label": "Mute"},
                         {"value": "play", "label": "Play / pause"},
                         {"value": "next", "label": "Next track"},
                         {"value": "prev", "label": "Previous track"},
                         {"value": "stop", "label": "Stop"}]}]},
        "hotkey": {"label": "Keyboard shortcut", "params": [
            {"key": "keys", "label": "Keys", "kind": "text", "placeholder": "ctrl+shift+esc",
             "help": "Join keys with +, e.g. win+shift+s, ctrl+c, f5"}]},
        "launch": {"label": "Launch app or command", "params": [
            {"key": "cmd", "label": "Command", "kind": "text", "placeholder": "notepad"}]},
        "url": {"label": "Open link", "params": [
            {"key": "url", "label": "URL", "kind": "text", "placeholder": "https://example.com"}]},
        "webhook": {"label": "Call a webhook", "params": [
            {"key": "url", "label": "URL", "kind": "text"},
            {"key": "method", "label": "Method", "kind": "select", "options": ["POST", "GET"], "default": "POST"},
            {"key": "body", "label": "Body (optional)", "kind": "text"}]},
    }

    def run(self, action, params, settings):
        if action == "media":
            tap([vk_code(params["key"])])
        elif action == "hotkey":
            keys = params["keys"]
            if isinstance(keys, str):
                keys = keys.split("+")
            tap([vk_code(k) for k in keys])
        elif action == "launch":
            subprocess.Popen(params["cmd"], shell=True)
        elif action == "url":
            webbrowser.open(params["url"])
        elif action == "webhook":
            body = params.get("body", "")
            req = urllib.request.Request(params["url"], data=body.encode() if body else None,
                                         method=params.get("method", "POST"))
            urllib.request.urlopen(req, timeout=5).read()
        else:
            raise ValueError(f"unknown system action {action!r}")


PLUGIN = System()
