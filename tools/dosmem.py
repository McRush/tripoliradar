"""Read-only access to a running DOSBox's emulated memory, and to EGAME's data segment in it.

DOSBox keeps the guest RAM in one host buffer (MemBase). We find it by searching the process for code bytes
of EGAME (code is identical in RAM, EXEPACK has already unpacked it), which also gives the load segment and
so DS = load_seg + DGROUP. Both builds are recognized (gamever.py: Russian DGROUP 1EE7, English 1ECF), whatever
the game folder holds; `Game.layout` is the one found. DS offsets in this module are Russian-build addresses
(NOTES.md), translated with the layout.

usage: dosmem.py                 locate DOSBox/EGAME and print the layout
       dosmem.py dump FILE       save guest conventional memory (1 MB) to FILE
       dosmem.py snap FILE       save the EGAME data segment (64 KB)
       dosmem.py diff A B        list DS words that changed between two snaps
       dosmem.py watch [HZ]      print the cockpit camera continuously
"""
import ctypes, ctypes.wintypes as wt, os, struct, sys

import gamever

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
MEM_COMMIT = 0x1000
READABLE = {0x02, 0x04, 0x08, 0x20, 0x40, 0x80}   # R, RW, WC, XR, XRW, XWC

k32 = ctypes.WinDLL('kernel32', use_last_error=True)
k32.OpenProcess.argtypes = (wt.DWORD, wt.BOOL, wt.DWORD)
k32.OpenProcess.restype = wt.HANDLE
k32.CloseHandle.argtypes = (wt.HANDLE,)
k32.ReadProcessMemory.argtypes = (wt.HANDLE, wt.LPCVOID, wt.LPVOID, ctypes.c_size_t,
                                  ctypes.POINTER(ctypes.c_size_t))
k32.ReadProcessMemory.restype = wt.BOOL


class MBI(ctypes.Structure):
    _fields_ = (('BaseAddress', ctypes.c_void_p), ('AllocationBase', ctypes.c_void_p),
                ('AllocationProtect', wt.DWORD), ('PartitionId', wt.WORD), ('RegionSize', ctypes.c_size_t),
                ('State', wt.DWORD), ('Protect', wt.DWORD), ('Type', wt.DWORD))


k32.VirtualQueryEx.argtypes = (wt.HANDLE, wt.LPCVOID, ctypes.POINTER(MBI), ctypes.c_size_t)
k32.VirtualQueryEx.restype = ctypes.c_size_t


