"""Static theater scenery, walked from the game's own compact map every frame.

The map is the 4-level hierarchy of cell grids in tools/world.py (.3DT groups, .3DG grid and patterns,
built-in level-4 grid). The cells around the camera are picked level by level (with a distance margin, see
DIST_MARGIN), culled by their group's bounding box against a cone somewhat wider than the view (CULL_WIDEN), and
the groups' models are drawn instanced, one batch per (level, model, part). The instances are refilled only when
the camera leaves the margin or the cone, so most frames upload nothing. Models are always drawn at their finest
detail level.

Coordinates: game x east, y north, z up (1 unit ~ 1 ft) -> GL (x, z, -y), so north is -Z.

Precision: each level's instance matrices are relative to an anchor snapped to a 4096 grid near the camera
when it was filled, and are refilled when the camera gets ANCHOR_RANGE away from it. Float32 errors then grow
with the distance from the camera, where they are far below a pixel.

Scenery overrides (live): the game swaps single placed objects for other models - mission special objects at
the start, rubble / wreck when destroyed - in a list keyed by (level, cell, index in the group). Those cells are
filled object by object, the swapped ones drawn with their replacement at the same spot (also over empty
placeholder models). Special objects are PHOTO.3D3 models the game appends to the theater library.

Coplanar ground: like the game, flat ground polygons (at height 0) are painted without depth test,
coarse level to fine, in the order their models appear in the groups; then everything raised is drawn
with depth test. The ground can't hide anything above it while the camera is above ground.
"""
import heapq
import math
import json
import logging
import os
import struct
import time
import glm
import moderngl as mgl
import numpy as np
import pygame as pg
from lofigl import ROOT
from lofigl.model import ArrayInstancedModel
from lofigl.vbo import F19VBO, F19_DECAL_STEP, F19_DECAL_STEP_REL, F19_EXPORT_DIR
from lofigl.texture import f19_colors
from lofigl.points import PointSprites
from lofigl.label import BitmapFont, Label
from lofigl.sky import Sky
from lofigl.camera import DEFAULT_FOV
from world import World, THEATERS, CELL, WORLD, level_scale
from scenes import engine_help
import settings

logger = logging.getLogger(__name__)

LEVELS = (4, 3, 2, 1)                       # draw order: coarse to fine
VIEW_DIST = {3: 'graphics.view_far', 2: 'graphics.view_mid', 1: 'graphics.view_near'}  # world units; level 4 is
                                                    # always drawn whole
DIST_KEYS = {'keys_world.dist_near': 1, 'keys_world.dist_mid': 2, 'keys_world.dist_far': 3}  # hold one and turn the
                                                    # wheel to change that view distance
WHEEL_STEP = 1.25
# The view distances above (and as set with 1/2/3 + wheel) are for the camera's default FoV; at other FoVs they
# scale with the on-screen magnification tan(DEFAULT_FOV / 2) / tan(fov / 2), so objects disappear at about the
# same size on screen; it never goes below 1, wide FoVs keep the reference distances. The scale is rounded to steps of 2^(1/VIEW_SCALE_STEPS) so zooming doesn't refill every frame.
VIEW_SCALE_STEPS = 8
FADE_BAND = 0.15                            # objects fade out over this fraction of their level's view distance
DIST_MARGIN = 0.1                           # cells are picked within view distance * (1 + this), and picked again
                                            # after the camera moves this * view distance (at least a cell)
CULL_WIDEN = 1.2                            # cells are culled against a cone this much wider than the view (tan of
                                            # half the FoV), culled again when the view leaves it or narrows as much
CULL_BEHIND = CELL                          # the cone's apex is this far behind the camera: moving keeps it inside
ANCHOR_RANGE = 2 * CELL                     # a level is refilled when the camera is this far from its anchor
LATITUDE = {'LB': 32.0, 'PG': 27.0, 'NC': 70.0, 'CE': 50.0}
# Day of the year (0 = January 1) per theater: a usable daytime sun and still a real night (sun below about -15 deg
# at midnight, so the stars come out). Noon / midnight sun: LB 35 / -81, PG 40 / -86, CE 55 / -25, NC 24 / -16 deg.
# Winter would mean polar night at North Cape; from mid-April its nights are only twilight.
DAY_OF_YEAR = {'LB': 0, 'PG': 0, 'NC': 90, 'CE': 120}     # NC: April 1, CE: May 1
# Night missions (live / replay, decided once at start) run 20:00 to ~23:00 by the game clock: January 1 puts the
# sun 32..37 deg below the horizon then in every theater, North Cape included.
NIGHT_DAY_OF_YEAR = {'LB': 0, 'PG': 0, 'NC': 0, 'CE': 0}
START = (WORLD / 2, WORLD / 2, 2000)        # game x, y, z
# camera speed: camera.speed (ft/s), Shift x camera.fast
CAMERA_NEAR = 0.5
FOG_REF = 2_000_000                         # fog = 1 - 2^(-density * distance^2), density = (graphics.fog / this)^2;
                                            # fixed, not tied to view distance
FLAT_LAYERS = 16                            # ground polygons may be lifted this many decal steps (vbo.py)
HUD_PERIOD = 15                             # frames between HUD updates
PLACEHOLDER_BOX = 1024                      # model units around an empty model, for the culling box
KEEP_ORIGINAL = {'NC': {70}}                # still drawn under their replacement (the Severomorsk sub dock + wreck)

