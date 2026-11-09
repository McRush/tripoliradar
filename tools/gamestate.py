"""Game-state reader for EGAME (F-19 flight engine): turns the 64 KB data segment into a dict.

Works on a saved DS snapshot (dosmem.py snap) or live from DOSBox. All field locations come from the
EGAME code (draw loop 0000:AC4C, weapon update 6C66, gun 4956, WLD loader 4F46); see NOTES.md.
Addresses here are those of the Russian build; DS() reads them wherever the data segment's own build
(Russian or English, gamever.py) has them.

Units: x east / y north in world units (map = 2^20 per side, 1 WLD unit = 32), z = altitude in feet,
angles in degrees (heading = compass, clockwise from north). Model numbers index STFLT.3D3
(aircraft, weapons, effects) or the theater .3D3 (targets).

usage: gamestate.py SNAP.bin [--theater LB]   print the state of a snapshot as JSON
       gamestate.py live [HZ]                 print a one-line summary continuously
       gamestate.py live --json               print one JSON state of the running game
"""
import json, os, struct, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from paths import PROJECT_DIR, game_file, world_export_dir
from wld import parse as parse_wld
import gamever

MAP = 1 << 20
WLD_FILES = {'LB': 'LIBYA.WLD', 'PG': 'GULF.WLD', 'NC': 'NC.WLD', 'CE': 'CE.WLD'}

# --- player
PLAYER_X, PLAYER_YS = 0x9666, 0x966C          # s32 world x, s32 y south-up
PLAYER_HDG, PLAYER_PITCH, PLAYER_ROLL, PLAYER_ALT = 0x4700, 0x4702, 0x4704, 0x4706
PLAYER_WLD_X, PLAYER_WLD_Y = 0x950C, 0x951C     # same position in WLD units (y south-up)
AIRSPEED = 0x8578                               # knots (= HUD)
FUEL = 0x4EF6                                   # lbs?, counts down to 0 ("no fuel")
COUNTERMEASURES = dict(flares=0x4EFA, chaff=0x4EFC, decoys=0x4EFE)   # mission start 12, 18, 3
BAYS = 0x4F00                                   # 4 x (catalog id, count)
SELECTED_BAY = 0x4F10
GUN_AMMO = 0x4F12
FRAME_DT = 0x4F22                               # ticks per frame, scales all motion
GUN_FIRING = 0x6656
VIEW_MODE = 0x94FE                              # bit 7 = external view
CATALOG, CATALOG_SIZE = 0x4F24, 26              # name[10], long name[12], weapon type, ?
EJECTED = 0x9468                                # 0 = in the plane; eject key (D82C) sets 1, then +1 per frame (26BD)
CONTROLS = 0x686C                               # key toggles: bit 0 = gear UP ('6'), 1 = '9', 2 = '8', 4 = '5', 5 = '4'
PLAYER_Y = 0x9464                               # s32 world y north (draw position; 8E48 = x)
PLAYER_DRAW_X = 0x8E48                          # where B21D draws the player: = 9666 except in weapon cams (LB23)
GROUND_Z = 0x950A                               # ground/deck height under the player (shadow z)
PANEL = 0x4F20                                  # 1 = cockpit panel shown (cockpit view)
NEAREST = 0x665E                                # distance to the nearest ground site, drives the cockpit zoom-up
HOME_BASE = 0x9762                              # WLD target index of the player's base (carrier if flags & 0x200)
DECK_HALF_X, DECK_HALF_Y = 0x6668, 0x686E       # carrier deck half-extents (world units)
SIM_STEP = 0x5546                               # +1 per simulation frame

