"""F-19 Stealth Fighter theater scenery: .3DT (object groups) + .3DG (cell grids) -> placed models.

Format reverse-engineered from EGAME.EXE (loaders 0E1A / 0F7C, cell lookup 099A,
coordinate scaling 0918, draw loop 059C..0837). See NOTES.md.

usage: world.py THEATER [outdir]        THEATER = LB | PG | NC | CE
       -> outdir/THEATER.json (+ THEATER_map.png with --png, THEATER.obj with --obj)
"""
import json, os, struct, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from paths import game_file, unpacked_egame, world_export_dir
import gamever

THEATERS = {  # code: (name, world file, index of built-in 8x8 level-4 grid in EGAME DS:0862)
    'LB': ('Libya', 'LIBYA.WLD', 0),
    'PG': ('Persian Gulf', 'GULF.WLD', 1),
    'NC': ('North Cape', 'NC.WLD', 2),
    'CE': ('Central Europe', 'CE.WLD', 3),
}
GRID_SIZE = [1024, 256, 64, 16, 8]     # cells per side for level 0..4 (EGAME DS:05F6)
CELL = 4096                            # every level uses 4096-unit cells in its own units
WORLD = 1 << 20                        # map side in world (level-1) units = 256 * 4096
WLD_UNIT = 32                          # 1 .WLD coordinate unit = 32 world units


def level_scale(level):
    """World (level-1) units per level unit (EGAME 0918: L0 = W*2, L1 = W, L2 = W/4, L3 = W/16, L4 = W/64)."""
    return 4 ** (level - 1)


def load_3dt(path):
    """'11', u16 n_groups[5], u16 obj_count[level][group], then objects: s16 x, y, z, u16 model."""
    d = open(path, 'rb').read()
    magic, *ngroups = struct.unpack_from('<6H', d, 0)
    p = 12
    counts = []
    for n in ngroups:
        counts.append(struct.unpack_from(f'<{n}H', d, p)); p += 2 * n
    levels = []
    for lv in counts:
        groups = []
        for n in lv:
            objs = []
            for _ in range(n):
                x, y, z, m = struct.unpack_from('<hhhH', d, p); p += 8
                assert m < 0x80, 'bit 7 (runtime-replaceable model, EGAME 131E) never set in files'
                objs.append(dict(x=x, y=y, z=z, model=m))
            groups.append(objs)
        levels.append(groups)
    assert p == len(d), f'{path}: {len(d) - p} bytes left'
    return dict(magic=magic, levels=levels)


def load_3dg(path):
    """'22', 16 bytes (overwritten by the loader), level-3 grid 16x16, then 3 x 512 bytes of
    4x4 child patterns (32 parents x 16) for levels 2, 1, 0."""
    d = open(path, 'rb').read()
    assert len(d) == 1810
    return dict(magic=struct.unpack_from('<H', d, 0)[0], unused=list(d[2:18]),
                l3=list(d[18:274]), patterns={2: list(d[274:786]), 1: list(d[786:1298]),
                                              0: list(d[1298:1810])})


def load_l4(index):
    e = open(unpacked_egame(), 'rb').read()
    img = e[struct.unpack_from('<H', e, 8)[0] * 16:]
    layout = gamever.installed()
    return list(img[layout.dgroup * 16 + layout.ds(0x862) + index * 64:][:64])


