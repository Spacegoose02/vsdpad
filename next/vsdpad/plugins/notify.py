"""Notification keys: a key lights up when an app on this PC has notifications (Discord, Outlook, WhatsApp...)
or when you have unread email.

App notifications read Windows' own notification list (what's in the notification centre), so they clear by
themselves when you dismiss them in Windows, or with a "Clear" key. Windows asks once whether VSD Pad may read
notifications (Settings → Privacy & security → Notifications). Needs the small "winrt" add-ons:
    pip install winrt-runtime winrt-Windows.Foundation winrt-Windows.Foundation.Collections
                winrt-Windows.UI.Notifications winrt-Windows.UI.Notifications.Management winrt-Windows.ApplicationModel
Email uses IMAP (Gmail, Outlook.com, iCloud, most providers); Gmail needs an app password.
"""
import asyncio, imaplib, re, socket, sys, threading, time

from .base import Plugin

APP = {"key": "app", "label": "App", "kind": "choice", "api": "apps",
       "help": "The app whose notifications light this key. Apps show here once they've sent a notification."}
FOLDER = {"key": "folder", "label": "Folder", "placeholder": "INBOX", "kind": "text"}
CACHE_S = 1.0
INSTALL = ("App notifications need a small add-on: run  pip install winrt-runtime winrt-Windows.Foundation "
           "winrt-Windows.Foundation.Collections winrt-Windows.UI.Notifications "
           "winrt-Windows.UI.Notifications.Management winrt-Windows.ApplicationModel")
DENIED = ("Windows isn't letting VSD Pad read notifications. Turn on Settings → Privacy & security → Notifications → "
          "“Allow apps to access your notifications”.")

_lock = threading.Lock()
_cache = {}


class NotifyError(Exception):
    pass


# ---------------------------------------------------------------- Windows notifications
def _winrt():
    if sys.platform != "win32":
        raise NotifyError("App notifications only work on Windows.")
    try:
        from winrt.windows.ui.notifications.management import UserNotificationListener
        from winrt.windows.ui.notifications import NotificationKinds, KnownNotificationBindings
    except ImportError:
        raise NotifyError(INSTALL) from None
    return UserNotificationListener, NotificationKinds, KnownNotificationBindings


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _list_async():
    L, kinds, bindings = _winrt()
    try:
        listener = L.current
    except Exception as e:
        raise NotifyError(f"Windows won't share notifications with VSD Pad ({e}).") from None
    try:
        status = int(await listener.request_access_async())
    except Exception:
        status = int(listener.get_access_status())
    if status != 1:   # 1 = Allowed
        raise NotifyError(DENIED)
    out = []
    for n in await listener.get_notifications_async(kinds.TOAST):
        try:
            app = n.app_info.display_info.display_name if n.app_info else "App"
        except Exception:
            app = "App"
        title = body = ""
        try:
            b = n.notification.visual.get_binding(bindings.toast_generic)
            texts = [t.text for t in b.get_text_elements()] if b else []
            title, body = (texts + ["", ""])[:2]
        except Exception:
            pass
        out.append({"id": n.id, "app": app or "App", "title": title or "", "body": body or ""})
    return out


def notifications():
    with _lock:
        hit = _cache.get("n")
        if hit and time.monotonic() - hit[0] < CACHE_S:
            return hit[1]
    got = _run(_list_async())
    with _lock:
        _cache["n"] = (time.monotonic(), got)
    return got


def matching(items, app):
    a = (app or "").strip().lower()
    return [n for n in items if not a or n["app"].lower() == a or a in n["app"].lower()]


async def _remove_async(ids):
    L, _, _ = _winrt()
    listener = L.current
    for i in ids:
        listener.remove_notification(i)


