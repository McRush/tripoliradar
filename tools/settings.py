"""Player settings: graphics, camera and keys, kept in settings.ini in the project folder.

The file is written with comments and defaults when it is missing, and again whenever the main menu changes a value
(Settings). A line or a whole file deleted gets its defaults back; an invalid value is reported and replaced by its
default. Code reads values where it uses them (get, key), so key bindings and some values apply at once; graphics
values apply to the next scene opened, display values after a restart (Setting.restart).

Keys are pygame key names ("f1", "left shift", "keypad 5", "[5]", "\\\\"...). Bindings are checked for clashes within
the parts of the viewer that are active together (Setting.context, see clash): a key taken by another action is
swapped with it.
"""
import configparser
import logging
import os
import re
from paths import PROJECT_DIR

logger = logging.getLogger(__name__)

PATH = os.path.join(PROJECT_DIR, 'settings.ini')

# contexts of key bindings: 'general' (engine, everywhere), 'camera' (free camera and sky: every scene), 'world'
# (scenery, live, replay), 'models' (showcase). Only 'world' and 'models' are never active together.
SEPARATE_CONTEXTS = {frozenset(('world', 'models'))}
MENU_KEYS = ('up', 'down', 'left', 'right', 'return', 'enter', 'backspace', 'delete')    # the menu's own


class Setting:
    """kind: 'choice' (one of `choices`), 'bool', 'int', 'float' (`choices` are the menu's steps, `lo` / `hi` the
    accepted range, default: the choices' range), 'size' ('auto' or WIDTHxHEIGHT), 'key'. `labels` names some
    values in the menu (e.g. 0: "off"); `unit` follows numbers."""
    def __init__(self, key, default, label, help="", kind=None, choices=(), lo=None, hi=None, restart=False,
                 context=None, labels=None, unit=""):
        self.key, self.default, self.label, self.help = key, default, label, help
        self.section, self.name = key.split('.')
        self.kind = kind or ('bool' if isinstance(default, bool) else 'int' if isinstance(default, int)
                             else 'float' if isinstance(default, float) else 'choice')
        self.choices = list(choices)
        numbers = [c for c in self.choices if isinstance(c, (int, float))]
        self.lo = lo if lo is not None else (min(numbers) if numbers else None)
        self.hi = hi if hi is not None else (max(numbers) if numbers else None)
        self.restart, self.context, self.labels, self.unit = restart, context, labels or {}, unit

    def parse(self, text):
        """The value of an ini string; ValueError if it isn't valid."""
        t = text.strip()
        if self.kind == 'bool':
            low = t.lower()
            if low in ('yes', 'true', 'on', '1'):
                return True
            if low in ('no', 'false', 'off', '0'):
                return False
            raise ValueError("expected yes or no")
        if self.kind in ('int', 'float'):
            v = int(t) if self.kind == 'int' else float(t)
            if (self.lo is not None and v < self.lo) or (self.hi is not None and v > self.hi):
                raise ValueError(f"expected {self.lo} to {self.hi}")
            return v
        if self.kind == 'size':
            if t.lower() == 'auto':
                return 'auto'
            if not re.fullmatch(r'\d{3,5}x\d{3,5}', t.lower()):
                raise ValueError("expected auto or WIDTHxHEIGHT")
            return t.lower()
        if self.kind == 'choice':
            if t.lower() not in self.choices:
                raise ValueError(f"expected one of: {', '.join(self.choices)}")
            return t.lower()
        if not t:                       # key: the name is checked when it is used (pygame must be running)
            raise ValueError("expected a key name")
        return t.lower()

    def format(self, value):
        """The ini string of a value."""
        if self.kind == 'bool':
            return 'yes' if value else 'no'
        return str(value)

    def describe(self, value):
        """The value as the menu shows it."""
        if value in self.labels:
            return self.labels[value]
        if self.kind == 'bool':
            return 'on' if value else 'off'
        if self.kind == 'key':
            return key_label(value)
        if self.kind in ('int', 'float'):
            text = f"{value:,}".replace(',', ' ') if self.kind == 'int' else f"{value:g}"
            return text + (f" {self.unit}" if self.unit else "")
        return str(value)


def S(*args, **kw):
    return Setting(*args, **kw)


def K(key, default, label, context):
    return Setting(key, default, label, kind='key', context=context)


