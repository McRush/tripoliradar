"""Game state for the renderer: EGAME's data segment from saved snaps, the running DOSBox or a replay file,
read through gamestate.py. A source's poll() returns (DS bytes, time) when there is something new to show, else
None; the time (time.perf_counter) is when the game made that frame, None for snaps. Interpolator blends timed
states. A ReplaySource has its own playback clock instead: replay_state() gives the state to draw.

Replay files (replays/*.f19r, written by Recorder while the live viewer runs): b'F19R', u16 version, 2-byte
theater, then one record per game frame: f64 time (s from the first frame), u32 n, n bytes of zlib(DS XOR the
previous record's DS). A record with the same time as the one before is a corrected read of that frame.

The renderer draws models at their own size: gamestate's `scale` (the game enlarged aircraft and weapons
to keep them visible at 320x200) is not used.

Snaps and replay frames are raw data segments of the build that made them (Russian or English EGAME);
gamestate.DS tells them apart by content, so either plays in either installation.
"""
import atexit
import bisect
import glob
import logging
import math
import os
import struct
import threading
import time
import zlib
import numpy as np
import gamestate as gs

logger = logging.getLogger(__name__)

REPLAY_DIR = os.path.join(gs.PROJECT_DIR, 'replays')
REPLAY_MAGIC, REPLAY_VERSION = b'F19R', 1
REPLAY_HEADER, REPLAY_RECORD = struct.Struct('<4sH2s'), struct.Struct('<dI')
DS_SIZE = 0x10000               # a data segment: every replay frame is one
REPLAY_MAX_FRAMES = 200_000     # all frames are kept in memory (13 GB): ~4-7 h of flight at the game's ~8-13 fps


class SnapSource:
    """Saved DS snapshots (dosmem.py snap), one shown at a time; step() moves through them."""
    def __init__(self, patterns):
        self.paths = []
        for p in patterns:
            self.paths += sorted(glob.glob(p)) or [p]      # cmd.exe doesn't expand wildcards
        self.index = 0
        self.changed = True
        self.cache = {}

    def read(self, i):
        if i not in self.cache:
            with open(self.paths[i], 'rb') as f:
                self.cache[i] = f.read()
        return self.cache[i]

    def keep_theater(self, theater):
        """Drop the snaps of other theaters (the scenery is built for one)."""
        keep = [p for i, p in enumerate(self.paths) if ds_theater(self.read(i)) == theater]
        if len(keep) < len(self.paths):
            logger.info(f"Skipped {len(self.paths) - len(keep)} snaps of other theaters than {theater}")
        self.paths, self.cache, self.index = keep, {}, 0

    def peek(self):
        return self.read(self.index)

    def poll(self):
        if not self.changed:
            return None
        self.changed = False
        return self.peek(), None

    def step(self, k):
        i = max(0, min(len(self.paths) - 1, self.index + k))
        self.changed, self.index = i != self.index, i

    @property
    def name(self):
        import settings
        keys = f"{settings.label('keys_live.step_back')}/{settings.label('keys_live.step_forward')}"
        return f"snap {os.path.basename(self.paths[self.index])} ({self.index + 1}/{len(self.paths)}, {keys})"


# --- frame sync with EGAME's main loop (1CDE): render 33D9 (camera set up first, then render_view 3B95),
# set [3F33] = 1 (page flip pending, cleared by the timer IRQ), 215C, simulation 3DC2 (inc [5546] near its end).
# (Russian-build addresses, read through the build's layout: the English build has the flag at 45DE.)
# Measured live (~13 fps): [3F33] rises, ~6 ms of object writes, [5546] increments, the camera is recomputed,
# then ~70 ms of drawing in which nothing we read changes. A read is consistent only in that quiet window:
# between the camera set-up and the next rise of [3F33].
FLIP_PENDING, SIM_STEP = 0x3F33, gs.SIM_STEP
POLL_PERIOD = 0.0005            # marker polling, s (~1 kHz with a 1 ms system timer)
GUARD_START, GUARD_MIN, GUARD_MAX = 0.004, 0.002, 0.030   # wait after the step increment, s; self-adjusting
GUARD_STEP_UP, GUARD_STEP_DOWN, GUARD_CALM = 0.002, 0.0005, 50   # +up on a late write, -down after CALM good frames
VERIFY_AFTER = 0.015            # re-read the dynamic tables this long after a capture, s
IDLE_AFTER, IDLE_PERIOD = 0.5, 0.25     # no simulation step for this long (paused, menus): plain reads, s
DYNAMIC = [                     # what the renderer reads; the verification compares these bytes
    (gs.CAM_X, 4), (gs.CAM_Y, 4), (gs.CAM_Z, 2), (gs.CAM_HDG, 2), (gs.CAM_PITCH, 2), (gs.CAM_ROLL, 2),
    (gs.PLAYER_X, 4), (gs.PLAYER_YS, 4), (gs.PLAYER_HDG, 8), (gs.PLAYER_DRAW_X, 4), (gs.PLAYER_Y, 4),
    (gs.AC_TABLE, gs.AC_SIZE * 16), (gs.WPN_TABLE, gs.WPN_SIZE * gs.WPN_SLOTS)]