# ---------------------------------------------------------------- email
def unread(settings, folder=None):
    host = (settings.get("imap_host") or "").strip()
    user = (settings.get("imap_user") or "").strip()
    pw = settings.get("imap_password") or ""
    if not (host and user and pw):
        raise NotifyError("Add your email server, address and password in Plugins → Notifications.")
    host, _, port = host.partition(":")
    folder = (folder or "INBOX").strip() or "INBOX"
    key = ("mail", host, user, folder)
    with _lock:
        hit = _cache.get(key)
        if hit and time.monotonic() - hit[0] < 20:
            return hit[1]
    try:
        m = imaplib.IMAP4_SSL(host, int(port or 993), timeout=10)
    except (OSError, socket.timeout, ValueError) as e:
        raise NotifyError(f"Can't reach the email server {host} ({e}).") from None
    try:
        try:
            m.login(user, pw)
        except imaplib.IMAP4.error:
            raise NotifyError("The email server didn't accept the login. Gmail and Outlook need an app password.") from None
        typ, data = m.status(f'"{folder}"', "(UNSEEN)")
        if typ != "OK":
            raise NotifyError(f"No folder called {folder}.")
        got = re.search(rb"UNSEEN (\d+)", data[0] or b"")
        n = int(got.group(1)) if got else 0
    finally:
        try:
            m.logout()
        except (OSError, imaplib.IMAP4.error):
            pass
    with _lock:
        _cache[key] = (time.monotonic(), n)
    return n


def _short(s, n):
    s = (s or "").strip()
    return s if len(s) <= n else s[:n - 1].rstrip() + "…"


class Notify(Plugin):
    id = "notify"
    name = "Notifications"
    description = "Keys that light up for app notifications (Discord, Outlook, WhatsApp...) and unread email."
    settings = [
        {"key": "imap_host", "label": "Email server (IMAP)", "placeholder": "imap.gmail.com",
         "help": "Only for the unread email key. Gmail: imap.gmail.com, Outlook.com: outlook.office365.com, iCloud: imap.mail.me.com"},
        {"key": "imap_user", "label": "Email address"},
        {"key": "imap_password", "label": "Email password (app password)", "secret": True,
         "help": "For Gmail make an app password at myaccount.google.com/apppasswords."},
    ]
    buttons = [{"id": "test", "label": "Check", "api": "test"}]
    actions = {
        "clear": {"label": "Clear an app's notifications", "params": [APP]},
    }
    widgets = {
        "app": {"label": "App notifications", "interval": 2, "params": [APP],
                "sample": {"icon": "🔔", "text": "3", "label": "Discord", "on": True}},
        "email": {"label": "Unread email", "interval": 30, "params": [FOLDER],
                  "sample": {"icon": "✉", "text": "5", "label": "Unread", "on": True}},
    }

    def run(self, action, params, ctx):
        if action != "clear":
            raise ValueError(f"unknown Notifications action {action!r}")
        ids = [n["id"] for n in matching(notifications(), params.get("app"))]
        if ids:
            _run(_remove_async(ids))
        with _lock:
            _cache.pop("n", None)

    def widget(self, name, params, ctx):
        if name == "app":
            got = matching(notifications(), params.get("app"))
            label = params.get("app") or "All apps"
            if not got:
                return {"icon": "🔔", "text": "0", "label": _short(label, 14), "on": False}
            latest = got[-1]
            return {"icon": "🔔", "text": str(len(got)), "label": _short(latest["title"] or label, 14), "on": True,
                    "color": [255, 90, 90]}
        if name == "email":
            n = unread(ctx.settings, params.get("folder"))
            return {"icon": "✉", "text": str(n), "label": "Unread", "on": n > 0, "color": [255, 90, 90] if n else None}
        return super().widget(name, params, ctx)

    def api(self, name, payload, ctx):
        if name == "test":
            parts, ok = [], True
            try:
                items = notifications()
                apps = sorted({n["app"] for n in items})
                parts.append(f"App notifications work ({len(items)} now" + (f": {', '.join(apps)})." if apps else ")."))
            except NotifyError as e:
                ok, parts = False, [str(e)]
            if ctx.settings.get("imap_host"):
                try:
                    parts.append(f"Email works: {unread(ctx.settings)} unread.")
                except NotifyError as e:
                    ok = False
                    parts.append(str(e))
            return {"ok": ok, "message": " ".join(parts)}
        if name == "apps":
            out = [{"value": "", "label": "Any app"}]
            seen = set()
            for a in ("Discord", "Outlook", "Mail", "WhatsApp", "Teams", "Slack", "Telegram"):
                seen.add(a.lower())
                out.append({"value": a, "label": a})
            try:
                for n in notifications():
                    if n["app"].lower() not in seen:
                        seen.add(n["app"].lower())
                        out.append({"value": n["app"], "label": n["app"]})
            except NotifyError:
                pass
            return out
        return super().api(name, payload, ctx)


PLUGIN = Notify()