class World:
    def __init__(self, code):
        self.code = code
        self.name, self.wld, l4i = THEATERS[code]
        self.t = load_3dt(game_file(code + '.3DT'))
        self.g = load_3dg(game_file(code + '.3DG'))
        self.l4 = load_l4(l4i)

    def group(self, level, x, y):
        """Group id for cell (x, y) of a level (EGAME 099A). y grows northward."""
        if level == 4:
            x += 2; y += 2
        n = GRID_SIZE[level]
        if not (0 <= x < n and 0 <= y < n):
            return 0
        if level == 4:
            return self.l4[y * 8 + x]
        if level == 3:
            return self.g['l3'][y * 16 + x]
        parent = self.group(level + 1, x >> 2, y >> 2)
        return self.g['patterns'][level][parent * 16 + (y & 3) * 4 + (x & 3)]

    @staticmethod
    def cell_range(level):
        """Cell indices along each axis of a level; level 4 includes the border ring (-2..5)."""
        return range(-2, 6) if level == 4 else range(GRID_SIZE[level])

    def cells(self, level):
        rng = self.cell_range(level)
        for cy in rng:
            for cx in rng:
                yield cx, cy, self.group(level, cx, cy)

    def placements(self, dots=False):
        """Flatten to world coordinates. Level-1 group 0 is the 'ground dots' filler (one light-point
        model in every empty level-1 cell) - skipped unless dots=True."""
        out = []
        for level in (4, 3, 2, 1):
            s = level_scale(level)
            for cx, cy, g in self.cells(level):
                if level == 1 and g == 0 and not dots:
                    continue
                for i, o in enumerate(self.t['levels'][level][g]):
                    out.append(dict(level=level, cell=[cx, cy], group=g, index=i, model=o['model'],
                                    scale=s,
                                    x=(cx * CELL + CELL // 2 + o['x']) * s,
                                    y=(cy * CELL + CELL // 2 + o['y']) * s,
                                    z=o['z'] * s))
        return out


def wld_xy(x, y):
    """World coords (y north) -> .WLD coords (y south)."""
    return x / WLD_UNIT, (WORLD - y) / WLD_UNIT


def wld_targets(w, pl):
    """Mission targets from the theater's .WLD, in world coordinates, with the scenery models ([level, model]) found
    at exactly that spot (201 of 210 targets over the 4 theaters match a placement exactly)."""
    import wld
    text = gamever.installed().text
    wf = wld.parse(game_file(w.wld))
    at = {}
    for p in pl:
        at.setdefault((p['x'], p['y']), []).append(p)
    out = []
    for t in wf['targets']:
        if not t['obj']:
            continue
        x, y = t['x'] * WLD_UNIT, WORLD - t['y'] * WLD_UNIT
        name = wf['names'][t['name_idx']] if t['name_idx'] else ''
        tname = wf['names'][t['obj']] if t['obj'] < len(wf['names']) else ''
        here = at.get((x, y), [])
        out.append(dict(index=t['i'], name=text(name[1]) if name else '', name_raw=name[1] if name else '',
                        type=t['obj'],
                        type_name=text(tname[1]) if tname else '', flags=t['flags'], a=t['a'],
                        x=x, y=y, models=[[p['level'], p['model']] for p in here]))
    return out


def export_json(w, path, dots=False):
    pl = w.placements(dots)
    for p in pl:
        p['wld'] = [round(v, 2) for v in wld_xy(p['x'], p['y'])]
    doc = dict(
        theater=w.code, name=w.name, models=f'{w.code}.3D3',
        units=dict(world_units_per_wld_unit=WLD_UNIT, map_size=WORLD,
                   axes='x east, y north, z up; model vertices use the same axes',
                   placement='world = (cell*4096 + 2048 + offset) * scale; vertex_world = world + vertex*scale'),
        levels={str(L): dict(scale=level_scale(L), cell=CELL * level_scale(L), grid=GRID_SIZE[L])
                for L in range(5)},
        grids=dict(l4=[w.l4[r * 8:r * 8 + 8] for r in range(8)],
                   l3=[w.g['l3'][r * 16:r * 16 + 16] for r in range(16)],
                   patterns={str(k): [v[i * 16:i * 16 + 16] for i in range(32)]
                             for k, v in w.g['patterns'].items()}),
        groups={str(L): w.t['levels'][L] for L in range(5)},
        targets=wld_targets(w, pl),
        placements=pl)
    with open(path, 'w') as f:
        json.dump(doc, f, separators=(',', ':'))
    return doc


def load_models(code):
    import model3d
    lib = model3d.load(game_file(code + '.3D3'))
    out = []
    for off in lib['offsets']:
        _, p, _ = model3d.parse_model(lib['data'], off, lib['pool'])
        out.append(model3d.parse_body(lib['data'], p, lib['pool'])[0])
    return out


def model_polys(body):
    """[(color, [(x,y,z)...]), ...] polygons and lines of a mesh body."""
    import model3d
    res = []
    if body['type'] != 'mesh':
        return res
    V = [v['pos'] for v in body['vertices']]
    for pr in body['prims']:
        if pr['color'] == 0xff:
            continue
        if pr['kind'] == 'poly':
            loop = model3d.edge_loop(body, pr)
            if loop:
                res.append((pr['color'] & 15, [V[k] for k in loop]))
        else:
            a, b = body['edges'][pr['edges'][0]]['v']
            res.append((pr['color'] & 15, [V[a], V[b]]))
    return res


def render_png(w, pl, path, px=32768):
    """Top-down map, north up. Levels drawn coarse to fine, low z first."""
    from PIL import Image, ImageDraw
    from model3d import EGA
    bodies = load_models(w.code)
    lo, hi = -2 * CELL * 64, 6 * CELL * 64
    k = px / (hi - lo)
    img = Image.new('RGB', (px, px), (32, 48, 64))
    dr = ImageDraw.Draw(img)
    for p in sorted(pl, key=lambda p: (-p['level'], p['z'])):
        s = p['scale']
        for col, pts in model_polys(bodies[p['model']]):
            q = [((p['x'] + v[0] * s - lo) * k, (hi - (p['y'] + v[1] * s)) * k) for v in pts]
            c = '#' + EGA[col]
            if len(q) >= 3:
                dr.polygon(q, fill=c)
            else:
                dr.line(q, fill=c)
    m0, m1 = (0 - lo) * k, (WORLD - lo) * k        # outline of the playable map
    dr.rectangle([m0, px - m1, m1, px - m0], outline=(255, 255, 255))
    img.save(path)


def export_obj(w, pl, path):
    """Whole theater as one OBJ (X east, Y up, Z south), one object per placement."""
    bodies = load_models(w.code)
    out = [f'# {w.name} ({w.code}) scenery, world units / 256', 'mtllib ega.mtl']
    nv = 0
    f = 1 / 256
    for n, p in enumerate(pl):
        polys = model_polys(bodies[p['model']])
        if not polys:
            continue
        out.append(f"o L{p['level']}_{n}_m{p['model']}")
        s = p['scale']
        cur = None
        for col, pts in polys:
            for v in pts:
                out.append(f"v {(p['x'] + v[0] * s) * f:.3f} {(p['z'] + v[2] * s) * f:.3f} "
                           f"{-(p['y'] + v[1] * s) * f:.3f}")
            if col != cur:
                out.append(f'usemtl ega{col}'); cur = col
            idx = ' '.join(str(nv + i + 1) for i in range(len(pts)))
            out.append(('f ' if len(pts) >= 3 else 'l ') + idx)
            nv += len(pts)
    open(path, 'w').write('\n'.join(out) + '\n')


if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    code = args[0].upper()
    outdir = args[1] if len(args) > 1 else world_export_dir()
    os.makedirs(outdir, exist_ok=True)
    w = World(code)
    doc = export_json(w, os.path.join(outdir, f'{code}.json'), dots='--dots' in sys.argv)
    pl = doc['placements']
    print(f'{code}: {len(pl)} placements')
    if '--png' in sys.argv:
        render_png(w, pl, os.path.join(outdir, f'{code}_map.png'))
    if '--obj' in sys.argv:
        export_obj(w, pl, os.path.join(outdir, f'{code}.obj'))
