"""Built-in plugin: switch the pad between pages and open folders of keys."""
import pagestate

from .base import Plugin


class Pages(Plugin):
    id = "pages"
    name = "Pages & folders"
    description = ("Switch pages with the round buttons, or open a folder of keys from a key and come Back. "
                   "Next/Previous skip folders.")
    settings = []
    actions = {
        "open": {"label": "Open folder / page…", "params": [
            {"key": "page", "label": "Folder or page", "kind": "choice", "api": "@pages"}]},
        "back": {"label": "Back (leave folder)", "params": []},
        "next": {"label": "Next page", "params": []},
        "previous": {"label": "Previous page", "params": []},
        "home": {"label": "Back to page 1", "params": []},
        "goto": {"label": "Go to page number…", "params": [
            {"key": "page", "label": "Page number", "kind": "number", "default": 1,
             "help": "1 is the first page"}]},
    }

    def run(self, action, params, settings):
        s = pagestate.STATE
        if action == "open":
            s.open(str(params.get("page", "")))
        elif action == "back":
            s.back()
        elif action == "next":
            s.step(1)
        elif action == "previous":
            s.step(-1)
        elif action == "home":
            s.goto(0)
        elif action == "goto":
            n = int(params.get("page", 1))
            if not 1 <= n <= s.count:
                raise ValueError(f"there is no page {n} (you have {s.count})")
            s.goto(n - 1)
        else:
            raise ValueError(f"unknown pages action {action!r}")


PLUGIN = Pages()
