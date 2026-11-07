"""VSD M18 controller (USB 5548:1000). Replaces VSD Craft - close it first.

Usage:  python app.py             run (draw icons, handle key presses)
        python app.py --draw      draw icons once and exit
        python app.py --test      print key IDs as you press them
        python app.py --check     validate config + plugins, no pad needed
        python app.py --press N   run the action on slot N, no pad needed
Requires: pip install hidapi pillow
"""
import ctypes, os, sys, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

_log_file = None
if sys.stdout is None or sys.stderr is None:  # started with pythonw (no console)
    _log_file = open(os.path.join(HERE, "vsdpad.log"), "a", buffering=1, encoding="utf-8")
    sys.stdout = sys.stderr = _log_file

import iconpacks
import pagestate
import plugins
from hardware import Pad, press_to_slot
from store import Store, spec_for_press, view

LOG_PATH = os.path.join(HERE, "vsdpad.log")


def log(msg):
    """Print and append to vsdpad.log with a timestamp (so problems can be diagnosed later)."""
    line = time.strftime("%H:%M:%S ") + str(msg)
    print(line, flush=True)
    try:
        if os.path.getsize(LOG_PATH) > 500_000:  # keep the log small
            os.replace(LOG_PATH, LOG_PATH + ".old")
    except OSError:
        pass
    try:
        if sys.stdout is not _log_file:  # already written when stdout is the log file
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except OSError:
        pass


def already_running(name):
    """Only one copy may drive the pad. Holds a named mutex for the process lifetime."""
    global _mutex
    _mutex = ctypes.windll.kernel32.CreateMutexW(None, False, name)
    return ctypes.windll.kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS


def run_action(store, action):
    plugins.run_action(action, store.get().get("plugins", {}))


def safe_run(store, action):
    try:
        run_action(store, action)
    except Exception as e:
        print("action failed:", repr(e), flush=True)


def repeat(store, held, key, action):
    time.sleep(0.4)
    while held.get(key):
        safe_run(store, action)
        time.sleep(0.08)


def draw_all(pad, cfg, page=0):
    """Draw one page of keys (0-based) on the pad."""
    pad.rotation = cfg.get("rotation", 0)
    pad.wake(cfg.get("brightness", 80))
    pad.clear()
    pcfg = view(cfg, page)
    for slot in range(1, 16):  # packs with a wallpaper also paint blank keys
        img = iconpacks.render_slot(pcfg, slot)
        if img is not None:
            pad.set_image(slot, img)
    pad.refresh()


def pad_loop(store, mode="", stop=None, status=None):
    """Connect to the pad, draw, handle presses; reconnect on errors."""
    stop = stop or threading.Event()
    status = status if status is not None else {}
    pad = None
    connected_at = last_event = time.time()
    while not stop.is_set():
        try:
            status["pad_connected"] = False
            if pad:
                pad.close()  # release the old handle before reconnecting
            pad = Pad()
            seen, cfg = store.version, store.get()
            pagestate.STATE.configure(cfg["pages"])
            pseen, page = pagestate.STATE.version, pagestate.STATE.current
            draw_all(pad, cfg, page)
            status["pad_connected"] = True
            connected_at = last_event = time.time()
            if mode == "--draw":
                return
            log("pad connected and drawn")
            held = {}
            last_ping = time.time()
            while not stop.is_set():
                if time.time() - last_ping > 5:  # keep-alive so the pad doesn't drop us
                    pad.keepalive()
                    last_ping = time.time()
                if store.version != seen or pagestate.STATE.version != pseen:  # config or page changed: redraw
                    seen, cfg = store.version, store.get()
                    pagestate.STATE.configure(cfg["pages"])
                    pseen, page = pagestate.STATE.version, pagestate.STATE.current
                    draw_all(pad, cfg, page)
                ev = pad.read_event()
                if not ev:
                    continue
                last_event = time.time()
                key, state = ev
                if mode == "--test":
                    print("key", key, "pressed" if state else "released")
                    continue
                slot = press_to_slot(key)
                spec = spec_for_press(cfg, page, slot)  # LCD keys come from the current page, round buttons are global
                if state:
                    status["last_press"] = key  # lets the settings page flash the key and identify round buttons
                    status["press_seq"] = status.get("press_seq", 0) + 1
                    print(f"pad key {key} = slot {slot} ->", spec.get("action") if spec else "no mapping", flush=True)
                if not spec or "action" not in spec:
                    continue
                if state:
                    threading.Thread(target=safe_run, args=(store, spec["action"]), daemon=True).start()
                    if spec.get("repeat"):
                        held[key] = True
                        threading.Thread(target=repeat, args=(store, held, key, spec["action"]), daemon=True).start()
                else:
                    held.pop(key, None)
        except KeyboardInterrupt:
            return
        except Exception as e:  # unplugged / error: retry
            status["pad_connected"] = False
            import traceback
            log(f"pad error: {type(e).__name__}: {e} (connected for {time.time() - connected_at:.1f}s, "
                f"last event {time.time() - last_event:.1f}s ago) - retrying in 2s")
            log(traceback.format_exc().strip().splitlines()[-3:])
            time.sleep(2)
    status["pad_connected"] = False
    if pad:
        pad.close()


