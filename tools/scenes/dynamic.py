"""Moving objects of the running game (or a snap) over the world scenery: the player, AI aircraft, weapons in
flight and their shadows, gun rounds, smoke puffs and explosion bursts, as EGAME's draw loop (AC4C) places them,
with STFLT models at their own size (smoke enlarged, see SMOKE_SIZE).

Orientation: game heading is compass (clockwise from north), pitch + = nose up, roll + = right wing down; a
model's nose points north (+y). Rotation = Rz(-heading) Rx(pitch) Ry(roll) in game axes, then to GL by
(x, y, z) -> (x, z, -y) like the vertices (vbo._f19_to_gl). The game camera uses the same angles.

Cameras: F11 free flight (WorldScene's), F1 locked to the game's view (cockpit, external,
weapon cams), F2 free flight following the target object in world axes (it carries the camera along, up stays
world up), F4 free flight in the reference frame of the target (it turns the camera with it, roll included);
F2 / F4 again releases the camera into world axes where it is (fly-bys), its roll easing back to level,
F3 toggles looking at the target (flybys; WASD still move). [ / ] pick the previous / next object (player, AI
aircraft, weapons in flight, parachute) as the target and put the camera behind it; from F1 that switches to F2.
A scene starts in F2 behind the player.
"""
import math
import glm
import numpy as np
import pygame as pg
from lofigl.model import ArrayInstancedModel
from lofigl.vbo import F19VBO, F19_PALETTE_SIZE
import time
import settings
from live import read_state, Interpolator

LIBRARY = "STFLT"
# keys: settings.py [keys_live] (cameras, targets, smooth motion, seek / snaps; Shift seeks x6) and
# [keys_replay] (pause, back to the start, previous / next frame, slower / faster)
GAME_HFOV = 2 * math.degrees(math.atan(160 / 256))     # 320 px wide at the game's 256 px focal length
PLAYER_LIFT = 9                 # the player's z is the ground under its wheels; model 7 reaches 9 below its origin
SHADOW_LIFT = 1.5                 # shadows above the ground (on a carrier deck) are lifted this much over the deck and
                                # its markings and wires (2 decal steps, about 3 units up there: vbo.py build_f19_model);
                                # shadows on flat ground need none, the ground is painted without depth test
NEAR_BEHIND, NEAR_ABOVE, NEAR_EXTRA = 3.0, 0.8, 30     # camera put behind a target: radii back, radii up, + units

def lift_shadow(shadow):
    """A shadow placement, lifted off the carrier deck (SHADOW_LIFT) when it lies on one."""
    return dict(shadow, z=shadow['z'] + SHADOW_LIFT) if shadow['z'] > 0 else shadow


BULLET_LENGTH = 50.0            # world units (feet): a gun round is drawn this long, trailing it,
BULLET_WIDTH = 1.0              # as two crossed strips this wide
BULLET_COLOR = 13               # the flickering EGA colour (WorldScene.update_cycle)
BULLET_VBO = "f19/bullet"

SMOKE_SIZE = 4.0                # the game draws puffs x8 and aircraft x2; x4 keeps that ratio to our 1:1 aircraft
EXPLOSION_RADIUS = 256.0        # world units: the burst's lines reach this far (B61F: R = 256 / depth px)
EXPLOSION_LINES = 8
EXPLOSION_FAN = math.radians(67.5)      # ground bursts: lines within this angle of straight up
EXPLOSION_COLORS = (12, 13, 14, 15)     # light red, (flickering) 13, yellow, white; one at random per line
EXPLOSION_WIDTH = 2.0           # world units, at least
EXPLOSION_MIN_SCREEN = 1 / 400  # and at least this fraction of the screen height (the game's: 1 px of 200)

GAME_TO_GL = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], dtype='f8')


def rotations(heading, pitch, roll):
    """(N, 3, 3) game-axes rotations from angles in degrees."""
    h, p, r = (np.radians(np.asarray(a, dtype='f8')) for a in (heading, pitch, roll))
    ch, sh, cp, sp, cr, sr = np.cos(h), np.sin(h), np.cos(p), np.sin(p), np.cos(r), np.sin(r)
    one, zero = np.ones_like(h), np.zeros_like(h)
    rz = np.stack([ch, sh, zero, -sh, ch, zero, zero, zero, one], -1).reshape(-1, 3, 3)    # Rz(-heading)
    rx = np.stack([one, zero, zero, zero, cp, -sp, zero, sp, cp], -1).reshape(-1, 3, 3)
    ry = np.stack([cr, zero, sr, zero, one, zero, -sr, zero, cr], -1).reshape(-1, 3, 3)
    return rz @ rx @ ry


