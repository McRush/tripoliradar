"""F-19 Stealth Fighter .WLD world file parser"""
import struct, sys

# Russian localization uses a KOI-7-like transliteration: Latin glyphs are drawn as Cyrillic.
# (The English build's text is plain ASCII: gamever.Layout.text applies this only to the Russian one.)
_LATIN = r"abcdefghijklmnopqrstuvwxyz[\]^`{|}~"
_CYRILLIC = "абцдефгхийклмнопярстужвьызшэщчюшэщч"
assert len(_LATIN) == len(_CYRILLIC), "the KOI-7 tables must pair up character by character"
_LOWER = dict(zip(_LATIN, _CYRILLIC))
def koi7(s):
    out = []
    for i, ch in enumerate(s):
        c = _LOWER.get(ch.lower(), ch)
        # symbol glyphs ([ \ ] ^ `) have no case; treat them as capitals at word start
        cap = ch.isupper() or (not ch.isalpha() and c != ch and (i == 0 or s[i-1] in ' -'))
        out.append(c.upper() if cap else c)
    return "".join(out)

def cstrings(buf):
    """Split name block into (offset, string) including empty strings."""
    res, start = [], 0
    for i, b in enumerate(buf):
        if b == 0:
            res.append((start, buf[start:i].decode('latin-1')))
            start = i + 1
    return res

def parse(path):
    d = open(path, 'rb').read()
    p = 0
    def u16():
        nonlocal p; v = struct.unpack_from('<H', d, p)[0]; p += 2; return v
    w = {}
    w['magic'] = u16(); w['n_targets'] = u16(); w['unk4'] = u16(); w['n_regular'] = u16()
    w['targets'] = []
    for i in range(w['n_targets']):
        r = d[p:p+16]; p += 16
        name_idx, x, y, a, flags, b, c, obj = struct.unpack('<HHHHHHHH', r)
        w['targets'].append(dict(i=i, raw=r.hex(' '), name_idx=name_idx, x=x, y=y,
                                 a=a, flags=flags, b=b, c=c, obj=obj & 0x7f, objhi=obj >> 7))
    w['n_recs2'] = u16()
    w['recs2'] = [d[p+i*36:p+(i+1)*36] for i in range(w['n_recs2'])]; p += 36 * w['n_recs2']
    w['tabA'] = d[p:p+100]; p += 100
    w['tabB'] = d[p:p+100]; p += 100
    w['obj_table'] = d[p:p+100]; p += 100
    w['grid'] = d[p:p+256]; p += 256
    w['names_off'] = p
    w['names'] = cstrings(d[p:])
    return w

if __name__ == '__main__':
    w = parse(sys.argv[1])
    print(f"magic={w['magic']:04x} n_targets={w['n_targets']} unk4={w['unk4']} n_regular={w['n_regular']} n_recs2={w['n_recs2']} names@{w['names_off']:#x}")
    print("-- targets: idx name_idx x y | raw")
    for t in w['targets']:
        print(f"{t['i']:3} n={t['name_idx']:3} x={t['x']:5} y={t['y']:5} a={t['a']:04x} fl={t['flags']:04x} obj={t['obj']:3} hi={t['objhi']} | {t['raw']}")
    print("-- recs2 (36 B):")
    for r in w['recs2']: print("  ", r.hex(' '))
    for k in ('tabA', 'tabB', 'obj_table'):
        print(f"-- {k}:", ' '.join(f'{i}:{v}' for i, v in enumerate(w[k]) if v))
    print("-- grid 16x16:")
    for r in range(16): print('  ', w['grid'][r*16:(r+1)*16].hex(' '))
    print("-- names:")
    from gamever import installed           # names: KOI-7 Russian in the Russian build, plain English otherwise
    text = installed().text
    for i, (o, s) in enumerate(w['names']): print(f"  [{i:2}] +{o:#05x} {s!r:28} {text(s)}")