# Models that turn about their origin: theater -> {model: (GL axis, degrees per second)}. SAM radars spin about the
# vertical in ~5 s; the CE windmill rotor (a disc in the model's y-z plane) about game x (east-west), slowly,
# clockwise seen from the east, as in the game.
RADAR_SPIN = ((0.0, -1.0, 0.0), 72.0)       # clockwise seen from above
SPINNING = {'LB': {49: RADAR_SPIN}, 'PG': {52: RADAR_SPIN}, 'NC': {36: RADAR_SPIN},
            'CE': {38: RADAR_SPIN, 75: ((-1.0, 0.0, 0.0), 45.0)}}

# EGA colour 13 is not a fixed colour: the game flickers it through these colours (fires, blasts, tracers),
# faster than its simulation frames. Here: one step every CYCLE_FRAMES rendered frames.
CYCLE_COLOR = 13
CYCLE_SEQUENCE = (4, 12, 14)                # red, light red, yellow
CYCLE_FRAMES = 2

# Night colours: the game draws the 3D world through a remap table, EGA colour NIGHT_REMAP[i] for model colour i
# (EGAME DS:040C, table 1 of 03FC, copied to 09E8 while [4F1A] = 1). It is not a hardware palette change, so the
# colour 13 flicker stays (13 -> 13). Explosion lines and gun rounds are not remapped (scenes/dynamic.py).
NIGHT_REMAP = (15, 1, 8, 0, 0, 3, 0, 8, 8, 9, 7, 7, 7, 13, 7, 3)
DAY_REMAP = tuple(range(16))
COLOR_MODES = ('auto', 'day', 'night')      # auto = the game's night flag (day without a game)

# Surface detail: world-space brightness noise over the theater scenery (default_instanced_forward.frag).
# Octave i reads channel i of DETAIL_NOISE; its tile spans the texture size times the octave's world units
# per texel. Keep those powers of two, so every octave tiles within the longest period.
DETAIL_NOISE = "RGBA512"                    # value noise, 4 independent channels
DETAIL_OCTAVES = ((2048, 0.35), (256, 0.30), (32, 0.20), (4, 0.15))   # (world units per texel, weight), up to 4
DETAIL_GROUND = 1.0                         # strength on flat ground, all levels (times graphics.detail)
DETAIL_SOLID = {4: 1.0, 3: 1.0, 2: 0.3, 1: 0.3}   # strength on raised parts: hills / buildings and vehicles
DETAIL_COLOR = (                            # max brightness change per EGA colour, as a fraction
    0.00, 0.06, 0.16, 0.08,                 # black, blue (water), green, cyan
    0.06, 0.00, 0.16, 0.08,                 # red, (black), brown, light grey
    0.10, 0.06, 0.16, 0.06,                 # dark grey, light blue, light green, light cyan
    0.06, 0.10, 0.10, 0.04)                 # light red, yellow, yellow, white
DETAIL_SHARPNESS = 4.0                      # triplanar blend exponent: higher = sharper switch between projections
DITHER_NOISE = "LDR_RGBA_0"                 # 1024x1024 blue noise
# screen-space dither against 8-bit banding: graphics.dither, in 8-bit steps (0 = off)

# Ground dots: the game's motion cues, a 'lights' model of 7 points in every empty level-1 cell.
# graphics.dots: shown at start (keys_world.dots toggles them); graphics.dots_size: pixels (the game: 1 of 200 lines)
DOTS_DIST = 40_000                          # world units around the camera
DOTS_BANDS = ((2500, 15), (5000, 7), (None, 8))   # (closer than, EGA colour), as EGAME 0FEF:1A98 picks them


def split_ground(data):
    """Split triangle rows (uv, normal, position) into flat ground at height ~0 and the rest."""
    tris = data.reshape(-1, 3, 8)
    pos = tris[..., 5:8]
    radius = float(np.linalg.norm(pos.reshape(-1, 3).max(axis=0) - pos.reshape(-1, 3).min(axis=0))) / 2
    tol = FLAT_LAYERS * max(F19_DECAL_STEP, F19_DECAL_STEP_REL * radius)
    flat = (np.abs(tris[:, 0, 3]) > 0.999) & (np.abs(pos[..., 1]) <= tol).all(axis=1)
    return tris[flat].reshape(-1, 8), tris[~flat].reshape(-1, 8)


def snap(p):
    """Anchor for instance matrices near GL position p: snapped to the cell grid, at height 0."""
    return (round(p[0] / CELL) * CELL, 0.0, round(p[2] / CELL) * CELL)


