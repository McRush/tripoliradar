"""Frame log: what every displayed frame cost and showed, to pinpoint hitches.

Started by the app (view.py --framelog: from launch) and saved when it stops. While on, each frame records:
  present_ms    time since the previous frame was handed to the display (buffer swap returned)
  cpu_*_ms      engine phases: events, camera, update (the scene's update), draw (scene draw calls),
                resolve (MSAA resolve / post-processing and labels), flip (buffer swap), tick (frame cap)
  <section>_ms  the scene's own parts, timed with FrameLog.timed(name)
  gpu_ms        GPU time of the frame's draw work (GL timer query, read back a few frames later)
  gc_ms, gc_gen Python garbage collection during the frame
  notes         FrameLog.note() texts (scenery refills, buffer growth...)
plus the scene's own fields (scene.frame_log_fields(), e.g. the live interpolation state).
On stop (or exit) the rows are written to <dir>/frames_<date>.csv with a report (<same name>.txt), also printed.

    python -m lofigl.framelog FILE.csv [REFRESH_HZ]     reprint the report of a saved log
"""
import csv
import gc
import logging
import os
import sys
import time
from time import perf_counter

logger = logging.getLogger(__name__)

GPU_QUERIES = 8         # timer queries in flight; a result is read when available, at most this many frames later
HITCH = 1.5             # a present interval this many refresh periods long is a dropped frame


class _Section:
    __slots__ = ('log', 'name', 't')

    def __init__(self, log, name):
        self.log, self.name = log, name

    def __enter__(self):
        self.t = perf_counter()

    def __exit__(self, *exc):
        cur = self.log.cur
        key = self.name + '_ms'
        cur[key] = cur.get(key, 0.0) + (perf_counter() - self.t) * 1000


class _Null:
    def __enter__(self):
        pass

    def __exit__(self, *exc):
        pass


_NULL = _Null()