def dynamic_bytes(b, layout):
    return b''.join(b[layout.ds(a):layout.ds(a) + n] for a, n in DYNAMIC)


def xor(a, b):
    return (np.frombuffer(a, np.uint8) ^ np.frombuffer(b, np.uint8)).tobytes()


class Recorder:
    """Appends the frames of one EGAME session to a new replay file. Every record is flushed, so a crash loses
    at most the frame being written (load_replay drops a cut-off record)."""
    def __init__(self, theater):
        os.makedirs(REPLAY_DIR, exist_ok=True)
        self.path = os.path.join(REPLAY_DIR, f"{theater}_{time.strftime('%Y-%m-%d_%H-%M-%S')}.f19r")
        self.f = open(self.path, 'wb')
        self.f.write(REPLAY_HEADER.pack(REPLAY_MAGIC, REPLAY_VERSION, theater.encode('ascii')))
        self.prev, self.t0, self.t = bytes(0x10000), None, 0.0
        self.frames, self.size = 0, REPLAY_HEADER.size

    def write(self, b, t):
        if self.t0 is None:
            self.t0 = t
        data = zlib.compress(xor(b, self.prev), 1)
        self.f.write(REPLAY_RECORD.pack(t - self.t0, len(data)) + data)
        self.f.flush()
        self.prev, self.t = b, t - self.t0
        self.frames += 1
        self.size += REPLAY_RECORD.size + len(data)

    def close(self):
        self.f.close()
        if self.frames:
            logger.info(f"Replay saved: {self.path} ({self.frames} frames, {self.t:.1f} s, {self.size / 1e6:.1f} MB)")
        else:
            os.remove(self.path)


