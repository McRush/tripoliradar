from .vbo import VBOManager
from .shader_program import ShaderProgram


class VAO:
    def __init__(self, ctx):
        self.ctx = ctx
        self.vbo = VBOManager(ctx)
        self.program = ShaderProgram(ctx)
        self.vaos = {}

        # Moon vao
        self.vaos['moon'] = self.get_vao(
            program=self.program.programs['moon'],
            vbo = self.vbo.vbos['quad'])

        # advanced_skybox vao
        self.vaos['advanced_skybox'] = self.get_vao(
            program=self.program.programs['advanced_skybox'],
            vbo=self.vbo.vbos['advanced_skybox'])

    def get_vao(self, program, vbo):
        vao = self.ctx.vertex_array(program, [(vbo.vbo, vbo.format, *vbo.attribs)], skip_errors=True)
        return vao

    def destroy(self):
        self.vbo.destroy()
        self.program.destroy()
