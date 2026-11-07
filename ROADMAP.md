# Roadmap

Prioritised ideas for the VSD M18 controller. Read `HANDOFF.md` first (architecture, rules, what is verified).
Sizes: **S** = under a day, **M** = a few days, **L** = a week or more. "Owner files" help parallel agents avoid clashes.
Ordering reflects what the user asked for and what unlocks the most; the user's favourites are marked ★.

## Now: stability and safety net

### R1. Pad connection drop (S) — investigate first
One real drop in ~40 min: `read error` at 272 s connected / 195 s idle, then `open failed`, then a clean reconnect (HANDOFF section 8).
- Try: log the pad's last keep-alive result; test other `CONNECT` intervals; debounce the "Pad not connected" flag for ~5 s;
  skip the full redraw on a quick reconnect if the config and page are unchanged; check whether idle ~3 min is a pad behaviour.
- Done when: a 2-hour idle soak shows no visible flicker, or the cause is documented. Owner files: `app.py`, `hardware.py`.

### R2. Backup, restore and sharing (S)
- GUI buttons: "Download my layout" / "Restore from file" (config without secrets by default; secrets only if the user ticks a box), plus a
  one-click "Restore last automatic backup". Validate with `store.validate` and the migration chain before replacing; always keep the previous file.
- Done when: round trip on a copy restores pages, folders, round buttons, theme and offsets; a corrupt file is rejected with a readable message.
  Owner files: `server.py`, `web/index.html`, `store.py`.

## Next: the features the user will notice

### R3. ★ Live status keys (L) — highest value
Keys that update by themselves.
- Examples: clock/date, weather (Open-Meteo needs no key), CPU/GPU/RAM, now playing, a light that shows on/off or its real colour,
  a toggle whose icon swaps with state, countdown timer.
- Design: plugins gain `widgets` (renderers with `interval`, returning label/icon/colour or an image); a key spec gets `live: {plugin, widget, params}`.
  A single `LiveKeys` thread refreshes only changed keys (`Pad.set_image` for that slot + `STP`), rate-limited (>= 1 s), paused while the key's page is not shown.
  Themes apply to live keys too (glow/label/brackets). HA/Hue state needs polling now; HA websocket is an option later.
- Pitfalls: image uploads are ~1.5 KB JPEG per key, cheap but do not flood the pad; do not redraw the whole page for one key; keep the GUI preview honest (show a sample value).
- Done when: a clock key, a weather key and a "Hue light state" key update on the real pad without flicker for an hour. Owner files: `plugins/*`, new `live.py`, `app.py`, `web/index.html`.

### R4. ★ Auto-switch page by active app (M)
- Rules in config: `profiles: [{match: {exe | title_contains}, page_id, revert: bool}]`. Foreground app via `GetForegroundWindow` /
  `GetWindowThreadProcessId` / `QueryFullProcessImageNameW` (ctypes), polled ~1 s.
- Do not fight the user: a manual page change pauses auto-switching for N seconds; "revert" returns to the previous page when the app loses focus.
- GUI: a Profiles section (pick a running app or type an exe name, choose a page). Done when OBS/game focus flips the pad and back, and manual navigation still wins.
  Owner files: new `profiles.py`, `pagestate.py`, `store.py` (v5), `web/index.html`.

### R5. ★ Steam game launcher (M)
- Read the Steam path from `HKCU\Software\Valve\Steam`, parse `steamapps/libraryfolders.vdf` and `appmanifest_*.acf`, launch with `steam://rungameid/<id>`.
  Cover art only from the user's local Steam `appcache/librarycache` (never bundled or scraped). Generate a "Games" folder (paged if > 14) and a "Recent" folder.
- Done when: games appear with their art as keys, launch works, missing art falls back to a themed text tile. Owner files: new `plugins/steam.py`, `iconpacks.py` (image keys).

### R6. Your own pictures, GIFs and wallpapers; theme import (M)
- Upload an image for a key; animated GIF (cap ~5 fps, CPU-friendly, pause when off-page); slice any photo across all 15 keys with a crop/zoom picker.
- Theme import/export as `.zip` (`pack.json` + PNGs only; zip-slip safe, size limits). Named per-icon PNGs in `pack.json`, per-key pack override,
  and a `retro` 8-bit pack (pixel icons scaled nearest-neighbour, built-in 5x7 bitmap font, scanlines).
- Done when: a user zip imports on a copy, a GIF key animates smoothly, and a hostile zip is rejected. Owner files: `iconpacks.py`, `server.py`, `web/index.html`.

### R7. Multi-action keys, long-press and double-press (M)
- `action` may become a sequence with delays (`[{...}, {"wait": 0.5}, {...}]`); `long_press` and `double_press` alternatives. Press duration is derivable from press/release events (already reported).
- Schema change (v5) with migration; GUI list editor with drag to reorder. Done when one key can open an app, wait, and type a snippet, and long-press differs from tap.

## Later

- **R8 Timers / Pomodoro key** (S/M, builds on R3).
- **R9 Sleep and schedules** (S/M): dim after N idle minutes and wake on press; brightness/theme at sunset or by time of day. Coordinate with R1 (pad idle behaviour).
- **R10 Integrations** (L each): OBS (WebSocket: record/stream state), Discord mute/deafen, Spotify (OAuth), Google Calendar "next event". Each is a plugin.
- **R11 Polish:** duplicate/reorder pages, per-folder theme, folder breadcrumbs, copy/paste keys, undo, light mode for the GUI, tray menu (switch page/theme/brightness),
  "start with Windows" toggle in the GUI, update check.
- **R12 Quality and packaging:** automated GUI tests (Playwright), CI running `tests\run_all.py`, type hints, DPAPI-encrypted secrets, optional per-session API token,
  PyInstaller one-file build / installer so the user does not need Python, crash reporting to `vsdpad.log` only.
- **R13 Other devices:** the protocol layer (`hardware.py`) is small; a second device (other Mirabox/Ajazz pads) would need a device profile (key count, image size, id map).

## Suggested split for parallel agents

| Track | Items | Files (mostly) | Notes |
|---|---|---|---|
| Stability | R1, R9 | `app.py`, `hardware.py` | Needs the user's pad for soak tests |
| Live keys | R3, R8, R10 | `plugins/*`, new `live.py` | Define the widget contract first, then plugins can proceed in parallel |
| Automation | R4, R7 | new `profiles.py`, `store.py` | Both change the config (v5): coordinate one migration |
| Visuals | R5 (art), R6 | `iconpacks.py`, `icons/*` | Image keys are shared groundwork |
| Safety net | R2, R12 | `server.py`, `tests/` | Safe to start immediately |

Rules for everyone: test against a copy of `config.json`; never print or request secrets; ask the user to look at the pad for visual changes;
update `HANDOFF.md` (status, known issues) and tick items here when done.