# --- mission clock and night (set-up 4B53, clock text 9DA3)
NIGHT = 0x4F1A                                  # 1 = night mission, fixed at mission start (no shadows, night colours)
CLOCK_START = 0x65E0                            # start offset, 2-second units: (mission seed & 15) << 8
CLOCK = 0x6650                                  # 2-second units since start, +1 every [4F22] sim steps
COLOR_TABLES = 0x03FC                           # 4 x 16 B colour remap tables, the active one is copied to 09E8:
COLOR_REMAP = 0x09E8                            # 0 day, 1 night 3D world, 2 night cockpit panel, 3 (weather case)

# --- aircraft (= .WLD recs2)
AC_TABLE, AC_SIZE, AC_COUNT = 0x8870, 36, 0x968E
AC_TYPES = 0x49D6                               # type t at +32t: name[18], vmax, ?, ?, missile, near/far model, kills

# --- weapons in flight
WPN_TABLE, WPN_SIZE, WPN_SLOTS = 0x5422, 24, 12  # slots 0-7 enemy, 8-11 player
WPN_TYPES, WPN_TYPE_SIZE = 0x4C36, 18            # type t: name[8], range, speed, guidance, ?, model

# --- gun rounds
BULLETS, BULLET_SIZE, BULLET_N = 0x9BA6, 12, 0x857C  # [857C]+4 slots

# --- effects
SMOKE, SMOKE_N = 0x5260, 8                     # (x_wld, y_wld, z, pitch angle); x = 0 -> free
SMOKE_FRAME = 0x52A0                           # slot of the newest puff
SMOKE_SOURCE = 0x554E                          # WLD target the smoke column rises from, -1 = none (puffs frozen)
CHUTE_X, CHUTE_Y, CHUTE_Z, CHUTE_SMALL = 0x950E, 0x9522, 0x95EE, 0x8B48
BLAST_X, BLAST_Y, BLAST_Z, BLAST_T = 0x9508, 0x9514, 0x951A, 0x6C72
BLAST_RADIUS = 256                             # world units: 256 / depth px at the game's 256 px focal length

# --- cockpit message line (show_message 92B1, drawn by 85C2)
MESSAGE, MESSAGE_T = 0x958C, 0x587C            # KOI-7 text; frames left, counted down only while drawn
MINI_VIEW = 0x427A                             # non-zero: alternative view mode (halves screen offsets)
MESSAGE_FORCE = 0x4F1C

# --- replacement models used when scenery is destroyed (EGAME 7A10)
RUBBLE_MODEL, WRECK_MODEL = 0x9670, 0x9510     # u8 theater model numbers (LB 74/88, PG 78/88)

# --- WLD targets (16-byte records, loaded verbatim then updated)
TARGETS, TARGET_COUNT = 0x80C8, 0x951E

# --- scenery overrides (EGAME 131E): (u8 level, u8 index in group, u8 cell x, u8 cell y, u16 model offset)
OVERRIDES, OVERRIDE_COUNT = 0x8B56, 0x666A
THEATER_MODEL_OFFSETS = 0x0602                  # u16 per theater .3D3 model
THEATER_MODEL_COUNT = 0x0600                    # models of the theater .3D3 file
# mission special objects (loader 0D5E): 2 x 18 B, the high byte of the first word = PHOTO.3D3 model (0 = none),
# appended to the theater library as models COUNT, COUNT + 1, ...
SPECIALS, SPECIAL_SIZE = 0x87B8, 18

# --- camera (render_view arguments, EGAME 3B95)
CAM_X, CAM_Y, CAM_Z, CAM_HDG, CAM_PITCH, CAM_ROLL = 0x886C, 0x8B44, 0x8B4E, 0x9BA4, 0x9500, 0x8B54