class LiveSource:
    """The running game. A background thread follows the game loop through two markers and reads the whole
    data segment (one 64 KB ReadProcessMemory) once per game frame, in the quiet window where the camera and the
    objects agree; poll() hands out each new frame once. Frames are published only while EGAME is loaded (its
    code still in place after the read) and in the viewer's theater. While recording they also go to a replay
    file, a new one for each EGAME session (mission)."""
    def __init__(self, record=False):
        from dosmem import Game
        self.game = Game()
        self.error = None               # why the game can't be read now (see lost); None: it can
        self.reported = set()           # unexpected errors already logged with a traceback
        self.dosbox_found = True        # False: no single DOSBox process while waiting for a mission
        self.theater = None
        self.other_theater = False      # the game is in a mission of another theater: nothing is shown
        self.recording, self.recorder, self.saved = record, None, 0
        self.lock = threading.Lock()
        self.frame = (1, self.peek(), time.perf_counter())     # (sequence number, DS bytes, frame time)
        self.taken = 0
        self.guard = GUARD_START
        self.stats = dict(frames=0, aborted=0, late=0, idle=False, fps=0.0, gap=0.0)    # gap: longest poll pause
        self.timer_period = False
        try:                                    # 1 ms sleeps (SDL usually sets this too)
            import ctypes
            self.timer_period = ctypes.WinDLL('winmm').timeBeginPeriod(1) == 0
        except OSError:
            pass
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()
        atexit.register(self.close)        # in case the viewer leaves without closing its scene

    def peek(self):
        b = self.game.read_ds(0, 0x10000)
        if not self.game.alive():
            raise OSError("EGAME is not running")
        return b

    def keep_theater(self, theater):
        self.theater = theater

    def close(self):
        """Stop the reader thread and finish the replay being recorded. Safe to call twice."""
        self.stopped.set()
        if self.thread is not threading.current_thread():
            self.thread.join(timeout=2.0)
        with self.lock:
            self.recording = False
            self.stop_recording()
        self.game.close()
        if self.timer_period:
            import ctypes
            ctypes.WinDLL('winmm').timeEndPeriod(1)
            self.timer_period = False
        atexit.unregister(self.close)

    def stop_recording(self):
        if self.recorder:
            self.recorder.close()
            self.recorder = None
            self.saved += 1

    def markers(self):
        flag, step = self.game.read_var(FLIP_PENDING, 1)[0], int.from_bytes(self.game.read_var(SIM_STEP, 2), 'little')
        if not self.game.alive():
            raise OSError("EGAME is not running")
        return flag, step

    def publish(self, b, t):
        """Hand out a frame if it is a good one (EGAME may be just setting up a mission)."""
        theater = gs.detect_theater(gs.DS(b, self.game.layout))
        self.other_theater = self.theater is not None and theater != self.theater
        if theater is None or self.other_theater:
            return
        try:
            read_state(b, self.game.layout)
        except (struct.error, LookupError, ValueError):    # garbage: tables point outside the data segment
            return                                          # (English build: KeyError, gamever.Layout.ds)
        with self.lock:
            self.frame = (self.frame[0] + 1, b, t)
            if self.recording:
                if self.recorder is None:
                    self.recorder = Recorder(theater)
                    logger.info(f"Recording to {self.recorder.path}")
                self.recorder.write(b, t)

    def run(self):
        """Follow the game. When it can't be read (the mission is over and EGAME gone, DOSBox closed, or an
        unexpected error) the last frame stays on screen, the replay file is finished, and EGAME is looked for
        again every second. Only close() stops the reader."""
        while not self.stopped.is_set():
            try:
                self.follow()
            except Exception as e:
                self.lost(e)
            if self.stopped.wait(1.0):
                break
            self.reconnect()

    def lost(self, e):
        """The game stopped being readable: `e` says why (OSError: EGAME or DOSBox gone; else a bug, logged with
        its traceback once per kind)."""
        if self.error is None:
            if isinstance(e, OSError):
                logger.info(f"EGAME is gone ({e}): showing its last frame, waiting for the next mission")
            elif repr(e) not in self.reported:
                self.reported.add(repr(e))
                logger.error("Game reader error: showing the last frame, waiting for the next mission", exc_info=e)
        self.error = e
        with self.lock:
            self.stop_recording()           # the next mission goes to a new file

    def reconnect(self):
        """Take a new Game if DOSBox is running EGAME again (a new mission, or the same one after an error)."""
        from dosmem import find_dosbox_pid
        try:
            pid = find_dosbox_pid()
        except (OSError, RuntimeError):     # none, or more than one
            self.dosbox_found = False
            return
        self.dosbox_found = True
        try:
            game = type(self.game)(pid)
        except (OSError, RuntimeError):     # DOSBox is in its own menus, debriefing (END.EXE)...
            return
        if self.stopped.is_set():           # closed meanwhile: close() has released the old one already
            game.close()
            return
        old, self.game, self.error = self.game, game, None
        old.close()

    def follow(self):
        """[3F33] is usually still 1 (flip pending) for a few ms after the step increment, while the camera is
        set up: that's fine. The next simulation has started once [3F33] rises again after being seen at 0."""
        st = self.stats
        _, prev_step = self.markers()
        step_time = last_read = time.perf_counter()
        captured, verify, seen_clear = True, None, False    # the step seen at start may be mid-frame: skip it
        calm, frame_times = 0, []

        def still_quiet(flag_before):
            flag, step = self.markers()
            return step == prev_step and not (flag and not flag_before)

        last = time.perf_counter()
        while not self.stopped.is_set():
            time.sleep(POLL_PERIOD)
            now = time.perf_counter()
            st['gap'], last = max(st['gap'], now - last), now   # the frame log takes and resets it
            flag, step = self.markers()
            if step != prev_step:
                prev_step, step_time, captured, verify, seen_clear = step, now, False, None, False
                frame_times = (frame_times + [now])[-16:]
                if len(frame_times) > 1:
                    st['fps'] = (len(frame_times) - 1) / (frame_times[-1] - frame_times[0])
                st['idle'] = False
            if not flag:
                seen_clear = True
            elif seen_clear:                    # the next simulation is running: too late for this frame
                if not captured:
                    st['aborted'] += 1
                    captured = True
                verify = None
                continue
            if not captured and now - step_time >= self.guard:
                b = self.peek()
                captured = True
                if still_quiet(flag):
                    self.publish(b, step_time)  # the frame's time: when its step was seen (+-1 ms)
                    st['frames'] += 1
                    verify, last_read = (now + VERIFY_AFTER, dynamic_bytes(b, self.game.layout)), now
                else:
                    st['aborted'] += 1
            elif verify and now >= verify[0]:
                b = self.peek()
                if still_quiet(flag):
                    if dynamic_bytes(b, self.game.layout) != verify[1]:   # still written after the capture: wait longer
                        self.publish(b, step_time)
                        st['late'] += 1
                        self.guard = min(GUARD_MAX, self.guard + GUARD_STEP_UP)
                        calm = 0
                    else:
                        calm += 1
                        if calm >= GUARD_CALM:
                            self.guard = max(GUARD_MIN, self.guard - GUARD_STEP_DOWN)
                            calm = 0
                verify = None
            elif now - step_time > IDLE_AFTER and now - last_read > IDLE_PERIOD:
                st['idle'] = True               # the game loop isn't running: nothing moves
                b = self.peek()
                if b != self.frame[1]:
                    self.publish(b, now)
                last_read = now

    def poll(self):
        with self.lock:
            seq, b, t = self.frame
        if seq == self.taken:
            return None
        self.taken = seq
        return b, t

    def step(self, k):
        pass

    @property
    def name(self):
        r = self.recorder
        if r:
            rec = f"   REC {clock(r.t)} {r.size / 1e6:.1f} MB"
        else:
            rec = f"   recording {'on' if self.recording else 'off'}"
        if self.saved:
            rec += f", {self.saved} saved"
        if self.error:
            why = "mission over" if isinstance(self.error, OSError) else "game read error (see view.log)"
            wait = "waiting for the next mission" if self.dosbox_found else "DOSBox not found"
            return f"{why}: showing the last frame, {wait}" + rec
        if self.other_theater:
            return f"the game is in another theater than {self.theater}: restart the viewer" + rec
        st = self.stats
        sync = "paused" if st['idle'] else f"{st['fps']:.1f} fps"
        return (f"DOSBox {sync}, guard {self.guard * 1000:.1f} ms, late {st['late']}, "
                f"missed {st['aborted']} of {st['frames'] + st['aborted']}" + rec)