SCHEMA = [
    # --- display: applied when the window opens
    S('display.mode', 'borderless', "Window", "fullscreen changes the monitor's video mode unless the size is auto; "
      "borderless covers the monitor; windowed is a framed window", choices=('fullscreen', 'borderless', 'windowed'),
      restart=True),
    S('display.size', 'auto', "Size", "auto: the monitor's size (3/4 of it windowed)", kind='size',
      choices=('auto', '1280x720', '1600x900', '1920x1080', '2560x1440', '3840x2160'), restart=True),
    S('display.monitor', 0, "Monitor", "0 is the main one", choices=range(8), restart=True),
    S('display.vsync', True, "Vertical sync", "Frames wait for the monitor's refresh: no tearing", restart=True),
    S('display.max_fps', 120, "Frame rate limit", "With vertical sync the monitor's refresh rate limits it too",
      choices=(30, 60, 120, 144, 240, 0), lo=0, hi=1000, labels={0: "none"}, unit="fps"),
    S('display.msaa', 16, "Anti-aliasing", "Samples per pixel; off: distant objects pop instead of fading",
      choices=(0, 2, 4, 8, 16), lo=0, hi=32, labels={0: "off"}, unit="x", restart=True),

    # --- graphics: applied to the next scene opened
    S('graphics.view_near', 150_000, "View distance, small objects", "Buildings, vehicles",
      choices=(50_000, 100_000, 150_000, 250_000, 400_000), lo=4096, hi=2_000_000, unit="ft"),
    S('graphics.view_mid', 500_000, "View distance, medium objects", "Airfields, lakes",
      choices=(250_000, 500_000, 750_000, 1_000_000), lo=16384, hi=4_000_000, unit="ft"),
    S('graphics.view_far', 2_000_000, "View distance, large objects", "Terrain, mountains",
      choices=(1_000_000, 2_000_000, 3_000_000, 4_000_000), lo=65536, hi=8_000_000, unit="ft"),
    S('graphics.fog', 5.5, "Haze", "Haze over the distance; 0: none", choices=(0.0, 2.0, 3.5, 5.5, 8.0, 11.0),
      lo=0.0, hi=50.0, labels={0.0: "off"}),
    S('graphics.detail', 1.0, "Surface noise", "Brightness noise on the flat colours; 0: none",
      choices=(0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0), lo=0.0, hi=4.0, labels={0.0: "off"}),
    S('graphics.line_width', 1.0, "Line width", "Masts, antennas, barrels up close; far away and off: 1 pixel",
      choices=(0.0, 0.5, 1.0, 1.5), lo=0.0, hi=16.0, labels={0.0: "off"}, unit="ft"),
    S('graphics.dither', 1.0, "Dither", "Fine noise against colour banding; 0: none",
      choices=(0.0, 0.5, 1.0, 2.0), lo=0.0, hi=4.0, labels={0.0: "off"}),
    S('graphics.saturation', 0.5, "Colour saturation", "Of the game's 16 EGA colours; 1: as the game, 0: grey",
      choices=(0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0), lo=0.0, hi=1.5),
    S('graphics.dots', False, "Ground dots at start", "The game's ground dots around the camera (C)"),
    S('graphics.dots_size', 3.0, "Ground dot size", "Pixels", choices=(1.0, 2.0, 3.0, 4.0, 6.0), lo=1.0, hi=16.0,
      unit="px"),
    S('graphics.dof', False, "Depth of field at start", "Blur away from the screen centre (P)"),
    S('graphics.dof_aperture', 'strong', "Depth of field strength", "(O switches it)",
      choices=('natural', 'strong', 'miniature')),
    S('graphics.dof_quality', 256, "Depth of field quality", "Samples at the largest blur; lower is faster",
      choices=(64, 128, 256, 512), lo=16, hi=1024),
    S('graphics.hud', True, "Text and HUD at start", "(\\ hides them)"),

    # --- camera
    S('camera.fov', 50.0, "Field of view", "Vertical, degrees (Page Up / Page Down zoom, middle button resets)",
      choices=(30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0), lo=5.0, hi=120.0, unit="deg"),
    S('camera.sensitivity', 0.04, "Mouse sensitivity", "Degrees per mouse step",
      choices=(0.01, 0.02, 0.03, 0.04, 0.06, 0.08, 0.12, 0.16), lo=0.001, hi=1.0),
    S('camera.invert_y', False, "Invert mouse", "Mouse forward looks down"),
    S('camera.speed', 1000.0, "Flight speed", "Free camera over the scenery, ft/s at start (the wheel changes it)",
      choices=(250.0, 500.0, 1000.0, 2000.0, 4000.0, 8000.0), lo=1.0, hi=100_000.0, unit="ft/s"),
    S('camera.fast', 25.0, "Fast flight", "Speed times this while the fast key is held",
      choices=(5.0, 10.0, 25.0, 50.0, 100.0), lo=1.0, hi=1000.0, unit="x"),

    # --- live game
    S('live.interpolate', True, "Smooth motion at start", "Blend the game's frames (I); it shows the game "
      "~0.1 s late"),
    S('live.record', True, "Record replays", "Every mission goes to its own file in replays/"),

    # --- keys
    K('keys_general.labels', '\\', "Hide / show text", 'general'),
    K('keys_general.postfx', 'p', "Depth of field on / off", 'general'),
    K('keys_general.aperture', 'o', "Depth of field strength", 'general'),
    K('keys_camera.forward', 'w', "Forward", 'camera'),
    K('keys_camera.back', 's', "Back", 'camera'),
    K('keys_camera.left', 'a', "Left", 'camera'),
    K('keys_camera.right', 'd', "Right", 'camera'),
    K('keys_camera.up', 'q', "Up", 'camera'),
    K('keys_camera.down', 'e', "Down", 'camera'),
    K('keys_camera.fast', 'left shift', "Fast (hold)", 'camera'),
    K('keys_camera.zoom_in', 'page up', "Zoom in", 'camera'),
    K('keys_camera.zoom_out', 'page down', "Zoom out", 'camera'),
    K('keys_camera.sun_stop', 'keypad 5', "Sun: stop / go", 'camera'),
    K('keys_camera.sun_forward', 'keypad 8', "Sun: forward", 'camera'),
    K('keys_camera.sun_back', 'keypad 2', "Sun: back", 'camera'),
    K('keys_camera.sun_reset', 'keypad 0', "Sun: back to the game clock", 'camera'),
    K('keys_world.colors', 'n', "Colours: auto / day / night", 'world'),
    K('keys_world.dots', 'c', "Ground dots on / off", 'world'),
    K('keys_world.dist_near', '1', "Hold + wheel: small objects distance", 'world'),
    K('keys_world.dist_mid', '2', "Hold + wheel: medium objects distance", 'world'),
    K('keys_world.dist_far', '3', "Hold + wheel: large objects distance", 'world'),
    K('keys_live.game_view', 'f1', "Game camera", 'world'),
    K('keys_live.follow', 'f2', "Follow the target", 'world'),
    K('keys_live.look_at', 'f3', "Look at the target", 'world'),
    K('keys_live.ride', 'f4', "Ride with the target", 'world'),
    K('keys_live.free_view', 'f11', "Free camera", 'world'),
    K('keys_live.target_prev', '[', "Previous target", 'world'),
    K('keys_live.target_next', ']', "Next target", 'world'),
    K('keys_live.interpolate', 'i', "Smooth motion on / off", 'world'),
    K('keys_live.step_back', 'left', "Replay: seek back (snaps: previous)", 'world'),
    K('keys_live.step_forward', 'right', "Replay: seek forward (snaps: next)", 'world'),
    K('keys_replay.pause', 'space', "Pause / play", 'world'),
    K('keys_replay.start', 'home', "Back to the start", 'world'),
    K('keys_replay.frame_back', ',', "Previous frame", 'world'),
    K('keys_replay.frame_forward', '.', "Next frame", 'world'),
    K('keys_replay.slower', '-', "Slower", 'world'),
    K('keys_replay.faster', '=', "Faster", 'world'),
    *[K(f'keys_models.{lib.lower()}', f'f{i + 1}', f"Library {lib}", 'models')
      for i, lib in enumerate(("STFLT", "PHOTO", "LB", "PG", "NC", "CE"))],
]
BY_KEY = {s.key: s for s in SCHEMA}
SECTION_TITLES = {
    'display': "Display", 'graphics': "Graphics", 'camera': "Camera", 'live': "Live game",
    'keys_general': "Keys: everywhere", 'keys_camera': "Keys: camera and sky", 'keys_world': "Keys: scenery",
    'keys_live': "Keys: live game and replays", 'keys_replay': "Keys: replays", 'keys_models': "Keys: models",
}

