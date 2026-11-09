import moderngl as mgl
import numpy as np
import glm
import settings
from .camera import to_dvec3

# GL_LINES models with a `line_width` get camera-facing strips over the lines, that times graphics.line_width world
# units wide (shaders/default_instanced_lines.geom), skipped where they would be narrower than this many pixels
LINE_STRIP_MIN_PX = 1.0


class BaseModel:
    def __init__(self, app, vao_name, tex_id, pos=(0, 0, 0), rot=(0, 0, 0), scale=(1, 1, 1)):
        self.app = app
        self.pos = pos
        self.vao_name = vao_name
        self.rot = glm.vec3([glm.radians(a) for a in rot])
        self.scale = scale
        self.m_model = self.get_model_matrix()
        self.tex_id = tex_id
        self.vao = app.mesh.vao.vaos[vao_name]
        self.program = self.vao.program
        self.camera = self.app.camera

    def update(self): ...

    def get_model_matrix(self):
        m_model = glm.mat4()
        # translate
        m_model = glm.translate(m_model, self.pos)
        # rotate
        m_model = glm.rotate(m_model, self.rot.z, glm.vec3(0, 0, 1))
        m_model = glm.rotate(m_model, self.rot.y, glm.vec3(0, 1, 0))
        m_model = glm.rotate(m_model, self.rot.x, glm.vec3(1, 0, 0))
        # scale
        m_model = glm.scale(m_model, self.scale)
        return m_model

    def render(self):
        self.update()
        self.vao.render()


class ObjInstance:
    """`pivot` is the point in model space that is placed at `pos` and rotated and scaled about."""
    def __init__(self, pos=(0, 0, 0), rot=(0, 0, 0), scale=(1, 1, 1), pivot=(0, 0, 0)):
        self.pos = pos
        self.rot = glm.vec3([glm.radians(a) for a in rot])
        self.scale = scale
        self.pivot = glm.vec3(pivot)
        self.m_model = self.get_model_matrix()

    def get_model_matrix(self, origin=(0, 0, 0)):
        """Model matrix relative to `origin`; the subtraction is done in double precision
        so that large world coordinates don't get rounded to float32 first."""
        m_model = glm.mat4()
        # translate
        m_model = glm.translate(m_model, glm.vec3(to_dvec3(self.pos) - to_dvec3(origin)))
        # rotate
        m_model = glm.rotate(m_model, self.rot.z, glm.vec3(0, 0, 1))
        m_model = glm.rotate(m_model, self.rot.y, glm.vec3(0, 1, 0))
        m_model = glm.rotate(m_model, self.rot.x, glm.vec3(1, 0, 0))
        # scale
        m_model = glm.scale(m_model, self.scale)
        # pivot
        m_model = glm.translate(m_model, -self.pivot)
        return m_model