class DS:
    """EGAME's data segment `b` read by Russian-build addresses. `layout` (gamever.Layout) is the build it comes
    from, found from its content when not given; ValueError if it isn't an EGAME data segment."""
    def __init__(self, b, layout=None):
        self.b = b
        self.layout = layout or gamever.of_ds(b)
        if self.layout is None:
            raise ValueError("not an EGAME data segment (no known weapon catalog)")
        self.at = self.layout.ds

    def s16(self, a): return struct.unpack_from('<h', self.b, self.at(a))[0]
    def u16(self, a): return struct.unpack_from('<H', self.b, self.at(a))[0]
    def s32(self, a): return struct.unpack_from('<i', self.b, self.at(a))[0]
    def byte(self, a): return self.b[self.at(a)]
    def bytes(self, a, n): return self.b[self.at(a):self.at(a) + n]
    def unpack(self, fmt, a): return struct.unpack_from(fmt, self.b, self.at(a))
    def raw_text(self, a, n): return self.bytes(a, n).split(b'\0')[0].decode('latin-1').strip()
    def text(self, a, n):
        """Game text as it reads on screen (KOI-7 Russian turned into Cyrillic in the Russian build)."""
        return self.layout.text(self.raw_text(a, n))


def deg(a):
    return round((a & 0xFFFF) * 360 / 65536, 2)


def sdeg(a):
    return round(a * 360 / 65536, 2)


def wld_pos(xw, yw):
    """WLD units (y south-up) -> world x, y north."""
    return xw * 32, MAP - yw * 32


_wld_cache = {}


def load_wld(theater):
    if theater not in _wld_cache:
        _wld_cache[theater] = parse_wld(game_file(WLD_FILES[theater]))
    return _wld_cache[theater]


def detect_theater(d):
    n = d.s16(TARGET_COUNT)
    for th in WLD_FILES:
        if load_wld(th)['n_targets'] == n:
            return th
    return None


def weapon_type(d, t):
    a = WPN_TYPES + WPN_TYPE_SIZE * t
    return dict(name=d.raw_text(a, 8), range=d.s16(a + 8), speed=d.s16(a + 10), guidance=d.s16(a + 12),
                model=d.s16(a + 16))


def aircraft_type(d, t):
    if not 0 <= t <= 18:                                        # 0 = MiG-23 ... 18 = 767, 19 = "none"
        return dict(name='', near_model=None, far_model=None, vmax=None, missile=None, kills=None)
    a = AC_TYPES + 32 * t
    name = (d.raw_text(a, 7) + ' ' + d.raw_text(a + 8, 10)).strip()
    return dict(name=d.layout.text(name), vmax=d.s16(a + 18), missile=d.s16(a + 24), near_model=d.s16(a + 26),
                far_model=d.s16(a + 28), kills=d.s16(a + 30))


def player(d):
    x, ys = d.s32(PLAYER_X), d.s32(PLAYER_YS)
    bays = []
    for k in range(4):
        cid, cnt = d.s16(BAYS + 4 * k), d.s16(BAYS + 4 * k + 2)
        c = CATALOG + CATALOG_SIZE * cid
        bays.append(dict(id=cid, count=cnt, name=d.raw_text(c, 10), long_name=d.text(c + 10, 12),
                         weapon_type=d.s16(c + 22)))
    alt, hdg = d.s16(PLAYER_ALT), d.s16(PLAYER_HDG)
    controls = d.u16(CONTROLS)
    gear_down = not controls & 1
    view = d.u16(VIEW_MODE)
    # draw loop AC4C / B21D: view 0x8B always draws model 6 and no shadow; other external views draw
    # 6 or 7 (gear) at alt + 16, scale 2, and the shadow (model 21, flat) below 1000 ft, not at night ([4F1A]);
    # the night test is left to the renderer, which ties shadows to the palette in use
    model = 6 if view == 0x8B else (7 if gear_down else 6)
    # B232: after ejecting, the crashed plane (alt 0) and its shadow are no longer drawn
    ejected = d.s16(EJECTED)
    visible = not (alt == 0 and ejected)
    shadow_z = d.s16(GROUND_Z)
    shadow = dict(model=21, x=d.s32(PLAYER_DRAW_X), y=d.s32(PLAYER_Y), z=shadow_z, heading=deg(hdg), pitch=0.0, roll=0.0, scale=2,
                  visible=visible and view != 0x8B and (alt & 0xFFFF) < 1000)
    return dict(x=x, y=MAP - ys, z=alt, draw_x=d.s32(PLAYER_DRAW_X), draw_y=d.s32(PLAYER_Y), draw_z=alt + 16,
                heading=deg(hdg), pitch=sdeg(d.s16(PLAYER_PITCH)), roll=sdeg(d.s16(PLAYER_ROLL)),
                airspeed=d.s16(AIRSPEED), fuel=d.s16(FUEL), gun_ammo=d.s16(GUN_AMMO),
                gun_firing=bool(d.s16(GUN_FIRING)), countermeasures={k: d.s16(a) for k, a in COUNTERMEASURES.items()},
                bays=bays, selected_bay=d.s16(SELECTED_BAY), model=model, scale=2, gear_down=gear_down,
                controls=controls, ejected=ejected, visible=visible, shadow=shadow)


