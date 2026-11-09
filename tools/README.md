# view.py — F-19 HD viewer

## Setup

Needs Python 3.10+ and a GPU with OpenGL 4.5. Live mode reads DOSBox's memory and works on Windows only.
Put the project folder inside the game folder (e.g. `F19\TripoliRadar`), or point the environment variable
`F19_GAME_DIR` at the game folder. From the project folder:

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

Both the Russian localization and the English (Steam) release of F-19 work: the project finds out which one the
game folder holds (`tools\gamever.py` prints it), and live mode recognizes either build running in DOSBox. Snaps
and replays play in either installation.

The viewer reads files made from the game: the model libraries (`export/3d`), the scenery placements
(`export/world/<ru|en>`) and the unpacked EGAME.EXE (`unpacked/<ru|en>`). At start it rebuilds the missing or
out-of-date ones behind a progress screen: about 10 seconds the first time. `tools\resources.py [--force]` does the
same from the console.

`view.bat` uses `.venv` when it exists (else `python` from PATH) and passes its arguments on.

## Modes

`tripoliradar.py` in the project folder is the same as `view.py` with no arguments: the main menu.

| Command | What it shows |
|---|---|
| `view.py` | Main menu over the Libyan coast: live game, replays, world, models; Esc in any mode comes back to it |
| `view.py models [STFLT\|PHOTO\|LB\|PG\|NC\|CE]` | Model showcase: one .3D3 library standing in two rows (default STFLT) |
| `view.py world [LB\|PG\|NC\|CE]` | Theater scenery, no game needed (default LB) |
| `view.py live` | Scenery + moving objects of the running game (DOSBox, in a mission), recording replays if Settings → Live game says so (on by default) |
| `view.py live --record` / `--no-record` | The same, recording replays or not whatever the setting |
| `view.py live SNAP.bin ...` | Saved RAM snaps (wildcards work); snaps of other theaters are skipped |
| `view.py replay [FILE.f19r]` | Play a recorded flight (default: the newest in `replays/`) |

Any mode also takes `--framelog` (record a frame log from launch to exit, to `framelogs/`) and `--stats` (print the mean frame and
scene times every 300 frames to the console). Messages go to the console and to `view.log` in the project folder,
rewritten at each start.

## Settings

Main menu → Settings: display, graphics, camera, live game and every key below. Left / Right change a value,
Enter on a key waits for the new key (one already used by another action is swapped with it), Del puts back the
default; each page ends with "Reset this page", and "Reset all settings" puts everything back. Values are kept in
`settings.ini` in the project folder (written on first run, with comments; delete a line or the file for the
defaults). Keys apply at once, graphics to the next scene opened, display settings after a restart.

## Keys everywhere

The keys in these tables are the defaults.

| Key | Action |
|---|---|
| Mouse | Look around |
| W / S, A / D, Q / E | Fly forward / back, left / right, up / down |
| Left Shift | Fly 25× faster |
| Page Up / Page Down | Zoom in / out |
| Middle mouse button | Reset zoom (50°) |
| P | Post-processing (depth of field) on / off |
| O | Next depth-of-field aperture |
| \ | Show / hide labels and HUD |
| Numpad 5 | Start / stop the sun moving |
| Numpad 8 / 2 | Move the sun forward / back |
| Esc | Back to the main menu (quit when started with a mode) |

## Main menu

| Key | Action |
|---|---|
| Up / Down | Select |
| Enter / Right | Open |
| Esc / Backspace / Left | Back; Esc at the top quits |

## Model showcase

| Key | Action |
|---|---|
| F1 … F6 | Switch library: STFLT, PHOTO, LB, PG, NC, CE |

## World, live and replay

| Key | Action |
|---|---|
| Mouse wheel | Flying speed |
| Hold 1 / 2 / 3 + wheel | View distance of scenery level 1 / 2 / 3 |
| C | Ground dots (the game's motion cues) on / off |

## Live and replay: cameras and objects

| Key | Action |
|---|---|
| F1 | Game camera (cockpit, external and weapon views as the game shows them) |
| F11 | Free flight from where you are |
| F2 | Follow the target in world axes (up stays up); again to release |
| F4 | Ride with the target in its own axes (rolls with it); again to release |
| F3 | Look at the target on / off (works in free flight and F2) |
| [ / ] | Previous / next target (player, aircraft, weapons in flight, parachute); puts the camera behind it |
| I | Interpolation (smooth motion between game frames) on / off |

## Live only

| Key | Action |
|---|---|
| Left / Right | Previous / next snap (snap mode) |

Replays are recorded while the live viewer runs (Settings → Live game → Record replays, on by default). Files go to
`replays/<theater>_<date>_<time>.f19r`, one per mission: when a mission ends the recording is saved and the viewer
waits for the next mission. A mission in a different theater needs a viewer restart.

## Replay only

| Key | Action |
|---|---|
| Space | Pause / play (at the end: play again from the start) |
| Left / Right | Seek 5 s back / forward (with Shift: 30 s) |
| , / . | Pause and step one game frame back / forward |
| - / = | Slower / faster: 1/16× … 8× |
| Home | Back to the start |

Pauses in the recording longer than 0.5 s (game paused, menus) are shortened to 0.5 s. A replay takes 64 KB of
RAM per game frame (about 3 GB per hour) and about 115 MB per hour on disk.

## Other tools

See the Tools section of `../NOTES.md` (`dosmem.py`, `gamestate.py`, `model3d.py`, `world.py`, …).