values = {s.key: s.default for s in SCHEMA}
started = {}            # the values when the viewer started (restart settings: what is in effect)
revision = 0            # counts changes of the settings scenes read when they open (not keys, not restart ones)
_codes = {}             # key setting -> pygame key code


# --- reading -----------------------------------------------------------------------------------------------

def get(key):
    return values[key]


def key(name):
    """The pygame key code bound to a key setting (an unknown key name: the default, reported once)."""
    code = _codes.get(name)
    if code is None:
        import pygame as pg
        try:
            code = pg.key.key_code(values[name])
        except ValueError:
            s = BY_KEY[name]
            logger.warning(f"settings.ini [{s.section}] {s.name}: unknown key '{values[name]}', using '{s.default}'")
            values[name] = s.default
            code = pg.key.key_code(s.default)
        _codes[name] = code
    return code


def key_label(value):
    """A key name as the player reads it: 'F1', 'Left Shift', 'Keypad 5', '['."""
    import pygame as pg
    try:
        name = pg.key.name(pg.key.key_code(value)) if value else ''
    except ValueError:
        name = value
    m = re.fullmatch(r'\[(.+)\]', name)
    if m and len(name) > 2:
        name = 'keypad ' + m[1]
    if name == 'enter':
        name = 'keypad enter'
    return name.upper() if len(name) == 1 else name.title()


