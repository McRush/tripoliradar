from time import perf_counter
import moderngl as mgl

STATS_FRAMES = 300              # view.py --stats: frames per printed mean (console only, not the log)


class SceneRenderer:
    def __init__(self, app):
        self.app = app
        self.ctx = app.ctx
        self.mesh = app.mesh
        self.scene_time = 0
        self.counter = 0

    def render_forward(self):
        start_time = perf_counter()
        self.app.scene.render()
        if not self.app.stats:      # view.py --stats: the mean scene time to the console
            return
        self.scene_time += perf_counter() - start_time
        self.counter += 1
        if self.counter == STATS_FRAMES:
            print("Scene:", self.scene_time / self.counter)
            self.scene_time = 0
            self.counter = 0

    def render_labels(self):
        # Screen-space overlay: no depth test, alpha blended over the final image
        self.ctx.disable(mgl.DEPTH_TEST)
        self.ctx.enable(mgl.BLEND)
        self.ctx.blend_func = mgl.SRC_ALPHA, mgl.ONE_MINUS_SRC_ALPHA
        for label in self.app.scene.labels.values():
            label.render()
        self.ctx.disable(mgl.BLEND)
        self.ctx.enable(mgl.DEPTH_TEST)

    def destroy(self):
        pass
