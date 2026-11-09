"""F-19 Stealth Fighter viewer on the LoFiGL engine (tools/lofigl), scenes in tools/scenes.

usage: view.py                                        main menu (Esc in any scene comes back to it)
       view.py models [STFLT|PHOTO|LB|PG|NC|CE]       model showcase; F1..F6 switch the library at runtime
       view.py world [LB|PG|NC|CE]                    theater scenery
       view.py live [--record|--no-record] [SNAP.bin ...]
                                                      scenery + moving objects of the running game (DOSBox),
                                                      or of saved snaps (Left/Right); F1 game camera, F11 free,
                                                      F2 follow target, F4 ride with target, F3 look at target, [ ] pick target;
                                                      replays are recorded (to replays/, one file per mission) as set in
                                                      Settings, Live game; --record / --no-record override it;
                                                      the sky follows the game clock (Num 8/2 shift it, 5 stop/go, 0 reset);
                                                      N cycles colours auto (the game's night flag) / day / night
       view.py replay [FILE.f19r]                     play a recorded flight (default: the newest) with all live cameras;
                                                      Space pause, Left/Right seek 5 s (Shift 30 s), , . frame, - = speed, Home
any mode: --framelog records a frame log from launch to exit (to framelogs/: per-frame timings and a hitch report,
       lofigl/framelog.py); --stats prints the mean frame and scene times every 300 frames (console only)
At start, the files made from the game installation (export/, unpacked/<ru|en>/EGAME.EXE) are rebuilt if missing or
out of date, with a progress screen (resources.py; the first start takes a few seconds).
Messages go to the console and to view.log in the project folder (the last run only, logsetup.py).

Each mode is a scene factory (models_scene() ... replay_scene()): the main menu opens them the same way.
Started with a mode, the viewer has no menu: Esc quits.
"""
import glob
import logging
import os
import re
import sys
import logsetup
logsetup.start()            # first: settings.py reports its problems when it is imported
from lofigl.engine import GraphicsEngine, SceneError   # noqa: E402
from paths import PROJECT_DIR, APP_ICON

logger = logging.getLogger("view")
FRAMELOG_DIR = os.path.join(PROJECT_DIR, 'framelogs')
MODES = ("menu", "models", "world", "live", "replay")


# --- scene factories: factory(app) builds the scene; SceneError tells the player why it can't ---------------

def models_scene(library="STFLT"):
    def factory(app):
        from scenes.showcase import ShowcaseScene
        return ShowcaseScene(app, library)
    return factory


def world_scene(theater="LB"):
    def factory(app):
        from scenes.world_scene import WorldScene
        return WorldScene(app, theater)
    return factory


def live_scene(snaps=(), record=None):
    """The running game, or saved snaps (file names / patterns). record None: as the setting live.record."""
    def factory(app):
        import settings
        from live import open_source
        try:
            source = open_source(list(snaps), settings.get('live.record') if record is None else record)
        except (OSError, RuntimeError) as e:
            msg = str(e)
            if msg.endswith("found: []"):
                msg = "DOSBox is not running"
            elif "EGAME" in msg:
                msg = "F-19 is not in a mission"
            raise SceneError(f"Can't watch the game: {msg}") from e
        return source_scene(app, source)
    return factory


def replay_scene(path=None):
    """A recorded flight; None: the newest one."""
    def factory(app):
        from live import ReplaySource, newest_replay
        try:
            source = ReplaySource(path or newest_replay())
        except (OSError, ValueError) as e:
            raise SceneError(str(e)) from e
        return source_scene(app, source)
    factory.loading_message = "Loading..."      # every frame is decoded in memory: seconds for a long flight
    return factory


def source_scene(app, source):
    """The theater scenery with the moving objects of a live / snap / replay source; the scene owns the source."""
    from scenes.world_scene import WorldScene
    from live import theater_of
    try:
        theater = getattr(source, 'theater', None) or theater_of(source)     # a replay knows its theater
        if theater is None:
            raise SceneError("Can't tell the theater: the game is not in a mission?")
        if hasattr(source, "keep_theater"):
            source.keep_theater(theater)
        return WorldScene(app, theater, source)
    except BaseException:
        close = getattr(source, 'close', None)
        if close:
            close()
        raise