def clock(t):
    return f"{t // 60:.0f}:{t % 60:04.1f}"


def inflate_frame(chunk):
    """The 64 KB of a replay record; None if it doesn't inflate to exactly that (corrupt or hostile file: the
    output is capped, so a small record can't expand to gigabytes)."""
    z = zlib.decompressobj()
    try:
        out = z.decompress(chunk, DS_SIZE)
        if len(out) != DS_SIZE or z.decompress(z.unconsumed_tail, 1) or not z.eof:
            return None
    except zlib.error:
        return None
    return out


def load_replay(path):
    """(theater, [frame time], [DS bytes]); a cut-off or corrupt end (crash) is dropped. ValueError if the file
    isn't a replay or holds more than REPLAY_MAX_FRAMES frames."""
    name = os.path.basename(path)
    with open(path, 'rb') as f:
        data = f.read()
    if len(data) < REPLAY_HEADER.size:
        raise ValueError(f"{name}: not an F-19 replay (empty or cut off)")
    magic, version, theater = REPLAY_HEADER.unpack_from(data)
    if magic != REPLAY_MAGIC or version != REPLAY_VERSION:
        raise ValueError(f"{name}: not an F-19 replay (version {REPLAY_VERSION})")
    theater = theater.decode('ascii', 'replace')
    if theater not in gs.WLD_FILES:
        raise ValueError(f"{name}: unknown theater '{theater}'")
    times, frames, prev = [], [], bytes(DS_SIZE)
    pos = REPLAY_HEADER.size
    while pos + REPLAY_RECORD.size <= len(data):
        t, n = REPLAY_RECORD.unpack_from(data, pos)
        pos += REPLAY_RECORD.size
        if pos + n > len(data) or not math.isfinite(t):
            break
        raw = inflate_frame(data[pos:pos + n])
        if raw is None:
            break
        b = xor(raw, prev)
        pos += n
        if times and t <= times[-1]:
            frames[-1] = b                  # a corrected read of the same frame
        else:
            if len(frames) >= REPLAY_MAX_FRAMES:
                raise ValueError(f"{name}: more than {REPLAY_MAX_FRAMES} frames "
                                 f"({REPLAY_MAX_FRAMES * DS_SIZE / 1e9:.1f} GB in memory)")
            times.append(t)
            frames.append(b)
        prev = b
    if not frames:
        raise ValueError(f"{name}: no frames")
    return theater, times, frames


