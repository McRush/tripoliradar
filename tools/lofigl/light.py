import glm

class Light:
    """The sun. Nothing is lit by it; only the sky, the moon and the fog color use its position."""
    def __init__(self, position=(0, 35, -35)):
        self.init_pos = glm.vec3(position) * 4  # Initial position where (0, 0, 0) is pivoting point. Used for Sun movement calculation
        self.normalized_pos = glm.normalize(self.init_pos)
        self.position = glm.vec3(position) * 4 # Actual position in the world space

    def set_initial_position(self, position=(0, 35, -35)):
        self.init_pos = glm.vec3(position)
        self.position = glm.vec3(position)

    def update(self, cam_pos, m_rot):
        rotated_pos = m_rot * self.init_pos
        self.normalized_pos = glm.normalize(rotated_pos)
        self.position = rotated_pos + cam_pos
