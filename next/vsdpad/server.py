"""Local settings server (127.0.0.1 only) + JSON API for the settings page.

Security: binds to localhost; checks Host (stops DNS rebinding) and Origin; every /api call needs the
per-run token that is written into the page (a custom header, so other web pages can't call the API);
POST bodies must be JSON and <= 25 MB; plugin secrets never go to the browser.
"""
import base64, hashlib, io, json, os, re, secrets, threading, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image

from . import PKG_DIR, __version__, themes_io
from .actions import safe_run, run_steps
from .navigator import Navigator
from .render import list_themes
from .schema import ConfigError, layout as find_layout

WEB = os.path.join(PKG_DIR, "web")
MAX_BODY = 25_000_000
MAX_IMAGE = 8_000_000


class _Server(ThreadingHTTPServer):
    allow_reuse_address = False  # on Windows this would let two copies share a port
    daemon_threads = True


class SettingsServer:
    def __init__(self, app, port=8765):
        self.app, self.port = app, port
        self.token = secrets.token_urlsafe(24)
        self.httpd = None

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}/"

    def start(self):
        for port in range(self.port, self.port + 11):
            try:
                self.httpd = _Server(("127.0.0.1", port), self._handler())
                self.port = port
                break
            except OSError:
                continue
        else:
            raise OSError("no free port for the settings server")
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
                "themes": list_themes(), "status": app.status(), "version": __version__,
                "history": app.store.history()[-15:]}

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
        nav = self._nav_for(body.get("device"), body.get("layout"))
        # run the first real step inline so the page gets the error message; the rest (after waits) in the background
        if any("wait" in s for s in steps):
            threading.Thread(target=safe_run, args=(self.app, steps, body.get("device"), nav), daemon=True).start()
        else:
            run_steps(self.app, steps, body.get("device"), nav)
        return {"ok": True, "page": nav.current}

    def goto(self, body):
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
        live = self.app.live_content(key, sample_fallback=True)
        _, img = self.app.renderer.render(theme, key, live, slot, cols, rows, dy)
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
        try:
            with Image.open(io.BytesIO(blob)) as im:
                if im.width * im.height > 40_000_000:
                    raise ValueError("That picture is too large.")
                im.seek(0)
                pic = im.convert("RGBA")
        except (OSError, SyntaxError):
            raise ValueError("That file isn't a picture I can read.") from None
        pic.thumbnail((432, 432), Image.LANCZOS)  # keys are 216 px; keep some room for cropping
        out = io.BytesIO()
        pic.save(out, "PNG")
        data = out.getvalue()
        name = hashlib.sha256(data).hexdigest()[:24] + ".png"
        with open(os.path.join(self.app.images_dir, name), "wb") as f:
            f.write(data)
        return {"ref": "img:" + name}

    # ---- HTTP plumbing --------------------------------------------------------------------
    def _handler(self):
        srv = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _allowed_hosts(self):
                p = self.server.server_address[1]
                return {f"127.0.0.1:{p}", f"localhost:{p}"}

            def _guard(self, post):
                if self.headers.get("Host", "") not in self._allowed_hosts():
                    return self._json({"error": "bad host"}, 403)
                origin = self.headers.get("Origin")
                if origin is not None and origin not in {"http://" + h for h in self._allowed_hosts()}:
                    return self._json({"error": "bad origin"}, 403)
                path = urllib.parse.urlparse(self.path).path
                if path.startswith("/api/"):
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
                    elif u.path == "/api/theme_preview.png":
                        self._png(srv.theme_preview(q))
                    elif u.path == "/api/image":
                        path = srv.app.image_path(q.get("ref", [""])[0])
                        if not path:
                            return self._json({"error": "not found"}, 404)
                        with Image.open(path) as im:
                            im = im.convert("RGB")
                            im.thumbnail((216, 216))
                            self._png(im)
                    elif u.path == "/api/export":
                        data = json.dumps(srv.app.store.export(q.get("secrets", ["0"])[0] == "1"), indent=2).encode()
                        self._send(200, "application/json", data,
                                   [("Content-Disposition", 'attachment; filename="vsdpad-layout.json"')])
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
                    if path == "/api/undo":
                        srv.app.store.undo()
                        return self._json({"ok": True})
                    if path == "/api/history/restore":
                        srv.app.store.restore(str(body.get("id", "")))
                        return self._json({"ok": True})
                    if path == "/api/import":
                        srv.app.store.import_(body.get("data"))
                        return self._json({"ok": True})
                    if path == "/api/theme_import":
                        blob = base64.b64decode(str(body.get("data", "")).split(",")[-1])
                        return self._json({"theme": themes_io.import_zip(blob, replace=bool(body.get("replace")))})
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
