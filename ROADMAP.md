# Roadmap

VSD Pad 2 lives in `next/` (see `next/HANDOFF.md`). The 1.x roadmap (R1–R13) is in git history; most of it shipped in 2.0.
Sizes: **S** = under a day, **M** = a few days, **L** = a week or more.

## Done in 2.0

Backup/restore + undo history (R2), live keys (R3), app-based page switching (R4), Steam launcher (R5), pictures on keys
and theme import/export (part of R6), multi-step keys with hold and double-press (R7), timers (R8), dim when idle
(part of R9), duplicate/reorder pages and copy/paste keys (part of R11), DPAPI secrets and a settings-API token (part of R12),
Elgato Stream Deck support (R13), **Wavelength** plugin (now playing + controls via the Hub).

## 1. Prove it on the real hardware (S) — first

- **Pad connection drop (was R1).** Still happens: 2026-11-07 the M18 vanished from USB ~7 min after connecting and came back
  20 s later on its own. 2.0 recovers cleanly and logs what it was doing, how long since the last press / keep-alive / draw,
  and how long the pad was gone. Next: test with USB selective suspend off; 2-hour idle soak; if it persists, try other keep-alive
  intervals. Done when a 2-hour soak has no drop, or the cause is written down.
- **Stream Deck first run.** Driver is written from the public protocol, untested. Check: image orientation per model, key order,
  brightness, presses. Owner files: `next/vsdpad/devices/streamdeck.py`.
- **Windows-only paths**: typing text, app switching, CPU/RAM keys, Steam folder, DPAPI. Quick manual checklist.

## 2. Start with Windows + installer (M)

- A **Start with Windows** switch in Settings (creates/removes the Startup shortcut), so nobody edits shortcuts by hand.
- Then a PyInstaller one-file build / installer so Python isn't needed. Crash reports only to `data/vsdpad.log`.

## 3. Music: Wavelength first (S/M)

- Done: Wavelength plugin (Hub's Home Assistant API): now playing key, play/pause/next/previous/volume/seek, any device.
- Next: "Continue there" key (hand the queue to another device), a cover-art key if the Hub ever exposes art safely.
- **Windows "Now playing"** (any app via the Windows media session) as a fallback for players other than Wavelength.
  Replaces the old Spotify item: no OAuth, works for every player.

## 4. OBS (M)

OBS WebSocket plugin: keys that show and toggle recording / streaming / mute and switch scenes, lit while live.

## 5. Faster smart-home state (S/M)

Home Assistant websocket instead of polling every 5 s, so light/switch keys change instantly.

## 6. Visual extras (M)

Animated GIF keys (~5 fps, paused off-page), slice one photo across all keys, a retro 8-bit theme, per-folder theme,
time-of-day / sunset brightness and theme (rest of R9).

## 7. Safety net (S)

Back the git repo up to a private GitHub repo; run `next\tests\run_all.py` in CI; Playwright tests for the settings page.

## Later / maybe

Discord mute/deafen (needs a Discord app approval), Google Calendar next event (OAuth), Stream Deck Mini and the Plus dials,
user plugin folder + plugin docs, tray menu (page / brightness / theme).

## Dropped

Light mode for the settings page, update checker (single-user app, no release channel), Spotify (covered by Wavelength + Windows now playing).
