"""Local settings server, 127.0.0.1 only. See HANDOFF.md section 5.4.

Security: localhost bind, Host/Origin checks (blocks DNS rebinding and cross-site posts),
JSON-only POSTs, secrets never sent to the browser.
"""
import io, json, os, re, threading, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image

import iconpacks
import pagestate
import plugins
from store import view

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
MAX_BODY = 1_000_000
ACTION_KEYS = {"plugin", "action", "params"}


def secret_keys(plugin):
    return {s["key"] for s in plugin.settings if s.get("secret")}


def public_config(cfg):
    """Config for the browser: plugin secrets removed; returns (cfg, {plugin_id: [secret keys that are set]})."""
    cfg = json.loads(json.dumps(cfg))
    secret_set = {}
    for pid, settings in cfg.get("plugins", {}).items():
        p = plugins.PLUGINS.get(pid)
        if not p:
            continue
        for k in secret_keys(p):
            if settings.get(k):
                secret_set.setdefault(pid, []).append(k)
            settings.pop(k, None)
    return cfg, secret_set


class _Server(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR lets a second process bind the SAME port; refuse that so a
    # second copy picks the next free port instead of silently sharing it.
    allow_reuse_address = False
    daemon_threads = True


class SettingsServer:
    def __init__(self, store, run_action, status, port=8765):
        self.store, self.run_action, self.status = store, run_action, status
        self.port = port
        self.httpd = None

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}/"

    def start(self):
        outer = self
        for port in range(self.port, self.port + 11):
            try:
                self.httpd = _Server(("127.0.0.1", port), self._handler())
                self.port = port
                break
            except OSError:
                continue
        else:
            raise OSError("no free port for the settings server")
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return outer.url

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()

    def _handler(self):
        srv = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            # --- helpers -------------------------------------------------
            def _allowed_hosts(self):
                p = self.server.server_address[1]
                return {f"127.0.0.1:{p}", f"localhost:{p}"}

            def _guard(self, post):
                if self.headers.get("Host", "") not in self._allowed_hosts():
                    self._json({"error": "bad host"}, 403)
                    return False
                if post:
                    origin = self.headers.get("Origin")
                    if origin is not None and origin not in {"http://" + h for h in self._allowed_hosts()}:
                        self._json({"error": "bad origin"}, 403)
                        return False
                    if not self.headers.get("Content-Type", "").startswith("application/json"):
                        self._json({"error": "JSON required"}, 415)
                        return False
                return True

            def _json(self, obj, code=200):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                if n > MAX_BODY:
                    raise ValueError("body too large")
                return json.loads(self.rfile.read(n) or b"{}")

            # --- routes --------------------------------------------------
            def do_GET(self):
                if not self._guard(False):
                    return
                u = urllib.parse.urlparse(self.path)
                try:
                    if u.path == "/":
                        with open(os.path.join(WEB, "index.html"), "rb") as f:
                            data = f.read()
                        self.send_response(200)
                        self.send_header("Content-Type", "text/html; charset=utf-8")
                        self.send_header("Content-Length", str(len(data)))
                        self.send_header("Cache-Control", "no-store")
                        self.end_headers()
                        self.wfile.write(data)
                    elif u.path == "/api/state":
                        cfg, secret_set = public_config(srv.store.get())
                        self._json({"config": cfg, "plugins": plugins.describe_all(),
                                    "packs": iconpacks.list_packs(),
                                    "secret_set": secret_set, "status": self._status()})
                    elif u.path == "/api/status":
                        self._json(self._status())
                    elif u.path == "/api/preview.png":
                        self._preview(urllib.parse.parse_qs(u.query))
                    elif u.path == "/api/pack_preview.png":
                        q = urllib.parse.parse_qs(u.query)
                        cfg = srv.store.get()
                        pg = max(0, min(len(cfg["pages"]) - 1, int(q.get("page", ["0"])[0])))
                        self._png(iconpacks.render_grid(view(cfg, pg), q.get("pack", ["modern"])[0]))
                    else:
                        self._json({"error": "not found"}, 404)
                except Exception as e:
                    self._json({"error": str(e)}, 500)

            def _status(self):
                st = dict(srv.status)
                st["page"] = pagestate.STATE.current  # 0-based page the pad is showing
                return st

            def _preview(self, q):
                color = q.get("color", ["#5a8cff"])[0]
                m = re.fullmatch(r"#?([0-9a-fA-F]{6})", color)
                rgb = tuple(int(m.group(1)[i:i + 2], 16) for i in (0, 2, 4)) if m else (90, 140, 255)
                spec = {"icon": q.get("icon", [""])[0][:4], "label": q.get("label", [""])[0][:24], "color": rgb}
                slot = int(q.get("slot", ["1"])[0])
                if not 1 <= slot <= 15:
                    raise ValueError("slot must be 1-15")
                dy = max(-10, min(10, int(q.get("dy", ["0"])[0])))
                cfg = {"icon_pack": q.get("pack", ["modern"])[0], "keys": {}, "row_offset": [dy, dy, dy]}
                img = iconpacks.render_slot(cfg, slot, spec)
                if img is None:
                    return self._json({"error": "blank key"}, 404)
                self._png(img.resize((72, 72), Image.LANCZOS).resize((144, 144), Image.BICUBIC))

            def _png(self, img):
                buf = io.BytesIO()
                img.save(buf, "PNG")
                data = buf.getvalue()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                if not self._guard(True):
                    return
                path = urllib.parse.urlparse(self.path).path
                try:
                    body = self._body()
                    if path == "/api/config":
                        self._json(self._save_config(body))
                    elif path == "/api/test":
                        self._json(self._test(body))
                    else:
                        m = re.fullmatch(r"/api/plugin/([\w-]+)/(settings|api/([\w-]+))", path)
                        if not m:
                            return self._json({"error": "not found"}, 404)
                        plugin = plugins.PLUGINS.get(m.group(1))
                        if not plugin:
                            return self._json({"error": "unknown plugin"}, 404)
                        if m.group(2) == "settings":
                            self._json(self._save_settings(plugin, body))
                        else:
                            self._json(self._plugin_api(plugin, m.group(3), body))
                except (ValueError, KeyError, TypeError) as e:
                    self._json({"error": str(e)}, 400)
                except Exception as e:
                    self._json({"error": f"{type(e).__name__}: {e}"}, 500)

            def _save_config(self, body):
                def apply(cfg):
                    for k in ("brightness", "rotation", "icon_pack", "row_offset", "round_buttons", "pages", "round"):
                        if k in body:
                            cfg[k] = body[k]
                srv.store.update(apply)  # validates; the pad loop redraws on the version bump
                pagestate.STATE.configure(srv.store.get()["pages"])
                return {"ok": True}

            def _test(self, body):
                action = body.get("action")
                if not isinstance(action, dict) or not ACTION_KEYS >= set(action):
                    raise ValueError("bad action")
                srv.run_action(srv.store, action)
                return {"ok": True}

            def _save_settings(self, plugin, body):
                values = body.get("settings", {})
                allowed = {s["key"] for s in plugin.settings}
                secrets = secret_keys(plugin)

                def apply(cfg):
                    cur = cfg.setdefault("plugins", {}).setdefault(plugin.id, {})
                    for k, v in values.items():
                        if k not in allowed or not isinstance(v, str):
                            continue
                        if k in secrets and v == "":
                            continue  # blank secret = keep the saved one
                        cur[k] = v.strip()
                srv.store.update(apply)
                return {"ok": True}

            def _plugin_api(self, plugin, name, payload):
                result = plugin.api(name, payload, srv.store.plugin_settings(plugin.id))
                if isinstance(result, dict) and "_save" in result:
                    save = result.pop("_save")
                    allowed = {s["key"] for s in plugin.settings}
                    srv.store.update(lambda cfg: cfg.setdefault("plugins", {}).setdefault(plugin.id, {})
                                     .update({k: v for k, v in save.items() if k in allowed}))
                return {"result": result}

        return Handler
