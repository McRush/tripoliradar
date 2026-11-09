"""Main menu: a tree of text items over a theater's scenery, the camera circling a spot of it with depth of field.
The theater is picked at random at each visit (not the one shown last time); the first visit of a run that had to
prepare the game data, i.e. the first run at an installation, shows Libya: the SAM radar at Tripoli named the app.

Keys: Up / Down select, Enter / Right open, Esc / Backspace / Left back (Esc at the top: quit, see GraphicsEngine).
On a value (Item.value, see scenes/settings_menu.py): Left / Right change it, Enter on a key binding waits for the
new key, Delete puts back the default. The menu reopens where it was left (MenuScene.last_path), the orbit goes on
where it was; leaving Settings after changing what scenes read when they open reopens it (the background shows them).
The items come from the caller (view.py): Item(text, action=fn(app) | children=[Item] or fn() -> [Item] | value,
enabled, note).
"""
import math
import os
import random
import glm
import numpy as np
import pygame as pg
from lofigl import ROOT
from lofigl.label import BitmapFont, Label
from scenes.world_scene import WorldScene, cell_centres
from world import THEATERS
import settings
from version import VERSION

FIRST_RUN_THEATER = "LB"        # the first visit at a new installation: Tripoli and its SAM radar
SPOTS = {'LB': (137219, 604148), 'CE': (500691, 284683), 'PG': (958537, 171866), 'NC': (610640, 592855)}    # game x, y the camera circles and looks at; other theaters: busiest_spot()
ORBIT_RADIUS = 18670            # world units (ft) around the spot
ORBIT_ALTITUDE = 1405
ORBIT_FOV = 11.0                # deg (x4.8 against the default 50)
ORBIT_PERIOD = 240.0            # s per turn, counter-clockwise seen from above
ORBIT_LOOK_HEIGHT = 0.0         # the camera looks at the spot raised by this (ft)
SPOT_WINDOW = 5                 # level-1 cells per side of the square the busiest spot is picked by
APERTURE = 1                    # depth of field aperture in the menu (lofigl/postfx.py DOF_APERTURES)
TEXT_LINES = 40                 # text scale: the window height / this many font lines, rounded (at least 1)

TITLE = "TRIPOLI RADAR"
SUBTITLE = "Classics, rendered anew"
HINT = "Up/Down select   Enter open   Esc back"
HINT_VALUE = "Up/Down select   Left/Right change   Del default   Esc back"
HINT_KEY = "Up/Down select   Enter new key   Del default   Esc back"
HINT_CAPTURE = "Press the new key   Esc cancel"
COLOR_TITLE = (1.0, 0.9, 0.6, 1.0)
COLOR_ITEM = (0.85, 0.85, 0.8, 1.0)
COLOR_SELECTED = (1.0, 0.8, 0.3, 1.0)
COLOR_DISABLED = (0.5, 0.5, 0.5, 1.0)
COLOR_NOTE = (0.7, 0.75, 0.8, 1.0)
COLOR_ERROR = (1.0, 0.45, 0.35, 1.0)
COLOR_INFO = (1.0, 0.9, 0.6, 1.0)
COLOR_SHADOW = (0.0, 0.0, 0.0, 0.8)

UP_KEYS = (pg.K_UP,)
DOWN_KEYS = (pg.K_DOWN,)
OPEN_KEYS = (pg.K_RETURN, pg.K_KP_ENTER, pg.K_RIGHT, pg.K_SPACE)
BACK_KEYS = (pg.K_BACKSPACE, pg.K_LEFT)
DEFAULT_KEYS = (pg.K_DELETE,)


class Item:
    """A menu line: `action(app)` runs it, or `children` (a list, or a function making one when it opens) is a
    submenu, or `value` is a setting shown after the text (text(), step(d), reset(), is_key, bind(code), note,
    changed()). A disabled item is shown greyed and can't be chosen. `note` is shown under the list when selected."""
    def __init__(self, text, action=None, children=None, enabled=True, note="", value=None):
        self.text, self.action, self.children, self.enabled, self.note = text, action, children, enabled, note
        self.value = value

    def open(self):
        return self.children() if callable(self.children) else list(self.children)


