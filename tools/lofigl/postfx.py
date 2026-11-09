import glm
import moderngl as mgl
import numpy as np
from OpenGL import GL
from . import ROOT
import os
import settings

POST_SHADER_DIR = os.path.join(ROOT, "shaders", "post")

# Depth of field. Blur radius of something infinitely far, in pixels at 1440 lines (scaled to the
# window); the circle of confusion of a pixel is that times |1 - focus / distance|.
DOF_APERTURES = (("natural", 6.0), ("strong", 16.0), ("miniature", 40.0))
# about graphics.dof_quality taps at the largest blur (fewer when the blur is small); 256: ~3 ms at 1440p on an RTX 4090
DOF_FOCUS_TIME = 0.25       # seconds for the autofocus to cover ~63% of a change
DOF_MAX_FOCUS = 1_000_000   # focus distance limit, when the centre of the screen is sky


class PostFX:
    """Post-processing of the resolved scene: the MSAA scene framebuffer is resolved (colour and depth)
    into textures, then full-screen passes draw to the window. The only pass now is depth of field
    with autofocus at the screen centre; `apply()` is called with the window as the target.
    A scene that paints flat ground at height 0 without depth writes sets `flat_ground = True`, and the
    passes fill in that ground's depth from the view ray."""
    def __init__(self, app):
        self.app = app
        self.ctx = ctx = app.ctx
        w, h = self.size = app.WIN_SIZE
        self.color = ctx.texture((w, h), 4)
        self.color.repeat_x = self.color.repeat_y = False
        # 32-bit float depth texture, as ModernGL can't make one: raw GL, wrapped for sampling
        depth_id = int(GL.glGenTextures(1))
        GL.glBindTexture(GL.GL_TEXTURE_2D, depth_id)
        GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_DEPTH_COMPONENT32F, w, h, 0, GL.GL_DEPTH_COMPONENT, GL.GL_FLOAT, None)
        for param, value in ((GL.GL_TEXTURE_MIN_FILTER, GL.GL_NEAREST), (GL.GL_TEXTURE_MAG_FILTER, GL.GL_NEAREST),
                             (GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_EDGE), (GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE)):
            GL.glTexParameteri(GL.GL_TEXTURE_2D, param, value)
        GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
        self.depth = ctx.external_texture(depth_id, (w, h), 1, 0, 'f4')
        self.resolve_id = int(GL.glGenFramebuffers(1))
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self.resolve_id)
        GL.glFramebufferTexture2D(GL.GL_FRAMEBUFFER, GL.GL_COLOR_ATTACHMENT0, GL.GL_TEXTURE_2D, self.color.glo, 0)
        GL.glFramebufferTexture2D(GL.GL_FRAMEBUFFER, GL.GL_DEPTH_ATTACHMENT, GL.GL_TEXTURE_2D, depth_id, 0)
        status = GL.glCheckFramebufferStatus(GL.GL_FRAMEBUFFER)
        if status != GL.GL_FRAMEBUFFER_COMPLETE:
            raise RuntimeError(f"Post-processing framebuffer incomplete: {status:#x}")
        ctx.screen.use()

        # autofocus: two 1x1 float textures, each frame reads one and writes the other
        self.focus = [ctx.texture((1, 1), 1, data=np.array([1000.0], dtype='f4').tobytes(), dtype='f4') for _ in range(2)]
        for t in self.focus:
            t.filter = (mgl.NEAREST, mgl.NEAREST)
        self.focus_fbos = [ctx.framebuffer(color_attachments=[t]) for t in self.focus]

        self.dof = self._program("dof")
        self.dof_focus = self._program("dof_focus")
        self.dof_vao = ctx.vertex_array(self.dof, [])
        self.focus_vao = ctx.vertex_array(self.dof_focus, [])
        names = [name for name, _ in DOF_APERTURES]
        aperture = settings.get('graphics.dof_aperture')
        self.aperture = names.index(aperture) if aperture in names else 1     # index into DOF_APERTURES

    def _program(self, name):
        with open(os.path.join(POST_SHADER_DIR, "fullscreen.vert")) as f:
            vert = f.read()
        with open(os.path.join(POST_SHADER_DIR, name + ".frag")) as f:
            frag = f.read()
        return self.ctx.program(vertex_shader=vert, fragment_shader=frag)

    @property
    def aperture_name(self):
        return DOF_APERTURES[self.aperture][0]

    def next_aperture(self):
        self.aperture = (self.aperture + 1) % len(DOF_APERTURES)

    def apply(self, scene_fbo_id, target):
        """Resolve the MSAA scene framebuffer and draw the post-processed image to `target`."""
        w, h = self.size
        GL.glBindFramebuffer(GL.GL_READ_FRAMEBUFFER, scene_fbo_id)
        GL.glBindFramebuffer(GL.GL_DRAW_FRAMEBUFFER, self.resolve_id)
        GL.glBlitFramebuffer(0, 0, w, h, 0, 0, w, h, GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT, GL.GL_NEAREST)

        ctx = self.ctx
        cam = self.app.camera
        ground = dict(u_inv_proj_view=glm.inverse(cam.m_proj * cam.m_view), u_cam_forward=cam.forward,
                      u_cam_height=glm.vec1(cam.position.y if getattr(self.app.scene, 'flat_ground', False) else 0.0))
        ctx.disable(mgl.DEPTH_TEST)        # (the triangle is counter-clockwise, culling can stay as it is)
        near = self.app.camera.near

        # autofocus into the other 1x1 texture
        prev, cur = self.focus[0], self.focus_fbos[1]
        dt = self.app.delta_time * 0.001
        self.depth.use(location=0)
        prev.use(location=1)
        p = self.dof_focus
        p['u_depth'], p['u_prev'] = 0, 1
        p['u_near'].value = near
        p['u_blend'].value = float(1.0 - np.exp(-dt / DOF_FOCUS_TIME))
        p['u_max_dist'].value = DOF_MAX_FOCUS
        self._write(p, ground)
        cur.use()
        self.focus_vao.render(mode=mgl.TRIANGLES, vertices=3)
        self.focus.reverse()
        self.focus_fbos.reverse()

        # depth of field to the target
        max_blur = DOF_APERTURES[self.aperture][1] * h / 1440
        self.color.use(location=0)
        self.depth.use(location=1)
        self.focus[0].use(location=2)
        p = self.dof
        p['u_color'], p['u_depth'], p['u_focus'] = 0, 1, 2
        p['u_near'].value = near
        p['u_max_blur'].value = max_blur
        p['u_rad_scale'].value = max(0.5, max_blur * max_blur / (2 * settings.get('graphics.dof_quality')))
        p['u_pixel'].value = (1.0 / w, 1.0 / h)
        self._write(p, ground)
        target.use()
        self.dof_vao.render(mode=mgl.TRIANGLES, vertices=3)
        ctx.enable(mgl.DEPTH_TEST)

    @staticmethod
    def _write(program, uniforms):
        for name, value in uniforms.items():
            program[name].write(value)

    def destroy(self):
        for obj in (self.dof_vao, self.focus_vao, self.dof, self.dof_focus, self.color, *self.focus_fbos, *self.focus):
            obj.release()
        GL.glDeleteFramebuffers(1, [self.resolve_id])
        GL.glDeleteTextures(1, [self.depth.glo])