def gl_point(x, y, z):
    return np.array([x, z, -y], dtype='f8')


def gl_rotation(o):
    """3x3 GL rotation of an object dict."""
    return GAME_TO_GL @ rotations(o['heading'], o['pitch'], o['roll'])[0] @ GAME_TO_GL.T


def model_matrices(objs, origin):
    """(N, 16) column-major GL model matrices of objects (dicts with x, y, z, heading, pitch, roll),
    translated relative to `origin` (GL, double precision)."""
    m = np.zeros((len(objs), 4, 4))
    if not objs:
        return m.reshape(0, 16)
    rot = rotations(*zip(*((o['heading'], o['pitch'], o['roll']) for o in objs)))
    size = np.array([o.get('size', 1.0) for o in objs])[:, None, None]
    m[:, :3, :3] = GAME_TO_GL @ rot @ GAME_TO_GL.T * size
    m[:, :3, 3] = np.array([gl_point(o['x'], o['y'], o['z']) for o in objs]) - origin
    m[:, 3, 3] = 1
    return m.transpose(0, 2, 1).reshape(-1, 16)


def strip_matrices(starts, ends, widths, origin):
    """(N, 16) column-major GL model matrices that map a strip VBO (x 0..1 = length, two crossed strips 1 unit
    wide in y and z) onto segments from `starts` to `ends` (GL points), `widths` world units wide."""
    m = np.zeros((len(starts), 4, 4))
    for i, (a, b, w) in enumerate(zip(starts, ends, widths)):
        v = b - a
        n = np.linalg.norm(v)
        d = v / n if n else np.array((0.0, 0.0, -1.0))
        side = np.cross(d, (0.0, 1.0, 0.0) if abs(d[1]) < 0.9 else (1.0, 0.0, 0.0))
        side /= np.linalg.norm(side)
        m[i, :3, 0] = v
        m[i, :3, 1] = np.cross(side, d) * w
        m[i, :3, 2] = side * w
        m[i, :3, 3] = a - origin
        m[i, 3, 3] = 1
    return m.transpose(0, 2, 1).reshape(-1, 16)


def bullet_matrices(bullets, origin):
    """Each gun round as a strip from its position BULLET_LENGTH back along its velocity."""
    starts, ends = [], []
    for b in bullets:
        v = gl_point(*b['velocity'])
        n = np.linalg.norm(v)
        d = v / n if n else np.array((0.0, 0.0, -1.0))
        starts.append(gl_point(b['x'], b['y'], b['z']))
        ends.append(starts[-1] - d * BULLET_LENGTH)
    return strip_matrices(starts, ends, [BULLET_WIDTH] * len(bullets), origin)


def explosion_lines(e, seed):
    """[(colour, start, end)] GL segments of a burst, as B61F draws it each frame but in 3D: lines from the centre
    in random colours; on the ground an upward fan (length rand(R), within EXPLOSION_FAN of vertical), in the air
    to random points of the +-R cube. `seed` (the simulation step) gives every game frame a new pattern."""
    rng = np.random.default_rng((seed, int(e['x']) & 0xFFFFFFFF, int(e['y']) & 0xFFFFFFFF))
    c = gl_point(e['x'], e['y'], e['z'])
    out = []
    for _ in range(EXPLOSION_LINES):
        if e['airburst']:
            d = rng.uniform(-EXPLOSION_RADIUS, EXPLOSION_RADIUS, 3)
        else:
            tilt, turn = rng.uniform(0, EXPLOSION_FAN), rng.uniform(0, 2 * math.pi)
            d = rng.uniform(0, EXPLOSION_RADIUS) * np.array(
                (math.sin(tilt) * math.cos(turn), math.cos(tilt), math.sin(tilt) * math.sin(turn)))
        out.append((int(rng.choice(EXPLOSION_COLORS)), c, c + d))
    return out


