"""Where things are: the project (this repository) and the F-19 installation it reads.

The game folder defaults to the folder that contains the project (D:/Games/F19 for D:/Games/F19/TripoliRadar).
Set the environment variable F19_GAME_DIR to use another one.

Files made from the game that depend on its EGAME build (Russian / English, gamever.py) go in a folder per
build, so one project can be moved between installations: unpacked/<ru|en>/, export/world/<ru|en>/.
The model libraries (export/3d) are the same in both builds.

Packed as an executable (PyInstaller, TripoliRadar.spec), the project folder is the one holding TripoliRadar.exe:
settings, logs, replays and the export are written there, and the game folder is its parent as before. The files
packed with the program (ico.png) are in BUNDLE_DIR."""
import os
import sys

FROZEN = getattr(sys, 'frozen', False)
PROJECT_DIR = os.path.dirname(sys.executable) if FROZEN else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUNDLE_DIR = getattr(sys, '_MEIPASS', PROJECT_DIR)
GAME_DIR = os.environ.get('F19_GAME_DIR') or os.path.dirname(PROJECT_DIR)
APP_ICON = os.path.join(BUNDLE_DIR, 'ico.png')


def game_file(name):
    """Path of a file of the game installation; a clear error if it isn't there."""
    path = os.path.join(GAME_DIR, name)
    if not os.path.exists(path):
        raise FileNotFoundError(f"F-19 file {name} not found in {GAME_DIR} "
                                "(put the project folder inside the game folder, or set F19_GAME_DIR)")
    return path


def game_build():
    """'ru' or 'en': the EGAME build of the game folder (gamever.installed())."""
    import gamever
    return gamever.installed().code


def unpacked_egame_path():
    return os.path.join(PROJECT_DIR, 'unpacked', game_build(), 'EGAME.EXE')


def unpacked_egame():
    """The unpacked EGAME.EXE (tools/unexepack.py) of the installed build, kept in the project."""
    path = unpacked_egame_path()
    if not os.path.exists(path):
        raise FileNotFoundError(f"{path} not found: run tools/resources.py (or view.py) first")
    return path


def world_export_dir():
    """export/world/<build>: theater scenery placements (world.py) made from the installed game."""
    return os.path.join(PROJECT_DIR, 'export', 'world', game_build())
