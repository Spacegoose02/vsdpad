# Run VSD Pad from your Unraid server

The server keeps your layout and runs the settings page, so you can edit your pads from any browser at home.
The PC with the pads plugged in **follows** the server: it gets every change a moment after you press **Save**, and keeps
working from its own copy if the server is off. The pads stay on the PC (they're USB), and so do keys that press media keys,
open apps or type text: pressing a key in the server's settings page presses it on the PC.

## 1. Start the server on Unraid

The image is built by GitHub (Actions → **Server image**) and published as `ghcr.io/spacegoose02/vsdpad:latest`.
It's public, so Unraid needs no sign-in. Add the container with the template: click **>_** (Terminal) at the top right of
Unraid and run

```
wget -O /boot/config/plugins/dockerMan/templates-user/my-vsdpad.xml https://raw.githubusercontent.com/Spacegoose02/vsdpad/revamp-v2/unraid/vsdpad.xml
```

Then **Docker** tab → **Add Container** → **Template**: *vsdpad*. Or fill it in yourself:

| Field | Value |
|---|---|
| Repository | `ghcr.io/spacegoose02/vsdpad:latest` |
| Port | container `8765` → host `8765` |
| Path | container `/data` → `/mnt/user/appdata/vsdpad` |
| Variable | `VSDPAD_PASSWORD` = a password you choose |
| Variable | `VSDPAD_GITHUB_TOKEN` = optional, for app updates (below) |

Press **Apply**, then open `http://<your-unraid-ip>:8765`. The browser asks for a user name (type anything) and the password.

## 2. Move your layout to the server and connect the PC (once)

On the PC, quit VSD Pad (tray icon → **Quit**), open PowerShell and run:

```
cd C:\code\vsdpad\next
python run.py --connect http://<your-unraid-ip>:8765 --password <your password> --upload-layout
```

If you use the installed app (VSDPad.exe) instead of Python, run the same thing with the program:

```
& "$env:LOCALAPPDATA\Programs\VSD Pad\VSDPad.exe" --connect http://<your-unraid-ip>:8765 --password <your password> --upload-layout
```

`--upload-layout` sends this PC's pages, pictures, themes, icon packs and plugin settings to the server (leave it out if the
server's layout should win). The PC remembers the server, so from now on just start VSD Pad as usual (or let it start with
Windows). Your pads show up at the top of the server's settings page with the PC's name next to them.

## Updates for the Windows app

Give the server a read-only GitHub token (`VSDPAD_GITHUB_TOKEN`) and the PCs that follow it get new versions of the
installed app from the server, with no token on the PC. Make one at github.com → Settings → Developer settings →
Fine-grained tokens: Repository *vsdpad*, Permissions *Contents → Read-only*. The server downloads each new installer
once into `/data/updates`, checks it against GitHub's checksum, and serves it to the PCs at `/sync/updates/…` (same
password as the sync). The PC checks every 6 hours and installs when the pad has been idle for 10 minutes.

## Good to know

- **Edit on the server.** The PC's own settings page still opens, but shows *Edit on your server* and won't save changes there
  (the next sync would undo them). Pressing keys and switching pages from it still works.
- **Stop following**: `python run.py --disconnect` (the PC keeps its last copy and works on its own again).
- **Passwords and tokens.** Your Home Assistant / Hue / OBS tokens travel from the server to the PC on your home network and
  are stored on the server in `/mnt/user/appdata/vsdpad/config.json`. Don't open port 8765 to the internet; use a VPN
  (e.g. Tailscale or WireGuard on Unraid) to edit from outside.
- **Themes you import** on the server are kept in `appdata/vsdpad/themes` and reach the PC like pictures do.
- **Updating the server**: Docker tab → *Check for updates* (each push to `revamp-v2` builds a new `latest`).
- **Without Unraid**: `docker compose up -d` with `docker-compose.yml` from the repo, or `docker build -t vsdpad .`.