def cockpit_boost(d):
    """In the cockpit view, away from ground sites, AI aircraft and weapons are drawn one size step larger
    (draw loop AE6B: panel shown and [665E] + altitude > 1500)."""
    return bool(d.s16(PANEL)) and (d.u16(NEAREST) + d.u16(PLAYER_ALT)) & 0xFFFF > 1500


def draw_scale(arg):
    """draw_object (CC7E) last argument -> model scale: the camera-relative position is shifted by arg - 3."""
    return 2.0 ** (3 - arg)


def camera(d):
    return dict(x=d.s32(CAM_X), y=d.s32(CAM_Y), z=d.s16(CAM_Z), heading=deg(d.s16(CAM_HDG)),
                pitch=sdeg(d.s16(CAM_PITCH)), roll=sdeg(d.s16(CAM_ROLL)),
                view_mode=d.u16(VIEW_MODE), external=bool(d.u16(VIEW_MODE) & 0x80),
                matrix=list(d.unpack('<9h', 0x0AC2)))


def aircraft(d):
    out = []
    boost = cockpit_boost(d)
    scale = draw_scale(2 - boost)
    # shadows sit on the carrier deck (z 128) when the aircraft is over the player's home carrier (AFF6)
    home = d.s16(HOME_BASE)
    hx, hy, hflags = d.unpack('<HHxxH', TARGETS + 16 * home)
    over_deck_dx, over_deck_dy = d.s16(DECK_HALF_X) >> 5, d.s16(DECK_HALF_Y) >> 5
    player_on_deck = d.s16(PLAYER_ALT) == 0x80
    for i in range(d.s16(AC_COUNT)):
        a = AC_TABLE + AC_SIZE * i
        (home, wx, wy, alt, x, ys, hdg, pitch, roll, typ, flags, vmax, f28, missile, on_map, f34) = \
            d.unpack('<hhhhiihhhhHhhhhh', a)
        if not flags & 2:                       # same test as the draw loop (AC4C); +32 is only the MFD-map flag
            continue
        t = aircraft_type(d, typ)
        on_deck = bool(hflags & 0x200) and abs(wx - hx) < over_deck_dx and abs(wy - hy) < over_deck_dy
        shadow = dict(model=5, x=x, y=MAP - ys, z=128 if on_deck else 0, heading=deg(hdg), pitch=0.0, roll=0.0,
                      scale=scale, visible=alt < 999 and (on_deck or not player_on_deck))
        out.append(dict(slot=i, type=typ, name=t['name'], model=t['near_model'], far_model=t['far_model'],
                        scale=scale, shadow=shadow,
                        x=x, y=MAP - ys, z=alt, heading=deg(hdg), pitch=sdeg(pitch), roll=sdeg(roll),
                        flags=flags, shot_down=bool(flags & 0x4000), on_map=on_map == 1,
                        home=home, vmax=vmax, missile=missile,
                        missile_name=weapon_type(d, missile)['name'] if missile else '', f28=f28))
    return out


