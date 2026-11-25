"""Settings server + JSON API for the settings page.

Security: binds to localhost; checks Host (stops DNS rebinding) and Origin; every /api call needs the
per-run token that is written into the page (a custom header, so other web pages can't call the API);
POST bodies must be JSON and <= 25 MB; plugin secrets never go to the browser.

As the server (run.py --server, see sync.py) it listens on the network instead, and every request needs the
server password (HTTP Basic). Host can't be checked there (any name or address may reach it), so Origin must
match Host. The PCs that follow it use the /sync/ endpoints with the same password.
"""
import base64, hashlib, html, io, json, os, re, secrets, shutil, sys, threading, time, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image

from . import PKG_DIR, __version__, autostart, iconpacks, layoutprofiles, sharepack, sync, themes_io
from .actions import safe_run, run_steps
from .navigator import Navigator
from .render import list_themes, load_theme
from .schema import ConfigError, layout as find_layout
from .update_mirror import ASSET_PATH, MirrorError, UpdateMirror

WEB = os.path.join(PKG_DIR, "web")
MAX_BODY = 25_000_000
MAX_IMAGE = 8_000_000
MAX_FRAMES = 120        # frames kept from an animated GIF
SESSION_COOKIE = "vsdpad_session"
SESSION_DAYS = 90       # signed in on the server for this long (per browser)
# what a PC that follows a server must not change by itself (the next sync would undo it): edit on the server
MANAGED_PATHS = {"/api/config", "/api/upload", "/api/undo", "/api/history/restore", "/api/import", "/api/profile",
                 "/api/pack_import", "/api/theme_import", "/api/iconpack_import", "/api/iconpack_drawn", "/api/iconpack_delete"}


class _Server(ThreadingHTTPServer):
    allow_reuse_address = sys.platform != "win32"  # on Windows this would let two copies share a port; elsewhere it
                                                   # only lets a restarted server (docker restart) take its port back
    daemon_threads = True


