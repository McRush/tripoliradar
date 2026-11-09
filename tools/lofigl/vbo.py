import logging
import os
import json
import numpy as np
import moderngl as mgl
from paths import PROJECT_DIR

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Generic base class -----------------------------------------------------------
# -----------------------------------------------------------------------------

class BaseVBO:
    """Wraps a moderngl buffer with convenience for release and standardises
    *format* / *attribs* properties expected by your shader programs."""

    def __init__(self, ctx: mgl.Context):
        self.ctx = ctx
        self.vbo = self._create_vbo()
        # subclasses must set: self.format (str) and self.attribs (list[str])

    # ------------------------------------------------------------------
    # expected overrides ------------------------------------------------
    # ------------------------------------------------------------------
    def get_vertex_data(self) -> np.ndarray:
        """Return contiguous *float32* numpy array containing interleaved vertex
        attributes in the order dictated by *self.format*."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # helpers -----------------------------------------------------------
    # ------------------------------------------------------------------
    def _create_vbo(self):
        vertex_data = self.get_vertex_data()
        return self.ctx.buffer(vertex_data)

    def destroy(self):
        self.vbo.release()


# -----------------------------------------------------------------------------
# F-19 .3D3 models -------------------------------------------------------------
# -----------------------------------------------------------------------------

F19_EXPORT_DIR = os.path.join(PROJECT_DIR, "export", "3d")     # = resources.MODELS_DIR
F19_PALETTE_SIZE = 16   # EGA colours; texel i of the palette texture holds colour i
F19_DECAL_STEP = 0.01   # model units a decal is lifted per layer above the coplanar polygon under it,
F19_DECAL_STEP_REL = 0.0003  # ... or this fraction of the model's radius if larger (big models get shown scaled down)
# Line primitives (masts, barrels) are drawn as camera-facing strips over the plain lines up close
# (shaders/default_instanced_lines.geom), graphics.line_width world units wide whatever the model's scale.
# Per library: that width times this; 0 = plain GL lines only.
F19_LINE_WIDTH = {"STFLT": 0.0}     # aircraft and weapons: their lines are fine detail
F19_LINE_WIDTH_DEFAULT = 1.0        # theaters, PHOTO


class F19VBO(BaseVBO):
    """Prebuilt vertex data of one F-19 model (uv, normal, position layout).
    The uv points at the model colour's texel in the EGA palette texture.
    `line_width` (LINES only): the strips drawn over the lines up close are this times graphics.line_width wide,
    0 = none."""

    def __init__(self, ctx: mgl.Context, vertex_data: np.ndarray, mode=mgl.TRIANGLES, cull_face=True,
                 line_width=0.0):
        self._vertex_data = vertex_data
        self.format = "2f 3f 3f"
        self.attribs = ["in_texcoord_0", "in_normal", "in_position"]
        self.mode = mode
        self.cull_face = cull_face
        self.line_width = line_width
        super().__init__(ctx)

    def get_vertex_data(self):
        return self._vertex_data


def _f19_to_gl(v):
    """Game axes (x east, y north, z up) -> OpenGL (X east, Y up, Z south). A rotation, so windings are kept."""
    v = np.asarray(v, dtype="f8")
    return np.stack([v[..., 0], v[..., 2], -v[..., 1]], axis=-1)


def _f19_edge_loop(body, prim):
    """Order a polygon's edges into a vertex loop; None if they don't form one closed chain."""
    segs = [body["edges"][i]["v"] for i in prim["edges"]]
    loop, rest = list(segs[0]), segs[1:]
    while rest:
        for i, (a, b) in enumerate(rest):
            if a == loop[-1]: loop.append(b); break
            if b == loop[-1]: loop.append(a); break
        else:
            return None
        rest.pop(i)
    if loop[0] == loop[-1]:
        loop.pop()
    return loop


def _overlap_2d(p, q, eps=1e-3):
    """Separating-axis test for two convex 2-D polygons; polygons that only touch don't overlap."""
    for poly in (p, q):
        edges = np.roll(poly, -1, axis=0) - poly
        for ex, ey in edges:
            axis = np.array([-ey, ex])
            a, b = p @ axis, q @ axis
            if a.max() <= b.min() + eps * np.linalg.norm(axis) or b.max() <= a.min() + eps * np.linalg.norm(axis):
                return False
    return True


def _plane_axes(n):
    """Two unit axes spanning the plane with unit normal n."""
    ax = np.cross(n, (1.0, 0.0, 0.0) if abs(n[0]) < 0.9 else (0.0, 1.0, 0.0))
    ax /= np.linalg.norm(ax)
    return ax, np.cross(n, ax)