def find_dosbox_pid():
    pids = (wt.DWORD * 4096)()
    got = wt.DWORD()
    ctypes.WinDLL('psapi').EnumProcesses(pids, ctypes.sizeof(pids), ctypes.byref(got))
    found = []
    for pid in pids[:got.value // 4]:
        h = k32.OpenProcess(0x1000, False, pid)            # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            continue
        name = ctypes.create_unicode_buffer(1024)
        n = wt.DWORD(1024)
        if k32.QueryFullProcessImageNameW(h, 0, name, ctypes.byref(n)):
            if os.path.basename(name.value).lower().startswith('dosbox'):
                found.append((pid, name.value))
        k32.CloseHandle(h)
    if len(found) != 1:
        raise RuntimeError(f'expected one DOSBox process, found: {found}')
    return found[0][0]


class Proc:
    def __init__(self, pid):
        self.pid = pid
        self.h = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
        if not self.h:
            raise ctypes.WinError(ctypes.get_last_error())

    def read(self, addr, n):
        buf = ctypes.create_string_buffer(n)
        got = ctypes.c_size_t()
        if not k32.ReadProcessMemory(self.h, addr, buf, n, ctypes.byref(got)) or got.value != n:
            raise ctypes.WinError(ctypes.get_last_error())
        return buf.raw

    def regions(self):
        """Committed readable regions as (base, size)."""
        mbi, addr, out = MBI(), 0, []
        while k32.VirtualQueryEx(self.h, addr, ctypes.byref(mbi), ctypes.sizeof(mbi)):
            base, size = mbi.BaseAddress or 0, mbi.RegionSize
            if mbi.State == MEM_COMMIT and (mbi.Protect & 0xFF) in READABLE and not mbi.Protect & 0x100:
                out.append((base, size))
            addr = base + size
            if addr >= 1 << 47:
                break
        return out

    def close(self):
        """Release the process handle. Safe to call twice."""
        if self.h:
            k32.CloseHandle(self.h)
            self.h = None


class Game:
    """Guest memory of DOSBox running EGAME: membase (host address of linear 0), load_seg, ds, and the EGAME
    build found (`layout`, gamever.Layout)."""

    def __init__(self, pid=None):
        self.p = Proc(pid or find_dosbox_pid())
        try:
            self._locate()
        except BaseException:
            self.close()                            # live.py retries every second: don't leak the handle
            raise

    def _locate(self):
        """Find EGAME in the process: image_host, layout, membase, load_seg, ds."""
        first = {}                                  # first signature -> builds starting with it (both do)
        for layout in gamever.LAYOUTS:
            first.setdefault(layout.sigs[0][1], []).append(layout)
        hits, good = 0, []
        for base, size in self.p.regions():
            try:
                data = self.p.read(base, size)
            except OSError:
                continue
            for sig, layouts in first.items():
                i = data.find(sig)
                while i >= 0:
                    hits += 1
                    for layout in layouts:          # the other signatures tell the builds apart
                        image_host = base + i - layout.sigs[0][0]
                        if all(self._peek(image_host + o, len(s)) == s for o, s in layout.sigs[1:]):
                            good.append((image_host, layout))
                    i = data.find(sig, i + 1)
        if not good:
            raise RuntimeError(f'EGAME code not found in DOSBox memory ({hits} partial hits)')
        self.hits = good
        self.image_host, self.layout = good[0]     # host address of the EGAME load segment
        self.membase, self.load_seg = self._find_membase(self.image_host)
        self.ds = self.load_seg + self.layout.dgroup

    def alive(self):
        """EGAME's code is still where we found it. When the mission ends DOSBox keeps the memory readable,
        but other programs (debriefing, menus) reuse it: the data segment is then garbage."""
        return all(self._peek(self.image_host + o, len(s)) == s for o, s in self.layout.sigs)

    def close(self):
        """Release the DOSBox process handle. Safe to call twice."""
        self.p.close()

    def _peek(self, addr, n):
        try:
            return self.p.read(addr, n)
        except OSError:
            return None

    def _find_membase(self, image_host):
        """Guest RAM starts with the interrupt vector table, and the load segment is paragraph-aligned
        relative to it. Try each candidate load segment and check the IVT/BIOS data look right."""
        for seg in range(0x0050, 0xA000):
            mb = image_host - seg * 16
            ivt = self._peek(mb, 0x400)
            if ivt is None:
                continue
            vec = struct.unpack('<256I', ivt)
            # BIOS vectors point into F000; a real IVT has many of them, and the PSP sits at load_seg - 0x10
            if sum(1 for v in vec if v >> 16 == 0xF000) < 64:
                continue
            psp = self._peek(mb + (seg - 0x10) * 16, 2)
            if psp == b'\xCD\x20':                  # PSP starts with INT 20h
                return mb, seg
        raise RuntimeError('could not locate guest memory base')

    def lin(self, seg, off=0):
        return self.membase + seg * 16 + off

    def read(self, seg, off, n):
        return self.p.read(self.lin(seg, off), n)

    def read_ds(self, off, n):
        """n bytes at DS:off of this build (no translation)."""
        return self.read(self.ds, off, n)

    def read_var(self, addr, n):
        """n bytes of the variable at Russian-build address `addr` (NOTES.md), wherever this build has it."""
        return self.read_ds(self.layout.ds(addr), n)

    def conventional(self):
        return self.p.read(self.membase, 0x100000)

    def camera(self, ds=None):
        """Cockpit view as passed to render_view (EGAME 3B95 -> 044A): angles heading = -[9BA4] (0 = north),
        pitch = [9500] (+ = nose up), roll = [8B54] (s16, 0x10000 = 360 deg; heading/roll signs to confirm), position x [886C] s32,
        y [8B44] s32 (world units, y north), z [8B4E] s16; view matrix DS:0AC2 3x3 s16, 1.15 fixed point."""
        ds = ds or self.read_ds(0, 0x10000)
        at = self.layout.ds
        s16 = lambda a: struct.unpack_from('<h', ds, at(a))[0]
        s32 = lambda a: struct.unpack_from('<i', ds, at(a))[0]
        return dict(x=s32(0x886C), y=s32(0x8B44), z=s16(0x8B4E),
                    heading=-s16(0x9BA4), pitch=s16(0x9500), roll=s16(0x8B54),
                    matrix=struct.unpack_from('<9h', ds, at(0x0AC2)))


def deg(a):
    return a * 360 / 65536


def diff(a, b, base=0):
    """Changed 16-bit words between two equal-size snapshots: [(offset, old, new)]."""
    import numpy as np
    x = np.frombuffer(a, '<i2'); y = np.frombuffer(b, '<i2')
    return [(base + 2 * i, int(x[i]), int(y[i])) for i in np.nonzero(x != y)[0]]


if __name__ == '__main__':
    import time
    cmd = sys.argv[1] if len(sys.argv) > 1 else ''
    if cmd == 'diff':           # diff A B: changed DS words between two snaps (no DOSBox needed)
        for off, o, n in diff(open(sys.argv[2], 'rb').read(), open(sys.argv[3], 'rb').read()):
            print(f'DS:{off:04X}  {o:6d} -> {n:6d}   ({o & 0xFFFF:04X} -> {n & 0xFFFF:04X})')
        sys.exit()
    g = Game()
    if cmd == 'dump':           # dump FILE: guest conventional memory
        open(sys.argv[2], 'wb').write(g.conventional())
        print('saved', sys.argv[2])
    elif cmd == 'snap':         # snap FILE: EGAME data segment (64 KB)
        open(sys.argv[2], 'wb').write(g.read_ds(0, 0x10000))
        print('saved', sys.argv[2])
    elif cmd == 'watch':        # watch [HZ]: print the camera continuously
        hz = float(sys.argv[2]) if len(sys.argv) > 2 else 5
        while True:
            c = g.camera()
            print(f"{time.time():.2f}  x {c['x']:8d}  y {c['y']:8d}  z {c['z']:6d}   "
                  f"hdg {deg(c['heading']):7.1f}  pitch {deg(c['pitch']):6.1f}  roll {deg(c['roll']):6.1f}   "
                  f"m {' '.join(f'{v / 32768:+.2f}' for v in c['matrix'])}", flush=True)
            time.sleep(1 / hz)
    else:
        print(f'DOSBox pid {g.p.pid}, {g.layout.name} EGAME')
        print(f'guest RAM at host 0x{g.membase:X}')
        print(f'EGAME load segment {g.load_seg:04X} (PSP {g.load_seg - 0x10:04X}), DS {g.ds:04X} '
              f'(DGROUP {g.layout.dgroup:04X})')
        print('camera', g.camera())
