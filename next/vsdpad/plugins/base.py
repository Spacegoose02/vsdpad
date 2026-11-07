"""Plugin contract.

A plugin module in vsdpad/plugins/ exposes PLUGIN, an instance of a Plugin subclass. It can offer:
- actions: things a key does when pressed (run)
- widgets: live key content that refreshes by itself (widget)
- api calls: helpers for the settings page, e.g. listing lights or pairing (api)
- images: key pictures for references like "steam:<appid>" (image)
Use only the standard library for network calls (urllib).

param = {"key", "label", "kind": "text"|"number"|"select"|"choice"|"check",
         "options": [...] (select), "api": "<api name>" (choice), "default", "placeholder", "help"}
"""


class Plugin:
    id = ""
    name = ""
    description = ""
    settings = []    # [{"key", "label", "secret": bool, "placeholder", "help"}]
    buttons = []     # [{"id", "label", "api"}] shown on the plugin's card
    actions = {}     # action id -> {"label", "params": [param]}
    widgets = {}     # widget id -> {"label", "params": [param], "interval": seconds, "sample": {...}}

    def run(self, action, params, ctx):
        """Do an action. ctx.settings = this plugin's settings; ctx.nav = the device's navigator.
        Raise an exception with a readable message on failure."""
        raise NotImplementedError

    def widget(self, name, params, ctx):
        """Return live content for a key: any of
        {"text": big value, "label", "icon", "color": [r,g,b], "on": bool, "progress": 0..1}."""
        raise ValueError(f"{self.id}: unknown widget {name!r}")

    def api(self, name, payload, ctx):
        """Helpers for the settings page. Return JSON-able data. A dict may include "_save": {setting: value}."""
        raise ValueError(f"{self.id}: unknown api call {name!r}")

    def image(self, ref):
        """Path to a picture for the image reference "<plugin id>:<ref>", or None."""
        return None

    def describe(self):
        return {"id": self.id, "name": self.name, "description": self.description, "settings": self.settings,
                "buttons": self.buttons, "actions": self.actions, "widgets": self.widgets}

    def secret_keys(self):
        return {s["key"] for s in self.settings if s.get("secret")}