def build_f19_model(body):
    """Turn a mesh body from export/3d/<LIB>.json into (triangle data, line data) float32 arrays.

    The game culls each polygon by its face plane (visible when the eye is in front of it), so every
    polygon is wound counter-clockwise around its plane normal and drawn with back-face culling:
    back-to-back faces with different colours work as in the game. Polygons with no plane are emitted
    with both windings. The game paints coplanar primitives in list order, later ones being decals, so
    a polygon is lifted along its normal one step above each earlier coplanar primitive of another
    colour it overlaps (painting a colour over itself changes nothing, so same-colour overlaps keep
    their layer and don't open cracks). Lines are decals too: a line lying on earlier primitives
    (markings, or an edge between two facets) is lifted by the same rule, along the mean normal of
    their planes."""
    verts = np.array([v["pos"] for v in body["vertices"]], dtype="f8").reshape(-1, 3)
    planes = body["planes"]
    radius = float(np.linalg.norm(verts.max(axis=0) - verts.min(axis=0))) / 2 if len(verts) else 0.0
    decal_step = max(F19_DECAL_STEP, F19_DECAL_STEP_REL * radius)
    tris, lines = [], []
    layers = {}   # plane index -> (normal, axes, [(2-D outline, depth, layer, colour)])

    def uv(color):
        return ((color & 15) + 0.5) / F19_PALETTE_SIZE, 0.5

    def emit(out, pts, normal, color):
        u, v = uv(color)
        for p in _f19_to_gl(pts):
            out.append((u, v, *normal, *p))

    for prim in body["prims"]:
        color = prim["color"]
        if color == 0xFF:      # not drawn
            continue
        if prim["kind"] == "line":
            a, b = body["edges"][prim["edges"][0]]["v"]
            seg = verts[[a, b]]
            # earlier polygons the line lies on (touching counts: lines often run along polygon edges)
            under = []    # (plane, 2-D segment, depth, layer)
            for plane, (n, (ax, ay), same) in layers.items():
                depth = seg @ n
                if abs(depth[0] - depth[1]) > 0.05:
                    continue
                s2 = np.stack([seg @ ax, seg @ ay], axis=1)
                top = max((l if c == color else l + 1 for o, d, l, c in same
                           if abs(d - depth[0]) < 0.05 and _overlap_2d(o, s2, eps=-1e-3)), default=None)
                if top is not None:
                    under.append((plane, s2, float(depth[0]), top))
            if under:
                layer = max(u[3] for u in under)
                lift = sum(layers[u[0]][0] for u in under)
                if np.linalg.norm(lift) < 1e-6:
                    lift = layers[under[0][0]][0]
                seg = seg + lift / np.linalg.norm(lift) * (layer * decal_step)
                for plane, s2, depth, _ in under:
                    layers[plane][2].append((s2, depth, layer, color))
            emit(lines, seg, (0.0, 0.0, 0.0), color)
            continue
        loop = _f19_edge_loop(body, prim)
        if not loop:
            continue
        if len(loop) < 3:
            emit(lines, verts[loop[:2]], (0.0, 0.0, 0.0), color)
            continue
        pts = verts[loop]
        newell = np.cross(pts, np.roll(pts, -1, axis=0)).sum(axis=0)
        if np.linalg.norm(newell) < 1e-9:
            continue
        newell /= np.linalg.norm(newell)

        plane = prim["plane"]
        n = np.array(planes[plane][:3], dtype="f8") if plane < len(planes) else None
        if n is not None:
            n /= np.linalg.norm(n)
            facing = float(newell @ n)
        if n is None or abs(facing) < 0.5:
            # no usable plane: visible from both sides
            for loop_pts, nrm in ((pts, newell), (pts[::-1], -newell)):
                normal = _f19_to_gl(nrm)
                for k in range(1, len(loop_pts) - 1):
                    emit(tris, loop_pts[[0, k, k + 1]], normal, color)
            continue
        if facing < 0:
            pts = pts[::-1]

        # decal layer among overlapping coplanar primitives drawn earlier
        if plane not in layers:
            layers[plane] = (n, _plane_axes(n), [])
        _, (ax, ay), same = layers[plane]
        outline = np.stack([pts @ ax, pts @ ay], axis=1)
        depth = float(pts[0] @ n)
        layer = 0
        for o, d, l, c in same:
            if abs(d - depth) < 0.05 and _overlap_2d(o, outline):
                layer = max(layer, l if c == color else l + 1)
        same.append((outline, depth, layer, color))
        pts = pts + n * (layer * decal_step)

        normal = _f19_to_gl(n)
        for k in range(1, len(pts) - 1):
            emit(tris, pts[[0, k, k + 1]], normal, color)

    def pack(rows):
        return np.array(rows, dtype="f4").reshape(-1, 8)

    return pack(tris), pack(lines)


def load_f19_palette(mtl_path):
    """EGA colours from the exported ega.mtl as a list of 16 (r, g, b) tuples, index = colour number."""
    colors = [(1.0, 0.0, 1.0)] * F19_PALETTE_SIZE
    index = None
    with open(mtl_path) as f:
        for line in f:
            parts = line.split()
            if parts[:1] == ["newmtl"]:
                index = int(parts[1][3:])          # "ega<n>"
            elif parts[:1] == ["Kd"] and index is not None:
                colors[index] = tuple(float(c) for c in parts[1:4])
    return colors


# -----------------------------------------------------------------------------
# Helper ----------------------------------------------------------------------
# -----------------------------------------------------------------------------