class FrameLog:
    def __init__(self, directory, refresh_hz=60):
        self.directory = directory
        self.period = 1.0 / (refresh_hz or 60)
        self.enabled = False
        self.rows = []
        self.cur = {}
        self.sections = {}
        self.queries = None         # GL query ids, made on first start (needs the GL context)
        self.pending = {}           # query slot -> row it measures
        self.frame = 0
        self.last_present = None

    # --- control ---------------------------------------------------------------------------------

    def start(self):
        if self.enabled:
            return
        if self.queries is None:
            from OpenGL import GL
            self.queries = [int(q) for q in GL.glGenQueries(GPU_QUERIES)]
        self.enabled, self.rows, self.pending, self.frame, self.last_present = True, [], {}, 0, None
        self.started = time.strftime('%Y-%m-%d_%H-%M-%S')
        gc.callbacks.append(self._gc)
        logger.info("Frame log: recording")

    def stop(self):
        if not self.enabled:
            return
        self.enabled = False
        if self._gc in gc.callbacks:
            gc.callbacks.remove(self._gc)
        self.save()

    @property
    def status(self):
        return f"REC {len(self.rows)} frames" if self.enabled else "off"

    # --- per frame ---------------------------------------------------------------------------------

    def begin(self):
        if not self.enabled:
            return
        self.cur, self.notes = {}, []
        self.t_begin = self.t_lap = perf_counter()

    def lap(self, name):
        """Time since the previous lap (or begin) as cpu_<name>_ms."""
        if not self.enabled:
            return
        now = perf_counter()
        self.cur[f'cpu_{name}_ms'] = (now - self.t_lap) * 1000
        self.t_lap = now

    def timed(self, name):
        """Context manager adding its time to <name>_ms of the frame."""
        if not self.enabled:
            return _NULL
        s = self.sections.get(name)
        if s is None:
            s = self.sections[name] = _Section(self, name)
        return s

    def note(self, text):
        if self.enabled:
            self.notes.append(text)

    def gpu_begin(self):
        if self.enabled:
            from OpenGL import GL
            GL.glBeginQuery(GL.GL_TIME_ELAPSED, self.queries[self.frame % GPU_QUERIES])

    def gpu_end(self):
        if self.enabled:
            from OpenGL import GL
            GL.glEndQuery(GL.GL_TIME_ELAPSED)

    def presented(self):
        """Call right after the buffer swap."""
        if not self.enabled:
            return
        now = perf_counter()
        self.cur['present_ms'] = (now - self.last_present) * 1000 if self.last_present else None
        self.last_present = now

    def end(self, scene=None):
        if not self.enabled:
            return
        from OpenGL import GL
        row = {'frame': self.frame, 't': self.t_begin, 'present_ms': None,
               'cpu_ms': (perf_counter() - self.t_begin) * 1000}
        row.update(self.cur)
        row['gpu_ms'] = None
        fields = getattr(scene, 'frame_log_fields', None)
        if fields:
            row.update(fields())
        row['notes'] = '; '.join(self.notes)
        self.rows.append(row)
        slot = self.frame % GPU_QUERIES
        self.pending[slot] = row
        for s, r in list(self.pending.items()):     # read back the finished queries
            q = self.queries[s]
            if GL.glGetQueryObjectiv(q, GL.GL_QUERY_RESULT_AVAILABLE):
                r['gpu_ms'] = GL.glGetQueryObjectuiv(q, GL.GL_QUERY_RESULT) / 1e6
                del self.pending[s]
        self.frame += 1

    def _gc(self, phase, info):
        if phase == 'start':
            self.t_gc = perf_counter()
        elif self.enabled:
            self.cur['gc_ms'] = self.cur.get('gc_ms', 0.0) + (perf_counter() - self.t_gc) * 1000
            self.cur['gc_gen'] = max(self.cur.get('gc_gen', 0), info['generation'])

    # --- output -------------------------------------------------------------------------------------

    def save(self):
        if not self.rows:
            logger.info("Frame log: no frames")
            return
        os.makedirs(self.directory, exist_ok=True)
        path = os.path.join(self.directory, f"frames_{self.started}.csv")
        t0 = self.rows[0]['t']
        for r in self.rows:
            r['t'] = r['t'] - t0
        write_csv(path, self.rows)
        text = report(self.rows, self.period)
        with open(path[:-4] + '.txt', 'w', encoding='utf-8') as f:
            f.write(text)
        print(text)                     # timings: the console only, not the log
        logger.info(f"Frame log: {len(self.rows)} frames saved to {path}")


def write_csv(path, rows):
    cols = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    cols.remove('notes')
    cols.append('notes')
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in rows:
            w.writerow(['' if r.get(k) is None else f"{r[k]:.3f}" if isinstance(r[k], float) else r[k]
                        for k in cols])


def read_csv(path):
    def value(s):
        if s == '':
            return None
        try:
            return float(s) if '.' in s or 'e' in s else int(s)
        except ValueError:
            return {'True': True, 'False': False}.get(s, s)
    with open(path, newline='', encoding='utf-8') as f:
        return [{k: (v if k == 'notes' else value(v)) for k, v in r.items()} for r in csv.DictReader(f)]


# --- report -----------------------------------------------------------------------------------------

def percentile(values, p):
    v = sorted(values)
    if not v:
        return 0.0
    return v[min(len(v) - 1, int(round(p / 100 * (len(v) - 1))))]


def breakdown(r, limit=6):
    """The biggest timings of a row, as text."""
    parts = sorted(((v, k[:-3]) for k, v in r.items()
                    if k.endswith('_ms') and k not in ('present_ms', 'cpu_ms') and isinstance(v, (int, float))
                    and k not in DATA_MS and v >= 0.5), reverse=True)
    return ' '.join(f"{k} {v:.1f}" for v, k in parts[:limit])


DATA_MS = {'delay_ms', 'newest_age_ms', 'game_interval_ms', 'arrival_lag_ms', 'guard_ms'}


