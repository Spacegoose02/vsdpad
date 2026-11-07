"""Built-in: pages, folders and pad brightness. Acts on the device whose key was pressed."""
from .base import Plugin


class Pages(Plugin):
    id = "pages"
    name = "Pages & pad"
    description = "Switch pages, open folders of keys and come back, change the pad's brightness."
    actions = {
        "open": {"label": "Open folder / page…", "params": [
            {"key": "page", "label": "Folder or page", "kind": "choice", "api": "@pages"}]},
        "back": {"label": "Back (leave folder)", "params": []},
        "next": {"label": "Next page", "params": []},
        "previous": {"label": "Previous page", "params": []},
        "home": {"label": "First page", "params": []},
        "goto": {"label": "Go to page number…", "params": [
            {"key": "page", "label": "Page number", "kind": "number", "default": 1, "help": "1 is the first page"}]},
        "brightness": {"label": "Change brightness", "params": [
            {"key": "step", "label": "Change (%)", "kind": "number", "default": 10, "help": "e.g. 10 or -10"}]},
        "brightness_set": {"label": "Set brightness", "params": [
            {"key": "value", "label": "Brightness (%)", "kind": "number", "default": 80}]},
    }

    def run(self, action, params, ctx):
        if action in ("brightness", "brightness_set"):
            def apply(cfg):
                cur = cfg.get("brightness", 80)
                new = cur + int(params.get("step", 10)) if action == "brightness" else int(params.get("value", 80))
                cfg["brightness"] = max(0, min(100, new))
            ctx.app.store.update(apply, snapshot=False)
            return
        nav = ctx.nav
        if nav is None:
            raise ValueError("Page actions only work from a pad key.")
        if action == "open":
            nav.open(str(params.get("page", "")))
        elif action == "back":
            nav.back()
        elif action == "next":
            nav.step(1)
        elif action == "previous":
            nav.step(-1)
        elif action == "home":
            nav.goto(0)
        elif action == "goto":
            n = int(params.get("page", 1))
            if not 1 <= n <= nav.count:
                raise ValueError(f"there is no page {n} (you have {nav.count})")
            nav.goto(n - 1)
        else:
            raise ValueError(f"unknown pages action {action!r}")


PLUGIN = Pages()