class SettingsServer:
    def __init__(self, app, port=8765, host="127.0.0.1", password=None, user="admin"):
        """host other than 127.0.0.1 = the server for the whole network (needs a password)."""
        self.app, self.port, self.host = app, port, host
        self.public = host not in ("127.0.0.1", "localhost")
        if self.public and not password:
            raise ValueError("a server on the network needs a password (set VSDPAD_PASSWORD)")
        self.password, self.user = password, (user or "admin").strip()
        self.sessions = Sessions(os.path.join(app.data_dir, "sessions.json")) if self.public else None
        self.token = secrets.token_urlsafe(24)
        self.httpd = None
        self._mirror = None

    def update_mirror(self):
        """Hands GitHub's newest Windows build to the PCs (token: VSDPAD_GITHUB_TOKEN or the server's Updates plugin)."""
        if self._mirror is None:
            def token():
                try:
                    return self.app.store.plugin_settings("updates").get("token")
                except Exception:
                    return None
            self._mirror = UpdateMirror(self.app.data_dir, token)
        return self._mirror

    @property
    def url(self):
        if self.public:
            from .remote import lan_addresses
            return f"http://{(lan_addresses() or [socket_name()])[0]}:{self.port}/"
        return f"http://127.0.0.1:{self.port}/"

    def start(self):
        ports = [self.port] if self.public else range(self.port, self.port + 11)  # the server keeps its port: PCs know it
        for port in ports:
            try:
                self.httpd = _Server((self.host, port), self._handler())
                self.port = port
                break
            except OSError:
                continue
        else:
            raise OSError(f"port {self.port} is in use" if self.public else "no free port for the settings server")
        threading.Thread(target=self.httpd.serve_forever, name="http", daemon=True).start()
        return self.url

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()

    # ---- API implementation (kept outside the handler so tests can call it) ---------------
    def state(self):
        app = self.app
        cfg, secret_set = app.store.strip_secrets(app.store.get())
        return {"config": cfg, "secret_set": secret_set, "plugins": [p.describe() for p in app.plugins.values()],
                "themes": list_themes(), "iconpacks": iconpacks.list_packs(),
                "icon_theme_packs": {t["id"]: iconpacks.theme_pack(load_theme(t["id"])) for t in list_themes()}, "status": app.status(), "version": __version__,
                "history": app.store.history()[-15:], "daynight": app.daynight.info(app.store.get()),
                "mode": "server" if app.hub else ("follower" if app.follower else "local"),
                "managed_by": app.follower.url if app.follower else None}

    def save_config(self, body):
        new = body.get("config")
        if not isinstance(new, dict):
            raise ConfigError("missing config")

        def apply(cfg):
            plugins = cfg.get("plugins", {})  # settings (and secrets) are only changed through the plugin cards
            cfg.clear()
            cfg.update(new)
            cfg["plugins"] = plugins
        self.app.store.update(apply)
        return {"ok": True, "version": self.app.store.version}

    def _nav_for(self, device_id, layout_id):
        r = self.app.runner(device_id) if device_id else None
        if r is not None:
            r.nav.configure(r.layout(self.app.store.get())["pages"])  # may be a moment ahead of the device loop
            return r.nav
        cfg = self.app.store.get()
        lay = find_layout(cfg, layout_id)
        nav = self.app.preview_nav.setdefault(lay["id"], Navigator())
        nav.configure(lay["pages"])
        return nav

    def run(self, body):
        steps = body.get("steps")
        if not isinstance(steps, list) or len(steps) > 20:
            raise ValueError("bad steps")
        for s in steps:
            if not isinstance(s, dict):
                raise ValueError("bad step")
        if self.app.hub and self.app.hub.send(body.get("device"), {"do": "run", "steps": steps, "device": body.get("device"),
                                                                    "layout": body.get("layout")}):
            return {"ok": True, "page": None, "sent": True}   # pressed on the PC with the pad, like a real press
        nav = self._nav_for(body.get("device"), body.get("layout"))
        # run the first real step inline so the page gets the error message; the rest (after waits) in the background
        if any("wait" in s for s in steps):
            threading.Thread(target=safe_run, args=(self.app, steps, body.get("device"), nav), daemon=True).start()
        else:
            run_steps(self.app, steps, body.get("device"), nav)
        return {"ok": True, "page": nav.current}

    def goto(self, body):
        if self.app.hub and self.app.hub.send(body.get("device"), {"do": "goto", "device": body.get("device"),
                                                                    "layout": body.get("layout"), "page": str(body.get("page", ""))}):
            return {"ok": True}
        nav = self._nav_for(body.get("device"), body.get("layout"))
        nav.goto_id(str(body.get("page", "")))
        return {"ok": True}

    def preview(self, body):
        key = body.get("key") if isinstance(body.get("key"), dict) else {}
        cols, rows = int(body.get("cols", 5)), int(body.get("rows", 3))
        slot = int(body.get("slot", 1))
        if not (1 <= cols <= 8 and 1 <= rows <= 4 and 1 <= slot <= cols * rows):
            raise ValueError("bad slot")
        dy = max(-10, min(10, int(body.get("dy", 0))))
        theme = str(body.get("theme") or "modern")
        backdrop = body.get("backdrop") if isinstance(body.get("backdrop"), str) else None
        live = self.app.live_content(key, sample_fallback=True)
        _, img = self.app.renderer.render(theme, key, live, slot, cols, rows, dy, backdrop)
        if img is None:
            return None
        return img.resize((144, 144), Image.LANCZOS)

    def theme_preview(self, q):
        cfg = self.app.store.get()
        lay = find_layout(cfg, q.get("layout", ["main"])[0])
        pi = max(0, min(len(lay["pages"]) - 1, int(q.get("page", ["0"])[0])))
        keys = lay["pages"][pi]["keys"]
        live = {s: self.app.live_content(k, True) for s, k in keys.items() if k.get("live")}
        return self.app.renderer.grid(q.get("theme", ["modern"])[0], keys, lay["cols"], lay["rows"], live)

    def autostart(self, body=None):
        """Start with Windows: GET = status, POST {"on": true/false} = switch it."""
        from .main import DEFAULT_DATA
        moved = []
        if body is not None:
            if body.get("on"):
                moved = autostart.enable(self.app.data_dir, DEFAULT_DATA)
            else:
                autostart.disable()
        return {**autostart.status(self.app.data_dir, DEFAULT_DATA), "moved": moved}

    def save_settings(self, plugin, body):
        values = body.get("settings", {})
        allowed = {s["key"] for s in plugin.settings}
        secret_keys = plugin.secret_keys()

        def apply(cfg):
            cur = cfg.setdefault("plugins", {}).setdefault(plugin.id, {})
            for k, v in values.items():
                if k not in allowed or not isinstance(v, str):
                    continue
                if k in secret_keys and v == "":
                    continue  # blank = keep the saved secret
                cur[k] = v.strip()
        self.app.store.update(apply, snapshot=False)
        return {"ok": True}

    def plugin_api(self, plugin, name, payload):
        from .actions import Context
        ctx = Context(self.app, settings=self.app.store.plugin_settings(plugin.id))
        result = plugin.api(name, payload if isinstance(payload, dict) else {}, ctx)
        if isinstance(result, dict) and "_save" in result:
            if self.app.follower:
                raise ConfigError(f"This PC gets its settings from your server. Do this in the settings page at {self.app.follower.url}")
            save = result.pop("_save")
            allowed = {s["key"] for s in plugin.settings}
            self.app.store.update(lambda cfg: cfg.setdefault("plugins", {}).setdefault(plugin.id, {})
                                  .update({k: v for k, v in save.items() if k in allowed}), snapshot=False)
        return {"result": result}

    def upload(self, body):
        try:
            blob = base64.b64decode(str(body.get("data", "")).split(",")[-1], validate=True)
        except ValueError:
            raise ValueError("That file didn't arrive in one piece.") from None
        if len(blob) > MAX_IMAGE:
            raise ValueError("That picture is too big (8 MB max).")
        backdrop = body.get("purpose") == "backdrop"  # a photo spread across a whole page: keep it big
        frames, durations = [], []
        try:
            with Image.open(io.BytesIO(blob)) as im:
                if im.width * im.height > 40_000_000:
                    raise ValueError("That picture is too large.")
                n = 1 if backdrop else min(getattr(im, "n_frames", 1), MAX_FRAMES)
                for i in range(n):  # an animated GIF keeps its frames (re-encoded, so only pictures get through)
                    im.seek(i)
                    frames.append(im.convert("RGBA"))
                    durations.append(int(im.info.get("duration") or 100))
        except (OSError, SyntaxError, EOFError):
            if not frames:
                raise ValueError("That file isn't a picture I can read.") from None
        for f in frames:
            f.thumbnail((2064, 1200) if backdrop else (288, 288), Image.LANCZOS)  # keys are 216 px
        out = io.BytesIO()
        if len(frames) > 1:
            frames[0].save(out, "GIF", save_all=True, append_images=frames[1:], duration=durations, loop=0, disposal=2)
            ext = ".gif"
        else:
            frames[0].save(out, "PNG")
            ext = ".png"
        data = out.getvalue()
        name = hashlib.sha256(data).hexdigest()[:24] + ext
        with open(os.path.join(self.app.images_dir, name), "wb") as f:
            f.write(data)
        return {"ref": "img:" + name}

    # ---- this PC and the VSD Pad server (Settings -> VSD Pad server) -------------------------
    def server_link(self):
        app = self.app
        if app.hub:
            return {"mode": "server", "name": sync.server_name(), "user": self.user, "pcs": app.status().get("agents", [])}
        f = app.follower
        if not f:
            return {"mode": "local"}
        return {"mode": "follower", **f.info()}

    def server_action(self, body):
        do = body.get("do")
        if self.app.hub:
            raise ValueError("This is the server.")
        if do == "find":
            return {"found": sync.discover()}
        if do == "connect":
            pw = str(body.get("password", ""))
            if not pw and self.app.follower:   # same server, keep the saved password
                pw = self.app.follower.client.password
            if not pw:
                raise ValueError("Type the server's password.")
            try:
                sync.connect(self.app, str(body.get("url", "")), pw, self, upload=bool(body.get("upload")))
            except OSError as e:
                raise ValueError(f"Couldn't connect: {e}") from None
            return self.server_link()
        if do == "disconnect":
            sync.disconnect(self.app)
            return self.server_link()
        raise ValueError("unknown server action")

    # ---- HTTP plumbing --------------------------------------------------------------------
    def _handler(self):
        srv = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _allowed_hosts(self):
                p = self.server.server_address[1]
                return {f"127.0.0.1:{p}", f"localhost:{p}"}

            def _cookie(self):
                for part in self.headers.get("Cookie", "").split(";"):
                    k, _, v = part.strip().partition("=")
                    if k == SESSION_COOKIE:
                        return v
                return ""

            def _password_ok(self):
                auth = self.headers.get("Authorization", "")
                if auth.startswith("Basic "):
                    try:
                        given = base64.b64decode(auth[6:], validate=True).decode("utf-8").partition(":")[2]
                    except ValueError:
                        given = ""
                    if secrets.compare_digest(given.encode(), srv.password.encode()):
                        return True
                    time.sleep(0.5)   # slow down guessing
                return False

            def _guard(self, post):
                path = urllib.parse.urlparse(self.path).path
                if srv.public:
                    if path == "/healthz":
                        return True
                    host = self.headers.get("Host", "")
                    origin = self.headers.get("Origin")
                    if origin is not None and origin not in ("http://" + host, "https://" + host):
                        return self._json({"error": "bad origin"}, 403)
                    if path in ("/login", "/logout", "/favicon.ico"):
                        pass   # the sign-in page itself
                    elif not (srv.sessions.check(self._cookie()) or self._password_ok()):
                        if path.startswith("/sync/"):   # a PC: user name + password (HTTP Basic)
                            self._send(401, "application/json", b'{"error": "password needed"}',
                                       [("WWW-Authenticate", 'Basic realm="VSD Pad", charset="UTF-8"')])
                        elif path.startswith("/api/"):
                            self._json({"error": "Please sign in again (reload the page)."}, 401)
                        else:                            # a browser: the sign-in page
                            self._send(303, "text/plain", b"", [("Location", "/login")])
                        return False
                else:
                    if self.headers.get("Host", "") not in self._allowed_hosts():
                        return self._json({"error": "bad host"}, 403)
                    origin = self.headers.get("Origin")
                    if origin is not None and origin not in {"http://" + h for h in self._allowed_hosts()}:
                        return self._json({"error": "bad origin"}, 403)
                if path.startswith("/sync/"):
                    if not srv.app.hub:
                        return self._json({"error": "not found"}, 404)
                elif path.startswith("/api/"):
                    tok = self.headers.get("X-VSDPad-Token") or urllib.parse.parse_qs(
                        urllib.parse.urlparse(self.path).query).get("t", [""])[0]
                    if not secrets.compare_digest(tok, srv.token):
                        return self._json({"error": "Please reload the settings page."}, 401)
                if post and not self.headers.get("Content-Type", "").startswith("application/json"):
                    return self._json({"error": "JSON required"}, 415)
                return True

            def _send(self, code, ctype, data, extra=()):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                for k, v in extra:
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

            def _json(self, obj, code=200):
                self._send(code, "application/json", json.dumps(obj).encode())
                return False

            def _png(self, img):
                buf = io.BytesIO()
                img.save(buf, "PNG")
                self._send(200, "image/png", buf.getvalue())

            # ---- updates for the PCs that follow this server (see update_mirror.py) ----------
            def _update_latest(self):
                try:
                    return self._json(srv.update_mirror().latest())
                except MirrorError as e:
                    return self._json({"error": str(e)}, e.status)

            def _update_asset(self, name):
                try:
                    path = srv.update_mirror().asset_path(name)
                except MirrorError as e:
                    return self._json({"error": str(e)}, e.status)
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(os.path.getsize(path)))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                with open(path, "rb") as f:
                    shutil.copyfileobj(f, self.wfile, 1 << 16)
                return False

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                if n > MAX_BODY:
                    raise ValueError("That's too much data in one go.")
                return json.loads(self.rfile.read(n) or b"{}")

            def do_GET(self):
                if self._guard(False) is not True:
                    return
                u = urllib.parse.urlparse(self.path)
                q = urllib.parse.parse_qs(u.query)
                try:
                    if u.path == "/healthz":   # also how PCs find the server on the home network (sync.discover)
                        if srv.app.hub:
                            return self._json({"app": "vsdpad-server", "name": sync.server_name(), "version": __version__})
                        return self._send(200, "text/plain", b"ok")
                    if u.path == "/login":
                        if srv.sessions.check(self._cookie()):
                            return self._send(303, "text/plain", b"", [("Location", "/")])
                        with open(os.path.join(WEB, "login.html"), "rb") as f:
                            page = f.read().replace(b"__VSDPAD_SERVER__", html.escape(sync.server_name()).encode())
                        return self._send(200, "text/html; charset=utf-8", page,
                                          [("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; "
                                            "script-src 'unsafe-inline'; frame-ancestors 'none'")])
                    if u.path == "/sync/hello":
                        return self._json({"ok": True, "version": __version__, "name": sync.server_name()})
                    if u.path == "/sync/updates/latest":
                        return self._update_latest()
                    if u.path.startswith(ASSET_PATH):
                        return self._update_asset(u.path[len(ASSET_PATH):])
                    if u.path == "/sync/file":
                        data = srv.app.hub.file(q.get("root", [""])[0], q.get("path", [""])[0])
                        if data is None:
                            return self._json({"error": "not found"}, 404)
                        return self._send(200, "application/octet-stream", data)
                    if u.path == "/":
                        with open(os.path.join(WEB, "index.html"), "rb") as f:
                            page = f.read().replace(b"__VSDPAD_TOKEN__", srv.token.encode())
                        self._send(200, "text/html; charset=utf-8", page,
                                   [("Content-Security-Policy", "default-src 'self'; img-src 'self' data: blob:; "
                                     "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; frame-ancestors 'none'")])
                    elif u.path == "/api/state":
                        self._json(srv.state())
                    elif u.path == "/api/status":
                        self._json(srv.app.status())
                    elif u.path == "/api/autostart":
                        self._json(srv.autostart())
                    elif u.path == "/api/theme_preview.png":
                        self._png(srv.theme_preview(q))
                    elif u.path == "/api/image":
                        path = srv.app.image_path(q.get("ref", [""])[0])
                        if not path:
                            return self._json({"error": "not found"}, 404)
                        if path.lower().endswith(".gif"):  # animated keys play in the settings page too
                            with open(path, "rb") as f:
                                return self._send(200, "image/gif", f.read())
                        with Image.open(path) as im:
                            im = im.convert("RGB")
                            im.thumbnail((216, 216))
                            self._png(im)
                    elif u.path == "/api/export":
                        data = json.dumps(srv.app.store.export(q.get("secrets", ["0"])[0] == "1"), indent=2).encode()
                        self._send(200, "application/json", data,
                                   [("Content-Disposition", 'attachment; filename="vsdpad-layout.json"')])
                    elif u.path == "/api/iconpacks":
                        self._json({"packs": iconpacks.list_packs()})
                    elif u.path == "/api/iconpack":
                        self._json(iconpacks.index(q.get("id", [""])[0]))
                    elif u.path == "/api/iconpack_sprite.png":
                        self._send(200, "image/png", iconpacks.sprite(q.get("id", [""])[0]))
                    elif u.path == "/api/iconpack_svg":  # only ever shown through <img>, which doesn't run scripts
                        self._send(200, "image/svg+xml", iconpacks.pending_svg(q.get("id", [""])[0], q.get("name", [""])[0]),
                                   [("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; sandbox")])
                    elif u.path == "/api/iconpack_export":
                        pid = q.get("id", [""])[0]
                        self._send(200, "application/zip", iconpacks.export_zip(pid),
                                   [("Content-Disposition", f'attachment; filename="{pid}-icons.zip"')])
                    elif u.path == "/api/server":            # this PC <-> the VSD Pad server (sync.py)
                        self._json(srv.server_link())
                    elif u.path == "/api/remote":            # phone / tablet remote (remote.py)
                        self._json(srv.app.remote.info())
                    elif u.path == "/api/pack_choices":      # sharing packs (sharepack.py)
                        self._json(sharepack.choices(srv.app))
                    elif u.path == "/api/pack_export":
                        prof = q.get("profile", [None])[0]
                        split = lambda k: [x for x in q.get(k, [""])[0].split(",") if x]  # noqa: E731
                        name = q.get("name", [""])[0][:60]
                        blob = sharepack.export(srv.app, prof, split("themes"), split("icons"), name)
                        fname = sharepack.file_name(name or (layoutprofiles.find(srv.app.store.get(), prof)["name"] if prof else "vsdpad"))
                        self._send(200, "application/zip", blob, [("Content-Disposition", f'attachment; filename="{fname}"')])
                    elif u.path == "/api/theme_export":
                        tid = q.get("theme", [""])[0]
                        self._send(200, "application/zip", themes_io.export_zip(tid),
                                   [("Content-Disposition", f'attachment; filename="{tid}.zip"')])
                    else:
                        self._json({"error": "not found"}, 404)
                except (ValueError, KeyError) as e:
                    self._json({"error": str(e)}, 400)
                except Exception as e:
                    self._json({"error": f"{type(e).__name__}: {e}"}, 500)

            def do_POST(self):
                if self._guard(True) is not True:
                    return
                path = urllib.parse.urlparse(self.path).path
                try:
                    body = self._body()
                    if not isinstance(body, dict):
                        raise ValueError("expected a JSON object")
                    if srv.app.follower and (path in MANAGED_PATHS or re.fullmatch(r"/api/plugin/[\w-]+/settings", path)):
                        return self._json({"error": "This PC gets its layout from your server. Make changes in the settings page at "
                                                    f"{srv.app.follower.url}"}, 409)
                    if path == "/login" and srv.public:
                        user, pw = str(body.get("user", "")).strip(), str(body.get("password", ""))
                        if not (secrets.compare_digest(user.lower().encode(), srv.user.lower().encode())
                                and secrets.compare_digest(pw.encode(), srv.password.encode())):
                            time.sleep(0.7)   # slow down guessing
                            return self._json({"error": "That user name or password isn't right."}, 401)
                        tok = srv.sessions.new()
                        return self._send(200, "application/json", b'{"ok": true}', [("Set-Cookie",
                            f"{SESSION_COOKIE}={tok}; Path=/; HttpOnly; SameSite=Strict; Max-Age={SESSION_DAYS * 86400}")])
                    if path == "/logout" and srv.public:
                        srv.sessions.drop(self._cookie())
                        return self._send(200, "application/json", b'{"ok": true}',
                                          [("Set-Cookie", f"{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0")])
                    if path == "/api/server":
                        return self._json(srv.server_action(body))
                    if path == "/sync/poll":
                        return self._json(srv.app.hub.poll(body, self.client_address[0]))
                    if path == "/sync/status":
                        return self._json(srv.app.hub.report(body, self.client_address[0]))
                    if path == "/sync/file":
                        return self._json(srv.app.hub.receive_file(body))
                    if path == "/sync/upload":
                        return self._json(srv.app.hub.upload(body))
                    if path == "/api/config":
                        return self._json(srv.save_config(body))
                    if path == "/api/run":
                        return self._json(srv.run(body))
                    if path == "/api/goto":
                        return self._json(srv.goto(body))
                    if path == "/api/preview":
                        img = srv.preview(body)
                        return self._png(img) if img is not None else self._send(204, "image/png", b"")
                    if path == "/api/upload":
                        return self._json(srv.upload(body))
                    if path == "/api/autostart":
                        return self._json(srv.autostart(body))
                    if path == "/api/undo":
                        srv.app.store.undo()
                        return self._json({"ok": True})
                    if path == "/api/history/restore":
                        srv.app.store.restore(str(body.get("id", "")))
                        return self._json({"ok": True})
                    if path == "/api/import":
                        srv.app.store.import_(body.get("data"))
                        return self._json({"ok": True})
                    if path == "/api/profile":           # profiles: switch / add / rename / delete / move
                        return self._json({"ok": True, **layoutprofiles.settings_api(srv.app, body)})
                    if path == "/api/remote":
                        rm, do = srv.app.remote, body.get("do")
                        acts = {"on": lambda: rm.set_enabled(True), "off": lambda: rm.set_enabled(False), "pair": rm.begin_pairing,
                                "cancel": rm.cancel_pairing, "remove": lambda: rm.remove(str(body.get("id", "")))}
                        if do not in acts:
                            raise ValueError("unknown remote action")
                        return self._json(acts[do]())
                    if path in ("/api/pack_inspect", "/api/pack_import"):
                        blob = base64.b64decode(str(body.get("data", "")).split(",")[-1])
                        if path == "/api/pack_inspect":
                            return self._json(sharepack.inspect(srv.app, blob))
                        return self._json(sharepack.install(srv.app, blob, profile=body.get("profile", True) is not False,
                                                            use_now=bool(body.get("use_now")), replace=bool(body.get("replace"))))
                    if path == "/api/theme_import":
                        blob = base64.b64decode(str(body.get("data", "")).split(",")[-1])
                        return self._json({"theme": themes_io.import_zip(blob, replace=bool(body.get("replace")))})
                    if path == "/api/iconpack_import":
                        blob = base64.b64decode(str(body.get("data", "")).split(",")[-1])
                        return self._json(iconpacks.import_zip(blob, name=body.get("name") or None, tint=body.get("tint", True) is not False,
                                                               replace=bool(body.get("replace"))))
                    if path == "/api/iconpack_drawn":
                        blob = base64.b64decode(str(body.get("data", "")).split(",")[-1])
                        return self._json(iconpacks.add_drawn(str(body.get("id", "")), str(body.get("name", "")), blob))
                    if path == "/api/iconpack_delete":
                        iconpacks.delete(str(body.get("id", "")))
                        return self._json({"ok": True})
                    if path == "/api/record":   # record a macro: start / status / stop (-> steps)
                        from .recorder import RECORDER
                        do = body.get("do")
                        try:
                            if do == "start":
                                RECORDER.start()
                                return self._json({"ok": True, **RECORDER.status()})
                            if do == "stop":
                                return self._json({"ok": True, "steps": RECORDER.stop()})
                            return self._json(RECORDER.status())
                        except OSError as e:
                            return self._json({"error": str(e)}, 400)
                    if path == "/api/live/watch":
                        specs = [s for s in body.get("specs", []) if isinstance(s, dict) and isinstance(s.get("plugin"), str)]
                        srv.app.watch_preview(specs)
                        return self._json({"ok": True})
                    m = re.fullmatch(r"/api/plugin/([\w-]+)/(settings|api/([\w-]+))", path)
                    if not m:
                        return self._json({"error": "not found"}, 404)
                    plugin = srv.app.plugins.get(m.group(1))
                    if not plugin:
                        return self._json({"error": "unknown plugin"}, 404)
                    if m.group(2) == "settings":
                        return self._json(srv.save_settings(plugin, body))
                    return self._json(srv.plugin_api(plugin, m.group(3), body))
                except (ValueError, KeyError, TypeError) as e:
                    self._json({"error": str(e)}, 400)
                except Exception as e:
                    self._json({"error": f"{type(e).__name__}: {e}"}, 500)

        return Handler


