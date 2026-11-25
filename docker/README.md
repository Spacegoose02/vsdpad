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

Press **Apply**, then open `http://<your-unraid-ip>:8765` and sign in as **admin** with your password
(change the user name with the `VSDPAD_USER` variable). Each browser stays signed in for 90 days.

## 2. Connect each PC (once per PC, no command line)

1. On the PC, open VSD Pad's settings page (double-click the tray icon) and go to **Settings** → **VSD Pad server**.
2. Click **Find my server**. Your server shows up in the list (or type its address, e.g. `192.168.1.20`).
3. Type the server's password and click **Connect**.
   On the **first** PC, the one whose layout you want to keep, tick **Send this PC's layout to the server** first.

That's it: the PC remembers the server and follows it from now on, also after a restart. Your pads show up at the top of
the server's settings page with the PC's name next to them. **Disconnect** in the same panel makes the PC stand on its own
again (it keeps its last copy).

(Scripts can still use `run.py --connect http://<server>:8765 --password … [--upload-layout]` and `--disconnect`.)

## Updates for the Windows app

Give the server a read-only GitHub token (`VSDPAD_GITHUB_TOKEN`) and the PCs that follow it get new versions of the
installed app from the server, with no token on the PC. Make one at github.com → Settings → Developer settings →
Fine-grained tokens: Repository *vsdpad*, Permissions *Contents → Read-only*. The server downloads each new installer
once into `/data/updates`, checks it against GitHub's checksum, and serves it to the PCs at `/sync/updates/…` (same
password as the sync). The PC checks every 6 hours and installs when the pad has been idle for 10 minutes.

## Good to know

- **Edit on the server.** The PC's own settings page still opens, but shows *Edit on your server* and won't save changes there
  (the next sync would undo them). Pressing keys and switching pages from it still works.
- **Passwords and tokens.** Your Home Assistant / Hue / OBS tokens travel from the server to the PC on your home network and
  are stored on the server in `/mnt/user/appdata/vsdpad/config.json`. Don't open port 8765 to the internet; use a VPN
  (e.g. Tailscale or WireGuard on Unraid) to edit from outside.
- **Themes you import** on the server are kept in `appdata/vsdpad/themes` and reach the PC like pictures do.
- **Updating the server**: Docker tab → *Check for updates* (each push to `revamp-v2` builds a new `latest`).
- **Without Unraid**: `docker compose up -d` with `docker-compose.yml` from the repo, or `docker build -t vsdpad .`.
