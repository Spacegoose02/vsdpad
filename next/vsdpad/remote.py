"""Phone or tablet remote: a page on your home network that shows the pad's keys and presses them.

Off until you switch it on in Settings. Then a second small web server listens on the network (port 8790). A phone
has to be paired first: the settings page shows a 6-digit code for 5 minutes, the phone types it in once and gets its
own key, which it keeps. Only a fingerprint (SHA-256) of that key is saved, in data/remote.json (not in config.json, so
it's never in a backup or a shared pack). Remove a phone in Settings and its key stops working at once.

The remote can only do what the pad's keys do (press keys, change page or profile). It can't see or change settings.
"""
import collections, hashlib, hmac, io, json, os, secrets, socket, threading, time, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import PKG_DIR, layoutprofiles
from .actions import Gestures
from .log import LOG
from .navigator import Navigator

DEFAULT_PORT = 8790
PAIR_S = 300            # a pairing code works for 5 minutes
PAIR_TRIES = 5          # ... and for 5 wrong guesses at most
MAX_PHONES = 20
HOLD_LIMIT_S = 30       # a key held from the phone is let go after this, even if the phone vanished
MAX_BODY = 10_000
WEB = os.path.join(PKG_DIR, "web")


def lan_addresses():
    """This PC's addresses on the home network (best guess first)."""
    out = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("10.255.255.255", 1))   # no packet is sent; this just picks the network card used
            out.append(s.getsockname()[0])
        finally:
            s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            out.append(info[4][0])
    except OSError:
        pass
    seen, good = set(), []
    for ip in out:
        if ip in seen or ip.startswith(("127.", "169.254.", "0.")):
            continue
        seen.add(ip)
        good.append(ip)
    return good


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