def check(store):
    """Validate that every configured action resolves to a real plugin action."""
    problems = 0
    cfg = store.get()
    groups = [(f"page {i} '{pg['name']}'", pg["keys"]) for i, pg in enumerate(cfg["pages"], 1)]
    groups.append(("round buttons", cfg.get("round", {})))
    for title, keys in groups:
        print(f"{title}:")
        for slot, spec in sorted(keys.items(), key=lambda kv: int(kv[0])):
            a = spec.get("action")
            if not a:
                continue
            p = plugins.PLUGINS.get(a.get("plugin"))
            ok = p is not None and a.get("action") in p.actions
            if ok and a.get("plugin") == "pages" and a.get("action") == "open":  # a folder key must point at a real page
                ok = a.get("params", {}).get("page") in {pg["id"] for pg in cfg["pages"]}
            problems += not ok
            print(f"  slot {slot:>2} {spec.get('label', ''):<10} -> {a.get('plugin')}.{a.get('action')} "
                  f"{'ok' if ok else 'PROBLEM'}")
    print("plugins:", ", ".join(plugins.PLUGINS) or "none")
    return problems


def tray_icon(url, stop, server):
    """System-tray icon (needed because auto-start runs without a window). Returns None if pystray is missing."""
    try:
        import pystray
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((2, 2, 61, 61), 12, fill=(29, 31, 39), outline=(90, 140, 255), width=3)
    for r in range(3):
        for c in range(3):
            d.rounded_rectangle((12 + c * 14, 12 + r * 14, 22 + c * 14, 22 + r * 14), 2, fill=(90, 140, 255))
    import webbrowser

    def quit_app(icon, item):
        stop.set()
        server.stop()
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("Open settings", lambda i, it: webbrowser.open(url), default=True),
        pystray.MenuItem("Quit", quit_app))
    return pystray.Icon("vsdpad", img, "VSD Pad", menu)


def parse_args(argv):
    import argparse
    ap = argparse.ArgumentParser(description="VSD M18 pad controller")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--draw", action="store_true", help="draw icons once and exit")
    g.add_argument("--test", action="store_true", help="print key IDs as pressed")
    g.add_argument("--check", action="store_true", help="validate config and plugins (no pad)")
    g.add_argument("--press", metavar="SLOT", help="run the action on a slot or round-button id (no pad)")
    g.add_argument("--gui-only", action="store_true", help="settings server only, no pad (for development)")
    ap.add_argument("--page", type=int, default=1, help="page number for --press (default 1)")
    ap.add_argument("--gui", action="store_true", help="open the settings page in the browser")
    ap.add_argument("--no-tray", action="store_true", help="no tray icon; run in this console")
    ap.add_argument("--config", default=os.path.join(HERE, "config.json"), help="config file path")
    return ap.parse_args(argv)


def main():
    a = parse_args(sys.argv[1:])
    offline = bool(a.check or a.press or a.gui_only or a.config != os.path.join(HERE, "config.json"))
    if already_running("Local\\VSDPadController" + (".dev" if offline else "")):
        print("VSD pad controller is already running.")
        if a.gui:  # a second launch with --gui just opens the running copy's settings page
            import webbrowser
            webbrowser.open("http://127.0.0.1:8765/")
        return
    plugins.load_plugins()
    store = Store(a.config)
    pagestate.STATE.configure(store.get()["pages"])
    if a.check:
        sys.exit(1 if check(store) else 0)
    if a.press:
        cfg = store.get()
        pagestate.STATE.configure(cfg["pages"])
        spec = spec_for_press(cfg, a.page - 1, int(a.press)) or {}
        if "action" not in spec:
            sys.exit(f"slot {a.press} (page {a.page}) has no action")
        run_action(store, spec["action"])
        print("ok", "| now on page", pagestate.STATE.current + 1)
        return
    mode = "--draw" if a.draw else "--test" if a.test else ""
    if mode:  # one-shot hardware tools: no server, no tray
        pad_loop(store, mode)
        return

    from server import SettingsServer
    status = {"pad_connected": False}
    stop = threading.Event()
    server = SettingsServer(store, lambda s, act: run_action(s, act), status)
    url = server.start()
    print("Settings:", url, flush=True)
    if a.gui:
        import webbrowser
        webbrowser.open(url)
    if a.gui_only:
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return
    icon = None if a.no_tray else tray_icon(url, stop, server)
    t = threading.Thread(target=pad_loop, args=(store, "", stop, status), daemon=True)
    t.start()
    try:
        if icon:
            icon.run()  # blocks until Quit; the pad loop runs in its thread
        else:
            t.join()
    except KeyboardInterrupt:
        pass
    stop.set()
    server.stop()


if __name__ == "__main__":
    main()
