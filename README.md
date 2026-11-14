# Ultimate-SteamCMD-mod-manager

Qt GUI program for downloading, updating and managing Steam Workshop mods via SteamCMD for the black flag sailers.

**This project is 100% slopped up by AI.** No tests, no guarantees. Read the code before running it.

## Features
- Profiles per game (App ID + mods folder)
- Embedded Workshop browser, one-click download (collections too)
- Update check against Workshop timestamps, no API key
- enable toggle, color tags, sorting, right-click menu
- Import an existing mod folder (RimWorld / Arma / Zomboid numeric IDs)

## Install & launch

### Windows
1. Install Python 3.10+ from python.org (check "Add to PATH").
2. `py -m pip install PyQt6 PyQt6-WebEngine`
3. Download SteamCMD: https://steamcdn-a.akamaihd.net/client/installer/steamcmd.zip — extract it, e.g. to `C:\steamcmd`.
4. `py steamcmd_mod_manager.py`

### Linux
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install PyQt6 PyQt6-WebEngine

mkdir -p ~/steamcmd
cd ~/steamcmd
curl -L https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz | tar -xz
chmod +x steamcmd.sh
./steamcmd.sh +quit

python3 steamcmd_mod_manager.py
```

or for fish users: source .venv/bin/activate.fish

On first launch it tries to find SteamCMD; if that fails, point it at `steamcmd.exe` / `steamcmd.sh` yourself.

## Notes
- Anonymous login works for most games. If a game refuses it, set a Steam login in the profile (password stored in plain text).
- Downloads land in SteamCMD's cache, then get copied into your mods folder as `<workshop_id>/`.
- Disabled mods are moved to `<mods folder>/.disabled/`.
- Config lives in `%APPDATA%\steamcmd-mod-manager` (Windows) or `~/.config/steamcmd-mod-manager` (Linux).
