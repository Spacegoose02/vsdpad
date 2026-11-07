# VSD Pad

A replacement controller for the **VSDinside VSD M18** macro pad (15 screen keys + 3 round buttons). It draws your icons and themes,
runs actions when you press keys, and has a settings page in your browser. Close VSD Craft first: only one program can use the pad.

## Start it

```
cd C:\code\vsdpad
python app.py --gui
```

It also starts by itself when you sign in to Windows (a `VSDPad` shortcut in your Startup folder), with an icon in the system tray
near the clock. Double-click the icon, or right-click and choose **Open settings**. **Quit** closes the app.

## The settings page (http://127.0.0.1:8765)

- **Keys:** click a key to press it. Drag one onto another to swap. Use the pencil (or double-click) to edit its label, icon, colour and action.
  Page tabs sit above the keys; **+ Page** adds a page and **+ Folder** adds a folder. Click **Make this a folder** on a key to turn it into a folder button.
  The round buttons are underneath. Press **Save & apply** to send changes to the pad.
- **Plugins:** connect Home Assistant (URL + a long-lived access token) and Philips Hue (Find bridge, press the bridge button, Pair).
- **Themes:** apply Cyberpunk City or Nuclear City (one picture across all 15 keys).
- **Settings:** brightness, and **Key position** sliders if a row of screens sits too high or low (your bottom row uses +8).

## If something goes wrong

- *The pad does nothing / icons look old:* make sure only one copy is running (tray icon, Task Manager) and VSD Craft is closed, then restart the app.
- *A log is kept in* `vsdpad.log` *(next to the app).* It records reconnects and errors with the time.
- *Your layout is saved in* `config.json`. Automatic backups from upgrades are `config.json.bak`, `.v2.bak`, `.v3.bak`.
  Your Home Assistant token and Hue key are stored there as plain text, so don't share that file.

## Fresh copy of the project

`config.json` (your layout and passwords) is deliberately **not** in git. On a new copy, run
`copy config.example.json config.json` to start from a small example layout, then add your own keys and plugins in the settings page.

## For developers

See `HANDOFF.md` (architecture, rules, known issues) and `ROADMAP.md` (what to build next). Run all checks with `python tests\run_all.py`.