def socket_name():
    import socket
    return socket.gethostname() or "localhost"


class Sessions:
    """Browsers signed in to the server. Only a fingerprint of each cookie is kept (data/sessions.json), so a
    restarted container doesn't sign everyone out and the file is useless to anyone who reads it."""

    def __init__(self, path):
        self.path, self._lock = path, threading.Lock()
        try:
            with open(path, encoding="utf-8") as f:
                self._s = {k: float(v) for k, v in json.load(f).items()}
        except (OSError, ValueError, AttributeError):
            self._s = {}

    @staticmethod
    def _fp(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def _save(self):
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._s, f)
            os.replace(tmp, self.path)
        except OSError:
            pass

    def new(self):
        tok = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock:
            self._s = {k: v for k, v in self._s.items() if v > now}
            if len(self._s) >= 200:   # keep the newest
                self._s = dict(sorted(self._s.items(), key=lambda kv: kv[1])[-199:])
            self._s[self._fp(tok)] = now + SESSION_DAYS * 86400
            self._save()
        return tok

    def check(self, token):
        if not token or len(token) > 100:
            return False
        with self._lock:
            return self._s.get(self._fp(token), 0) > time.time()

    def drop(self, token):
        with self._lock:
            if self._s.pop(self._fp(token or ""), None) is not None:
                self._save()