def label(name):
    """Label of the key bound to a key setting, for help texts."""
    return key_label(values[name])


def is_default(key):
    return values[key] == BY_KEY[key].default


def restart_pending():
    """A setting that needs a restart differs from the one in effect."""
    return any(s.restart and values[s.key] != started.get(s.key, s.default) for s in SCHEMA)


# --- changing ----------------------------------------------------------------------------------------------

def put(key, value, save_now=True):
    global revision
    if values[key] == value:
        return
    values[key] = value
    _codes.pop(key, None)
    s = BY_KEY[key]
    if s.kind != 'key' and not s.restart:
        revision += 1
    if save_now:
        save()


def step(key, d):
    """The next (d = 1) or previous (d = -1) menu choice; bools flip."""
    s = BY_KEY[key]
    v = values[key]
    if s.kind == 'bool':
        put(key, not v)
        return
    if not s.choices:
        return
    choices = s.choices
    if v in choices:
        i = (choices.index(v) + d) % len(choices)
    elif s.kind in ('int', 'float'):            # a value from the file between the menu's steps
        ordered = sorted(choices)
        above = [c for c in ordered if c > v]
        below = [c for c in ordered if c < v]
        put(key, (above[0] if above else ordered[0]) if d > 0 else (below[-1] if below else ordered[-1]))
        return
    else:
        i = 0
    put(key, choices[i])


def reset(key):
    put(key, BY_KEY[key].default)


def reset_section(section):
    for s in SCHEMA:
        if s.section == section:
            put(s.key, s.default, save_now=False)
    save()


def reset_all():
    for s in SCHEMA:
        put(s.key, s.default, save_now=False)
    save()


def clash(a, b):
    """Two key contexts are active together."""
    return frozenset((a, b)) not in SEPARATE_CONTEXTS


def bind(name, code):
    """Bind a key to a key setting. Returns a message for the player: refused, or swapped with the action that had
    the key (it gets this one's old key)."""
    import pygame as pg
    s = BY_KEY[name]
    new = pg.key.name(code)
    if code == pg.K_ESCAPE:
        return "Esc is kept for going back"
    if s.context in ('general', 'camera') and new in MENU_KEYS:
        return f"{key_label(new)} is used by the menu"
    old = values[name]
    if key(name) == code:
        return None
    msg = None
    for other in SCHEMA:
        if other.kind == 'key' and other.key != name and clash(s.context, other.context) and key(other.key) == code:
            put(other.key, old, save_now=False)
            msg = f"{key_label(new)} was '{other.label}' ({SECTION_TITLES[other.section]}): it gets {key_label(old)}"
    put(name, new, save_now=False)
    save()
    return msg


# --- the file ----------------------------------------------------------------------------------------------

def load(path=None):
    """Read the file (write it with the defaults when it is missing)."""
    path = path or PATH
    if not os.path.exists(path):
        save(path)
    else:
        cp = configparser.ConfigParser(interpolation=None, comment_prefixes=(';', '#'), inline_comment_prefixes=None)
        try:
            cp.read(path, encoding='utf-8')
        except configparser.Error as e:
            logger.warning(f"{path}: {e}; using the defaults")
            cp = configparser.ConfigParser()
        for s in SCHEMA:
            if cp.has_option(s.section, s.name):
                text = cp.get(s.section, s.name)
                try:
                    values[s.key] = s.parse(text)
                except ValueError as e:
                    logger.warning(f"settings.ini [{s.section}] {s.name} = {text}: {e}; using {s.format(s.default)}")
    started.update(values)
    _codes.clear()


def save(path=None):
    path = path or PATH
    lines = ["; TripoliRadar settings. Change them here or in the main menu (Settings).",
             "; Delete a line, or the whole file, to get the default back.", ""]
    section = None
    for s in SCHEMA:
        if s.section != section:
            section = s.section
            lines += ["", f"[{section}]"]
        notes = [s.label + (": " + s.help if s.help else "")]
        if s.kind == 'choice':
            notes.append("one of: " + ", ".join(s.choices))
        elif s.kind in ('int', 'float') and s.lo is not None:
            notes.append(f"{s.lo} to {s.hi}")
        notes.append(f"default: {s.format(s.default)}")
        if s.restart:
            notes.append("applies after a restart")
        lines.append("; " + "; ".join(notes))
        lines.append(f"{s.name} = {s.format(values[s.key])}")
    try:
        with open(path, 'w', encoding='utf-8') as f:
            f.write("\n".join(lines) + "\n")
    except OSError as e:
        logger.warning(f"Can't save the settings to {path}: {e}")


load()
