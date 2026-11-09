"""The EGAME.EXE builds the project supports: the Russian localization and the English Steam release.
Their code and data are the same; text lengths differ, so the data segment
(DGROUP) and the variables in it sit at other offsets.

Addresses in the code are written as in the Russian build and translated with `layout.ds(addr)`.
The English map was made by aligning the two disassemblies instruction by instruction and
pairing their memory operands (931 variables), then checked on the running game.

Which build:
  installed()   the one in the game folder (GAME_DIR): read from EGAME.EXE's start-up code
  of_ds(b)      the one a 64 KB data segment comes from (live read, snap, replay frame): by where its
                weapon catalog is
"""
import bisect
import struct

from wld import koi7

CATALOG = 0x4F24                # weapon catalog (gamestate.CATALOG); its first name tells the builds apart
CATALOG_FIRST = b'AIM-9M\0'


class Layout:
    """One EGAME build. `dgroup` = DGROUP paragraph relative to the load segment (DS = load segment + dgroup);
    `sigs` = (image offset, bytes) of code without relocations, to find EGAME in DOSBox memory; `regions` =
    (start, end, shift): Russian addresses start <= a < end are at a + shift; `moved` = single variables placed
    elsewhere; `russian_text` = names and messages are KOI-7 transliterated Russian."""

    def __init__(self, code, name, dgroup, sigs, russian_text, regions=None, moved=None):
        self.code, self.name, self.dgroup, self.sigs = code, name, dgroup, sigs
        self.russian_text = russian_text
        self.regions = sorted(regions) if regions is not None else None
        self.starts = [r[0] for r in self.regions or ()]
        self.moved = moved or {}
        self.cache = {}

    def ds(self, a):
        """Russian-build DS offset -> this build's DS offset."""
        if self.regions is None:
            return a
        out = self.cache.get(a)
        if out is None:
            out = self.moved.get(a)
            if out is None:
                i = bisect.bisect_right(self.starts, a) - 1
                if i < 0 or a >= self.regions[i][1]:
                    raise KeyError(f"DS:{a:04X} (Russian EGAME) has no known place in the {self.name} EGAME "
                                   "(see tools/gamever.py)")
                out = a + self.regions[i][2]
            self.cache[a] = out
        return out

    def text(self, s):
        """Game text as it reads on screen (Russian: Latin letters stand for Cyrillic ones)."""
        return koi7(s) if self.russian_text else s

    def __repr__(self):
        return f"<EGAME {self.name}>"


RUSSIAN = Layout(
    'ru', 'Russian', 0x1EE7,
    sigs=[(0x0918, bytes.fromhex('558bec8b46040bc0746c3d0100745f3d020074403d030074213d040075628b46068b5608'
                                 '05200083d200b1060ac97450')),
          (0x099A, bytes.fromhex('558bec56837e040475088346060283460802837e06007c19837e08007c138b5e04d1e38b'
                                 'b7f6053976067d053976087c'))],
    russian_text=True)

ENGLISH = Layout(
    'en', 'English', 0x1ECF,
    sigs=[(0x0906, bytes.fromhex('558bec8b46040bc0746c3d0100745f3d020074403d030074213d040075628b46068b5608'
                                 '05200083d200b1060ac97450')),
          (0x0988, bytes.fromhex('558bec56837e040475088346060283460802837e06007c19837e08007c138b5e04d1e38b'
                                 'b7ea053976067d053976087c'))],
    russian_text=False,
    regions=[                   # Russian start, end, shift: code-verified ranges; gaps stay unmapped
        (0x0000, 0x0068, 0),    # C runtime header
        (0x0076, 0x398A, -12),  # colour tables 03FC, model table 0600, L4 grids 0862, remap 09E8, matrix 0AC2
        (0x3B8A, 0x3F22, -16),
        (0x3F60, 0x45BA, -76),  # 427A
        (0x45BA, 0x45EC, -16),
        (0x45EE, 0x45F6, -128),
        (0x4624, 0x63E0, -38),  # player angles 4700, types 49D6 / 4C36, loadout 4F00, catalog 4F24, weapons 5422
        (0x65E0, 0x6870, -32),  # start of uninitialized data: clock 65E0, overrides count 666A, controls 686C
        (0x6870, 0x9EC2, -30),  # targets 80C8, aircraft 8870, camera, player 9666, bullets 9BA6, scenery camera 9EB0
    ],
    moved={0x3F33: 0x45DE})     # page flip pending (live.py frame sync)

LAYOUTS = (RUSSIAN, ENGLISH)


def from_image(img, cs, ip):
    """The build of an unpacked EGAME image with entry CS:IP: its C start-up code loads DGROUP with
    `mov di, DGROUP` right after the DOS version check."""
    entry = cs * 16 + ip
    code = img[entry:entry + 16]
    for layout in LAYOUTS:
        if code[10:13] == b'\xBF' + struct.pack('<H', layout.dgroup):
            return layout
    raise RuntimeError(f"Unsupported EGAME.EXE (start-up code {code.hex(' ')}): "
                       f"only the {' and '.join(l.name for l in LAYOUTS)} builds are known")


_installed = None


def installed():
    """The build in the game folder (EGAME.EXE unpacked in memory, ~15 ms, once per run)."""
    global _installed
    if _installed is None:
        import unexepack
        from paths import game_file
        img, _, (cs, ip, _, _), _ = unexepack.unpack(open(game_file('EGAME.EXE'), 'rb').read())
        _installed = from_image(img, cs, ip)
    return _installed


def of_ds(b):
    """The build a data segment comes from, None if it isn't EGAME's."""
    for layout in LAYOUTS:
        a = layout.ds(CATALOG)
        if b[a:a + len(CATALOG_FIRST)] == CATALOG_FIRST:
            return layout
    return None


if __name__ == '__main__':
    print(f"Game folder: {installed().name} EGAME.EXE")
