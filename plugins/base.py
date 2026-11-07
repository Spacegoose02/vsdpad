"""Plugin contract. See HANDOFF.md section 5.2.

A plugin module exposes a PLUGIN instance of a Plugin subclass; plugins/__init__.py
auto-discovers it. Use only the standard library (urllib) for network calls.
"""


class Plugin:
    id = ""            # short stable id, used in config ("plugin": "hue")
    name = ""          # shown in the GUI
    description = ""
    settings = []      # [{"key", "label", "secret": bool, "placeholder", "help"}]
    buttons = []       # [{"id", "label", "api"}] shown on the Plugins tab
    actions = {}       # action_id -> {"label": str, "params": [param, ...]}
    # param: {"key", "label", "kind": "text"|"number"|"select"|"choice",
    #         "options": [...]    for "select" (list of str or {"value","label"}),
    #         "api": "lights"     for "choice" (plugin.api() returns [{"value","label"}]),
    #         "default", "placeholder", "help"}

    def run(self, action, params, settings):
        """Perform an action. Raise an exception with a readable message on failure."""
        raise NotImplementedError

    def api(self, name, payload, settings):
        """Helper calls for the GUI (connection test, listing devices, pairing).
        Return JSON-able data. A dict may include "_save": {setting_key: value} to
        persist settings. Conventionally api("test") returns {"ok": bool, "message": str}."""
        raise ValueError(f"{self.id}: unknown api call {name!r}")

    def describe(self):
        """Public schema for the GUI (contains no secrets)."""
        return {"id": self.id, "name": self.name, "description": self.description,
                "settings": self.settings, "buttons": self.buttons, "actions": self.actions}
