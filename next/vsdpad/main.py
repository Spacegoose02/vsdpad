"""Command line entry point. See README.md."""
import argparse, ctypes, json, os, sys, threading

from . import PKG_DIR, __version__

NEXT_DIR = os.path.dirname(PKG_DIR)
DEFAULT_DATA = os.path.join(NEXT_DIR, "data")
OLD_CONFIG = os.path.join(os.path.dirname(NEXT_DIR), "config.json")   # the 1.x app's layout, one folder up
MUTEX = "Local\\VSDPadController"   # same name as 1.x: only one program may drive the pad


def already_running(name):
    if sys.platform != "win32":
        return False
    global _mutex
    _mutex = ctypes.windll.kernel32.CreateMutexW(None, False, name)
    return ctypes.windll.kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS


def first_run(data_dir, import_from=None, log=print):
    """Create data/config.json: import the 1.x layout if there is one (read-only), else a starter layout."""
    path = os.path.join(data_dir, "config.json")
    if os.path.exists(path):
        return False
    os.makedirs(data_dir, exist_ok=True)
    src = import_from or (OLD_CONFIG if os.path.exists(OLD_CONFIG) else None)
    if src:
        with open(src, encoding="utf-8") as f:
            raw = json.load(f)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(raw, f, indent=2, ensure_ascii=False)  # the Store migrates it to v5 on load
        log(f"Imported your layout from {src}")
    else:
        from .migrate import default_config
        with open(path, "w", encoding="utf-8") as f:
            json.dump(default_config(), f, indent=2, ensure_ascii=False)
        log("Created a starter layout")
    return True


def tray_icon(url, on_quit):
    try:
        import pystray
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    import webbrowser
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((2, 2, 61, 61), 12, fill=(20, 22, 30), outline=(255, 170, 40), width=3)
    for r in range(3):
        for c in range(3):
            d.rounded_rectangle((12 + c * 14, 12 + r * 14, 22 + c * 14, 22 + r * 14), 2, fill=(255, 170, 40))

    def quit_app(icon, item):
        on_quit()
        icon.stop()

    menu = pystray.Menu(pystray.MenuItem("Open settings", lambda i, it: webbrowser.open(url), default=True),
                        pystray.MenuItem("Quit", quit_app))
    return pystray.Icon("vsdpad", img, "VSD Pad", menu)


def check(app):
    """Print every key's actions and live content, flag anything that won't work. Returns the problem count."""
    from .schema import all_keys
    cfg, problems = app.store.get(), 0
    page_ids = {p["id"] for lay in cfg["layouts"] for p in lay["pages"]}
    for lay, page, slot, key in all_keys(cfg):
        where = f"{lay['name']} / {page['name'] if page else 'buttons'} / {slot}"
        for g in ("tap", "hold", "double"):
            for st in key.get(g) or []:
                if "wait" in st:
                    continue
                p = app.plugins.get(st.get("plugin"))
                ok = p is not None and st.get("action") in p.actions
                if ok and st["plugin"] == "pages" and st["action"] == "open":
                    ok = st.get("params", {}).get("page") in page_ids
                problems += not ok
                print(f"  {where:<34} {g:<6} {st.get('plugin')}.{st.get('action')}  {'ok' if ok else 'PROBLEM'}")
        if key.get("live"):
            lv = key["live"]
            p = app.plugins.get(lv.get("plugin"))
            ok = p is not None and lv.get("widget") in p.widgets
            problems += not ok
            print(f"  {where:<34} live   {lv.get('plugin')}.{lv.get('widget')}  {'ok' if ok else 'PROBLEM'}")
    print("plugins:", ", ".join(app.plugins))
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(prog="vsdpad", description=f"VSD Pad {__version__}")
    ap.add_argument("--gui", action="store_true", help="open the settings page in your browser")
    ap.add_argument("--no-tray", action="store_true", help="no tray icon; run in this console (Ctrl+C quits)")
    ap.add_argument("--data", default=DEFAULT_DATA, help="data folder (config, history, pictures, log)")
    ap.add_argument("--import-from", help="first run only: import this 1.x config.json")
    ap.add_argument("--check", action="store_true", help="check the layout and exit")
    ap.add_argument("--simulate", nargs="?", const="m18", metavar="PADS",
                    help="no hardware: pretend pads, e.g. --simulate or --simulate m18,mk2,xl "
                         "(for trying things out while the real app runs)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args(argv)

    if sys.stdout is None:  # pythonw
        sys.stdout = open(os.devnull, "w")
    from .log import setup
    log = setup(a.data, a.verbose)
    first_run(a.data, a.import_from, log.info)

    offline = a.check or a.simulate
    if not offline and already_running(MUTEX):
        print("VSD Pad is already running (the old or the new version). Quit it from the tray icon first.")
        if a.gui:
            import webbrowser
            webbrowser.open(f"http://127.0.0.1:{a.port}/")
        return 1

    from .app import App
    fake = None
    if a.simulate:
        from .devices.fake import FakeDevice, info_for
        kinds = {"m18": dict(id="m18", model="VSD M18 (simulated)"),
                 "mk2": dict(id="sd-SIMMK2", model="Stream Deck MK.2 (simulated)", buttons=()),
                 "xl": dict(id="sd-SIMXL", model="Stream Deck XL (simulated)", cols=8, rows=4, buttons=()),
                 "neo": dict(id="sd-SIMNEO", model="Stream Deck Neo (simulated)", cols=4, rows=2, buttons=(9, 10))}
        fake = [FakeDevice(info_for(**kinds[k.strip()])) for k in a.simulate.split(",") if k.strip() in kinds]
    app = App(a.data, fake_devices=fake)
    if a.check:
        return 1 if check(app) else 0

    from .server import SettingsServer
    server = SettingsServer(app, a.port)
    url = server.start()
    log.info("VSD Pad %s - settings at %s", __version__, url)
    print("Settings:", url, flush=True)
    app.start()
    if a.gui:
        import webbrowser
        webbrowser.open(url)
    stop = threading.Event()

    def quit_all():
        stop.set()

    icon = None if a.no_tray else tray_icon(url, quit_all)
    try:
        if icon:
            threading.Thread(target=lambda: (stop.wait(), icon.stop()), daemon=True).start()
            icon.run()
        else:
            while not stop.wait(1):
                pass
    except KeyboardInterrupt:
        pass
    log.info("quitting")
    app.stop()
    server.stop()
    return 0
