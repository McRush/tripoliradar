"""Render a contact sheet of all models in an exported 3D3 JSON (painter's algorithm, EGA colours).
usage: preview.py export/3d/LB.json out.png"""
import json, sys, math
import matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon
sys.path.insert(0, __import__('os').path.dirname(__file__))
from model3d import EGA, edge_loop

def project(p, az=math.radians(35), el=math.radians(25)):
    x, y, z = p                       # game: x east, y north, z up
    ca, sa, ce, se = math.cos(az), math.sin(az), math.cos(el), math.sin(el)
    X = x * ca - y * sa
    D = x * sa + y * ca               # depth (towards viewer = smaller)
    return X, z * ce - D * se, D * ce + z * se

j = json.load(open(sys.argv[1]))
bodies = j['bodies']; models = j['models']
cols = 10; rows = math.ceil(len(models) / cols)
fig, axs = plt.subplots(rows, cols, figsize=(cols * 2, rows * 2.1), facecolor='#203040')
for ax in axs.flat: ax.axis('off'); ax.set_facecolor('#203040')
for m, ax in zip(models, axs.flat):
    b = bodies[hex(m['body'])]
    ax.set_title(f"{m['index']}", color='w', fontsize=8)
    if b['type'] != 'mesh':
        ax.text(0.5, 0.5, b['type'], color='w', ha='center', transform=ax.transAxes); continue
    P = [project(v['pos']) for v in b['vertices']]
    items = []
    for pr in b['prims']:
        if pr['color'] == 0xff: continue
        if pr['kind'] == 'poly':
            loop = edge_loop(b, pr)
            if not loop: continue
            pts = [P[k] for k in loop]
        else:
            pts = [P[k] for k in b['edges'][pr['edges'][0]]['v']]
        items.append((sum(p[2] for p in pts) / len(pts), pr['kind'], pts, '#' + EGA[pr['color'] & 15]))
    for d, kind, pts, c in sorted(items, key=lambda t: t[0]):
        if kind == 'poly':
            ax.add_patch(Polygon([(p[0], p[1]) for p in pts], closed=True, fc=c, ec='k', lw=0.2))
        else:
            ax.plot([p[0] for p in pts], [p[1] for p in pts], color=c, lw=0.8)
    if P:
        xs = [p[0] for p in P]; ys = [p[1] for p in P]
        r = max(max(xs) - min(xs), max(ys) - min(ys), 1) / 2 * 1.1
        cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
        ax.set_xlim(cx - r, cx + r); ax.set_ylim(cy - r, cy + r); ax.set_aspect('equal')
plt.tight_layout(); plt.savefig(sys.argv[2], dpi=70, facecolor=fig.get_facecolor())