def weapons(d):
    out = []
    scale = draw_scale(3 - cockpit_boost(d))
    for k in range(WPN_SLOTS):
        a = WPN_TABLE + WPN_SIZE * k
        (xw, yw, z, spd, hdg, pitch, roll, timer, typ, f12, f14, f16) = d.unpack('<HHhhhhhhhhhh', a)
        if timer == 0:
            continue
        t = weapon_type(d, typ)
        x, y = wld_pos(xw, yw)
        w = dict(slot=k, owner='player' if k >= 8 else 'enemy', type=typ, name=t['name'], model=t['model'],
                 scale=scale, x=x, y=y, z=z, heading=deg(hdg), pitch=sdeg(pitch), roll=sdeg(roll),
                 draw_roll=sdeg(((roll + 0x2000 + 0x8000) & 0xFFFF) - 0x8000),       # drawn rolled +45 deg (B17A)
                 speed=spd * 64, timer=timer, on_map=xw < 32768 and yw < 32768, f12=f12)
        if k >= 8:
            w['target'] = f14                                   # WLD target index, -1 = none
        else:
            w['region'] = f14                                   # map cell code, recomputed each frame
            w['launcher'] = (dict(site=f16) if f16 > 0 else dict(aircraft=-f16))
        out.append(w)
    return out


def bullets(d):
    n = d.s16(BULLET_N)
    out = []
    for k in range(n + 4):
        xw, yw, z, dx, dy, dz = d.unpack('<6h', BULLETS + BULLET_SIZE * k)
        if xw == 0:
            continue
        x, y = wld_pos(xw, yw)
        out.append(dict(slot=k, owner='player' if k < n else 'other', x=x, y=y, z=z,
                        velocity=(dx * 32, -dy * 32, dz)))          # per frame step
    return out


def effects(d):
    """Smoke: every slot is one drawn model instance (draw loop ADDC): STFLT 3 for the 4 newest slots, 17 for
    the 4 older ones, at heading 0 / roll 0, pitch = the slot angle, scale 8. Each simulation step while a
    column source exists (EGAME 4AA8): z += 10, y_wld += z >> 9 (drifts south), angle += 0x600 (8.44 deg);
    every 16 steps slot (step >> 4) & 7 is respawned at the source, z 128, random angle. Without a source the
    puffs freeze and one slot is cleared every 2 steps (59AC)."""
    newest = d.u16(SMOKE_FRAME) & 7
    source = d.s16(SMOKE_SOURCE)
    smoke = []
    for k in range(SMOKE_N):
        xw, yw, z, angle = d.unpack('<HHhH', SMOKE + 8 * k)
        if xw == 0:
            continue
        x, y = wld_pos(xw, yw)
        smoke.append(dict(slot=k, model=17 if (newest - k) & 7 >= 4 else 3, x=x, y=y, z=z,
                          heading=0.0, pitch=deg(angle), roll=0.0, scale=draw_scale(0), angle_raw=angle))
    out = dict(smoke=smoke, smoke_source=source, smoke_animating=source != -1)
    cz = d.s16(CHUTE_Z)
    if cz > 0:
        x, y = wld_pos(d.u16(CHUTE_X), d.u16(CHUTE_Y))
        out['parachute'] = dict(x=x, y=y, z=cz, model=14, heading=0.0, pitch=0.0, roll=0.0,
                                scale=draw_scale(4 if d.s16(CHUTE_SMALL) > 0 else 3))
    out['explosion'] = explosion(d)
    return out


