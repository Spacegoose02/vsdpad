"""Built-in: switch profiles (whole sets of pages, e.g. Work / Gaming / Streaming) from a key."""
from .. import layoutprofiles as lp
from .base import Plugin


class Profiles(Plugin):
    id = "profiles"
    name = "Profiles"
    description = "Switch between whole sets of pages, e.g. Work, Gaming and Streaming. Make profiles on the Profiles tab."
    actions = {
        "switch": {"label": "Switch to profile…", "params": [
            {"key": "profile", "label": "Profile", "kind": "choice", "api": "list"}]},
        "next": {"label": "Next profile", "params": []},
        "previous": {"label": "Previous profile", "params": []},
    }
    widgets = {
        "current": {"label": "Profile in use", "params": [], "interval": 2,
                    "sample": {"text": "Work", "label": "Profile", "icon": "▦"}},
    }

    def run(self, action, params, ctx):
        if action == "switch":
            lp.switch_now(ctx.app, params.get("profile", ""))
        elif action == "next":
            lp.step_now(ctx.app, 1)
        elif action == "previous":
            lp.step_now(ctx.app, -1)
        else:
            raise ValueError(f"unknown profiles action {action!r}")

    def widget(self, name, params, ctx):
        if name != "current":
            raise ValueError(f"profiles: unknown widget {name!r}")
        return {"text": lp.active(ctx.app.store.get())["name"], "label": "Profile", "icon": "▦"}

    def api(self, name, payload, ctx):
        if name == "list":
            return [{"value": p["id"], "label": p["name"]} for p in lp.summary(ctx.app.store.get())]
        raise ValueError(f"profiles: unknown api call {name!r}")


PLUGIN = Profiles()