class ForwardInstancedModel:
    """Instanced, unlit, forward-rendered model.

    Rendering is camera-relative: instance matrices are stored relative to the batch `origin`
    (world space, double precision), and each frame the shader gets `origin - camera.position`
    as a small float32 offset. Keep a batch compact (e.g. one map tile) so float32 stays precise
    within it. If `origin` is None, the centroid of the instances is used.
    The primitive mode and back-face culling come from the VBO (`mode`, `cull_face`) when it sets them.
    `detail` is the strength of the shader's world-space brightness noise for this model (0 = none);
    the noise itself is configured by the scene through the program's u_detail_* uniforms.
    Instances fade out over `fade_band` world units before the horizontal distance `fade_far` from the
    camera (0 = no fade), through alpha-to-coverage, so the order of drawing doesn't matter.
    A LINES VBO with a `line_width` is drawn twice: plain lines (1 pixel far away), then strips that times
    graphics.line_width wide (read when the model is made; 0: the plain lines only)."""
    def __init__(self, app, vbo_name, tex_id, objects, origin=None, detail=0.0):
        self.app = app
        self.detail = detail
        self.fade_far, self.fade_band = 0.0, 1.0
        if origin is None:
            origin = sum((to_dvec3(obj.pos) for obj in objects), glm.dvec3(0)) / max(len(objects), 1)
        self.origin = to_dvec3(origin)
        self.vbo_name = vbo_name
        self.tex_id = tex_id
        self.objects = objects
        self.num_instances = len(objects)
        self.vbo = app.mesh.vao.vbo.vbos[vbo_name]
        self.mode = getattr(self.vbo, 'mode', mgl.TRIANGLES)
        self.cull_face = getattr(self.vbo, 'cull_face', False)
        programs = app.mesh.vao.program.programs
        self.program = programs['default_instanced_forward']
        self.line_width = (getattr(self.vbo, 'line_width', 0.0) * settings.get('graphics.line_width')
                           if self.mode == mgl.LINES else 0.0)
        self.strip_program = programs['default_instanced_lines'] if self.line_width > 0 else None
        self.strip_vao = None
        self.camera = self.app.camera
        self.m_model_vbo: mgl.Buffer = None

        # Create data buffers for instancing
        self.vao = self.get_vao()
        self.on_init()

    def update_buffers(self):
        m_model_list = []

        for obj in self.objects:
            m_model_list += sum(obj.get_model_matrix(self.origin).to_list(), [])

        if self.m_model_vbo is not None:
            self.m_model_vbo.release()
        self.m_model_vbo = self.app.ctx.buffer(np.array(m_model_list, dtype='float32'))
        self.num_instances = len(self.objects)

    def get_vao(self):
        """VAO of the main program (and the strip program's into self.strip_vao) over fresh instance buffers."""
        self.update_buffers()
        if self.strip_program is not None:
            self.strip_vao = self.vertex_array(self.strip_program)
        return self.vertex_array(self.program)

    def vertex_array(self, program):
        return self.app.ctx.vertex_array(
            program,
            [
                (self.vbo.vbo, '2f 3f 3f /v', 'in_texcoord_0', 'in_normal', 'in_position'),
                (self.m_model_vbo, '16f /i', 'm_model',),
            ],
            skip_errors=True
        )

    def programs(self):
        return [self.program] if self.strip_program is None else [self.program, self.strip_program]

    def update_vao(self):
        self.vao.release()
        if self.strip_vao is not None:
            self.strip_vao.release()
        self.vao = self.get_vao()

    def destroy(self):
        self.vao.release()
        if self.strip_vao is not None:
            self.strip_vao.release()
        self.m_model_vbo.release()

    def update(self):
        self.texture.use(location=0)
        for program in self.programs():
            program['m_view'].write(self.camera.m_view)
            program['m_proj'].write(self.camera.m_proj)
            program['u_offset'].write(self.camera.get_offset(self.origin))
            program['sunDir'].write(self.app.light.normalized_pos)
            program['u_fog_density'].value = self.app.fog_density
            program['u_detail'].value = self.detail
            program['u_fade_far'].value = self.fade_far
            program['u_fade_band'].value = max(self.fade_band, 1e-6)
        if self.strip_program is not None:
            self.strip_program['u_line_width'].value = self.line_width
            self.strip_program['u_min_px'].value = LINE_STRIP_MIN_PX
            self.strip_program['u_px_scale'].value = (2 * np.tan(np.radians(self.camera.fov) / 2)
                                                      / self.app.WIN_SIZE[1])

    def render(self):
        self.update()
        if self.cull_face:
            self.app.ctx.enable(mgl.CULL_FACE)
        self.vao.render(mode=self.mode, instances=self.num_instances)
        if self.strip_vao is not None:
            self.strip_vao.render(mode=mgl.LINES, instances=self.num_instances)
        if self.cull_face:
            self.app.ctx.disable(mgl.CULL_FACE)

    def on_init(self):
        # texture
        self.texture = self.app.mesh.texture.textures[self.tex_id]
        self.texture.use(location=0)
        for program in self.programs():
            program['u_texture_0'] = 0
            # mvp
            program['m_proj'].write(self.camera.m_proj)
            program['m_view'].write(self.camera.m_view)