class _Server(ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = True


class Remote:
    def __init__(self, app, port=DEFAULT_PORT):
        self.app, self.default_port = app, port
        self.path = os.path.join(app.data_dir, "remote.json")
        self._lock = threading.RLock()
        self.httpd, self.port, self.problem = None, None, ""
        self.pairing = None          # {"code", "until", "tries"}
        self._virtual = {}           # layout id -> Gestures for a phone-only pad
        self._holds = {}             # (pad, slot) -> Timer that lets go of a key held from the phone
        self._png = collections.OrderedDict()
        self._seen_saved = 0
        self._last_try = 0.0
        self.live_specs = []
        self.state = self._load()

    # ---- saved state -------------------------------------------------------------------------------
    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                st = json.load(f)
            if not isinstance(st, dict):
                raise ValueError
        except (OSError, ValueError):
            st = {}
        phones = [p for p in st.get("phones", []) if isinstance(p, dict) and isinstance(p.get("hash"), str)
                  and isinstance(p.get("id"), str)]
        port = st.get("port")
        return {"enabled": st.get("enabled") is True, "port": port if isinstance(port, int) and 1024 <= port <= 65535 else self.default_port,
                "phones": phones[:MAX_PHONES]}

    def _save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2)
        os.replace(tmp, self.path)

    # ---- on / off ------------------------------------------------------------------------------------
    @property
    def running(self):
        return self.httpd is not None

    def start(self):
        """Start listening if the remote is switched on (called when the app starts)."""
        if self.state["enabled"]:
            self._serve()

    def _serve(self):
        with self._lock:
            if self.httpd:
                return
            want = self.state["port"]
            for port in range(want, want + 10):
                try:
                    self.httpd = _Server(("0.0.0.0", port), self._handler())
                    self.port = port
                    break
                except OSError as e:
                    self.problem = f"Couldn't listen on the network: {e}"
            else:
                LOG.warning("phone remote: %s", self.problem)
                return
            self.problem = ""
            threading.Thread(target=self.httpd.serve_forever, name="remote-http", daemon=True).start()
            LOG.info("phone remote on port %s", self.port)

    def stop(self):
        with self._lock:
            httpd, self.httpd = self.httpd, None
            for t in self._holds.values():
                t.cancel()
            self._holds.clear()
            for g in self._virtual.values():
                g.cancel_all()
        if httpd:
            httpd.shutdown()
            httpd.server_close()

    def set_enabled(self, on):
        with self._lock:
            self.state["enabled"] = bool(on)
            self._save()
        if on:
            self._serve()
        else:
            self.pairing = None
            self.stop()
        return self.info()

    # ---- pairing -------------------------------------------------------------------------------------
    def begin_pairing(self):
        if not self.running:
            raise ValueError("Switch the phone remote on first.")
        if len(self.state["phones"]) >= MAX_PHONES:
            raise ValueError(f"You can pair up to {MAX_PHONES} phones and tablets. Remove one first.")
        self.pairing = {"code": f"{secrets.randbelow(1_000_000):06d}", "until": time.time() + PAIR_S, "tries": 0}
        return self.info()

    def cancel_pairing(self):
        self.pairing = None
        return self.info()

    def pair(self, code, name):
        """Swap the code shown in Settings for this phone's own key. Raises PermissionError on a wrong code."""
        with self._lock:
            now = time.monotonic()
            if now - self._last_try < 1.0:      # one guess a second, whoever is guessing
                raise PermissionError("Too fast: wait a second and try again.")
            self._last_try = now
            p = self.pairing
            if not p or time.time() > p["until"]:
                self.pairing = None
                raise PermissionError("No pairing code is showing. On the PC: Settings, Phone remote, Pair a phone.")
            if not hmac.compare_digest(str(code or "").strip().replace(" ", ""), p["code"]):
                p["tries"] += 1
                if p["tries"] >= PAIR_TRIES:
                    self.pairing = None
                    raise PermissionError("Wrong code too many times. Make a new code on the PC.")
                raise PermissionError("That code isn't right. Check the code on the PC.")
            self.pairing = None
            token = secrets.token_urlsafe(32)
            name = " ".join(str(name or "").split())[:40] or "Phone"
            phone = {"id": secrets.token_hex(4), "name": name, "hash": _hash(token), "added": int(time.time()), "seen": int(time.time())}
            self.state["phones"].append(phone)
            self._save()
            LOG.info("phone remote: paired %s", name)
            return token, phone

    def remove(self, phone_id):
        with self._lock:
            before = len(self.state["phones"])
            self.state["phones"] = [p for p in self.state["phones"] if p["id"] != phone_id]
            if len(self.state["phones"]) == before:
                raise ValueError("That phone isn't paired.")
            self._save()
        return self.info()

    def auth(self, token):
        if not token:
            return None
        h = _hash(token)
        for p in self.state["phones"]:
            if hmac.compare_digest(p["hash"], h):
                now = int(time.time())
                p["seen"] = now
                if now - self._seen_saved > 600:   # remember "last used" now and then, not on every tap
                    self._seen_saved = now
                    with self._lock:
                        self._save()
                return p
        return None

    def info(self):
        """For the settings page."""
        pair = self.pairing if self.pairing and time.time() < self.pairing["until"] else None
        urls = [f"http://{ip}:{self.port}/" for ip in lan_addresses()] if self.running else []
        qr_svg = None
        if pair and urls:   # scanning it with the phone's camera opens the page and pairs in one go
            from . import qr
            qr_svg = qr.svg(f"{urls[0]}#pair={pair['code']}")
        return {"enabled": self.state["enabled"], "running": self.running, "port": self.port or self.state["port"],
                "problem": self.problem, "urls": urls, "qr": qr_svg,
                "pairing": {"code": pair["code"], "left": int(pair["until"] - time.time())} if pair else None,
                "phones": [{k: p.get(k) for k in ("id", "name", "added", "seen")} for p in self.state["phones"]]}

    # ---- the pads a phone can use ----------------------------------------------------------------
    def targets(self):
        """Connected pads first, then a phone-only pad for each set of pages no connected pad is showing."""
        cfg = self.app.store.get()
        out, shown = [], set()
        for r in self.app.runners():
            if not r.connected:
                continue
            lay = r.layout(cfg)
            shown.add(lay["id"])
            out.append({"id": r.info.id, "name": r.dcfg(cfg).get("name", r.info.model), "layout": lay, "nav": r.nav,
                        "gestures": r.gestures, "runner": r, "key_count": r.info.key_count,
                        "buttons": list(r.dcfg(cfg).get("button_order") or r.info.buttons or [])})
        for lay in cfg["layouts"]:
            if lay["id"] in shown:
                continue
            nav = self.app.preview_nav.setdefault(lay["id"], Navigator())
            nav.configure(lay["pages"])
            with self._lock:
                g = self._virtual.get(lay["id"])
                if g is None or g.nav is not nav:
                    lid = lay["id"]
                    g = self._virtual[lid] = Gestures(self.app, None, nav, lambda b, lid=lid: self._virtual_lookup(lid, b))
            names = [d.get("name") for d in cfg["devices"].values() if d.get("layout") == lay["id"]]
            out.append({"id": "v-" + lay["id"], "name": (names[0] if names else lay["name"]) + " (phone only)",
                        "layout": lay, "nav": nav, "gestures": g, "runner": None, "key_count": lay["cols"] * lay["rows"],
                        "buttons": sorted(int(b) for b in lay.get("buttons", {}))})
        return out

    def _virtual_lookup(self, layout_id, button):
        cfg = self.app.store.get()
        lay = next((l for l in cfg["layouts"] if l["id"] == layout_id), None)
        if lay is None:
            return None
        nav = self.app.preview_nav.get(layout_id)
        if button <= lay["cols"] * lay["rows"]:
            idx = nav.current if nav else 0
            return lay["pages"][min(idx, len(lay["pages"]) - 1)]["keys"].get(str(button))
        return lay.get("buttons", {}).get(str(button))

    def _target(self, pad_id):
        ts = self.targets()
        for t in ts:
            if t["id"] == pad_id:
                return t
        if not pad_id and ts:
            return ts[0]
        raise LookupError("That pad isn't connected any more.")

    def _page(self, t):
        pages = t["layout"]["pages"]
        return pages[min(t["nav"].current, len(pages) - 1)]

    def _key_image(self, t, cfg, slot):
        """(signature, image or None) of a key as the pad would show it (without the M18 row nudge)."""
        lay, page = t["layout"], self._page(t)
        dn = self.app.daynight.state(cfg)
        theme = page.get("theme") or dn["theme"] or cfg.get("theme", "modern")
        key = page["keys"].get(str(slot))
        live = self.app.live_content(key)
        return self.app.renderer.render(theme, key, live, slot, lay["cols"], lay["rows"], 0, page.get("backdrop"))

    def pad_state(self, pad_id):
        cfg = self.app.store.get()
        ts = self.targets()
        t = next((x for x in ts if x["id"] == pad_id), ts[0] if ts else None)
        out = {"pads": [{"id": x["id"], "name": x["name"]} for x in ts], "profiles": layoutprofiles.summary(cfg), "pad": None}
        if t is None:
            return out
        lay, page = t["layout"], self._page(t)
        keys, specs = [], []
        for slot in range(1, lay["cols"] * lay["rows"] + 1):
            sig, img = self._key_image(t, cfg, slot)
            keys.append(hashlib.sha1(sig.encode()).hexdigest()[:12] if img is not None else "")
            k = page["keys"].get(str(slot))
            if k and k.get("live"):
                specs.append(k["live"])
        self.live_specs = specs[:40]
        buttons = []
        for b in t["buttons"]:
            k = lay.get("buttons", {}).get(str(b)) or {}
            buttons.append({"id": b, "label": k.get("label") or "", "icon": k.get("icon") if len(k.get("icon") or "") <= 2 else ""})
        out["pad"] = {"id": t["id"], "name": t["name"], "cols": lay["cols"], "rows": lay["rows"], "keys": keys, "buttons": buttons,
                      "page": page["id"], "page_name": page["name"], "folder": bool(page.get("folder")),
                      "pages": [{"id": p["id"], "name": p["name"]} for p in lay["pages"] if not p.get("folder")]}
        return out

    def key_png(self, pad_id, slot):
        from PIL import Image
        cfg = self.app.store.get()
        t = self._target(pad_id)
        if not 1 <= slot <= t["layout"]["cols"] * t["layout"]["rows"]:
            raise ValueError("bad key")
        sig, img = self._key_image(t, cfg, slot)
        with self._lock:
            if sig in self._png:
                return self._png[sig]
        if img is None:
            img = Image.new("RGB", (216, 216), (0, 0, 0))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "PNG")
        data = buf.getvalue()
        with self._lock:
            self._png[sig] = data
            while len(self._png) > 200:
                self._png.popitem(last=False)
        return data

    # ---- pressing ----------------------------------------------------------------------------------
    def press(self, pad_id, button, down):
        t = self._target(pad_id)
        button = int(button)
        if not (1 <= button <= t["key_count"] or button in t["buttons"]):
            raise ValueError("bad key")
        r = t["runner"]
        if r is not None:   # counts as using the pad: wakes it if it dimmed
            r.last_input = time.monotonic()
            r.dimmed = False
        hold_key = (t["id"], button)
        with self._lock:
            old = self._holds.pop(hold_key, None)
        if old:
            old.cancel()
        if down:
            t["gestures"].press(button)
            timer = threading.Timer(HOLD_LIMIT_S, self._let_go, args=(t["gestures"], hold_key, button))
            timer.daemon = True
            with self._lock:
                self._holds[hold_key] = timer
            timer.start()
        else:
            t["gestures"].release(button)

    def _let_go(self, gestures, hold_key, button):
        with self._lock:
            self._holds.pop(hold_key, None)
        gestures.release(button)

    def goto(self, pad_id, page_id):
        t = self._target(pad_id)
        t["nav"].goto_id(str(page_id))

    # ---- HTTP ----------------------------------------------------------------------------------------
    def _handler(self):
        remote = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, ctype, data, extra=()):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                for k, v in extra:
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

            def _json(self, obj, code=200):
                self._send(code, "application/json", json.dumps(obj).encode())

            def _phone(self):
                return remote.auth(self.headers.get("X-Remote-Token", ""))

            def do_GET(self):
                u = urllib.parse.urlparse(self.path)
                q = urllib.parse.parse_qs(u.query)
                try:
                    if u.path == "/":
                        with open(os.path.join(WEB, "remote.html"), "rb") as f:
                            page = f.read()
                        return self._send(200, "text/html; charset=utf-8", page, [
                            ("Content-Security-Policy", "default-src 'self'; img-src 'self' blob: data:; style-src 'unsafe-inline'; "
                             "script-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'")])
                    if u.path == "/icon.png":
                        from .main import app_icon
                        buf = io.BytesIO()
                        app_icon(180).save(buf, "PNG")
                        return self._send(200, "image/png", buf.getvalue())
                    if not u.path.startswith("/r/"):
                        return self._json({"error": "not found"}, 404)
                    if not self._phone():
                        return self._json({"error": "unpaired"}, 401)
                    if u.path == "/r/state":
                        return self._json(remote.pad_state(q.get("pad", [""])[0]))
                    if u.path == "/r/key":
                        return self._send(200, "image/png", remote.key_png(q.get("pad", [""])[0], int(q.get("slot", ["0"])[0])))
                    return self._json({"error": "not found"}, 404)
                except LookupError as e:
                    self._json({"error": str(e)}, 404)
                except ValueError as e:
                    self._json({"error": str(e)}, 400)
                except Exception as e:
                    LOG.exception("phone remote")
                    self._json({"error": f"{type(e).__name__}: {e}"}, 500)

            def do_POST(self):
                u = urllib.parse.urlparse(self.path)
                try:
                    if not self.headers.get("Content-Type", "").startswith("application/json"):
                        return self._json({"error": "JSON required"}, 415)
                    n = int(self.headers.get("Content-Length") or 0)
                    if n > MAX_BODY:
                        return self._json({"error": "too big"}, 413)
                    body = json.loads(self.rfile.read(n) or b"{}")
                    if not isinstance(body, dict):
                        raise ValueError("expected a JSON object")
                    if u.path == "/r/pair":
                        try:
                            token, phone = remote.pair(body.get("code"), body.get("name"))
                        except PermissionError as e:
                            return self._json({"error": str(e)}, 403)
                        return self._json({"token": token, "name": phone["name"]})
                    if not self._phone():
                        return self._json({"error": "unpaired"}, 401)
                    if u.path == "/r/press":
                        remote.press(str(body.get("pad", "")), body.get("key"), body.get("down") is not False)
                        return self._json({"ok": True})
                    if u.path == "/r/page":
                        remote.goto(str(body.get("pad", "")), body.get("page"))
                        return self._json({"ok": True})
                    if u.path == "/r/profile":
                        return self._json({"ok": True, "name": layoutprofiles.switch_now(remote.app, body.get("profile"))})
                    return self._json({"error": "not found"}, 404)
                except LookupError as e:
                    self._json({"error": str(e)}, 404)
                except (ValueError, TypeError) as e:
                    self._json({"error": str(e)}, 400)
                except Exception as e:
                    LOG.exception("phone remote")
                    self._json({"error": f"{type(e).__name__}: {e}"}, 500)

        return Handler
