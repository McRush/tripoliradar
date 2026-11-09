import math
import glm
import pygame as pg
import settings

NEAR = 1.0
SPEED = 0.03
DEFAULT_FOV = 50        # the reference FoV (scenes scale view distances to it); the start FoV is a setting
ROLL_LEVEL_TIME = 1.5   # s for the free camera's roll to ease (in and out) back to level


def to_dvec3(v):
    """Exact double-precision vec3. Note: glm.dvec3((x, y, z)) given a tuple goes through
    float32 and loses precision at large coordinates; glm.dvec3(x, y, z) does not."""
    if isinstance(v, glm.dvec3):
        return glm.dvec3(v)
    return glm.dvec3(*v)


class Camera:
    def __init__(self, app, position=(0, 0, 4), yaw=0, pitch=0, fov=None):
        self.app = app
        self.reset(position, yaw, pitch, fov)

    def reset(self, position=(0, 0, 4), yaw=0, pitch=0, fov=None):
        """Back to a free camera with the default settings (a new scene). The object stays the same:
        models keep a reference to it."""
        self.aspect_ratio = self.app.WIN_SIZE[0] / self.app.WIN_SIZE[1]
        # double precision: the world is ~2^20 units wide, float32 would lose ~0.1 unit
        self.position = to_dvec3(position)
        self.up = glm.vec3(0, 1, 0)
        self.right = glm.vec3(1, 0, 0)
        self.forward = glm.vec3(0, 0, -1)
        self.yaw = yaw
        self.pitch = pitch
        self.fov = fov if fov is not None else settings.get('camera.fov')
        self.near = NEAR         # the far plane is at infinity
        self.speed = SPEED      # units per ms
        self.locked = False     # placed by set_view() each frame instead of the mouse and keys
        # Reference frame (origin dvec3, rotation mat3) or None: while attached, the mouse and keys work on
        # `local` (position, forward, up, right in the frame) and the world pose follows the frame (set_frame).
        self.frame = None
        self.local = None
        self.roll = 0.0         # deg about forward, applied to the view only; eases to 0 in free flight
        self.roll_start, self.roll_age = 0.0, 0.0
        # view matrix
        self.m_view = self.get_view_matrix()
        # projection matrix
        self.m_proj = self.get_projection_matrix()

    def click(self):
        if pg.mouse.get_pressed()[1]:
            self.fov = settings.get('camera.fov')
            self.m_proj = self.get_projection_matrix()

    def rotate(self):
        rel_x, rel_y = pg.mouse.get_rel()
        step = settings.get('camera.sensitivity') * (self.fov / DEFAULT_FOV)
        self.yaw += rel_x * step
        self.pitch += rel_y * step * (1 if settings.get('camera.invert_y') else -1)
        self.pitch = max(-89, min(89, self.pitch))

    def update_camera_vectors(self):
        yaw, pitch = glm.radians(self.yaw), glm.radians(self.pitch)

        self.forward.x = glm.cos(yaw) * glm.cos(pitch)
        self.forward.y = glm.sin(pitch)
        self.forward.z = glm.sin(yaw) * glm.cos(pitch)

        self.forward = glm.normalize(self.forward)
        self.right = glm.normalize(glm.cross(self.forward, glm.vec3(0, 1, 0)))
        self.up = glm.normalize(glm.cross(self.right, self.forward))

    def update(self):
        if self.locked:
            pg.mouse.get_rel()          # drop the mouse motion, or it jumps the view on unlocking
        else:
            if self.frame:
                self.position, self.forward, self.up, self.right = self.local
            self.move()
            self.rotate()
            self.update_camera_vectors()
            if self.frame:
                self.local = (self.position, glm.vec3(self.forward), glm.vec3(self.up), glm.vec3(self.right))
                self.apply_frame()
        if self.roll and not self.locked and not self.frame:
            self.roll_age += self.app.delta_time * 0.001
            f = min(1.0, self.roll_age / ROLL_LEVEL_TIME)
            self.roll = self.roll_start * (1.0 - f * f * (3.0 - 2.0 * f))     # smoothstep
        self.zoom()
        self.click()
        self.m_view = self.get_view_matrix()

    def view_up(self):
        """Up of the view: `up` turned by `roll` about forward."""
        if not self.roll:
            return self.up
        return glm.angleAxis(glm.radians(self.roll), self.forward) * self.up

    def set_view(self, position, forward, up):
        """Place the camera directly, roll included. Yaw and pitch follow, so free flight continues from
        here (without the roll) once the camera is unlocked."""
        self.position = to_dvec3(position)
        self.forward = glm.normalize(glm.vec3(*forward))
        self.right = glm.normalize(glm.cross(self.forward, glm.vec3(*up)))
        self.up = glm.normalize(glm.cross(self.right, self.forward))
        self.set_angles(self.forward)
        self.m_view = self.get_view_matrix()

    def set_angles(self, forward):
        self.pitch = max(-89.0, min(89.0, glm.degrees(glm.asin(max(-1.0, min(1.0, forward.y))))))
        self.yaw = glm.degrees(glm.atan(forward.z, forward.x))

    def attach(self, origin, rotation):
        """Make a frame the reference, keeping the current world pose. Yaw and pitch become relative to it."""
        self.up = self.view_up()                # a roll still easing out becomes part of the pose
        self.right = glm.normalize(glm.cross(self.forward, self.up))
        self.roll = 0.0
        self.frame = (to_dvec3(origin), glm.mat3(rotation))
        origin, rot = self.frame
        inv = glm.transpose(rot)
        self.local = (glm.dmat3(inv) * (self.position - origin), inv * self.forward, inv * self.up, inv * self.right)
        self.set_angles(self.local[1])

    def detach(self):
        """Back to world axes, keeping the current world pose: its roll goes to `roll` and eases out."""
        if self.frame:
            self.frame = self.local = None
            up = self.view_up()
            right = glm.cross(self.forward, glm.vec3(0, 1, 0))
            if glm.length(right) > 1e-4:        # looking straight up or down: no level to ease to
                level = glm.cross(glm.normalize(right), self.forward)
                self.roll = glm.degrees(math.atan2(glm.dot(glm.cross(level, up), self.forward), glm.dot(level, up)))
                self.roll_start, self.roll_age = self.roll, 0.0
                up = level
            self.set_view(self.position, self.forward, up)

    def set_frame(self, origin, rotation):
        """Move the reference frame; the camera keeps its place in it."""
        self.frame = (to_dvec3(origin), glm.mat3(rotation))
        self.apply_frame()

    def apply_frame(self):
        origin, rot = self.frame
        position, forward, up, right = self.local
        self.position = origin + glm.dmat3(rot) * position
        self.forward, self.up, self.right = rot * forward, rot * up, rot * right
        self.m_view = self.get_view_matrix()

    def move(self):
        velocity = self.speed * self.app.delta_time
        keys = pg.key.get_pressed()
        k = settings.key
        if keys[k('keys_camera.fast')]:
            velocity *= settings.get('camera.fast')
        if keys[k('keys_camera.forward')]:
            self.position += glm.dvec3(self.forward) * velocity
        elif keys[k('keys_camera.back')]:
            self.position -= glm.dvec3(self.forward) * velocity
        if keys[k('keys_camera.left')]:
            self.position -= glm.dvec3(self.right) * velocity
        elif keys[k('keys_camera.right')]:
            self.position += glm.dvec3(self.right) * velocity
        if keys[k('keys_camera.up')]:
            self.position += glm.dvec3(self.up) * velocity
        elif keys[k('keys_camera.down')]:
            self.position -= glm.dvec3(self.up) * velocity

    def zoom(self):
        keys = pg.key.get_pressed()
        if keys[settings.key('keys_camera.zoom_in')] and self.fov > 1:
            self.fov -= self.fov * .02
            self.m_proj = self.get_projection_matrix()
        elif keys[settings.key('keys_camera.zoom_out')] and self.fov < 150:
            self.fov += self.fov * .02
            self.m_proj = self.get_projection_matrix()

    def get_view_matrix(self):
        # Rotation only: rendering is camera-relative, the camera sits at the origin.
        # Models add (origin - camera.position) themselves, see get_offset().
        return glm.lookAt(glm.vec3(0), self.forward, self.view_up())

    def get_offset(self, origin):
        """Camera-relative offset of a world-space origin, subtracted in double precision."""
        return glm.vec3(to_dvec3(origin) - self.position)

    def get_projection_matrix(self):
        """Reversed-depth perspective with an infinite far plane, for clip z in [0, 1] (see
        GraphicsEngine.init_scene_framebuffer): z_ndc = near / distance, 1 at the near plane, 0 at
        infinity."""
        f = 1.0 / glm.tan(glm.radians(self.fov) / 2)
        m = glm.mat4(0.0)
        m[0][0] = f / self.aspect_ratio
        m[1][1] = f
        m[2][3] = -1.0          # w_clip = -z_eye
        m[3][2] = self.near     # z_clip = near
        return m