def explosion(d):
    """The burst drawn by B61F: screen-space, not a model. While timer != 0, each frame draws 8 lines from the
    projected centre, each in a random colour 12..15 (light red, light magenta, yellow, white):
    - z <= 0 (ground): to len = rand(R) at angle rand(+-67.5 deg) from straight up (world-vertical in the
      cockpit view, where the player's roll is subtracted), i.e. an upward fan;
    - z > 0 (air): to a random point of the square +-R around the centre.
    R = 256 world units at the burst (256 / depth px). |timer| counts down by 1 per frame (<= 8 frames);
    timer > 0 = a target was destroyed (with a message), < 0 = impact / miss / crash."""
    x, y = wld_pos(d.u16(BLAST_X), d.u16(BLAST_Y))
    z, t = d.s16(BLAST_Z), d.s16(BLAST_T)
    return dict(active=t != 0, x=x, y=y, z=z, frames_left=abs(t), timer=t, kill=t > 0, airburst=z > 0,
                radius=BLAST_RADIUS, lines=8, colors=[12, 13, 14, 15])


def clock(d):
    """In-game clock as shown in the cockpit (EGAME 9DA3): t = [65E0] + [6650] in 2-second units; the hour
    is t // 1800 with its tens digit raised by 1 (day, 10:xx) or 2 (night, 20:xx). Night only depends on the
    mission flag, not on the time."""
    t = d.u16(CLOCK_START) + d.u16(CLOCK)
    night = bool(d.s16(NIGHT) & 1)
    h, m, s = 10 * (1 + night) + t // 1800, t // 30 % 60, t * 2 % 60
    return dict(text=f'{h:02d}:{m:02d}:{s:02d}', hours=h, minutes=m, seconds=s, seconds_of_day=h * 3600 + m * 60 + s,
                night=night, world_colors=list(d.bytes(COLOR_TABLES + 16, 16)) if night
                else list(range(16)))


def message(d):
    """Cockpit message line: centred at screen y 24 in white; shown in the cockpit view only (or when [4F1C]
    is set), [587C] frames, the timer only counts down while it is shown."""
    t = d.s16(MESSAGE_T)
    shown_here = (d.u16(VIEW_MODE) == 0 and not d.byte(MINI_VIEW)) or bool(d.s16(MESSAGE_FORCE))
    return dict(text=d.text(MESSAGE, 64).upper(), frames_left=max(t, 0), visible=t > 0 and shown_here)


def targets(d, theater):
    w = load_wld(theater) if theater else None
    text = gamever.installed().text             # the .WLD file is the installed game's
    names = [text(s) for _, s in w['names']] if w else []
    out = []
    for i in range(d.s16(TARGET_COUNT)):
        name_idx, xw, yw, a, flags, b, c, obj = d.unpack('<HHHHHHHH', TARGETS + 16 * i)
        x, y = wld_pos(xw, yw)
        t = dict(index=i, x=x, y=y, model=obj & 0x7F, destroyed=bool(flags & 0x80), flags=flags, a=a, b=b, c=c)
        if names:
            t['name'] = names[name_idx] if name_idx and name_idx < len(names) else ''
            t['kind'] = names[obj & 0x7F] if (obj & 0x7F) < len(names) else ''
        out.append(t)
    return out


_placements = {}


def placements(theater):
    """(level, cell x, cell y, index) -> placement from export/world/<build>/XX.json (tools/world.py), if
    exported."""
    if theater not in _placements:
        path = os.path.join(world_export_dir(), theater + '.json')
        idx = {}
        if os.path.exists(path):
            for p in json.load(open(path, encoding='utf-8'))['placements']:
                idx[(p['level'], p['cell'][0], p['cell'][1], p['index'])] = p
        _placements[theater] = idx
    return _placements[theater]


def specials(d):
    """Theater model number -> PHOTO.3D3 model of the mission special objects loaded after the theater library."""
    out, n = {}, d.u16(THEATER_MODEL_COUNT)
    for k in range(2):
        photo = d.u16(SPECIALS + SPECIAL_SIZE * k) >> 8
        if photo:
            out[n] = photo
            n += 1
    return out


