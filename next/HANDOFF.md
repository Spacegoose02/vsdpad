# VSD Pad 2 — handoff

A rewrite of the 1.x app (one folder up). Read the 1.x `HANDOFF.md` for the M18 protocol history; this file covers the new design.

## Layout

```
run.py                 entry point (python run.py --gui | --check | --simulate [m18,mk2,xl,neo] | --data DIR | --no-tray)
vsdpad/main.py         CLI, first run (imports ../config.json read-only), single-instance mutex (same name as 1.x), tray
vsdpad/app.py          App (plugins, store, live keys, app rules, device scan) + DeviceRunner (one thread per pad)
vsdpad/devices/        base contract, m18.py (verified protocol), streamdeck.py (gen-2 protocol, NOT yet tested on hardware), fake.py
vsdpad/schema.py       config v5 format + validation;  migrate.py  v1-v4 -> v5;  store.py  atomic save, history/undo, DPAPI secrets, export/import
vsdpad/navigator.py    pages/folders per device;  actions.py  steps, waits, tap/hold/double/repeat (Gestures)
vsdpad/live.py         LiveEngine: refreshes widgets for visible keys, shared by spec, per-widget interval
vsdpad/profiles.py     page switching by foreground app (Windows), never fights manual navigation
vsdpad/layoutprofiles.py  profiles (whole page sets) kept in config `layout_profiles`; the active one lives in the normal keys; schedule
vsdpad/remote.py       phone remote: 2nd server on 0.0.0.0:8790, only when switched on; 6-digit pairing -> per-phone token (hash in data/remote.json)
vsdpad/web/remote.html phone page;  qr.py  tiny QR maker for the pairing link
vsdpad/sync.py         server mode (run.py --server, Docker/Unraid: ../Dockerfile, ../docker/, ../unraid/) + the PC that follows it
                       (--connect): long-poll /sync/poll for config + changed files, /sync/status reports pads, presses forwarded
vsdpad/sharepack.py    .vsdpack export/import (profile + images + theme zips + icon pack zips; never plugin settings)
vsdpad/render.py       216 px key renderer (modern + wallpaper themes, pictures, live text/progress/on-off), signature cache
vsdpad/themes_io.py    safe theme .zip import/export;  themes/  cyberpunk, nuclear
vsdpad/server.py       127.0.0.1 settings API (Host/Origin checks + per-run token header)
vsdpad/web/index.html  settings UI (vanilla JS, no build, no external requests)
vsdpad/plugins/        system, pages, homeassistant, hue, clock, timer, sysinfo, weather (Open-Meteo), steam
tests/                 test_core.py, test_plugins.py (mock HA/Hue), test_server.py, run_all.py
```

## Key ideas

- **Devices**: a driver reports `cols x rows` keys (+ plain buttons with ids above the key count). Slot 1 = top-left.
  Drivers scale/rotate/encode a 216 px image. `DeviceRunner` diffs per-slot render signatures and only sends changed keys, then commits.
- **Layouts**: a page set for one grid size. Devices point at a layout (`devices.<id>.layout`); new devices get one automatically.
- **Keys**: `tap/hold/double` step lists; keys without hold/double fire on press (no delay). `Gestures.cancel_all()` on disconnect.
- **Plugins** offer `actions` (run), `widgets` (live content), `api` (settings-page helpers) and `image(ref)` for `"<plugin>:<ref>"` pictures.

## Not verified yet (needs the user's hardware / PC)

1. Stream Deck driver on a real deck (protocol from public docs; image rotation per model is the likeliest thing to be off).
2. M18 single-key updates (BAT for one key + STP without CLE) — 1.x always cleared and redrew all 15.
3. Windows-only paths: DPAPI, typing text (SendInput), foreground-app detection, CPU/RAM, Steam registry lookup, tray.
4. Python 3.10 on the user's PC (tests pass on 3.10 and 3.13 here).