def newest_replay():
    files = glob.glob(os.path.join(REPLAY_DIR, '*.f19r'))
    if not files:
        raise FileNotFoundError(f"No replays in {REPLAY_DIR} (live game records them: Settings, Live game)")
    return max(files, key=os.path.getmtime)


GAP_MAX = 0.5                   # longer pauses between frames (game paused, menus) are cut to this, s
SEEK = 5.0                      # Left / Right, s
SPEEDS = (1 / 16, 1 / 8, 1 / 4, 1 / 2, 1, 2, 4, 8)
STATE_CACHE = 64                # parsed frames kept


class ReplaySource:
    """A recorded flight, all frames decoded in memory, shown on its own clock: pause, seek, speed, single
    frames. Every frame is known in advance, so states are blended exactly between the two frames around the
    clock, with no delay."""
    def __init__(self, path):
        self.path = path
        self.theater, times, self.frames = load_replay(path)
        self.times = [0.0]
        for a, b in zip(times, times[1:]):
            self.times.append(self.times[-1] + min(b - a, GAP_MAX))
        self.end = self.times[-1]
        self.t, self.speed, self.paused, self.last = 0.0, 1, False, None
        self.cache = {}
        logger.info(f"Replay {path}: {len(self.frames)} frames, {clock(self.end)}, "
                    f"{len(self.frames) * 0x10000 / 1e6:.0f} MB in memory")

    def peek(self):
        return self.frames[0]

    def poll(self):
        return None

    def state_of(self, i):
        if i not in self.cache:
            if len(self.cache) >= STATE_CACHE:
                self.cache.clear()
            self.cache[i] = read_state(self.frames[i])
        return self.cache[i]

    def index(self):
        """The last frame at or before the clock."""
        return max(0, bisect.bisect_right(self.times, self.t) - 1)

    def replay_state(self, now, interpolate=True):
        if self.last is not None and not self.paused:
            self.t += (now - self.last) * self.speed
            if self.t >= self.end:
                self.t, self.paused = self.end, True
        self.last = now
        i = self.index()
        if not interpolate or i + 1 >= len(self.frames):
            return self.state_of(i)
        t0, t1 = self.times[i], self.times[i + 1]
        return blend_state(self.state_of(i), self.state_of(i + 1), (self.t - t0) / (t1 - t0))

    # --- controls (keys in scenes/dynamic.py)
    def step(self, k):
        self.seek(self.t + k * SEEK)

    def seek(self, t):
        self.t = max(0.0, min(self.end, t))

    def toggle_pause(self):
        if self.paused and self.t >= self.end:
            self.t = 0.0
        self.paused = not self.paused

    def frame(self, k):
        """Pause and show the previous / next game frame exactly."""
        self.paused = True
        i = self.index()
        if k < 0 and self.t > self.times[i]:
            self.t = self.times[i]
        else:
            self.t = self.times[max(0, min(len(self.times) - 1, i + k))]

    def faster(self, k):
        i = SPEEDS.index(self.speed) if self.speed in SPEEDS else SPEEDS.index(1)
        self.speed = SPEEDS[max(0, min(len(SPEEDS) - 1, i + k))]

    @property
    def name(self):
        import settings
        k = settings.label
        speed = f"x{self.speed:g}" if self.speed >= 1 else f"x1/{1 / self.speed:g}"
        return (f"replay {os.path.basename(self.path)}  {clock(self.t)} / {clock(self.end)}  {speed} "
                f"({k('keys_replay.slower')} {k('keys_replay.faster')})  "
                f"{'PAUSED' if self.paused else 'playing'} ({k('keys_replay.pause')})   "
                f"seek {k('keys_live.step_back')}/{k('keys_live.step_forward')} (Shift x6), "
                f"frame {k('keys_replay.frame_back')} {k('keys_replay.frame_forward')}  "
                f"start {k('keys_replay.start')}")