# --- main menu -------------------------------------------------------------------------------------------

def menu_scene(first_run=False):
    """The main menu; its items are made each time it opens (the replay list is read then). `first_run`: this run
    prepared the game data (a new installation), the first visit shows Libya (scenes/menu.py)."""
    def factory(app):
        from scenes.menu import MenuScene
        return MenuScene(app, menu_items(), first_run)
    return factory


def menu_items():
    from scenes.menu import Item
    from scenes.settings_menu import settings_items
    from scenes.showcase import LIBRARIES
    from world import THEATERS

    def opener(scene_factory):
        return lambda app: app.switch_scene(scene_factory)

    return [
        Item("Live game", opener(live_scene()),
             note="Connect to a mission running in DOSBox"),
        Item("Replays", children=replay_items, note="Watch your previous flights"),
        Item("World", children=[Item(f"{code}  {name}", opener(world_scene(code)))
                                for code, (name, *_) in THEATERS.items()], note="View a static world"),
        Item("Models", children=[Item(lib, opener(models_scene(lib))) for lib in LIBRARIES], note="Model showcase"),
        Item("Settings", children=settings_items, note="Adjust graphics and controls"),
        Item("Quit", lambda app: app.quit()),
    ]


def replay_items():
    """The recorded flights, newest first."""
    from scenes.menu import Item
    from live import REPLAY_DIR
    files = sorted(glob.glob(os.path.join(REPLAY_DIR, '*.f19r')), key=os.path.getmtime, reverse=True)
    items = []
    for path in files:
        name = os.path.basename(path)
        m = re.match(r'([A-Z]{2})_(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})', name)
        text = f"{m[1]}  {m[2]} {m[3]}:{m[4]}" if m else os.path.splitext(name)[0]
        items.append(Item(f"{text}  {os.path.getsize(path) / 1e6:6.1f} MB",
                          lambda app, p=path: app.switch_scene(replay_scene(p))))
    return items or [Item("No replays yet: Live game records them (Settings, Live game)", enabled=False)]


def scene_from_args(argv):
    """The scene factory for a command line (without the engine options)."""
    from scenes.showcase import LIBRARIES
    from world import THEATERS
    if not argv:
        return None
    mode = argv[0].lower() if argv[0].lower() in MODES else "models"
    if argv[0].lower() == mode:
        argv = argv[1:]
    if mode == "menu":
        return None
    if mode == "replay":
        return replay_scene(argv[0] if argv else None)
    if mode == "live":
        record = False if "--no-record" in argv else True if "--record" in argv else None
        return live_scene([a for a in argv if a not in ("--record", "--no-record")], record)
    if mode == "world":
        theater = argv[0].upper() if argv else "LB"
        if theater not in THEATERS:
            sys.exit(f"Unknown theater '{theater}', expected one of: {' '.join(THEATERS)}")
        return world_scene(theater)
    library = argv[0].upper() if argv else "STFLT"
    if library not in LIBRARIES:
        sys.exit(f"Unknown model library '{library}', expected one of: {' '.join(LIBRARIES)}")
    return models_scene(library)


ENGINE_OPTIONS = ("--framelog", "--stats")


def main(argv):
    from version import VERSION
    logger.info(f"TripoliRadar {VERSION}, view.py {' '.join(argv)}".rstrip())
    factory = scene_from_args([a for a in argv if a not in ENGINE_OPTIONS])
    import resources
    preload = resources.tasks()     # the export the viewer reads, rebuilt from the game files when missing
    home = None
    if factory is None:
        factory = home = menu_scene(first_run=bool(preload))
    try:
        GraphicsEngine(factory, framelog_dir=FRAMELOG_DIR, framelog="--framelog" in argv, home=home,
                       stats="--stats" in argv, preload=preload, title="Tripoli Radar", icon=APP_ICON).run()
    except SceneError as e:
        logger.error(str(e))
        logsetup.alert(e)
        sys.exit(1)


if __name__ == '__main__':
    main(sys.argv[1:])
