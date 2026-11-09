"""F-19 Stealth Fighter .3D3 model library parser / exporter.

Format reverse-engineered from EGAME.EXE renderer (segment 0FEF, see NOTES.md).

usage: model3d.py FILE.3D3 [OUTDIR]     -> OUTDIR/<name>.json + OUTDIR/<name>_NN.obj
       OUTDIR defaults to export/3d, where the viewer reads the models.
"""
import json, os, struct, sys
from paths import PROJECT_DIR

EXPORT_DIR = os.path.join(PROJECT_DIR, 'export', '3d')

# Standard EGA 16-colour palette. Model colour index goes through DS:09E8 (identity for 0..15).
EGA = ['000000', '0000aa', '00aa00', '00aaaa', 'aa0000', '000000', 'aa5500', 'aaaaaa',
       '555555', '5555ff', '55ff55', '55ffff', 'ff5555', 'ffff55', 'ffff55', 'ffffff']


class ParseError(Exception):
    pass


class Reader:
    def __init__(self, d, p):
        self.d, self.p = d, p

    def u8(self):
        v = self.d[self.p]; self.p += 1; return v

    def u16(self):
        v = struct.unpack_from('<H', self.d, self.p)[0]; self.p += 2; return v

    def s16(self):
        v = struct.unpack_from('<h', self.d, self.p)[0]; self.p += 2; return v

    def mask(self, wide):
        return self.u16() | (self.u16() << 16 if wide else 0)


def load(path):
    d = open(path, 'rb').read()
    magic, n = struct.unpack_from('<HH', d, 0)
    offs = list(struct.unpack_from(f'<{n}H', d, 4))
    p = 4 + 2 * n
    data_len = struct.unpack_from('<H', d, p)[0]; p += 2
    data = d[p:p + data_len]
    r = Reader(d, p + data_len)
    if r.p >= len(d) or d[r.p] == 0:   # STFLT/PHOTO: empty shared vertex pool
        return dict(magic=magic, offsets=offs, data=data, pool=[], tail_left=0)
    k = r.u8()
    idx = [d[r.p + i * k:r.p + (i + 1) * k] for i in range(3)]; r.p += 3 * k
    tabs = []
    for _ in range(3):
        c = r.u8(); tabs.append([r.s16() for _ in range(c)])
    pool = [(tabs[0][idx[0][i]], tabs[1][idx[1][i]], tabs[2][idx[2][i]]) for i in range(k)]
    return dict(magic=magic, offsets=offs, data=data, pool=pool, tail_left=len(d) - r.p)


def parse_body(data, p, pool):
    """Parse one model body (after size byte and LOD entries). Returns dict, end offset."""
    r = Reader(data, p)
    head = data[p]
    kind = head & 0x3f
    if kind == 0x3f:        # single dot at object origin: 3f colour
        r.p += 1
        return dict(type='dot', color=r.u8()), r.p
    if kind == 0x3e:        # point lights on pool vertices: 3e ?? n idx...  (colour by distance)
        r.p += 2; n = r.u8()
        pts = [pool[r.u8()] for _ in range(n)]
        return dict(type='lights', vertices=pts), r.p
    if head & 0x60 == 0x60:  # EGAME 09D8: byte consumed, low 2 bits select a ground clamp
        r.p += 1; head = data[r.p]
    r.p += 1
    nplanes = head & 0x1f
    wide = nplanes > 16
    planes = [[r.s16() for _ in range(4)] for _ in range(nplanes)]

    b = r.u8()
    verts = []
    if b & 0x80:
        for _ in range(b & 0x7f):
            m = r.mask(wide); verts.append(dict(mask=m, pos=pool[r.u8()]))
    else:
        for _ in range(b & 0x7f):
            m = r.mask(wide); verts.append(dict(mask=m, pos=(r.s16(), r.s16(), r.s16())))

    edges = []
    for _ in range(r.u8()):
        m = r.mask(wide); edges.append(dict(mask=m, v=(r.u8(), r.u8())))

    def prim():
        t = r.u8()
        if t & 3 == 1:
            plane = t >> 3                # face plane index (bit 2 is always 0)
            n = r.u8()
            e = [r.u8() for _ in range(n)]
            return dict(kind='poly', plane=plane, edges=e, color=r.u8())
        m = r.mask(wide)
        e = r.u8()
        return dict(kind='line', type=t, mask=m, edges=[e], color=r.u8())

    prims = []
    groups = None
    c = r.u8()
    if c == 0xff:           # plane-sorted groups (BSP-like draw order)
        base = r.p
        tree = data[base:base + 2 * nplanes + 1]
        r.p = base + 2 * nplanes + 1
        goffs = [r.u16() for _ in range(nplanes)]
        gcnt = [r.u8() for _ in range(nplanes)]
        gbase = r.p
        groups, end = [], gbase
        for g in range(nplanes):
            r.p = gbase + goffs[g]
            grp = []
            for _ in range(gcnt[g]):
                grp.append(len(prims)); prims.append(prim())
            groups.append(grp)
            end = max(end, r.p)
        r.p = end
        groups = dict(tree=tree.hex(' '), groups=groups)
    else:
        for _ in range(c):
            prims.append(prim())
    return dict(type='mesh', flags=head & 0xe0, planes=planes, vertices=verts, edges=edges,
                prims=prims, sorted=groups), r.p