def open_source(args, record=False):
    """Snap files / patterns, or the running game when there are none."""
    return SnapSource(args) if args else LiveSource(record)


def ds_theater(b):
    """Theater of a data segment, None if it isn't EGAME's (another program, mission set-up)."""
    try:
        return gs.detect_theater(gs.DS(b))
    except ValueError:
        return None


def theater_of(source):
    return ds_theater(source.peek())


def read_state(ds_bytes, layout=None):
    """The part of gamestate.state() the renderer needs every frame. `layout`: the EGAME build (gamever.py),
    found from the content if not given."""
    d = gs.DS(ds_bytes, layout)
    return dict(step=d.u16(gs.SIM_STEP), clock=gs.clock(d), player=gs.player(d), camera=gs.camera(d),
                aircraft=gs.aircraft(d), weapons=gs.weapons(d), bullets=gs.bullets(d), effects=gs.effects(d),
                scenery=gs.scenery(d, None), specials=gs.specials(d))


# --- interpolation: the viewer draws at 60+ fps, the game makes ~10..20 frames/s at uneven intervals. States are kept
# with their frame times and shown on a playback clock running `delay` behind, blended between the two around it.
# The clock runs at real speed and is only nudged (+-SKEW) toward now - delay, so uneven frames don't make it jump.
# No extrapolation: when the clock passes the newest state, that one is held (a visible freeze) until the next comes.
# A state reaches the renderer some time after its frame time (the reader's guard, then up to a render frame until
# poll), so what the clock needs is the "reach" of each frame: from the previous frame's time to this frame's arrival
# (interval + lag). The delay is a high quantile of the recent reaches plus a margin; it rises at once when frames
# come later and falls back slowly, so the picture runs smoothly a little behind the game.
DELAY_WINDOW = 100              # recent game frames the quantile is taken over (~5 s)
DELAY_QUANTILE = 0.98
DELAY_MARGIN = 0.010            # s
DELAY_DECAY = 4.0               # s, time constant of the delay falling back to a lower quantile
DELAY_MIN, DELAY_MAX = 0.02, 0.5
IDLE_INTERVAL = 0.3             # longer intervals (pause, menus) don't count
SKEW, GAIN, RESYNC = 0.1, 2.0, 0.25   # max clock speed change, correction rate 1/s, jump if off by more (s)
KEEP = 16                       # states kept (enough for DELAY_MAX at 30 game fps)
JUMP = 3000                     # an object moving farther than this (world units) between frames is a new one: snap
SMOKE_RISE, SMOKE_DRIFT = 40, 400     # a puff moves at most this far up / sideways between kept frames
POSITION = ('x', 'y', 'z', 'draw_x', 'draw_y')
ANGLES = ('heading', 'pitch', 'roll', 'draw_roll')


def lerp_angle(a, b, t):
    return a + ((b - a + 180.0) % 360.0 - 180.0) * t


def blend(a, b, t):
    """Object dict b with its numeric pose moved back toward a by (1 - t); nested dicts (shadows) too."""
    out = dict(b)
    for k in POSITION:
        if k in b and k in a:
            out[k] = a[k] + (b[k] - a[k]) * t
    for k in ANGLES:
        if k in b and k in a:
            out[k] = lerp_angle(a[k], b[k], t)
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(a.get(k), dict) and 'x' in v:
            out[k] = blend(a[k], v, t)
    return out


def same_object(a, b):
    return (a.get('type') == b.get('type') and a.get('model') == b.get('model')
            and abs(a['x'] - b['x']) + abs(a['y'] - b['y']) + abs(a['z'] - b['z']) < JUMP)


def camera_cut(a, b):
    ca, cb = a['camera'], b['camera']
    return ca['view_mode'] != cb['view_mode'] or abs(ca['x'] - cb['x']) + abs(ca['y'] - cb['y']) >= JUMP