class ArrayInstancedModel(ForwardInstancedModel):
    """ForwardInstancedModel fed with a ready (N, 16) float32 array of column-major model matrices
    (relative to `origin`) instead of ObjInstance objects. set_instances() replaces them all; the
    instance buffer grows by doubling and is otherwise reused."""
    def __init__(self, app, vbo_name, tex_id, capacity=64, detail=0.0):
        self.capacity = capacity
        super().__init__(app, vbo_name, tex_id, [], origin=(0, 0, 0), detail=detail)

    def update_buffers(self):
        if self.m_model_vbo is not None:
            self.m_model_vbo.release()
        self.m_model_vbo = self.app.ctx.buffer(reserve=self.capacity * 64)
        self.num_instances = 0

    def set_instances(self, m_models, origin):
        data = np.ascontiguousarray(m_models, dtype='f4').reshape(-1, 16)
        self.origin = to_dvec3(origin)
        if len(data) > self.capacity:
            while self.capacity < len(data):
                self.capacity *= 2
            self.update_vao()
            self.app.framelog.note(f"grow {self.vbo_name} {self.capacity}")
        if len(data):
            self.m_model_vbo.write(data)
        self.num_instances = len(data)

    def render(self):
        if self.num_instances:
            super().render()


class TurntableModel(ForwardInstancedModel):
    """ForwardInstancedModel whose instances spin about their vertical axis at `speed` deg/s."""
    def __init__(self, app, vbo_name, tex_id, objects, speed=20.0, origin=None):
        self.speed = speed
        self.base_yaw = [obj.rot.y for obj in objects]
        super().__init__(app, vbo_name, tex_id, objects, origin)

    def update(self):
        angle = glm.radians(self.app.time * self.speed)
        for obj, yaw in zip(self.objects, self.base_yaw):
            obj.rot.y = yaw + angle
        m_model_list = []
        for obj in self.objects:
            m_model_list += sum(obj.get_model_matrix(self.origin).to_list(), [])
        self.m_model_vbo.write(np.array(m_model_list, dtype='float32'))
        super().update()


class Moon(BaseModel):
    def __init__(self, app, celestial_axis, vao_name='moon', tex_id=0, pos=(0, 0, 0), rot=(0, 0, 0), scale=(1, 1, 1)):
        super().__init__(app, vao_name, tex_id, pos, rot, scale)
        self.celestial_axis = celestial_axis
        self.on_init()
        self.set_celestial_axis()

    def set_celestial_axis(self):
        self.program['u_celestial_axis'].write(self.celestial_axis)

    def update(self):
        self.m_model = self.get_model_matrix()
 
        self.texture.use(location=0)
        self.program['camPos'].write(glm.vec3(self.camera.position))
        self.program['light.position'].write(self.app.light.position)
        self.program['m_view'].write(self.camera.m_view)
        self.program['m_model'].write(self.m_model)
        self.program['m_proj'].write(self.camera.m_proj)

    def on_init(self):
        # texture
        self.texture = self.app.mesh.texture.textures[self.tex_id]
        self.program['u_texture_0'] = 0
        self.texture.use(location=0)
        # mvp
        self.program['m_proj'].write(self.camera.m_proj)
        self.program['m_view'].write(self.camera.m_view)
        self.program['m_model'].write(self.m_model)
        # light
        self.program['light.position'].write(self.app.light.position)


class AdvancedSkyBox(BaseModel):
    """Procedural sky gradient and sun disc over the whole screen (no texture: the stars are lofigl/stars.py)."""
    def __init__(self, app, vao_name='advanced_skybox', tex_id=None,
                 pos=(0, 0, 0), rot=(0, 0, 0), scale=(1, 1, 1)):
        super().__init__(app, vao_name, tex_id, pos, rot, scale)
        self.on_init()

    def update(self):
        m_view = glm.mat4(glm.mat3(self.camera.m_view))
        self.program['m_invProjView'].write(glm.inverse(self.camera.m_proj * m_view))
        self.program['light_pos'].write(self.app.light.normalized_pos)

    def on_init(self):
        self.program['light_pos'].write(self.app.light.normalized_pos)