def busiest_spot(world):
    """GL position of the centre of the SPOT_WINDOW-square of level-1 cells with the most raised scenery (models
    with more than flat ground: buildings, hangars, vehicles)."""
    c, t = world.cells[1], world.tables[1]
    raised = np.array([bool(world.parts[model]['solid']) for model, _ in t['srcs']] + [False])
    group = np.repeat(np.arange(len(t['len'])), t['len'])
    per_group = np.bincount(group, weights=raised[t['src']], minlength=len(t['len']))
    count = np.zeros(c['row'].shape)
    first = world.world.cell_range(1).start
    count[c['coords'][:, 1] - first, c['coords'][:, 0] - first] = per_group[c['groups']]
    k = SPOT_WINDOW
    summed = np.lib.stride_tricks.sliding_window_view(np.pad(count, k // 2), (k, k)).sum(axis=(2, 3))
    y, x = np.unravel_index(np.argmax(summed), summed.shape)
    return cell_centres(1, np.array([[x + first, y + first]]))[0]


class MenuScene:
    last_path = []              # indices of the open submenus and the selection, kept between visits
    orbit_time = 100.0          # s along the orbit, kept between visits
    theater = None              # the theater in the background (None: no visit yet in this run)
    reopening = False           # the menu reopens itself to show changed settings: same theater

    def __init__(self, app, items, first_run=False):
        """`first_run`: the viewer has just prepared the game data (a new installation), see view.py."""
        self.app = app
        self.flat_ground = True                 # PostFX: the world's flat ground has no depth (see WorldScene)
        theater = MenuScene.theater = self.pick_theater(first_run)
        self.world = WorldScene(app, theater)
        spot = SPOTS.get(theater)
        self.spot = np.array([spot[0], 0.0, -spot[1]]) if spot else busiest_spot(self.world)
        app.camera.locked = True
        app.camera.fov = ORBIT_FOV
        app.camera.m_proj = app.camera.get_projection_matrix()
        app.show_labels = True                  # hidden with '\' in another scene, the menu would be empty
        self.saved_postfx = app.postfx_on, app.postfx.aperture
        app.postfx_on, app.postfx.aperture = True, APERTURE

        self.font = BitmapFont(app, os.path.join(ROOT, "cozette_hidpi.json"), "billboards_nearest/cozette_hidpi_atlas")
        self.scale = max(1, round(app.WIN_SIZE[1] / (TEXT_LINES * self.font.line_height)))
        self.labels = {}
        self.status = (app.error, COLOR_ERROR) if app.error else None     # a message line: (text, colour)
        app.error = None
        self.capturing = None                   # the key binding item waiting for its key (the engine sends all keys)
        self.revision = settings.revision       # settings the scenes read when they open: changed -> reopen

        # the open menus: [(title, items, selected, first shown row)]; reopen the last path where it still fits
        self.stack = [("", items, 0, 0)]
        path = list(MenuScene.last_path)
        for depth, index in enumerate(path):
            items = self.stack[-1][1]
            if not 0 <= index < len(items) or not items[index].enabled:
                break
            self.select(index)
            if depth < len(path) - 1 and items[index].children is not None:
                self.stack.append((items[index].text, items[index].open(), 0, 0))
        self.layout()
        self.update_camera()

    @staticmethod
    def pick_theater(first_run):
        """This visit's theater: the same when the menu reopens itself, FIRST_RUN_THEATER on the first visit of a
        first run, else a random one other than the last."""
        reopening, MenuScene.reopening = MenuScene.reopening, False
        if reopening and MenuScene.theater:
            return MenuScene.theater
        if first_run and MenuScene.theater is None:
            return FIRST_RUN_THEATER
        return random.choice([t for t in THEATERS if t != MenuScene.theater])

    # --- menu --------------------------------------------------------------------------------------------

    def select(self, index):
        title, items, _, top = self.stack[-1]
        top = min(max(top, index - self.max_rows() + 1), index)
        self.stack[-1] = (title, items, index, top)
        MenuScene.last_path = [s[2] for s in self.stack]

    def move(self, step):
        _, items, sel, _ = self.stack[-1]
        enabled = [i for i, item in enumerate(items) if item.enabled]
        if not enabled:
            return
        later = [i for i in enabled if (i - sel) * step > 0]
        self.select(min(later, key=lambda i: abs(i - sel)) if later else (enabled[0] if step > 0 else enabled[-1]))
        self.layout()

    def choose(self):
        _, items, sel, _ = self.stack[-1]
        if not items or not items[sel].enabled:
            return
        item = items[sel]
        self.status = None
        if item.value is not None:
            if item.value.is_key:
                self.capturing = item
            else:
                item.value.step(1)
            self.layout()
        elif item.children is not None:
            self.stack.append((item.text, item.open(), 0, 0))
            self.select(0)
            opened = self.stack[-1][1]
            if opened and not opened[0].enabled:
                self.move(1)
            self.layout()
        elif item.action is not None:
            item.action(self.app)
            self.layout()

    def back(self):
        """Close the open submenu; False at the top."""
        if len(self.stack) == 1:
            return False
        self.stack.pop()
        MenuScene.last_path = [s[2] for s in self.stack]
        self.status = None
        self.layout()
        if len(self.stack) == 1 and settings.revision != self.revision:
            MenuScene.reopening = True
            self.app.switch_scene(self.app.scene_factory)       # the background shows the new settings
        return True

    def selected(self):
        _, items, sel, _ = self.stack[-1]
        return items[sel] if items and items[sel].enabled else None

    # --- drawing -----------------------------------------------------------------------------------------

    def add_text(self, name, text, pos, scale, color):
        """A label with a drop shadow (the shadow first: labels are drawn in order)."""
        off = max(1, scale)
        self.labels[name + "#shadow"] = Label(self.app, self.font, text, pos=(pos[0] + off, pos[1] + off),
                                              scale=scale, color=COLOR_SHADOW)
        self.labels[name] = Label(self.app, self.font, text, pos=pos, scale=scale, color=color)

    def rows(self):
        """Layout in window pixels: x, title y, path y, first item y, item step, note y, error y, hint y."""
        w, h = self.app.WIN_SIZE
        line = self.font.line_height * self.scale
        x, title = round(w * 0.07), round(h * 0.08)
        path = title + 5 * line
        first = path + 2 * line
        hint = h - round(h * 0.06) - line
        error = hint - 3 * line
        note = error - 2 * line
        return x, title, path, first, round(line * 1.5), note, error, hint

    def max_rows(self):
        """Items shown at once; longer lists scroll."""
        _, _, _, first, step, note, _, _ = self.rows()
        return max(3, (note - step - first) // step)

    def layout(self):
        for label in self.labels.values():
            label.release()
        self.labels = {}
        s = self.scale
        line = self.font.line_height * s
        x, title_y, path_y, y, step, note_y, error_y, hint_y = self.rows()
        self.add_text("title", TITLE, (x, title_y), 2 * s, COLOR_TITLE)
        self.add_text("subtitle", SUBTITLE, (x, title_y + 2 * line + line // 2), s, COLOR_NOTE)
        path = " / ".join(t for t, _, _, _ in self.stack[1:])
        if path:
            self.add_text("path", path, (x, path_y), s, COLOR_NOTE)

        _, items, sel, top = self.stack[-1]
        rows = self.max_rows()
        width = max((len(item.text) for item in items if item.value is not None), default=0)
        if top > 0:
            self.add_text("more_up", "  ...", (x, y - step), s, COLOR_DISABLED)
        for row, i in enumerate(range(top, min(len(items), top + rows))):
            item = items[i]
            chosen = i == sel and item.enabled
            color = COLOR_SELECTED if chosen else COLOR_ITEM if item.enabled else COLOR_DISABLED
            text = item.text + (" >" if item.children is not None and item.enabled else "")
            if item.value is not None:
                shown = "press a key..." if item is self.capturing else item.value.text()
                text = f"{item.text:<{width}}   {shown}{' *' if item.value.changed() else ''}"
            self.add_text(f"item_{row}", ("> " if chosen else "  ") + text, (x, y), s, color)
            y += step
        if top + rows < len(items):
            self.add_text("more_down", "  ...", (x, y), s, COLOR_DISABLED)
        item = self.selected()
        note = item.value.note if item and item.value is not None else item.note if item else ""
        if note:
            self.add_text("note", note, (x, note_y), s, COLOR_NOTE)
        status = self.status
        if status is None and settings.restart_pending():
            status = ("Restart the viewer to apply the display settings", COLOR_INFO)
        if status:
            self.add_text("status", status[0], (x, error_y), s, status[1])
        hint = HINT
        if self.capturing:
            hint = HINT_CAPTURE
        elif item and item.value is not None:
            hint = HINT_KEY if item.value.is_key else HINT_VALUE
        self.add_text("hint", hint, (x, hint_y), s, COLOR_NOTE)
        version = f"v{VERSION}"         # bottom right, on the hint line, as far from the edge as the text is
        width = sum(self.font.get_glyph(c).advance for c in version) * s
        self.add_text("version", version, (self.app.WIN_SIZE[0] - x - width, hint_y), s, COLOR_NOTE)

    # --- scene hooks -------------------------------------------------------------------------------------

    def update_camera(self):
        a = 2 * math.pi * MenuScene.orbit_time / ORBIT_PERIOD
        cx, _, cz = self.spot
        pos = glm.dvec3(cx + ORBIT_RADIUS * math.cos(a), ORBIT_ALTITUDE, cz + ORBIT_RADIUS * math.sin(a))
        look = glm.dvec3(cx, ORBIT_LOOK_HEIGHT, cz) - pos
        self.app.camera.set_view(pos, glm.vec3(look), (0.0, 1.0, 0.0))

    def update(self):
        MenuScene.orbit_time += self.app.delta_time * 0.001
        self.update_camera()
        self.world.update()

    def render(self):
        self.world.render()

    def setup_light(self):
        self.world.setup_light()

    def frame_log_fields(self):
        return self.world.frame_log_fields()

    def handle_event(self, event):
        if event.type != pg.KEYDOWN:
            return
        if self.capturing:
            msg = self.capturing.value.bind(event.key)
            self.capturing = None
            self.status = (msg, COLOR_INFO) if msg else None
            self.layout()
            return
        item = self.selected()
        if item and item.value is not None:
            if event.key in (pg.K_LEFT, pg.K_RIGHT) and not item.value.is_key:
                item.value.step(1 if event.key == pg.K_RIGHT else -1)
                self.status = None
                self.layout()
                return
            if event.key in DEFAULT_KEYS:
                item.value.reset()
                self.status = None
                self.layout()
                return
        if event.key in UP_KEYS:
            self.move(-1)
        elif event.key in DOWN_KEYS:
            self.move(1)
        elif event.key in OPEN_KEYS:
            self.choose()
        elif event.key in BACK_KEYS:
            self.back()

    def on_escape(self):
        if self.capturing:
            self.capturing = None
            self.layout()
            return True
        return self.back()

    def say(self, text, color=COLOR_INFO):
        """A message on the status line (actions use it)."""
        self.status = (text, color)
        self.layout()

    def close(self):
        self.app.postfx_on, self.app.postfx.aperture = self.saved_postfx
        for label in self.labels.values():
            label.release()
        self.labels = {}
        self.world.close()
