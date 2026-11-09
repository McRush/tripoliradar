import json
import logging
import moderngl as mgl
import numpy as np

logger = logging.getLogger(__name__)


class Glyph:
    def __init__(self, uv_rect, size, offset, advance):
        self.uv_rect = uv_rect  # (u, v) of top-left and bottom-right corners, GL convention
        self.size = size        # (w, h) in font pixels
        self.offset = offset    # bitmap bottom-left relative to the pen on the baseline, y up
        self.advance = advance  # horizontal pen advance in font pixels


class BitmapFont:
    """Glyph metrics from a baked JSON (see bdf_to_png.py) plus the matching atlas texture."""

    FALLBACK_CHAR = ord('?')

    def __init__(self, app, meta_path, tex_id):
        self.texture = app.mesh.texture.textures[tex_id]

        with open(meta_path) as f:
            meta = json.load(f)

        # Older bakes have no offsets/advance: sit every glyph on the baseline
        # and space them by the widest glyph (Cozette is monospace anyway).
        max_width = max(g['size'][0] for g in meta.values())
        if any('offset' not in g for g in meta.values()):
            logger.warning(f"'{meta_path}' has no glyph offsets, re-bake it with bdf_to_png.py "
                           "for correct descenders")

        self.glyphs = {}
        for code, g in meta.items():
            u_min, v_min, u_max, v_max = g['uv']
            # The baked UVs have v = 0 at the top of the image, but textures are
            # uploaded flipped (v = 0 at the bottom), so flip v here.
            uv_rect = (u_min, 1.0 - v_min, u_max, 1.0 - v_max)
            self.glyphs[int(code)] = Glyph(uv_rect, tuple(g['size']),
                                           tuple(g.get('offset', (0, 0))),
                                           g.get('advance', max_width))

        self.ascent = max(g.offset[1] + g.size[1] for g in self.glyphs.values())
        self.descent = -min(g.offset[1] for g in self.glyphs.values())
        self.line_height = self.ascent + self.descent

    def get_glyph(self, char):
        return self.glyphs.get(ord(char)) or self.glyphs[self.FALLBACK_CHAR]


class Label:
    """Screen-space text, one instanced quad per glyph.

    pos   -- top-left corner of the text in window pixels, y grows downwards
    scale -- pixel multiplier; keep it an integer for crisp nearest-neighbour glyphs
    color -- RGBA, 0..1
    """

    def __init__(self, app, font, text, pos=(0, 0), scale=1, color=(1.0, 1.0, 1.0, 1.0)):
        self.app = app
        self.font = font
        self.pos = pos
        self.scale = scale
        self.color = color
        self.text = text
        self.visible = True
        self.vbo = app.mesh.vao.vbo.vbos['unit_quad']
        self.program = app.mesh.vao.program.programs['label']
        self.instance_vbo: mgl.Buffer = None
        self.vao: mgl.VertexArray = None
        self.num_instances = 0

        self.update_buffers()
        self.on_init()

    def set_text(self, text):
        if text != self.text:
            self.text = text
            self.update_buffers()

    def set_pos(self, pos):
        self.pos = pos
        self.update_buffers()

    def build_instances(self):
        """Lay out the text into per-glyph (x, y, w, h, u0, v0, u1, v1) rows."""
        font, s = self.font, self.scale
        pen_x, baseline = self.pos[0], self.pos[1] + font.ascent * s
        instances = []

        for char in self.text:
            if char == '\n':
                pen_x = self.pos[0]
                baseline += font.line_height * s
                continue

            glyph = font.get_glyph(char)
            if not char.isspace():
                w, h = glyph.size
                ox, oy = glyph.offset
                x = pen_x + ox * s
                y = baseline - (oy + h) * s
                instances.append((x, y, w * s, h * s, *glyph.uv_rect))
            pen_x += glyph.advance * s

        return instances

    def update_buffers(self):
        instances = self.build_instances()
        self.num_instances = len(instances)
        self.release()
        if not instances:
            return

        self.instance_vbo = self.app.ctx.buffer(np.array(instances, dtype='f4'))
        self.vao = self.app.ctx.vertex_array(
            self.program,
            [
                (self.vbo.vbo, '2f /v', 'in_corner'),
                (self.instance_vbo, '4f 4f /i', 'in_rect', 'in_uv_rect'),
            ],
            skip_errors=True
        )

    def update(self):
        self.font.texture.use(location=0)
        self.program['u_color'].value = tuple(self.color)
        self.program['u_resolution'].value = self.app.WIN_SIZE

    def render(self):
        if not self.visible or not self.num_instances:
            return
        self.update()
        self.vao.render(mgl.TRIANGLE_STRIP, instances=self.num_instances)

    def on_init(self):
        self.program['u_font_atlas'] = 0

    def release(self):
        if self.vao:
            self.vao.release()
            self.vao = None
        if self.instance_vbo:
            self.instance_vbo.release()
            self.instance_vbo = None
