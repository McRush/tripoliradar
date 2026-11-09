from .vao import VAO
from .texture import TextureManager


class Mesh:
    def __init__(self, app):
        self.app = app
        self.vao = VAO(app.ctx)
        self.texture = TextureManager(app)

    def load_f19(self):
        """The F-19 models and palette from the export (export/3d)."""
        self.vao.vbo.load_f19()
        self.texture.load_f19_palette()

    def destroy(self):
        self.vao.destroy()
        self.texture.destroy()