def _flatten(vertices, indices):
    """Return *float32* array flattening *indices* onto *vertices*."""
    return np.array([vertices[i] for tri in indices for i in tri], dtype="f4")


# -----------------------------------------------------------------------------
# Procedural VBOs --------------------------------------------------------------
# -----------------------------------------------------------------------------

class QuadVBO(BaseVBO):
    def __init__(self, ctx):
        self.format = "2f 3f 4f"
        self.attribs = ["in_texcoord_0", "in_normal", "in_position"]
        super().__init__(ctx)

    def get_vertex_data(self):
        vertices = [(-1.0, -1.0, 0.0, 1.0), (1.0, -1.0, 0.0, 1.0),
                    (1.0,  1.0, 0.0, 1.0), (-1.0, 1.0, 0.0, 1.0)]
        indices = [(0, 2, 3), (0, 1, 2)]
        pos = _flatten(vertices, indices)

        uv_vertices = [(0, 0), (1, 0), (1, 1), (0, 1)]
        uv_indices = [(0, 2, 3), (0, 1, 2)]
        uv = _flatten(uv_vertices, uv_indices)

        normals = np.array([(0, 0, 1)] * 6, dtype="f4")
        return np.hstack([uv, normals, pos])


class UnitQuadVBO(BaseVBO):
    """Corners of a [0, 1] quad as a triangle strip, used for instanced screen-space glyphs."""

    def __init__(self, ctx):
        self.format = "2f"
        self.attribs = ["in_corner"]
        super().__init__(ctx)

    def get_vertex_data(self):
        return np.array([(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0)], dtype="f4")


class AdvancedSkyBoxVBO(BaseVBO):
    def __init__(self, ctx):
        self.format = "3f"
        self.attribs = ["in_position"]
        super().__init__(ctx)

    def get_vertex_data(self):
        z = 0.0  # reversed depth: the sky is at infinity, behind everything (depth test GEQUAL)
        vertices = [(-1, -1, z), (3, -1, z), (-1, 3, z)]
        return np.array(vertices, dtype="f4")


# -----------------------------------------------------------------------------
# VBO Manager -----------------------------------------------------------------
# -----------------------------------------------------------------------------

class VBOManager:
    """Central registry of the VBOs: *procedural* meshes and the F-19 models.

    • **Procedural meshes** are instantiated eagerly.
    • **F-19 models** are built from every model library JSON in *f19_dir*
      (export/3d) with keys ``f19/<LIB>_<nn>`` for polygons and
      ``f19/<LIB>_<nn>_lines`` for line primitives (masts, antennas) when present.
      ``f19_models[LIB]`` lists the model indices that were loaded. They are loaded by
      :meth:`load_f19`, not by the constructor: the engine first makes sure the export is there.
    """

    def __init__(self, ctx: mgl.Context, f19_dir: str = F19_EXPORT_DIR):
        self.ctx = ctx
        self.vbos: dict[str, BaseVBO] = {}
        self.f19_models: dict[str, list[int]] = {}
        self.f19_dir = f19_dir

        # Procedural meshes: the moon's quad, the label glyph quad, the full-screen sky triangle
        self.vbos.update({
            "quad": QuadVBO(ctx),
            "unit_quad": UnitQuadVBO(ctx),
            "advanced_skybox": AdvancedSkyBoxVBO(ctx),
        })

    def load_f19(self):
        """F-19 model libraries: a VBO per mesh model of every library JSON in f19_dir."""
        self._scan_and_load_f19(self.f19_dir)

    # ------------------------------------------------------------------
    # Public API --------------------------------------------------------
    # ------------------------------------------------------------------
    def __getitem__(self, key: str):
        return self.vbos[key]

    def add_vbo(self, key: str, vbo: BaseVBO):
        self.vbos[key] = vbo

    def destroy(self):
        for vbo in self.vbos.values():
            vbo.destroy()

    # ------------------------------------------------------------------
    # internals ---------------------------------------------------------
    # ------------------------------------------------------------------
    def _scan_and_load_f19(self, root: str):
        """Create :class:`F19VBO` for every mesh model of every library JSON in *root*."""
        if not os.path.isdir(root):
            logger.warning(f"F-19 export directory '{root}' not found")
            return
        for fname in sorted(os.listdir(root)):
            if not fname.lower().endswith(".json"):
                continue
            with open(os.path.join(root, fname), encoding="utf-8") as f:
                lib = json.load(f)
            name = os.path.splitext(fname)[0].upper()
            loaded = self.f19_models[name] = []
            for model in lib["models"]:
                body = lib["bodies"][hex(model["body"])]
                if body["type"] != "mesh":
                    continue
                tris, lines = build_f19_model(body)
                key = f"f19/{name}_{model['index']:02d}"
                if len(tris):
                    self.vbos[key] = F19VBO(self.ctx, tris)
                if len(lines):
                    self.vbos[key + "_lines"] = F19VBO(self.ctx, lines, mode=mgl.LINES, cull_face=False,
                                                       line_width=F19_LINE_WIDTH.get(name, F19_LINE_WIDTH_DEFAULT))
                if len(tris) or len(lines):
                    loaded.append(model["index"])