def scenery(d, theater):
    """Placed scenery objects the game has swapped for another model: mission special objects
    (inserted at start) and destroyed objects (wrecks / rubble)."""
    offsets = d.unpack('<128H', THEATER_MODEL_OFFSETS)
    pl = placements(theater) if theater else {}
    out, seen = [], set()
    for k in reversed(range(d.s16(OVERRIDE_COUNT))):        # the game searches from the end: last entry wins
        level, index, cx, cy, off = d.unpack('<BBBBH', OVERRIDES + 8 * k)
        if (level, index, cx, cy) in seen:
            continue
        seen.add((level, index, cx, cy))
        model = offsets.index(off) if off in offsets else None
        # destruction (7A10) swaps in the rubble or the (naval) wreck model; anything else is a mission special
        destroyed = model is not None and model in (d.byte(RUBBLE_MODEL), d.byte(WRECK_MODEL))
        o = dict(level=level, cell=(cx, cy), index=index, model=model, destroyed=destroyed)
        p = pl.get((level, cx, cy, index))
        if p:
            o.update(original_model=p['model'], x=p['x'], y=p['y'], z=p['z'], scale=p['scale'])
        out.append(o)
    return out[::-1]


def state(ds_bytes, theater=None, layout=None):
    d = DS(ds_bytes, layout)
    theater = theater or detect_theater(d)
    return dict(theater=theater, build=d.layout.code, frame_dt=d.s16(FRAME_DT), step=d.u16(SIM_STEP), clock=clock(d), player=player(d), camera=camera(d),
                message=message(d), aircraft=aircraft(d), weapons=weapons(d), bullets=bullets(d), effects=effects(d),
                targets=targets(d, theater), scenery=scenery(d, theater), specials=specials(d))


def summary(s):
    p = s['player']
    parts = [f"{s['clock']['text']}{' night' if s['clock']['night'] else ''}  x {p['x']:7d} y {p['y']:7d} z {p['z']:5d} hdg {p['heading']:5.1f} {p['airspeed']:4d}kt "
             f"{'gear ' if p['gear_down'] else ''}"
             f"fuel {p['fuel']:5d} gun {p['gun_ammo']:4d}"]
    for a in s['aircraft']:
        parts.append(f"A{a['slot']}:{a['name'].split()[0] if a['name'] else a['type']}{'*' if a['shot_down'] else ''}")
    for w in s['weapons']:
        parts.append(f"W{w['slot']}:{w['name']}")
    if s['bullets']:
        parts.append(f"bullets {len(s['bullets'])}")
    if s['effects']['smoke']:
        parts.append(f"smoke {len(s['effects']['smoke'])}")
    if 'parachute' in s['effects']:
        parts.append('chute')
    if s['effects']['explosion']['active']:
        parts.append('BOOM')
    if s['message']['visible']:
        parts.append(f"msg '{s['message']['text']}'")
    wrecks = sum(o['destroyed'] for o in s['scenery'])
    if wrecks:
        parts.append(f"wrecks {wrecks}")
    dead = [t['index'] for t in s['targets'] if t['destroyed']]
    if dead:
        parts.append(f"destroyed {dead}")
    return '  '.join(parts)


if __name__ == '__main__':
    args = sys.argv[1:]
    th = None
    if '--theater' in args:
        i = args.index('--theater'); th = args[i + 1]; del args[i:i + 2]
    if args and args[0] == 'live':
        import time
        from dosmem import Game
        g = Game()
        if '--json' in args:
            print(json.dumps(state(g.read_ds(0, 0x10000), th, g.layout), ensure_ascii=False, indent=1))
        else:
            hz = float(args[1]) if len(args) > 1 else 2
            while True:
                print(summary(state(g.read_ds(0, 0x10000), th, g.layout)), flush=True)
                time.sleep(1 / hz)
    elif args:
        print(json.dumps(state(open(args[0], 'rb').read(), th), ensure_ascii=False, indent=1))
    else:
        print(__doc__)