def blend_state(a, b, t):
    """State b, with every object that also exists in a (same slot, type, no jump) blended. Across a camera cut
    the newer frame is shown whole: a snapped camera over blended objects would be off by up to a frame."""
    if camera_cut(a, b):
        return b
    out = dict(b)
    for key in ('aircraft', 'weapons'):
        prev = {o['slot']: o for o in a[key]}
        out[key] = [blend(prev[o['slot']], o, t) if o['slot'] in prev and same_object(prev[o['slot']], o) else o
                    for o in b[key]]
    pa, pb = a['player'], b['player']
    out['player'] = blend(pa, pb, t) if abs(pa['draw_x'] - pb['draw_x']) + abs(pa['draw_y'] - pb['draw_y']) < JUMP         else pb
    out['camera'] = blend(a['camera'], b['camera'], t)
    # gun rounds fly straight, one velocity step per frame (EGAME 4956): back from b along it, new rounds too
    out['bullets'] = [dict(o, x=o['x'] - o['velocity'][0] * (1 - t), y=o['y'] - o['velocity'][1] * (1 - t),
                           z=o['z'] - o['velocity'][2] * (1 - t)) for o in b['bullets']]
    ea, eb = a['effects'], b['effects']
    out['effects'] = dict(eb)
    ca, cb = ea.get('parachute'), eb.get('parachute')
    if ca and cb and same_object(ca, cb):
        out['effects']['parachute'] = blend(ca, cb, t)
    # smoke puffs rise, drift south and turn every step (EGAME 4AA8); a respawned slot starts low somewhere else.
    # The model changes 3 -> 17 as a puff ages: that is the same puff
    prev = {o['slot']: o for o in ea['smoke']}
    out['effects']['smoke'] = [blend(prev[o['slot']], o, t) if o['slot'] in prev and same_puff(prev[o['slot']], o)
                               else o for o in eb['smoke']]
    return out


def same_puff(a, b):
    return 0 <= b['z'] - a['z'] <= SMOKE_RISE and abs(a['x'] - b['x']) + abs(a['y'] - b['y']) <= SMOKE_DRIFT


class Interpolator:
    def __init__(self):
        self.states = []        # [(time, state)], oldest first
        self.enabled = True
        self.reaches = []       # recent reaches (see the notes above DELAY_WINDOW), s
        self.current = None     # the delay, s
        self.updated = None     # arrival time of the last delay update
        self.play = self.last_now = None
        self.shown, self.held = None, False     # see state()

    def add(self, state, t, arrived=None):
        """A new state made at game time `t` (perf_counter), here since `arrived` (default: now)."""
        if t is None or not self.enabled:       # snaps: no timeline
            self.states = [(t, state)]
            return
        last = self.states[-1][0] if self.states else None
        if last is not None and t <= last:
            self.states[-1] = (last, state)     # a corrected read of the same frame
            return
        arrived = time.perf_counter() if arrived is None else arrived
        if last is not None and t - last < IDLE_INTERVAL:
            self.reaches = (self.reaches + [arrived - last])[-DELAY_WINDOW:]
            self.update_delay(arrived)
        self.states = (self.states + [(t, state)])[-KEEP:]

    def update_delay(self, now):
        r = sorted(self.reaches)
        target = r[min(len(r) - 1, int(DELAY_QUANTILE * len(r)))] + DELAY_MARGIN
        target = min(DELAY_MAX, max(DELAY_MIN, target))
        if self.current is None or target >= self.current:
            self.current = target
        else:
            self.current += (target - self.current) * (1.0 - math.exp(-(now - self.updated) / DELAY_DECAY))
        self.updated = now

    def delay(self):
        return 0.0 if self.current is None else self.current

    def clock(self, now):
        target = now - self.delay()
        if self.play is None or abs(target - self.play) > RESYNC:
            self.play = target
        else:
            rate = 1.0 + max(-SKEW, min(SKEW, GAIN * (target - self.play)))
            self.play += (now - self.last_now) * rate
        self.last_now = now
        return self.play

    def state(self, now):
        """The state to show at `now`. Also sets `shown`, the game time it shows (None: no timeline), and
        `held`: the clock has passed the newest state, so that one is shown as it is until the next arrives."""
        self.held = False
        if not self.states:
            self.shown = None
            return None
        newest = self.states[-1]
        if not self.enabled or newest[0] is None or len(self.states) < 2:
            self.play = None
            self.shown = newest[0]
            return newest[1]
        at = self.clock(now)
        for (t0, s0), (t1, s1) in zip(self.states, self.states[1:]):
            if t0 <= at < t1:
                self.shown = at
                return blend_state(s0, s1, (at - t0) / (t1 - t0))
        if at >= newest[0]:
            self.shown, self.held = newest[0], True
            return newest[1]
        self.shown = self.states[0][0]
        return self.states[0][1]
