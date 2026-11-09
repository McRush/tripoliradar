import os
import glm
import numpy as np
import pygame as pg
from lofigl import ROOT
from lofigl.model import ObjInstance, TurntableModel
from lofigl.label import BitmapFont, Label
from lofigl.sky import Sky
from scenes import engine_help
import settings

# Model showcase: the models of one .3D3 library stand in two rows along an aisle running north (-Z)
# from the origin and spin slowly; the camera starts at the south end of the aisle, a little above it
# so that flat models (ground tiles, runways) are seen from above. Models wider than SHOWCASE_FIT are
# shown scaled down (theater ground tiles are 4096 units across, aircraft about 64), and the label says so.
LIBRARIES = ["STFLT", "PHOTO", "LB", "PG", "NC", "CE"]    # keys_models.<library> switch between them
SHOWCASE_FIT = 100        # largest horizontal radius shown at full size
SHOWCASE_AISLE = 70       # free half-width of the aisle
SHOWCASE_GAP = 80         # free space between neighbouring models in a row
SHOWCASE_LABEL_DIST = 350 # labels of models farther than this are hidden
SHOWCASE_SPIN = 15.0      # deg/s
SHOWCASE_CAMERA = (0, 45, 220)
SHOWCASE_CAMERA_PITCH = -10

# Names come from the aircraft/weapon type tables in EGAME RAM (near-model and model fields, see gamestate.py);
# 2, 3 and 17 are not referenced by those tables. Other libraries are labelled by model number only.
STFLT_NAMES = {
    0: "MiG-25, MiG-31, F/A-18",
    1: "AIM-9, AIM-120, HARM",
    4: "F/A-18, MiG-23 (simple)",
    5: "aircraft shadow",
    6: "F-19, gear up",
    7: "F-19, gear down",
    8: "F-14",
    9: "An-72",
    10: "MiG-23",
    11: "F-4E",
    12: "Il-76",
    13: "AGM-65 Maverick",
    14: "parachute",
    15: "bomb",
    16: "Yak-38, F-5",
    18: "Tu-95, 767",
    19: "SAM / air-to-air missile",
    20: "MiG-29, Su-27",
    21: "F-19 shadow",
}

