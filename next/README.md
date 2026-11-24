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
- **Profiles**: whole sets of pages (e.g. Work, Gaming, Streaming). Switch with a key, from your phone, or at set times.
- **Phone or tablet remote**: your phone shows the pad's keys and presses them, over your home Wi-Fi. Off until you switch it on.
- **Sharing packs**: one `.vsdpack` file with a profile's pages plus the pictures, themes and icon packs they use.
- Only keys that change are redrawn (no flicker), a held key can't keep repeating after the pad drops,
  and the Home Assistant token / Hue key are encrypted for your Windows account.

## Profiles

1. Open the **Profiles** tab, type a name (e.g. *Gaming*), choose *A copy of the one in use* or *Empty pages*, click **+ Add and use**.
2. The **Keys** tab now edits Gaming. Make your keys and click **Save & apply**.
3. To switch from the pad: pick a key, add the step **Profiles → Switch to profile…** (or **Next profile**), Save.
4. To switch at set times: Profiles tab, **+ Add a time**, pick the time, profile and days, Save.

Brightness, dimming, day/night and plugin settings are the same in every profile. App switching rules and the theme belong to each profile.

## Phone or tablet remote

1. Settings & backup → **Phone or tablet remote** → tick *Let phones and tablets on my home Wi-Fi use the pad*.
   The first time, Windows asks whether VSD Pad may use the network: tick **Private networks** and click **Allow**.
2. Click **Pair a phone**. Point the phone's camera at the square code (or open the address shown and type the 6 digits).
3. On iPhone: Share → *Add to Home Screen* gives it its own icon. Remove a phone any time in the same panel.

It only works on your home network, each phone needs its own code, and it can press keys but never change settings.

## Sharing packs

Settings & backup → **Share a pack**: pick the profile, tick any extra themes or icon packs, click **Download pack**.
To bring one in, click **Import a pack**: its pages are added as a new profile, so nothing you have is replaced.
Tokens, keys, addresses and paired phones are never put in a pack.

## Updates

The installed VSD Pad keeps itself up to date. Every change published on GitHub becomes a new build, and the app checks
every 6 hours. When the pad hasn't been used for 10 minutes it downloads the new build, checks it, quits, and the
installer updates it and starts it again (a small progress window shows for a few seconds). Your layout is never touched.

- **Linked to your VSD Pad server** (Unraid): nothing to set up. The PC gets updates from the server, which gets them from GitHub.
  Give the server a read-only GitHub token once (`VSDPAD_GITHUB_TOKEN`, see the server's README).
- **No server**: Plugins → **Updates** → paste a read-only GitHub token (the help text says how to make one).
- **Check for updates** / **Update now** are in Plugins → Updates, and can also be keys. *Install updates by themselves: no* only checks.

## "Windows protected your PC" or "virus detected"

VSD Pad isn't signed with a paid certificate yet, and it's made with PyInstaller, whose files antivirus programs often
mistake for malware. It's a false alarm. Builds from now on compile their own launcher and carry version details, which
avoids most of these, and updates no longer start the installer through a hidden command window.

If Windows Security still stops it:

1. Open **Windows Security** → **Virus & threat protection** → **Protection history**, click the VSD Pad entry, and note
   the threat name and file. Choose **Actions → Allow on device** (or **Restore** if it was quarantined).
2. Tell Microsoft it's a false alarm: https://www.microsoft.com/en-us/wdsi/filesubmission → *Home customer* → upload the flagged file, choose *Incorrectly detected as malware*. They usually clear it within a few days,
   for everyone.
3. Only if it keeps coming back: Virus & threat protection → Manage settings → **Exclusions** → add the folder
   `%LOCALAPPDATA%\Programs\VSD Pad`.

A **SmartScreen** "Windows protected your PC" box on the setup file: click **More info → Run anyway**.

The lasting fix is code signing. The GitHub build signs the app and installer by itself once a certificate is added as the
`WINDOWS_SIGN_PFX` (base64 of the .pfx) and `WINDOWS_SIGN_PASSWORD` secrets.

## Files

Everything you make is in `next\data\`: `config.json` (layout), `history\` (earlier versions), `images\` (your pictures),
`vsdpad.log` (what happened, with times), `remote.json` (paired phones, stored as fingerprints only). It's not in git because it holds your tokens.

## For developers

`python tests\run_all.py` runs every check (no pad needed). `python run.py --simulate m18,xl --no-tray --data %TEMP%\vsdpad-try`
runs the whole app with pretend pads, even while the real app is running. See `HANDOFF.md`.