def cell_centres(level, coords):
    """GL centres (N, 3) of cells (N, 2) of a level."""
    s = level_scale(level)
    x, y = ((coords[:, i] * CELL + CELL // 2) * s for i in (0, 1))
    return np.stack([x, np.zeros(len(coords)), -y], axis=1).astype('f8')


def near_cells(centres, level, gx, gy, dist):
    """Mask of the cells (GL centres) whose square comes within `dist` of game (gx, gy)."""
    half = CELL * level_scale(level) / 2
    dx = np.maximum(np.abs(centres[:, 0] - gx) - half, 0)
    dy = np.maximum(np.abs(centres[:, 2] + gy) - half, 0)
    return dx * dx + dy * dy <= dist * dist


def gather(start, length, groups):
    """For a flat table of per-group rows (group g's rows are start[g]..start[g] + length[g]): the rows of a
    sequence of groups, and for each row the position in `groups` it comes from."""
    lens = length[groups]
    owner = np.repeat(np.arange(len(groups)), lens)
    first = np.cumsum(lens) - lens
    rows = np.arange(int(lens.sum())) - first[owner] + start[groups][owner]
    return rows, owner


class CullCone:
    """The camera's view widened CULL_WIDEN times (tan of half the FoV, both ways) with its apex CULL_BEHIND
    behind the camera; side planes only, no near or far plane. Cells culled against it stay valid while the
    view is inside it (contains)."""
    def __init__(self, cam):
        r, tx, ty = self.basis(cam)
        self.tan = ty
        w = CULL_WIDEN
        n_eye = np.array([(1, 0, -tx * w), (-1, 0, -tx * w), (0, 1, -ty * w), (0, -1, -ty * w)])
        self.normals = n_eye @ r                        # to world: R^T n for each row n
        self.apex = np.array(cam.position) + r[2] * CULL_BEHIND     # eye +z points backwards

    @staticmethod
    def basis(cam):
        """World-to-eye rotation (rows) and the view's tan of half the FoV across and up."""
        ty = math.tan(math.radians(cam.fov) / 2)
        return np.array(glm.mat3(cam.m_view), dtype='f8'), ty * cam.aspect_ratio, ty

    def contains(self, cam):
        """True if the camera's view lies inside the cone and is not much narrower than it."""
        r, tx, ty = self.basis(cam)
        if self.tan > ty * CULL_WIDEN ** 2:
            return False
        if (self.normals @ (np.array(cam.position) - self.apex) < 0).any():
            return False
        corners = np.array([(sx * tx, sy * ty, -1.0) for sx in (-1, 1) for sy in (-1, 1)]) @ r
        return bool((corners @ self.normals.T >= 0).all())

    def visible(self, lo, hi):
        """Mask of boxes (N, 3) not fully outside any side plane."""
        vis = np.ones(len(lo), dtype=bool)
        for n in self.normals:
            pv = np.where(n > 0, hi, lo) - self.apex     # box corner farthest along the plane normal
            vis &= pv @ n >= 0
        return vis


def source_night(source):
    """The game's night flag at the start of a live / replay / snap source (False if it can't be read)."""
    import gamestate as gs
    try:
        return gs.clock(gs.DS(source.peek()))['night']
    except (OSError, ValueError, struct.error):
        return False


class WorldScene:
    """`source` (tools/live.py) adds the game's moving objects and cameras (scenes/dynamic.py)."""
    def __init__(self, app, theater="LB", source=None):
        self.app = app
        self.flat_ground = True     # flat ground is painted at height 0 without depth writes (see PostFX)
        self.code = theater
        self.world = World(theater)
        self.view_dist = {lv: settings.get(k) for lv, k in VIEW_DIST.items()}  # reference distances at DEFAULT_FOV
        self.view_scale = 1.0               # FoV factor, see VIEW_SCALE_STEPS
        self.eff_dist = dict(self.view_dist)    # the distances used: view_dist * view_scale
        self.detail = settings.get('graphics.detail')
        self.labels = {}
        self.font = BitmapFont(app, os.path.join(ROOT, "cozette_hidpi.json"), "billboards_nearest/cozette_hidpi_atlas")
        self.labels["hud"] = Label(app, self.font, "", pos=(32, 32), scale=1, color=(1.0, 0.9, 0.6, 1.0))
        self.night_mission = source is not None and source_night(source)
        dates = NIGHT_DAY_OF_YEAR if self.night_mission else DAY_OF_YEAR
        self.sky = Sky(app, latitude=LATITUDE[theater], day_of_year=dates[theater],
                       moon_params={'speed_multiplier': 0.9, 'inclination_deg': 5.0, 'phase_offset': 0.0})
        self.skybox = self.sky.skybox

        self.palette = f19_colors()
        self.cycle_phase = None
        self.color_mode = 'auto'
        self.remap = DAY_REMAP      # what f19_palette shows now
        self.load_parts()
        self.load_groups()
        self.load_cells()
        self.make_batches()
        self.setup_detail()
        self.load_dots()
        self.update_fade()

        self.cone = None            # CullCone the cells are culled against
        self.candidates = {}        # level -> cells within view distance (+ margin) of where they were picked
        self.filled = {}            # level -> (cone, candidates, overrides key) its instances were filled for
        self.anchors = {}           # level -> GL position its instance matrices are relative to
        self.overrides = {}         # level -> {(cx, cy): {index in group: replacement model}}
        self.overrides_key = ()
        self.spinning = {}          # (level, source) -> instance positions relative to the level's anchor, of SPINNING models
        self.frame = 0

        cam = app.camera
        x, y, z = START
        cam.position = glm.dvec3(x, z, -y)
        cam.yaw, cam.pitch = -90, -10       # looking north
        cam.near = CAMERA_NEAR
        cam.speed = settings.get('camera.speed') / 1000      # units per ms
        cam.m_proj = cam.get_projection_matrix()
        app.fog_density = (settings.get('graphics.fog') / FOG_REF) ** 2
        self.dynamic = None
        if source is not None:
            from scenes.dynamic import DynamicLayer
            self.dynamic = DynamicLayer(app, source)

    # --- loading -------------------------------------------------------------------------------------

    def load_parts(self):
        """Per model of the theater library: VBO keys of its flat ground part and its other parts,
        and its GL bounding box in model units."""
        self.parts = {}
        self.special_parts = {}     # theater model number -> PHOTO model, see ensure_special_part
        self.own_vbos = []          # keys of the VBOs this scene added to the shared VBOManager (close)
        with open(os.path.join(F19_EXPORT_DIR, self.code + ".json"), encoding="utf-8") as f:
            lib = json.load(f)
        self.placeholders = {m['index'] for m in lib['models']           # empty meshes: special object slots
                             if lib['bodies'][hex(m['body'])]['type'] == 'mesh' and not lib['bodies'][hex(m['body'])]['prims']}
        for index in self.app.mesh.vao.vbo.f19_models.get(self.code, []):
            self.parts[index] = self.make_part(f"f19/{self.code}_{index:02d}")

    def make_part(self, key):
        vbos = self.app.mesh.vao.vbo
        ground, solid, points = None, [], [np.zeros((1, 3))]
        if key in vbos.vbos:
            data = vbos.vbos[key].get_vertex_data()
            points.append(data[:, 5:8])
            flat, rest = split_ground(data)
            if len(flat):
                ground = key + "#ground"
                self.add_vbo(ground, flat)
            if len(rest):
                solid.append(key + "#solid")
                self.add_vbo(key + "#solid", rest)
        if key + "_lines" in vbos.vbos:
            solid.append(key + "_lines")
            points.append(vbos.vbos[key + "_lines"].get_vertex_data()[:, 5:8])
        points = np.concatenate(points)
        return dict(ground=ground, solid=solid, lo=points.min(axis=0).astype('f8'), hi=points.max(axis=0).astype('f8'))

    def add_vbo(self, key, data):
        """A VBO of the scene's own (the same data as long as it has the same key: made once). One left by a
        scene that failed to open is taken over."""
        vbos = self.app.mesh.vao.vbo
        if key not in vbos.vbos:
            vbos.add_vbo(key, F19VBO(self.app.ctx, data))
        if key not in self.own_vbos:
            self.own_vbos.append(key)

    def ensure_special_part(self, model):
        """A mission special object (theater model number past the library): the PHOTO.3D3 model the game loaded
        there. Its part is rebuilt if a later mission puts another PHOTO model at that number."""
        photo = self.dynamic.state.get('specials', {}).get(model) if self.dynamic else None
        if photo is None or self.special_parts.get(model) == photo:
            return
        self.special_parts[model] = photo
        self.parts[model] = self.make_part(f"f19/PHOTO_{photo:02d}")
        for key in [k for k in self.batches if k[1][0] == model]:     # stale batches of the previous PHOTO model
            for b in self.batches.pop(key):
                for lst in [self.solid] + list(self.ground.values()):
                    if b in lst:
                        lst.remove(b)

    def load_groups(self):
        """Per level and group: instance offsets from the cell centre (GL, world units) by source, where
        a source is (model, raised) and raised placements (z != 0) draw their ground part with depth
        test like any other raised geometry; and the group's bounding box around the cell centre."""
        self.groups = {}
        for level in LEVELS:
            s = level_scale(level)
            out = []
            for objs in self.world.t['levels'][level]:
                offsets, items, lo, hi = {}, {}, [], []
                for i, o in enumerate(objs):
                    off = np.array([o['x'], o['z'], -o['y']], dtype='f8') * s
                    src = (o['model'], o['z'] != 0)
                    items[i] = (src, off)       # every object, as the game counts them for overrides
                    part = self.parts.get(o['model'])
                    if part is None or not (part['ground'] or part['solid']):   # dots, lights, placeholders
                        if o['model'] in self.placeholders:
                            lo.append(off - PLACEHOLDER_BOX * s)     # room for a special object put there
                            hi.append(off + PLACEHOLDER_BOX * s)
                        continue
                    offsets.setdefault(src, []).append(off)
                    lo.append(off + part['lo'] * s)
                    hi.append(off + part['hi'] * s)
                if lo:
                    out.append(dict(offsets={k: np.array(v) for k, v in offsets.items()}, items=items,
                                    lo=np.min(lo, axis=0), hi=np.max(hi, axis=0)))
                else:
                    out.append(None)
            self.groups[level] = out

    def load_cells(self):
        """Per level, every cell with something to draw as arrays (pick_cells chooses them by distance): groups,
        cell coords (N, 2), GL centres (N, 3), the groups' bounding boxes around them, and `row`, a grid from
        cell coords (minus the level's first index) to the row (-1: nothing to draw). Also every cell's coords and
        group (`all_cells`, for load_dots), and per level the groups' instances as one flat table (`tables`,
        see gather): source index (into `srcs`) and GL offset from the cell centre of each, grouped by group."""
        self.cells, self.all_cells, self.tables = {}, {}, {}
        for level in LEVELS:
            rng = self.world.cell_range(level)
            every = np.array(list(self.world.cells(level)), dtype=int).reshape(-1, 3)
            self.all_cells[level] = every[:, :2], every[:, 2]
            groups = self.groups[level]
            drawn = np.array([g is not None for g in groups])[every[:, 2]]
            coords, gids = every[drawn, :2], every[drawn, 2]
            centres = cell_centres(level, coords)
            box = lambda k: np.array([g[k] if g else np.zeros(3) for g in groups]).reshape(-1, 3)[gids] + centres
            row = np.full((len(rng), len(rng)), -1, dtype=int)
            row[coords[:, 1] - rng.start, coords[:, 0] - rng.start] = np.arange(len(coords))
            self.cells[level] = dict(groups=gids, coords=coords, centres=centres, lo=box('lo'), hi=box('hi'), row=row)

            srcs = sorted({src for g in groups if g for src in g['offsets']})
            index = {src: i for i, src in enumerate(srcs)}
            start, length, src_ids, offs = [], [], [np.zeros(0, dtype=np.int16)], [np.zeros((0, 3))]
            total = 0
            for g in groups:
                start.append(total)
                n = 0
                for src, o in (g['offsets'].items() if g else ()):
                    src_ids.append(np.full(len(o), index[src], dtype=np.int16))
                    offs.append(o)
                    n += len(o)
                length.append(n)
                total += n
            self.tables[level] = dict(srcs=srcs, start=np.array(start, dtype=int), len=np.array(length, dtype=int),
                                      src=np.concatenate(src_ids), off=np.concatenate(offs))

    def ground_order(self, level):
        """Models with a ground part at a level, ordered so that a model listed later in a group is
        painted later (the game paints group objects in list order). Ties go to the lower model number."""
        models = {m for g in self.groups[level] if g for m, raised in g['offsets']
                  if not raised and self.parts[m]['ground']}
        after = {m: set() for m in models}
        for objs in self.world.t['levels'][level]:
            seq = [o['model'] for o in objs if o['model'] in models and o['z'] == 0]
            for i, a in enumerate(seq):
                after[a].update(b for b in seq[i + 1:] if b != a)
        indeg = {m: 0 for m in models}
        for a in after:
            for b in after[a]:
                indeg[b] += 1
        ready = [m for m in models if indeg[m] == 0]
        heapq.heapify(ready)
        order, cycles = [], 0
        while len(order) < len(models):
            if not ready:               # conflicting orders in different groups: break at the lowest model
                cycles += 1
                m = min(m for m in models if m not in order)
            else:
                m = heapq.heappop(ready)
                if m in order:
                    continue
            order.append(m)
            for b in after[m]:
                indeg[b] -= 1
                if indeg[b] == 0 and b not in order:
                    heapq.heappush(ready, b)
        if cycles:
            logger.info(f"{self.code} level {level}: {cycles} ground order conflicts")
        return order

    def make_batches(self):
        """One ArrayInstancedModel per (level, source, part). Ground batches are kept per level in
        painting order, all the others in one list drawn with depth test."""
        self.batches = {}           # (level, source) -> [ArrayInstancedModel]
        self.ground = {}            # level -> [ArrayInstancedModel] in painting order
        self.solid = []
        for level in LEVELS:
            sources = sorted({src for g in self.groups[level] if g for src in g['offsets']})
            ground = {m: b for m, b in (self.source_batches(level, src) for src in sources) if b}
            self.ground[level] = [ground[m] for m in self.ground_order(level)]

    def source_batches(self, level, src):
        """Make the batches of one source: (model, its ground batch) when it has an unraised ground part,
        for the caller to put in painting order, else (model, None)."""
        model, raised = src
        part = self.parts[model]
        batches = self.batches[(level, src)] = []
        ground = None
        if part['ground']:
            b = ArrayInstancedModel(self.app, part['ground'], "f19_palette",
                                    detail=(DETAIL_SOLID[level] if raised else DETAIL_GROUND) * self.detail)
            batches.append(b)
            if raised:
                self.solid.append(b)
            else:
                ground = b
        for key in part['solid']:
            b = ArrayInstancedModel(self.app, key, "f19_palette", detail=DETAIL_SOLID[level] * self.detail)
            batches.append(b)
            self.solid.append(b)
        return model, ground

    def ensure_batches(self, level, src):
        """Batches for a replacement model the level's groups don't use; its ground part is painted last."""
        if (level, src) in self.batches:
            return
        _, ground = self.source_batches(level, src)
        if ground:
            self.ground[level].append(ground)
        self.update_fade()

    def load_dots(self):
        """Point sprites for the theater's 'lights' models (the ground dots); per level and group, their
        points around the cell centre (GL, world units)."""
        with open(os.path.join(F19_EXPORT_DIR, self.code + ".json"), encoding="utf-8") as f:
            lib = json.load(f)
        lights = {}
        for m in lib["models"]:
            body = lib["bodies"][hex(m["body"])]
            if body["type"] == "lights":
                v = np.array(body["vertices"], dtype='f8').reshape(-1, 3)
                lights[m["index"]] = np.stack([v[:, 0], v[:, 2], -v[:, 1]], axis=1)
        self.dot_groups = {}
        for level in LEVELS:
            s = level_scale(level)
            for g, objs in enumerate(self.world.t['levels'][level]):
                pts = [(np.array([o['x'], o['z'], -o['y']], dtype='f8') + lights[o['model']]) * s
                       for o in objs if o['model'] in lights]
                if pts:
                    self.dot_groups[(level, g)] = np.concatenate(pts)
        palette = self.palette
        self.dots = PointSprites(self.app, size=settings.get('graphics.dots_size'),
                                 bands=[(d, palette[c]) for d, c in DOTS_BANDS],
                                 fade_far=DOTS_DIST, fade_band=FADE_BAND * DOTS_DIST)
        self.dots_on = settings.get('graphics.dots')
        self.dots_key = None
        # per level: the dot groups as a flat table (see gather) and the cells that have dots
        self.dot_tables = {}
        for level in sorted({lv for lv, _ in self.dot_groups}):
            n = len(self.world.t['levels'][level])
            pts = [self.dot_groups.get((level, g), np.zeros((0, 3))) for g in range(n)]
            length = np.array([len(p) for p in pts], dtype=int)
            coords, gids = self.all_cells[level]
            has = length[gids] > 0
            self.dot_tables[level] = dict(start=np.cumsum(length) - length, len=length, pts=np.concatenate(pts),
                                          groups=gids[has], centres=cell_centres(level, coords[has]))

    def update_dots(self, gx, gy):
        """While the dots are shown, refill them around the camera when it moves a quarter of a level-1 cell."""
        if not self.dots_on:
            return
        key = (int(gx // (CELL / 4)), int(gy // (CELL / 4)))
        if key == self.dots_key:
            return
        self.dots_key = key
        anchor = snap((gx, 0.0, -gy))
        found = [np.empty((0, 3))]
        for level, d in self.dot_tables.items():
            near = np.nonzero(near_cells(d['centres'], level, gx, gy, DOTS_DIST))[0]
            rows, owner = gather(d['start'], d['len'], d['groups'][near])
            found.append(d['pts'][rows] + d['centres'][near[owner]] - anchor)
        self.dots.set_points(np.concatenate(found), anchor)

    # --- per frame -----------------------------------------------------------------------------------

    def pick_cells(self, level, gx, gy):
        """The cells within view distance + margin (see DIST_MARGIN) of game (gx, gy), picked again when the
        camera leaves the margin or the distance changes: dict with their rows in self.cells[level] and boxes."""
        dist = self.eff_dist.get(level)
        cand = self.candidates.get(level)
        if cand and cand['dist'] == dist and (dist is None or math.hypot(gx - cand['x'], gy - cand['y']) <= cand['margin']):
            return cand
        c = self.cells[level]
        if dist is None:
            rows, margin = np.arange(len(c['groups'])), 0.0
        else:
            margin = max(DIST_MARGIN * dist, CELL * level_scale(level))
            rows = np.nonzero(near_cells(c['centres'], level, gx, gy, dist + margin))[0]
        cand = self.candidates[level] = dict(dist=dist, x=gx, y=gy, margin=margin, rows=rows,
                                             lo=c['lo'][rows], hi=c['hi'][rows])
        return cand

    def fill_level(self, level, rows):
        """Upload the instances of the given cells (rows of self.cells[level]) to the level's batches, relative
        to its anchor. Cells with scenery overrides go object by object, the others straight from the table."""
        s = level_scale(level)
        c, t = self.cells[level], self.tables[level]
        overrides = self.overrides.get(level, {})
        special = rows[:0]
        if overrides:
            first, n = self.world.cell_range(level).start, len(c['row'])
            ov = [c['row'][cy - first, cx - first] for cx, cy in overrides
                  if 0 <= cx - first < n and 0 <= cy - first < n]
            swapped = np.isin(rows, ov)
            special, rows = rows[swapped], rows[~swapped]
        idx, owner = gather(t['start'], t['len'], c['groups'][rows])
        order = np.argsort(t['src'][idx], kind='stable')      # by source; cell and group order within one
        idx, owner = idx[order], owner[order]
        src = t['src'][idx]
        pos = t['off'][idx] + c['centres'][rows[owner]]
        bounds = np.searchsorted(src, np.arange(len(t['srcs']) + 1))
        found = {t['srcs'][i]: [pos[a:b]] for i, (a, b) in enumerate(zip(bounds[:-1], bounds[1:])) if b > a}
        keep = KEEP_ORIGINAL.get(self.code, ())
        for r in special:
            group, cc = self.groups[level][c['groups'][r]], c['centres'][r]
            swapped = overrides[tuple(c['coords'][r].tolist())]
            for i, (src, off) in group['items'].items():
                if (i not in swapped or src[0] in keep) and src in group['offsets']:
                    found.setdefault(src, []).append((off + cc)[None])
            for i, model in swapped.items():
                if model is not None:
                    self.ensure_special_part(model)
                if i in group['items'] and model in self.parts:
                    src = (model, group['items'][i][0][1])
                    self.ensure_batches(level, src)
                    found.setdefault(src, []).append((group['items'][i][1] + cc)[None])
        anchor = self.anchors[level]
        for (lv, src), batches in self.batches.items():
            if lv != level:
                continue
            pos = np.concatenate(found[src]) - anchor if src in found else np.empty((0, 3))
            if src[0] in SPINNING.get(self.code, {}):
                self.spinning[(level, src)] = pos       # matrices set every frame (update_spinning)
                continue
            mats = np.zeros((len(pos), 16), dtype='f4')
            mats[:, 0] = mats[:, 5] = mats[:, 10] = s
            mats[:, 15] = 1
            mats[:, 12:15] = pos
            for b in batches:
                b.set_instances(mats, anchor)

    def update_scenery(self):
        """Refill a level when its cells were picked again, the cone was replaced, the overrides changed or the
        camera got too far from the level's anchor."""
        cam = self.app.camera
        p = np.array(cam.position)
        gx, gy = p[0], -p[2]
        log = self.app.framelog
        if self.cone is None or not self.cone.contains(cam):
            self.cone = CullCone(cam)
            log.note("cone")
        for level in LEVELS:
            cand = self.pick_cells(level, gx, gy)
            filled, anchor = self.filled.get(level), self.anchors.get(level)
            if (filled and filled[0] is self.cone and filled[1] is cand and filled[2] == self.overrides_key
                    and math.hypot(p[0] - anchor[0], p[2] - anchor[2]) <= ANCHOR_RANGE):
                continue
            t = time.perf_counter() if log.enabled else 0.0
            self.anchors[level] = np.array(snap(p))
            rows = cand['rows'][self.cone.visible(cand['lo'], cand['hi'])]
            self.fill_level(level, rows)
            self.filled[level] = (self.cone, cand, self.overrides_key)
            log.note(f"fill L{level} {len(rows)} cells {(time.perf_counter() - t) * 1000:.1f} ms")
        self.update_dots(gx, gy)

    def update_spinning(self):
        """Instance matrices of the spinning models, turned to the current angle."""
        spin = SPINNING.get(self.code, {})
        for (level, src), pos in self.spinning.items():
            axis, rate = spin[src[0]]
            rot = glm.mat3(glm.rotate(glm.radians(rate * self.app.time), glm.vec3(axis))) * level_scale(level)
            mats = np.zeros((len(pos), 16), dtype='f4')
            cols = np.array(rot.to_list())                  # glm lists columns
            mats[:, 0:3], mats[:, 4:7], mats[:, 8:11] = cols
            mats[:, 15] = 1
            mats[:, 12:15] = pos
            for b in self.batches[(level, src)]:
                b.set_instances(mats, self.anchors[level])

    def update_overrides(self):
        """Take the game's scenery overrides; a change refills the levels (update_scenery)."""
        key = tuple((o['level'], tuple(o['cell']), o['index'], o['model']) for o in self.dynamic.state['scenery'])
        if key == self.overrides_key:
            return
        self.overrides_key = key
        self.overrides = {}
        for level, cell, index, model in key:
            self.overrides.setdefault(level, {}).setdefault(cell, {})[index] = model

    def update_view_scale(self, force=False):
        """Scale the view distances to the camera's FoV; refade when the scale changes."""
        zoom = math.tan(math.radians(DEFAULT_FOV) / 2) / math.tan(math.radians(self.app.camera.fov) / 2)
        scale = 2 ** (max(0, round(math.log2(zoom) * VIEW_SCALE_STEPS)) / VIEW_SCALE_STEPS)
        if scale == self.view_scale and not force:
            return
        self.view_scale = scale
        self.eff_dist = {lv: d * scale for lv, d in self.view_dist.items()}
        self.update_fade()

    def update_fade(self):
        """Fade each level's objects out towards its view distance (level 4, drawn whole, never fades)."""
        for (level, _), batches in self.batches.items():
            dist = self.eff_dist.get(level, 0.0)
            for b in batches:
                b.fade_far, b.fade_band = dist, FADE_BAND * dist

    def update_hud(self):
        cam = self.app.camera
        p = cam.position
        n = sum(b.num_instances for bs in self.batches.values() for b in bs[:1])
        draws = sum(1 for bs in self.batches.values() for b in bs if b.num_instances)
        dist = "  ".join(f"L{lv} {self.eff_dist[lv] / 1000:.0f}k" for lv in (3, 2, 1))
        dist += f" (x{self.view_scale:.2f} at FoV {cam.fov:.0f})"
        k = settings.label
        held = "/".join(k(name) for name in DIST_KEYS)
        self.labels["hud"].set_text(
            engine_help() + "\n"
            + (self.dynamic.help() + "\n" if self.dynamic else "")
            + f"{THEATERS[self.code][0]}   x {p.x:.0f}  y {-p.z:.0f}  alt {p.y:.0f} ft   "
            f"speed {cam.speed * 1000:.0f} ft/s (wheel)\n"
            f"view {dist} (hold {held} + wheel)   dots {'on' if self.dots_on else 'off'} ({k('keys_world.dots')})   "
            f"DOF {self.app.postfx.aperture_name if self.app.postfx_on else 'off'} "
            f"({k('keys_general.postfx')}, {k('keys_general.aperture')})"
            + (f"   frame log {self.app.framelog.status}" if self.app.framelog.enabled else "") + "\n"
            f"{self.sky_text()}\n"
            f"objects {n}   draws {draws}   fps {self.app.clock.get_fps():.0f}"
            + (f"\n{self.dynamic.hud_text()}" if self.dynamic else ""))

    def game_night(self):
        state = self.dynamic.state if self.dynamic else None
        return bool(state and state['clock']['night'])

    def wanted_remap(self):
        night = self.game_night() if self.color_mode == 'auto' else self.color_mode == 'night'
        return NIGHT_REMAP if night else DAY_REMAP

    def update_palette(self):
        """Day / night colours: rewrite f19_palette (all scenery and aircraft) and the dot colours on a change,
        and the flickering colour every CYCLE_FRAMES. f19_palette_day only gets the flicker."""
        remap = self.wanted_remap()
        if self.dynamic:
            self.dynamic.shadows = remap != NIGHT_REMAP
        phase = self.frame // CYCLE_FRAMES % len(CYCLE_SEQUENCE)
        if remap == self.remap and phase == self.cycle_phase:
            return
        if remap != self.remap:
            self.remap = remap
            self.dots.set_bands([(d, self.palette[remap[c]]) for d, c in DOTS_BANDS])
            self.update_hud()
        self.cycle_phase = phase
        colors = [self.palette[CYCLE_SEQUENCE[phase]] if c == CYCLE_COLOR else self.palette[c] for c in remap]
        textures = self.app.mesh.texture.textures
        textures["f19_palette"].write(bytes(round(v * 255) for rgb in colors for v in (*rgb, 1.0)))
        day = self.palette[CYCLE_SEQUENCE[phase]]
        textures["f19_palette_day"].write(bytes(round(v * 255) for v in (*day, 1.0)), viewport=(CYCLE_COLOR, 0, 1, 1))

    def update(self):
        log = self.app.framelog
        if self.dynamic:
            with log.timed('dynamic'):
                self.dynamic.update()   # first: it may move the camera, and it has the game clock
                self.update_overrides()
            if self.dynamic.state:
                self.sky.set_clock(self.dynamic.state['clock']['seconds_of_day'])
        with log.timed('sky'):
            self.sky.update(self.app.delta_time)
            self.update_view_scale()    # after dynamic: the game camera mode sets the FoV
        with log.timed('palette'):
            self.update_palette()
        self.app.light.update(glm.vec3(self.app.camera.position), self.sky.m_rot_sun)
        with log.timed('scenery'):
            self.update_scenery()
            self.update_spinning()
        if self.frame % HUD_PERIOD == 0:
            with log.timed('hud'):
                self.update_hud()
        self.frame += 1

    def frame_log_fields(self):
        return self.dynamic.frame_log_fields() if self.dynamic else {}

    def setup_detail(self):
        """Constant uniforms of the surface detail noise (the two programs are shared by all batches)."""
        textures = self.app.mesh.texture.textures
        self.detail_noise, self.dither_noise = textures[DETAIL_NOISE], textures[DITHER_NOISE]
        size = self.detail_noise.width
        octaves = list(DETAIL_OCTAVES) + [(1, 0.0)] * (4 - len(DETAIL_OCTAVES))
        self.detail_period = size * max(units for units, _ in DETAIL_OCTAVES)
        programs = self.app.mesh.vao.program.programs
        self.detail_programs = [programs['default_instanced_forward'], programs['default_instanced_lines']]
        for prog in self.detail_programs:
            prog['u_detail_noise'] = 1
            prog['u_dither_noise'] = 2
            prog['u_detail_scale'].value = tuple(1.0 / (size * units) for units, _ in octaves)
            prog['u_detail_weight'].value = tuple(weight for _, weight in octaves)
            prog['u_detail_color'].write(np.array(DETAIL_COLOR, dtype='f4'))
            prog['u_detail_sharpness'].value = DETAIL_SHARPNESS
            prog['u_dither'].value = settings.get('graphics.dither')

    def render(self):
        ctx = self.app.ctx
        # noise origin: the camera position wrapped to the longest octave period, in double precision
        p = self.app.camera.position
        origin = tuple(c % self.detail_period for c in (p.x, p.y, p.z))
        for prog in self.detail_programs:
            prog['u_detail_origin'].value = origin
        self.detail_noise.use(location=1)
        self.dither_noise.use(location=2)
        ctx.disable(mgl.DEPTH_TEST)
        self.skybox.render()
        self.sky.stars.render()     # before the ground: flat ground writes no depth, so it must paint over them
        for level in LEVELS:
            for b in self.ground[level]:
                b.render()
        ctx.enable(mgl.DEPTH_TEST)
        for b in self.solid:
            b.render()
        if self.dynamic:
            self.dynamic.render()
        if self.dots_on:
            self.dots.render()

    def setup_light(self):
        self.app.light.set_initial_position(self.sky.sun_init_pos)

    def close(self):
        """Release the scene's GL objects and stop its source (the engine puts back the day palette)."""
        if self.dynamic:
            self.dynamic.close()
        for batches in self.batches.values():
            for b in batches:
                b.destroy()
        self.batches.clear()
        self.ground.clear()
        self.solid.clear()
        vbos = self.app.mesh.vao.vbo.vbos
        for key in self.own_vbos:
            vbo = vbos.pop(key, None)
            if vbo is not None:
                vbo.destroy()
        self.own_vbos.clear()
        self.dots.destroy()
        for label in self.labels.values():
            label.release()
        self.labels.clear()
        self.sky.destroy()

    def sky_text(self):
        t = self.sky.local_time()
        sky = f"sky {int(t // 3600):02d}:{int(t % 3600 // 60):02d}"
        k = settings.label
        keys = (f"{k('keys_camera.sun_forward')}/{k('keys_camera.sun_back')} shift, "
                f"{k('keys_camera.sun_stop')} stop/go")
        if self.sky.clock is not None:
            off = self.sky.offset if self.sky.sun_is_moving else self.sky.sun_time - self.sky.clock_sun_time()
            sign = '-' if off < 0 else '+'
            off = abs(off) % 86400
            sky += (f" (game clock {sign}{int(off // 3600)}:{int(off % 3600 // 60):02d}"
                    f"{'' if self.sky.sun_is_moving else ', stopped'})   {keys}, {k('keys_camera.sun_reset')} reset")
        else:
            sky += f" ({'moving' if self.sky.sun_is_moving else 'stopped'})   {keys}"
        date = "Jan 1" if self.sky.day_of_year == 0 else f"day {self.sky.day_of_year}"
        mode = self.color_mode + (f" ({'night' if self.remap == NIGHT_REMAP else 'day'})"
                                  if self.color_mode == 'auto' else '')
        return (f"{sky}   date {date}{' (night mission)' if self.night_mission else ''}   "
                f"colours {mode} ({k('keys_world.colors')})")

    def handle_event(self, event):
        if self.dynamic and self.dynamic.handle_event(event):
            self.update_hud()
            return
        if event.type == pg.KEYDOWN and event.key == settings.key('keys_world.colors'):
            self.color_mode = COLOR_MODES[(COLOR_MODES.index(self.color_mode) + 1) % len(COLOR_MODES)]
            self.update_hud()
            return
        if event.type == pg.KEYDOWN and event.key == settings.key('keys_world.dots'):
            self.dots_on = not self.dots_on
            self.dots_key = None
            self.update_hud()
            return
        if event.type != pg.MOUSEWHEEL or not event.y:
            return
        factor = WHEEL_STEP ** event.y
        keys = pg.key.get_pressed()
        held = [lv for name, lv in DIST_KEYS.items() if keys[settings.key(name)]]
        if held:
            for lv in held:
                self.view_dist[lv] = max(CELL * level_scale(lv), self.view_dist[lv] * factor)
            self.update_view_scale(force=True)
        else:
            self.app.camera.speed *= factor
        self.update_hud()