class ShowcaseScene:
    def __init__(self, app, library="STFLT"):
        self.app = app
        self.objects = []
        self.labels = {}
        self.showcase = []      # (label name, world position the label hangs over)
        self.library = None
        self.font = BitmapFont(app, os.path.join(ROOT, "cozette_hidpi.json"), "billboards_nearest/cozette_hidpi_atlas")
        self.load_labels()
        self.load_library(library if library in LIBRARIES else LIBRARIES[0])
        self.sky = Sky(self.app, latitude=30.0, day_of_year=0, moon_params={'speed_multiplier': 0.9, 'inclination_deg': 5.0, 'phase_offset': 0.0})
        #self.moon = self.sky.moon
        self.skybox = self.sky.skybox

    def setup_light(self):
        self.app.light.set_initial_position(self.sky.sun_init_pos)

    def add_object(self, obj):
        self.objects.append(obj)

    def clear_showcase(self):
        for obj in self.objects:
            obj.destroy()
        self.objects.clear()
        for name, _pos in self.showcase:
            self.labels.pop(name).release()
        self.showcase.clear()

    def load_library(self, library):
        """Replace the showcase with the models of `library` and put the camera at the start of the aisle."""
        self.clear_showcase()
        self.library = library
        app = self.app
        vbos = app.mesh.vao.vbo
        indices = vbos.f19_models.get(library, [])
        row_end = {-1: 0.0, 1: 0.0}     # z where the next model of each row may start

        # even models on the west side, odd on the east, facing the aisle
        for k, index in enumerate(indices):
            key = f"f19/{library}_{index:02d}"
            parts = [part for part in (key, key + "_lines") if part in vbos.vbos]
            points = np.concatenate([vbos.vbos[part].get_vertex_data()[:, 5:8] for part in parts])
            lo, hi = points.min(axis=0), points.max(axis=0)
            centre = (lo + hi) / 2
            radius = max(float(np.hypot(*(hi - lo)[[0, 2]])) / 2, 1.0)    # horizontal: models spin about Y
            scale = min(1.0, SHOWCASE_FIT / radius)
            shown = radius * scale

            side = 1 if k % 2 else -1
            z = row_end[side] - shown
            row_end[side] = z - shown - SHOWCASE_GAP
            pos = (side * (SHOWCASE_AISLE + shown), 0.0, z)
            for part in parts:
                instance = ObjInstance(pos=pos, rot=(0, 90 * side, 0), scale=(scale,) * 3,
                                       pivot=(float(centre[0]), 0.0, float(centre[2])))
                self.add_object(TurntableModel(app, part, "f19_palette", [instance], speed=SHOWCASE_SPIN))

            name = f"model_{index:02d}"
            text = f"{library} {index:02d}"
            if library == "STFLT" and index in STFLT_NAMES:
                text += f"\n{STFLT_NAMES[index]}"
            if scale < 1.0:
                text += f"\nscale 1/{1 / scale:.{1 if scale > 0.1 else 0}f}"
            self.add_label(name, Label(app, self.font, text, scale=2, color=(1.0, 1.0, 1.0, 1.0)))
            self.showcase.append((name, (pos[0], float(hi[1]) * scale + 8, pos[2])))

        self.labels["title"].set_text(f"F-19 Stealth Fighter: models {library}.3D3 ({len(indices)})\n"
                                      + "  ".join(f"{settings.label('keys_models.' + lib.lower())} {lib}"
                                                  for lib in LIBRARIES))
        camera = app.camera
        camera.position = glm.dvec3(*SHOWCASE_CAMERA)
        camera.yaw, camera.pitch = -90, SHOWCASE_CAMERA_PITCH

    def close(self):
        self.clear_showcase()
        for label in self.labels.values():
            label.release()
        self.labels.clear()
        self.sky.destroy()

    def render(self):
        for obj in self.objects:
            obj.render()
        # the sky and the stars fill only the pixels left at the far plane
        self.skybox.render()
        self.sky.stars.render()

    def handle_event(self, event):
        if event.type != pg.KEYDOWN:
            return
        for lib in LIBRARIES:
            if event.key == settings.key('keys_models.' + lib.lower()):
                self.load_library(lib)
                return

    def add_label(self, name, label):
        self.labels[name] = label

    def load_labels(self):
        self.add_label("title", Label(self.app, self.font, "", pos=(32, 32), scale=2, color=(1.0, 0.9, 0.6, 1.0)))
        help_y = 32 + 5 * self.font.line_height        # below the two title lines at scale 2, plus a gap
        self.add_label("help", Label(self.app, self.font, engine_help(), pos=(32, help_y), scale=1,
                                     color=(1.0, 0.9, 0.6, 1.0)))

    def text_width(self, label):
        lines = label.text.split("\n")
        return max(sum(self.font.get_glyph(c).advance for c in line) for line in lines) * label.scale

    def update_showcase_labels(self):
        """Hang each model's label over it: project the anchor to window pixels, hide it behind the camera."""
        cam = self.app.camera
        w, h = self.app.WIN_SIZE
        m_vp = cam.m_proj * cam.m_view
        for name, pos in self.showcase:
            label = self.labels[name]
            clip = m_vp * glm.vec4(cam.get_offset(pos), 1.0)
            label.visible = 1.0 < clip.w < SHOWCASE_LABEL_DIST
            if not label.visible:
                continue
            lines = label.text.count("\n") + 1
            x = (clip.x / clip.w * 0.5 + 0.5) * w - self.text_width(label) / 2
            y = (0.5 - clip.y / clip.w * 0.5) * h - self.font.line_height * label.scale * lines
            new_pos = (round(x), round(y))
            if new_pos != tuple(label.pos):
                label.set_pos(new_pos)

    def update(self):
        self.sky.update(self.app.delta_time)
        self.app.light.update(glm.vec3(self.app.camera.position), self.sky.m_rot_sun)
        self.update_showcase_labels()
