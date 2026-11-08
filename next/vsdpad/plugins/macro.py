"""Macro steps: mouse clicks, moves and scrolling, and keys held down across steps (e.g. hold Shift, click, let go).

Keys held with "Hold a key down" are always let go when the macro ends, even if a step fails, so nothing is ever
left stuck down. Windows only.
"""
import ctypes, sys, threading, time

from .base import Plugin
from .system import vk_code, EXTENDED

BUTTONS = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010), "middle": (0x0020, 0x0040)}
BUTTON = {"key": "button", "label": "Button", "kind": "select", "options": [
    {"value": "left", "label": "Left"}, {"value": "right", "label": "Right"}, {"value": "middle", "label": "Middle"}]}
POS_HELP = "Screen position in pixels. Leave both empty to use wherever the mouse is. Recording a macro fills these in."

_held = threading.local()   # each macro runs on its own thread: what *this* macro is holding down


def _user32():
    if sys.platform != "win32":
        raise OSError("mouse and keyboard steps only work on Windows")
    return ctypes.windll.user32


def _num(params, key, default=None):
    v = params.get(key)
    if v is None or v == "":
        return default
    try:
        return int(float(v))
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be a number") from None


def _move_to(params):
    x, y = _num(params, "x"), _num(params, "y")
    if x is not None and y is not None:
        _user32().SetCursorPos(x, y)
    elif (x is None) != (y is None):
        raise ValueError("Fill in both x and y, or leave both empty.")


def _held_set():
    if not hasattr(_held, "keys"):
        _held.keys, _held.buttons = [], []
    return _held


def release_all():
    """Let go of everything this macro is holding (called when its steps finish)."""
    h = _held_set()
    if not (h.keys or h.buttons):
        return
    u = _user32()
    for b in reversed(h.buttons):
        u.mouse_event(BUTTONS[b][1], 0, 0, 0, 0)
    for c in reversed(h.keys):
        u.keybd_event(c, 0, (1 if c in EXTENDED else 0) | 2, 0)
    h.keys, h.buttons = [], []


class Macro(Plugin):
    id = "macro"
    name = "Mouse & keys"
    description = "Mouse clicks, moves and scrolling, and keys held down across steps, for macros."
    actions = {
        "click": {"label": "Mouse click", "params": [
            BUTTON, {"key": "x", "label": "x", "kind": "number", "help": POS_HELP}, {"key": "y", "label": "y", "kind": "number"},
            {"key": "count", "label": "Clicks", "kind": "select", "options": [
                {"value": "1", "label": "Single"}, {"value": "2", "label": "Double"}]}]},
        "move": {"label": "Move the mouse", "params": [
            {"key": "x", "label": "x", "kind": "number"}, {"key": "y", "label": "y", "kind": "number"},
            {"key": "relative", "label": "Move by (instead of move to)", "kind": "check"}]},
        "scroll": {"label": "Scroll", "params": [
            {"key": "amount", "label": "Notches", "kind": "number", "default": -3, "help": "Negative scrolls down, positive up"},
            {"key": "sideways", "label": "Sideways", "kind": "check"}]},
        "mouse_down": {"label": "Hold a mouse button down", "params": [
            BUTTON, {"key": "x", "label": "x", "kind": "number", "help": POS_HELP}, {"key": "y", "label": "y", "kind": "number"}]},
        "mouse_up": {"label": "Let go of a mouse button", "params": [
            BUTTON, {"key": "x", "label": "x", "kind": "number", "help": "Where to let go (for dragging). Leave empty to stay put."},
            {"key": "y", "label": "y", "kind": "number"}]},
        "key_down": {"label": "Hold a key down", "params": [
            {"key": "key", "label": "Key", "kind": "text", "placeholder": "shift",
             "help": "e.g. shift, ctrl, alt, win, w, space, f5. It's let go automatically when the macro ends."}]},
        "key_up": {"label": "Let go of a key", "params": [
            {"key": "key", "label": "Key", "kind": "text", "placeholder": "shift"}]},
    }

    def run(self, action, params, ctx):
        u = _user32()
        h = _held_set()
        if action == "click":
            _move_to(params)
            down, up = BUTTONS.get(params.get("button") or "left", BUTTONS["left"])
            for i in range(2 if str(params.get("count")) == "2" else 1):
                if i:
                    time.sleep(0.05)
                u.mouse_event(down, 0, 0, 0, 0)
                u.mouse_event(up, 0, 0, 0, 0)
        elif action == "move":
            if params.get("relative"):
                u.mouse_event(0x0001, _num(params, "x", 0), _num(params, "y", 0), 0, 0)
            else:
                if _num(params, "x") is None or _num(params, "y") is None:
                    raise ValueError("Fill in x and y to move the mouse.")
                _move_to(params)
        elif action == "scroll":
            amount = _num(params, "amount", -3)
            u.mouse_event(0x01000 if params.get("sideways") else 0x0800, 0, 0, ctypes.c_uint32(amount * 120).value, 0)
        elif action == "mouse_down":
            b = params.get("button") or "left"
            _move_to(params)
            u.mouse_event(BUTTONS[b][0], 0, 0, 0, 0)
            h.buttons.append(b)
        elif action == "mouse_up":
            b = params.get("button") or "left"
            _move_to(params)
            u.mouse_event(BUTTONS[b][1], 0, 0, 0, 0)
            if b in h.buttons:
                h.buttons.remove(b)
        elif action == "key_down":
            c = vk_code(str(params.get("key", "")))
            u.keybd_event(c, 0, 1 if c in EXTENDED else 0, 0)
            h.keys.append(c)
        elif action == "key_up":
            c = vk_code(str(params.get("key", "")))
            u.keybd_event(c, 0, (1 if c in EXTENDED else 0) | 2, 0)
            if c in h.keys:
                h.keys.remove(c)
        else:
            raise ValueError(f"unknown mouse & keys action {action!r}")

    def end_steps(self):
        if sys.platform == "win32":
            release_all()


PLUGIN = Macro()
