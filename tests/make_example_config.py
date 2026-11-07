"""Regenerates config.example.json (a small, secret-free starter layout) and validates it.
Run:  python tests/make_example_config.py
"""
import json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import store  # noqa: E402


def sysact(action, **params):
    return {"plugin": "system", "action": action, "params": params}


def pages_act(action, **params):
    return {"plugin": "pages", "action": action, "params": params}


MAIN_ID, FOLDER_ID = "pmain01", "plights1"
cfg = {
    "version": store.CONFIG_VERSION,
    "brightness": 80,
    "rotation": 0,
    "icon_pack": "modern",
    "row_offset": [0, 0, 0],
    "round_buttons": [37, 48, 49],
    "round": {
        "37": {"label": "Prev", "action": pages_act("previous")},
        "49": {"label": "Next", "action": pages_act("next")},
    },
    "pages": [
        {"id": MAIN_ID, "name": "Main", "folder": False, "keys": {
            "1": {"icon": "♪", "label": "Vol -", "color": [90, 160, 255], "repeat": True, "action": sysact("media", key="vol_down")},
            "2": {"icon": "♫", "label": "Vol +", "color": [90, 160, 255], "repeat": True, "action": sysact("media", key="vol_up")},
            "3": {"icon": "✕", "label": "Mute", "color": [255, 110, 110], "action": sysact("media", key="mute")},
            "4": {"icon": "▶", "label": "Play", "color": [120, 220, 150], "action": sysact("media", key="play")},
            "5": {"icon": "▶▶", "label": "Next", "color": [120, 220, 150], "action": sysact("media", key="next")},
            "6": {"icon": "⌨", "label": "Notepad", "color": [255, 200, 90], "action": sysact("launch", cmd="notepad")},
            "7": {"icon": "☎", "label": "Calc", "color": [255, 200, 90], "action": sysact("launch", cmd="calc")},
            "8": {"icon": "✦", "label": "Snip", "color": [200, 140, 255], "action": sysact("hotkey", keys="win+shift+s")},
            "11": {"icon": "📁", "label": "Lights", "color": [255, 220, 120], "action": pages_act("open", page=FOLDER_ID)},
        }},
        {"id": FOLDER_ID, "name": "Lights", "folder": True, "keys": {
            "1": {"icon": "←", "label": "Back", "color": [200, 200, 210], "action": pages_act("back")},
        }},
    ],
    "plugins": {},   # add Home Assistant / Hue in the app's Plugins tab; never commit those values
}
store.validate(cfg)
path = os.path.join(ROOT, "config.example.json")
with open(path, "w", encoding="utf-8") as f:
    json.dump(cfg, f, indent=2, ensure_ascii=False)
    f.write("\n")
print("wrote", path)
