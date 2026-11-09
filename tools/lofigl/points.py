import moderngl as mgl
import numpy as np
from .camera import to_dvec3

MAX_BANDS = 4   # matches point_sprite.vert


class PointSprites:
    """Screen-facing points (one GL point each), drawn with depth test and the same fog as meshes.

    Positions are relative to a world-space `origin` (double precision) like ArrayInstancedModel, so a
    batch stays precise in float32. Each point has an RGBA colour. Size is `size` pixels, or the
    projected size of `world_size` world units when that is larger (0 = pixels only), so a distant
    object can shrink to a point and grow back into its model's place.
    `bands` optionally colours points by camera distance instead: [(distance, rgba), ...] in increasing
    distance; a point takes the first band it is closer than, else the last one (use None as its distance).
    `round` cuts the square sprite to a disc. Points fade out over `fade_band` world units before the
    horizontal distance `fade_far` (0 = no fade); colour alpha works the same way (alpha-to-coverage)."""
    def __init__(self, app, size=3.0, world_size=10.0, round=False, bands=None, capacity=128,
                 fade_far=0.0, fade_band=1.0):
        self.app = app
        self.ctx = app.ctx
        self.camera = app.camera
        self.program = app.mesh.vao.program.programs['point_sprite']
        self.size, self.world_size, self.round = size, world_size, round
        self.fade_far, self.fade_band = fade_far, fade_band
        self.set_bands(bands)
        self.origin = to_dvec3((0, 0, 0))
        self.capacity = capacity
        self.num_points = 0
        self.buffer = None
        self.vao = None
        self._allocate()

    def _allocate(self):
        if self.vao is not None:
            self.vao.release()
            self.buffer.release()
        self.buffer = self.ctx.buffer(reserve=self.capacity * 28)
        self.vao = self.ctx.vertex_array(self.program, [(self.buffer, '3f 4f', 'in_position', 'in_color')],
                                         skip_errors=True)

    def set_bands(self, bands):
        bands = list(bands or [])[:MAX_BANDS]
        self.band_count = len(bands)
        dist = [float('inf') if d is None else float(d) for d, _ in bands] + [0.0] * (MAX_BANDS - len(bands))
        colors = [tuple(c) + (1.0,) * (4 - len(c)) for _, c in bands] + [(0.0,) * 4] * (MAX_BANDS - len(bands))
        self.band_dist = np.array(dist, dtype='f4')
        self.band_color = np.array(colors, dtype='f4')

    def set_points(self, positions, origin, colors=(1.0, 1.0, 1.0, 1.0)):
        """Replace all points: positions (N, 3) relative to `origin`; colors (N, 4) or one RGBA for all."""
        pos = np.asarray(positions, dtype='f4').reshape(-1, 3)
        col = np.broadcast_to(np.asarray(colors, dtype='f4'), (len(pos), 4))
        self.origin = to_dvec3(origin)
        if len(pos) > self.capacity:
            while self.capacity < len(pos):
                self.capacity *= 2
            self._allocate()
        if len(pos):
            self.buffer.write(np.ascontiguousarray(np.hstack([pos, col]), dtype='f4'))
        self.num_points = len(pos)

    def render(self):
        if not self.num_points:
            return
        prog = self.program
        prog['m_proj'].write(self.camera.m_proj)
        prog['m_view'].write(self.camera.m_view)
        prog['u_offset'].write(self.camera.get_offset(self.origin))
        prog['u_size'].value = self.size
        prog['u_world_size'].value = self.world_size
        prog['u_pixel_scale'].value = self.camera.m_proj[1][1] * self.app.WIN_SIZE[1] / 2
        prog['u_round'].value = self.round
        prog['u_band_count'].value = self.band_count
        prog['u_band_dist'].write(self.band_dist)
        prog['u_band_color'].write(self.band_color)
        prog['u_fade_far'].value = self.fade_far
        prog['u_fade_band'].value = max(self.fade_band, 1e-6)
        prog['sunDir'].write(self.app.light.normalized_pos)
        prog['u_fog_density'].value = self.app.fog_density
        self.ctx.enable(mgl.PROGRAM_POINT_SIZE)
        self.vao.render(mode=mgl.POINTS, vertices=self.num_points)
        self.ctx.disable(mgl.PROGRAM_POINT_SIZE)

    def destroy(self):
        self.vao.release()
        self.buffer.release()