def report(rows, period):
    """Dropped frames with their cause, and (live) the stretches where the shown game time stood still."""
    out = []
    p_ms = period * 1000
    present = [r['present_ms'] for r in rows if r.get('present_ms')]
    span = rows[-1]['t'] - rows[0]['t'] if len(rows) > 1 else 0.0
    out.append(f"=== Frame log: {len(rows)} frames in {span:.1f} s, refresh {1 / period:.0f} Hz ===")
    if present:
        out.append(f"present interval ms: median {percentile(present, 50):.2f}  p99 {percentile(present, 99):.2f}  "
                   f"max {max(present):.1f}")
    for key in ('cpu_ms', 'gpu_ms'):
        v = [r[key] for r in rows if r.get(key) is not None]
        if v:
            out.append(f"{key[:-3]} ms per frame: median {percentile(v, 50):.2f}  p99 {percentile(v, 99):.2f}  "
                       f"max {max(v):.1f}")
    gcs = [r for r in rows if r.get('gc_ms')]
    if gcs:
        out.append(f"garbage collections: {len(gcs)} frames, gen 2 in {sum(1 for r in gcs if r.get('gc_gen') == 2)}, "
                   f"max {max(r['gc_ms'] for r in gcs):.1f} ms")

    # 1) dropped frames: the swap came late. CPU: the frame's own work took the period; GPU: its draw work did.
    drops = [i for i, r in enumerate(rows) if r.get('present_ms') and r['present_ms'] > HITCH * p_ms]
    out.append(f"\n--- dropped frames (present > {HITCH * p_ms:.1f} ms): {len(drops)} ---")
    causes = {}
    for i in drops:
        r = rows[i]
        cpu, gpu = r.get('cpu_ms') or 0.0, r.get('gpu_ms') or 0.0
        work = cpu - (r.get('cpu_flip_ms') or 0.0) - (r.get('cpu_tick_ms') or 0.0)
        if work > 0.9 * p_ms:
            cause = 'CPU'
        elif gpu > 0.9 * p_ms:
            cause = 'GPU'
        elif (r.get('cpu_flip_ms') or 0.0) > 1.5 * p_ms:
            cause = 'swap blocked'
        else:
            cause = 'unclear'
        causes[cause] = causes.get(cause, 0) + 1
    if causes:
        out.append("by cause: " + ', '.join(f"{k} {v}" for k, v in sorted(causes.items(), key=lambda kv: -kv[1])))
    for i in drops[:40]:
        r = rows[i]
        gpu = f"{r['gpu_ms']:.1f}" if r.get('gpu_ms') is not None else '?'
        out.append(f"  t {r['t']:8.2f}s  present {r['present_ms']:6.1f}  cpu {r['cpu_ms']:5.1f}  gpu {gpu:>5}  | "
                   f"{breakdown(r)}" + (f" | {r['notes']}" if r.get('notes') else ''))

    # 2) content holds: the shown game time didn't advance while frames went on (live interpolation ran dry)
    if any('content_t' in r for r in rows):
        out += content_report(rows, p_ms)

    # 3) the slowest frames by CPU, whatever their present interval
    worst = sorted((r for r in rows if r.get('cpu_ms') is not None), key=lambda r: -r['cpu_ms'])[:10]
    out.append("\n--- slowest frames (CPU) ---")
    for r in worst:
        out.append(f"  t {r['t']:8.2f}s  cpu {r['cpu_ms']:5.1f}  | {breakdown(r)}" + (f" | {r['notes']}" if r.get('notes') else ''))
    notes = {}
    for r in rows:
        for n in (r.get('notes') or '').split('; '):
            if n:
                k = n.split(' ')[0]
                notes[k] = notes.get(k, 0) + 1
    if notes:
        out.append("\nnotes: " + ', '.join(f"{k} x{v}" for k, v in sorted(notes.items())))
    return '\n'.join(out) + '\n'


