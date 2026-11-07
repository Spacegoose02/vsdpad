"""Plugin discovery. Every plugins/<name>.py exposing PLUGIN (a Plugin instance) is registered."""
import importlib, os, pkgutil, traceback

PLUGINS = {}


def load_plugins():
    PLUGINS.clear()
    here = os.path.dirname(__file__)
    for m in pkgutil.iter_modules([here]):
        if m.name.startswith("_") or m.name == "base":
            continue
        try:
            mod = importlib.import_module(f"plugins.{m.name}")
            plugin = getattr(mod, "PLUGIN", None)
            if plugin is not None:
                PLUGINS[plugin.id] = plugin
        except Exception:  # one broken plugin must not take the app down
            print(f"plugin {m.name} failed to load:\n{traceback.format_exc()}", flush=True)
    return PLUGINS


def describe_all():
    return [p.describe() for p in PLUGINS.values()]


def run_action(action, plugin_settings):
    """Run a v2 action dict: {"plugin", "action", "params"}.
    plugin_settings: {plugin_id: {...}} from the config."""
    p = PLUGINS.get(action.get("plugin"))
    if p is None:
        raise ValueError(f"unknown plugin {action.get('plugin')!r}")
    name = action.get("action")
    if name not in p.actions:
        raise ValueError(f"plugin {p.id!r} has no action {name!r}")
    return p.run(name, action.get("params", {}), plugin_settings.get(p.id, {}))