def edge_loop(body, prim):
    """Convert a polygon's edge list into an ordered vertex loop."""
    E = body['edges']
    segs = [E[i]['v'] for i in prim['edges']]
    if not segs:
        return []
    loop = list(segs[0])
    rest = segs[1:]
    while rest:
        for i, (a, b) in enumerate(rest):
            if a == loop[-1]: loop.append(b); break
            if b == loop[-1]: loop.append(a); break
        else:
            return None       # not a closed chain
        rest.pop(i)
    if loop[0] == loop[-1]:
        loop.pop()
    return loop


def lod_chain(data, p):
    """Walk LOD entries [0x80|lvl][s16 rel] starting at p (EGAME 09AB).
    If distance > 128<<lvl the game jumps to p+rel (which may be another LOD entry), else p+=3.
    Returns (entries, near_body_offset)."""
    lods = []
    while data[p] & 0x80:
        lvl = data[p] & 7
        rel = struct.unpack_from('<h', data, p + 1)[0]
        lods.append(dict(level=lvl, beyond_dist=128 << lvl, target=p + rel))
        p += 3
    return lods, p


def parse_model(data, off, pool):
    """Model = size byte, LOD chain, body."""
    lods, p = lod_chain(data, off + 1)
    for l in lods:
        l['far_body'] = lod_chain(data, l['target'])[1]
    return data[off], p, lods


def export(path, outdir, objs=True):
    """OUTDIR/<name>.json (+ ega.mtl), and with `objs` an OUTDIR/<name>_NN.obj per mesh model."""
    lib = load(path)
    data, pool = lib['data'], lib['pool']
    name = os.path.splitext(os.path.basename(path))[0].upper()
    os.makedirs(outdir, exist_ok=True)
    models, bodies = [], {}

    def body_at(p):
        if p not in bodies:
            try:
                b, end = parse_body(data, p, pool)
                b['offset'], b['end'] = p, end
            except (IndexError, struct.error, ParseError) as e:
                b = dict(type='error', offset=p, error=repr(e))
            bodies[p] = b
        return bodies[p]

    for i, off in enumerate(lib['offsets']):
        size, p, lods = parse_model(data, off, pool)
        m = dict(index=i, offset=off, size_class=size, lods=lods, body=p)
        body_at(p)
        for l in lods:
            body_at(l['far_body'])
        models.append(m)

    for i, m in enumerate(models):
        b = bodies[m['body']]
        if objs and b['type'] == 'mesh':
            write_obj(os.path.join(outdir, f'{name}_{i:02d}.obj'), b, f'{name} model {i}')
    out = os.path.join(outdir, f'{name}.json')
    with open(out + '.tmp', 'w') as f:     # an interrupted export leaves no half-written file behind
        json.dump(dict(file=os.path.basename(path), pool=pool, models=models,
                       bodies={hex(k): v for k, v in sorted(bodies.items())}), f, indent=1)
    os.replace(out + '.tmp', out)
    write_mtl(os.path.join(outdir, 'ega.mtl'))
    return lib, models, bodies


def write_mtl(path):
    with open(path, 'w') as f:
        for i, h in enumerate(EGA):
            r, g, b = (int(h[k:k + 2], 16) / 255 for k in (0, 2, 4))
            f.write(f'newmtl ega{i}\nKd {r:.3f} {g:.3f} {b:.3f}\nKa 0 0 0\n\n')


def write_obj(path, body, title):
    # game axes: w0 = east, w1 = north, w2 = up  ->  OBJ (Y up, right-handed): X=w0, Y=w2, Z=-w1
    out = [f'# {title}', 'mtllib ega.mtl']
    for v in body['vertices']:
        x, y, z = v['pos']
        out.append(f'v {x} {z} {-y}')
    cur = None
    for pr in body['prims']:
        col = pr['color']
        if col == 0xff:
            continue
        mat = f'ega{col & 15}'
        if mat != cur:
            out.append(f'usemtl {mat}'); cur = mat
        if pr['kind'] == 'poly':
            loop = edge_loop(body, pr)
            if loop and len(loop) >= 3:
                out.append('f ' + ' '.join(str(k + 1) for k in loop))
            elif loop:
                out.append('l ' + ' '.join(str(k + 1) for k in loop))
        else:
            a, b = body['edges'][pr['edges'][0]]['v']
            out.append(f'l {a + 1} {b + 1}')
    open(path, 'w').write('\n'.join(out) + '\n')


if __name__ == '__main__':
    src = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else EXPORT_DIR
    lib, models, bodies = export(src, out)
    errs = [b for b in bodies.values() if b['type'] == 'error']
    print(f'{src}: {len(models)} models, {len(bodies)} bodies, pool {len(lib["pool"])}, '
          f'errors {len(errs)}, tail_left {lib["tail_left"]}')
    for b in errs[:10]:
        print('  ', hex(b['offset']), b['error'])