class DynamicLayer:
    @staticmethod
    def help():
        k = settings.label
        return (f"{k('keys_live.game_view')} - Player View, {k('keys_live.follow')} - Toggle Follow, "
                f"{k('keys_live.look_at')} - Toggle Tracking, {k('keys_live.ride')} - Toggle Rotation, "
                f"{k('keys_live.free_view')} - Free Camera")

    def __init__(self, app, source):
        self.app = app
        self.source = source
        self.batches = {}       # VBO key -> ArrayInstancedModel, made when a model first shows up
        self.interp = Interpolator()
        self.interp.enabled = settings.get('live.interpolate')
        self.arrival = None     # the game frame that came in this frame, for the frame log
        self.state = None       # what is drawn: blended between the game's last frames (live source)
        self.shadows = True     # the scene turns them off with the night palette (the game has none at night)
        self.objects = []
        self.mode = 'free'      # 'free' (F11), 'game' (F1), 'follow' (F2, world axes), 'frame' (F4, object axes)
        self.look_at = False    # F3
        self.target = None      # (key, type) of the selected object, see targets()
        self.radii = {}
        self.bullets = self.make_strip_batch(BULLET_VBO, BULLET_COLOR)
        self.blasts = {c: self.make_strip_batch(f"f19/strip_{c}", c) for c in EXPLOSION_COLORS}
        self.poll()
        self.ensure_target()
        self.place_near(self.find_target())
        self.set_mode('follow')     # starts following the player (F2)

    def poll(self):
        if hasattr(self.source, 'replay_state'):    # a replay runs its own clock
            self.state = self.source.replay_state(time.perf_counter(), self.interp.enabled)
            self.objects = self.collect()
            return
        log = self.app.framelog
        with log.timed('source_poll'):
            new = self.source.poll()
        self.arrival = None
        if new is not None:
            b, t = new
            with log.timed('read_state'):
                state = read_state(b)
            if self.app.framelog.enabled:
                prev = self.interp.states[-1] if self.interp.states else (None, None)
                self.arrival = dict(step=state['step'], t=t, prev_t=prev[0],
                                    prev_step=prev[1]['step'] if prev[1] else None, at=time.perf_counter())
            self.interp.add(state, t)
        self.state = self.interp.state(time.perf_counter())
        self.objects = self.collect()

    def frame_log_fields(self):
        """Per-frame fields for the frame log (lofigl/framelog.py): the interpolation state and the game frame that
        arrived this frame, if any (its step count since the previous one, interval, and how long after the game
        made it it got here)."""
        src, ip = self.source, self.interp
        out = dict(mode=self.mode, interp=ip.enabled)
        if hasattr(src, 'replay_state'):
            out.update(content_t=src.t, hold=False, paused=src.paused)
            return out
        newest = ip.states[-1][0] if ip.states else None
        now = time.perf_counter()
        out.update(content_t=ip.shown, hold=ip.held, delay_ms=ip.delay() * 1000,
                   newest_age_ms=(now - newest) * 1000 if newest is not None else None)
        a = self.arrival
        out['new_state'] = a is not None
        if a:
            out['step'] = a['step']
            if a['t'] is not None:
                out['arrival_lag_ms'] = (a['at'] - a['t']) * 1000
            if a['prev_t'] is not None and a['t'] is not None:
                out['game_interval_ms'] = (a['t'] - a['prev_t']) * 1000
                out['correction'] = a['t'] <= a['prev_t']      # a re-read of the same game frame
            if a['prev_step'] is not None:
                out['step_delta'] = (a['step'] - a['prev_step']) & 0xFFFF
        st = getattr(src, 'stats', None)
        if st:
            out.update(aborted=st['aborted'], late=st['late'], guard_ms=src.guard * 1000)
            gap, st['gap'] = st['gap'], 0.0
            if gap > 0.010:                     # the reader thread stopped polling this long
                out['reader_gap_ms'] = gap * 1000
        return out

    def collect(self):
        """[(model, placement)] of everything to draw."""
        s = self.state
        out = []
        p = s['player']
        # like the game: no player in its own cockpit view (the game camera sits inside the model)
        # and none after ejecting once the plane has crashed (alt 0; the game drops it and its shadow)
        if p['visible'] and (self.mode != 'game' or s['camera']['external']):
            out.append((p['model'], dict(p, x=p['draw_x'], y=p['draw_y'], z=p['z'] + PLAYER_LIFT)))
            if p['shadow']['visible'] and self.shadows:
                out.append((p['shadow']['model'], lift_shadow(p['shadow'])))
        for a in s['aircraft']:
            if a['model'] is not None and a['model'] >= 0:
                out.append((a['model'], a))
            if a['shadow']['visible'] and self.shadows:
                out.append((a['shadow']['model'], lift_shadow(a['shadow'])))
        for w in s['weapons']:
            if w['on_map']:
                out.append((w['model'], dict(w, roll=w['draw_roll'])))
        chute = s['effects'].get('parachute')
        if chute:
            out.append((chute['model'], chute))
        for puff in s['effects']['smoke']:
            out.append((puff['model'], dict(puff, size=SMOKE_SIZE)))
        return out

    def make_strip_batch(self, key, color):
        """Two crossed strips along x 0..1, 1 unit wide, in one palette colour (gun rounds, explosion lines).
        Day colours always: the game doesn't remap these at night."""
        vbos = self.app.mesh.vao.vbo
        if key not in vbos.vbos:
            u, w = (color + 0.5) / F19_PALETTE_SIZE, 0.5
            tris = []
            for a, b in (((0, w, 0), (0, -w, 0)), ((0, 0, w), (0, 0, -w))):     # two crossed strips along x
                p = [np.array(a), np.array(b), np.array(b) + (1, 0, 0), np.array(a) + (1, 0, 0)]
                tris += [p[0], p[1], p[2], p[0], p[2], p[3]]
            data = np.array([(u, 0.5, 0, 0, 0, *v) for v in tris], dtype='f4')
            vbos.add_vbo(key, F19VBO(self.app.ctx, data, cull_face=False))
        return ArrayInstancedModel(self.app, key, "f19_palette_day", capacity=32)

    def batch_list(self, model):
        vbos = self.app.mesh.vao.vbo.vbos
        key = f"f19/{LIBRARY}_{model:02d}"
        out = []
        for k in (key, key + "_lines"):
            if k in vbos:
                if k not in self.batches:
                    self.batches[k] = ArrayInstancedModel(self.app, k, "f19_palette", capacity=16)
                    self.app.framelog.note(f"batch {k}")
                out.append(self.batches[k])
        return out

    def fill(self):
        """Upload instances relative to the camera (rebuilt every frame: a few dozen objects)."""
        origin = np.array(self.app.camera.position, dtype='f8')
        by_model = {}
        for model, o in self.objects:
            by_model.setdefault(model, []).append(o)
        used = set()
        for model, objs in by_model.items():
            mats = model_matrices(objs, origin)
            for b in self.batch_list(model):
                b.set_instances(mats, tuple(origin))
                used.add(b.vbo_name)
        for k, b in self.batches.items():
            if k not in used:
                b.num_instances = 0
        self.bullets.set_instances(bullet_matrices(self.state['bullets'], origin), tuple(origin))
        self.fill_explosion(origin)

    def fill_explosion(self, origin):
        """The burst while the game's timer runs (<= 8 game frames), lines at least EXPLOSION_MIN_SCREEN wide."""
        e = self.state['effects']['explosion']
        lines = explosion_lines(e, self.state['step']) if e['active'] else []
        span = 2 * math.tan(math.radians(self.app.camera.fov) / 2) * EXPLOSION_MIN_SCREEN   # at distance 1
        for color, batch in self.blasts.items():
            mine = [(a, b) for c, a, b in lines if c == color]
            widths = [max(EXPLOSION_WIDTH, span * np.linalg.norm((a + b) / 2 - origin))
                      for a, b in mine]
            batch.set_instances(strip_matrices([a for a, _ in mine], [b for _, b in mine], widths, origin),
                                tuple(origin))

    # --- cameras -------------------------------------------------------------------------------------

    def game_camera_view(self):
        """GL position, forward, up of the game's camera."""
        c = self.state['camera']
        r = GAME_TO_GL @ rotations(c['heading'], c['pitch'], c['roll'])[0]
        return gl_point(c['x'], c['y'], c['z']), r @ (0, 1, 0), r @ (0, 0, 1)

    def targets(self):
        """[(key, name, model, placement)] of the objects the camera can follow, the player first."""
        s = self.state
        p = s['player']
        out = [(('player', 0), 'F-19', p['model'], dict(p, x=p['draw_x'], y=p['draw_y'], z=p['z'] + PLAYER_LIFT))]
        for a in s['aircraft']:
            if a['model'] is not None and a['model'] >= 0:
                out.append((('aircraft', a['slot']), a['name'] or f"aircraft {a['slot']}", a['model'], a))
        for w in s['weapons']:
            if w['on_map']:
                out.append((('weapon', w['slot']), w['name'], w['model'], w))
        chute = s['effects'].get('parachute')
        if chute:
            out.append((('parachute', 0), 'parachute', chute['model'], chute))
        return out

    def find_target(self):
        """The selected object, None once its slot is free or holds another type."""
        if self.target:
            key, typ = self.target
            for t in self.targets():
                if t[0] == key and t[3].get('type') == typ:
                    return t
        return None

    def ensure_target(self):
        if self.find_target() is None:
            t = self.targets()[0]
            self.target = t[0], t[3].get('type')

    def radius(self, model):
        if model not in self.radii:
            vbos = self.app.mesh.vao.vbo.vbos
            key = f"f19/{LIBRARY}_{model:02d}"
            pts = vbos[key].get_vertex_data()[:, 5:8] if key in vbos else np.zeros((1, 3))
            self.radii[model] = float(np.linalg.norm(pts, axis=1).max()) or 10.0
        return self.radii[model]

    def place_near(self, target):
        """Behind and above an object (along its heading), looking at it."""
        _, _, model, o = target
        r = self.radius(model)
        back, h = NEAR_BEHIND * r + NEAR_EXTRA, math.radians(o['heading'])
        pos = gl_point(o['x'], o['y'], o['z'])
        eye = gl_point(o['x'] - back * math.sin(h), o['y'] - back * math.cos(h), o['z'] + NEAR_ABOVE * r)
        self.app.camera.roll = 0.0
        self.app.camera.set_view(eye, pos - eye, (0, 1, 0))

    def set_mode(self, mode):
        cam = self.app.camera
        cam.detach()
        self.mode = mode
        cam.locked = mode == 'game'
        if mode == 'game':
            self.look_at = False
            cam.roll = 0.0
            aspect = self.app.WIN_SIZE[0] / self.app.WIN_SIZE[1]
            cam.fov = 2 * math.degrees(math.atan(math.tan(math.radians(GAME_HFOV / 2)) / aspect))
            cam.m_proj = cam.get_projection_matrix()
        elif mode in ('follow', 'frame'):
            o = self.find_target()[3]
            cam.attach(gl_point(o['x'], o['y'], o['z']), self.frame_rotation(o))
        self.objects = self.collect()

    def frame_rotation(self, o):
        """Rotation of the camera's reference frame: the object's own in F4, world axes in F2."""
        return gl_rotation(o) if self.mode == 'frame' else np.eye(3)

    def select(self, step):
        """Next / previous object as the target, the camera put behind it (F1 switches to F2)."""
        ts = self.targets()
        keys = [t[0] for t in ts]
        cur = self.find_target()
        i = (keys.index(cur[0]) + step) % len(ts) if cur else 0
        self.target = ts[i][0], ts[i][3].get('type')
        mode = 'follow' if self.mode == 'game' else self.mode
        self.set_mode('free')
        self.place_near(ts[i])
        self.set_mode(mode)

    def update_camera(self):
        cam = self.app.camera
        t = self.find_target()
        if t is None and (self.mode in ('follow', 'frame') or self.look_at):
            self.look_at = False                # the target is gone: stay where we are
            self.set_mode('free')
        if self.mode == 'game':
            cam.set_view(*self.game_camera_view())
            return
        if t is None:
            return
        o = t[3]
        pos, rot = gl_point(o['x'], o['y'], o['z']), self.frame_rotation(o)
        attached = self.mode in ('follow', 'frame')
        if attached:
            cam.set_frame(pos, rot)
        if self.look_at:
            d = pos - np.array(cam.position)
            up = rot @ (0, 1, 0) if attached else np.array((0.0, 1.0, 0.0))
            if np.linalg.norm(np.cross(d, up)) > 1e-3 * np.linalg.norm(d):
                cam.set_view(cam.position, d, up)
                if attached:
                    cam.attach(pos, rot)

    # --- scene hooks ---------------------------------------------------------------------------------

    def update(self):
        log = self.app.framelog
        with log.timed('dyn_poll'):
            self.poll()
        with log.timed('dyn_camera'):
            self.update_camera()
        with log.timed('dyn_fill'):
            self.fill()

    def close(self):
        """Release the batches (the strip VBOs stay: shared, made once) and close the source (live: its thread)."""
        for b in [*self.batches.values(), self.bullets, *self.blasts.values()]:
            b.destroy()
        self.batches.clear()
        self.blasts.clear()
        close = getattr(self.source, 'close', None)
        if close:
            close()

    def render(self):
        for b in self.batches.values():
            b.render()
        self.bullets.render()
        for b in self.blasts.values():
            b.render()

    def handle_event(self, event):
        """True if the event was used."""
        if event.type != pg.KEYDOWN:
            return False
        k = settings.key
        key = event.key
        replay = hasattr(self.source, 'replay_state')
        if key == k('keys_live.game_view'):
            self.set_mode('game')
        elif key == k('keys_live.free_view'):
            self.look_at = False
            self.set_mode('free')
        elif key in (k('keys_live.follow'), k('keys_live.ride')):
            mode = 'follow' if key == k('keys_live.follow') else 'frame'
            if self.mode == mode:
                self.set_mode('free')
            else:
                self.ensure_target()
                self.set_mode(mode)
        elif key == k('keys_live.look_at'):
            self.ensure_target()
            self.look_at = not self.look_at
            if self.mode == 'game':
                self.set_mode('free')
        elif key in (k('keys_live.target_prev'), k('keys_live.target_next')):
            self.select(-1 if key == k('keys_live.target_prev') else 1)
        elif key == k('keys_live.interpolate'):
            self.interp.enabled = not self.interp.enabled
        elif key in (k('keys_live.step_back'), k('keys_live.step_forward')):
            self.source.step((-1 if key == k('keys_live.step_back') else 1) * (6 if event.mod & pg.KMOD_SHIFT else 1))
        elif replay and key == k('keys_replay.pause'):
            self.source.toggle_pause()
        elif replay and key == k('keys_replay.start'):
            self.source.seek(0.0)
        elif replay and key in (k('keys_replay.frame_back'), k('keys_replay.frame_forward')):
            self.source.frame(-1 if key == k('keys_replay.frame_back') else 1)
        elif replay and key in (k('keys_replay.slower'), k('keys_replay.faster')):
            self.source.faster(-1 if key == k('keys_replay.slower') else 1)
        else:
            return False
        return True

    def hud_text(self):
        s = self.state
        flying = sum(1 for a in s['aircraft'] if a['z'] > 10)
        if not self.interp.enabled:
            interp = "off"
        elif hasattr(self.source, 'replay_state'):
            interp = "on"
        else:
            interp = f"{self.interp.delay() * 1000:.0f} ms behind"
        t = self.find_target()
        name = t[1] if t else 'none'
        k = settings.label
        camera = {'game': f"game ({k('keys_live.game_view')})", 'free': f"free ({k('keys_live.free_view')})",
                  'follow': f"following {name} ({k('keys_live.follow')})",
                  'frame': f"riding with {name} ({k('keys_live.ride')})"}[self.mode]
        if self.look_at:
            camera += f", looking at {name} ({k('keys_live.look_at')})"
        return (f"{self.source.name}   step {s['step']}\n"
                f"interpolation {interp} ({k('keys_live.interpolate')})\n"
                f"camera {camera}   target {name} ({k('keys_live.target_prev')} {k('keys_live.target_next')})   "
                f"aircraft {len(s['aircraft'])} ({flying} airborne)   weapons {sum(w['on_map'] for w in s['weapons'])}   bullets {len(s['bullets'])}   "
                f"smoke {len(s['effects']['smoke'])}{'   BURST' if s['effects']['explosion']['active'] else ''}")
