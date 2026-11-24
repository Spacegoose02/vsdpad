"""Updates: keeps the installed VSD Pad up to date from your VSD Pad server or GitHub (see vsdpad/updater.py)."""
from .base import Plugin
from .. import updater


def _u():
    if updater.UPDATER is None:
        raise ValueError("Updates aren't running in this copy.")
    return updater.UPDATER


class Updates(Plugin):
    id = "updates"
    name = "Updates"
    description = "Keeps the installed VSD Pad up to date: new versions install by themselves."
    settings = [
        {"key": "token", "label": "GitHub token", "secret": True,
         "help": "Not needed when this PC is linked to your VSD Pad server (it gets updates from there). Otherwise: a "
                 "read-only token for your private vsdpad repository (github.com → Settings → Developer settings → "
                 "Fine-grained tokens; Repository: vsdpad; Permissions: Contents → Read-only)."},
        {"key": "from", "label": "Updates from", "placeholder": "server",
         "help": "server = your VSD Pad server when this PC is linked to one, else GitHub. github = always GitHub."},
        {"key": "auto", "label": "Install updates by themselves", "placeholder": "yes",
         "help": "yes = install when the pad hasn't been used for 10 minutes (it restarts in a few seconds). "
                 "no = only check; use the button or an \"Update now\" key."},
    ]
    buttons = [{"id": "check", "label": "Check for updates", "api": "check"},
               {"id": "install", "label": "Update now", "api": "install"}]
    actions = {"check": {"label": "Check for updates", "params": []},
               "install": {"label": "Update now", "params": []}}
    widgets = {"status": {"label": "Update available", "interval": 300, "params": [],
                          "sample": {"text": "✓", "label": "Up to date"}}}

    def run(self, action, params, ctx):
        if action == "check":
            _u().check()
        elif action == "install":
            _u().install()
        else:
            raise ValueError(f"updates: unknown action {action!r}")

    def widget(self, name, params, ctx):
        if name != "status":
            return super().widget(name, params, ctx)
        st = _u().public()
        if st.get("installing"):
            return {"text": "…", "label": "Updating", "on": True}
        if st.get("available"):
            return {"text": "⬇", "label": f"Update {st['latest']}", "on": True, "color": [255, 170, 40]}
        if st.get("error"):
            return {"text": "!", "label": "Update check", "on": False}
        return {"text": "✓", "label": "Up to date" if st.get("checked") else "Updates", "on": False}

    def api(self, name, payload, ctx):
        try:
            if name == "check":
                st = _u().check()
                build = f"build {st['current']}" if st["current"] else "the Python version"
                where = " on your VSD Pad server" if st.get("source") == "server" else ""
                if st["available"]:
                    return {"ok": True, "message": f"Build {st['latest']} is out{where} (you have {build}). "
                                                   + ("Click Update now." if updater.FROZEN else "Update this copy with git.")}
                return {"ok": True, "message": f"You're up to date (newest is build {st['latest']}, you have {build})."}
            if name == "install":
                return _u().install()
        except ValueError as e:
            return {"ok": False, "message": str(e)}
        return super().api(name, payload, ctx)


PLUGIN = Updates()