def content_report(rows, p_ms):
    """Stretches of frames showing the same game time (a hold), each with what ended it: the next game frame's
    interval, its step count (more than 1: the reader missed frames) and how late it arrived."""
    out = []
    holds, cur = [], None
    for i, r in enumerate(rows):
        held = r.get('hold') and not r.get('paused')
        if held:
            if cur is None:
                cur = dict(start=i, frames=0, ms=0.0)
            cur['frames'] += 1
            cur['ms'] += r.get('present_ms') or p_ms
        elif cur is not None:
            cur['end'] = i
            holds.append(cur)
            cur = None
    visible = [h for h in holds if h['ms'] >= 0.9 * p_ms]
    span = (rows[-1]['t'] - rows[0]['t']) or 1.0
    out.append(f"\n--- content holds (shown game time stopped): {len(holds)}, "
               f"{len(visible)} of a frame or longer ({len(visible) / span * 60:.1f}/min), "
               f"max {max((h['ms'] for h in holds), default=0):.0f} ms ---")
    why = {}
    lines = []
    for h in visible:
        r0 = rows[h['start']]
        # the game frame that ended the hold: the first new state at or after the hold's end
        nxt = next((r for r in rows[h['end']:h['end'] + 30] if r.get('new_state') and not r.get('correction')), None)
        if nxt is None:
            cause, detail = 'unknown', ''
        else:
            steps = nxt.get('step_delta') or 1
            interval, delay, lag = nxt.get('game_interval_ms') or 0, r0.get('delay_ms') or 0, nxt.get('arrival_lag_ms') or 0
            # the next state is on screen at (its frame time + lag), the clock needs it at (newest + delay)
            if steps > 1:
                cause = 'reader missed frames'
            elif interval > delay:
                cause = 'game frame slow'
            elif interval + lag > delay:
                cause = 'arrival lag'
            else:
                cause = 'other'
            detail = f"game interval {interval:.0f} ms vs delay {delay:.0f} ms, steps {steps}, arrived {lag:.0f} ms after it was made"
        why[cause] = why.get(cause, 0) + 1
        lines.append(f"  t {r0['t']:8.2f}s  hold {h['ms']:5.0f} ms ({h['frames']} frames)  {cause}: {detail}"
                     + (f"  [camera {r0['mode']}]" if r0.get('mode') else ''))
    if why:
        out.append("by cause: " + ', '.join(f"{k} {v}" for k, v in sorted(why.items(), key=lambda kv: -kv[1])))
    out += lines[:60]
    if len(lines) > 60:
        out.append(f"  ... {len(lines) - 60} more")
    news = [r for r in rows if r.get('new_state') and not r.get('correction')]
    if news:
        iv = [r['game_interval_ms'] for r in news if r.get('game_interval_ms') and r['game_interval_ms'] < 300]
        lag = [r['arrival_lag_ms'] for r in news if r.get('arrival_lag_ms') is not None]
        missed = sum((r.get('step_delta') or 1) - 1 for r in news if (r.get('step_delta') or 1) > 1)
        if iv:
            out.append(f"game frames: {len(news)}, interval ms median {percentile(iv, 50):.0f} p90 {percentile(iv, 90):.0f} "
                       f"max {max(iv):.0f}; missed steps {missed}; arrival lag ms median {percentile(lag, 50):.1f} "
                       f"p90 {percentile(lag, 90):.1f} max {max(lag, default=0):.1f}")
    delays = [r['delay_ms'] for r in rows if r.get('delay_ms')]
    if delays:
        out.append(f"interpolation delay ms (how far behind the game the picture runs): median {percentile(delays, 50):.0f} "
                   f"p90 {percentile(delays, 90):.0f} max {max(delays):.0f}")
    return out


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    hz = float(sys.argv[2]) if len(sys.argv) > 2 else 60
    print(report(read_csv(sys.argv[1]), 1 / hz))
