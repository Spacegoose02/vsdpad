# VSD Pad 2

The new version of the pad controller. It drives your **VSD M18** and **Elgato Stream Deck** pads (Original v2, MK.2, XL, Neo,
and the keys of the Stream Deck +), with a settings page in your browser. It lives in this `next` folder and doesn't touch the old app
or its `config.json`, so you can go back at any time.

## Try it

1. Quit the old app: right-click the VSD Pad icon near the clock → **Quit**. Close VSD Craft and the Elgato Stream Deck app too
   (only one program can use a pad).
2. Open PowerShell and run:
   ```
   cd C:\code\vsdpad\next
   python run.py --gui
   ```
3. The first start copies your current layout (pages, folders, round buttons, theme, Home Assistant and Hue settings) from the old
   `config.json`. The old file is only read, never changed.

To go back to the old app: Quit the new one from its tray icon and start the old one as before (`cd C:\code\vsdpad`, `python app.py --gui`).

## What's new

- **Stream Deck support** next to the M18. Same-size decks can share pages; others (XL, Neo) get their own. Each pad flips pages on its own.
- **Live keys** that update by themselves: clock, date, weather, CPU and memory, Home Assistant sensors and switches,
  Hue lights in their real colour, countdown timers.
- **Hold and double-press**, and **several steps per key** (e.g. open an app, wait, type some text).
- **Pictures on keys** (upload any picture) and a **Steam games** folder with your cover art.
- **App switching**: show a page while an app is in front (e.g. OBS), then go back.
- **Undo** and **history**: every save keeps the previous version; download/restore your layout from a file.
- Dims after a while if you want; a press wakes it.
- Only keys that change are redrawn (no flicker), a held key can't keep repeating after the pad drops,
  and the Home Assistant token / Hue key are encrypted for your Windows account.

## Files

Everything you make is in `next\data\`: `config.json` (layout), `history\` (earlier versions), `images\` (your pictures),
`vsdpad.log` (what happened, with times). It's not in git because it holds your tokens.

## For developers

`python tests\run_all.py` runs every check (no pad needed). `python run.py --simulate m18,xl --no-tray --data %TEMP%\vsdpad-try`
runs the whole app with pretend pads, even while the real app is running. See `HANDOFF.md`.
