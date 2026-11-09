import gc
import logging
import os
import pygame as pg
import moderngl as mgl
import sys
import threading
from OpenGL import GL
from time import perf_counter
from .camera import Camera
from .light import Light
from .mesh import Mesh
from .scene_renderer import SceneRenderer, STATS_FRAMES
from .postfx import PostFX
from .framelog import FrameLog
from .label import BitmapFont, Label
from . import ROOT
import settings

logger = logging.getLogger(__name__)

FOG_DENSITY = 3e-8              # fog = 1 - 2^(-density * distance^2); scenes may change it
PROGRESS_LINES = 30             # progress screen text: the window height / this many font lines, rounded

# Display (settings.py [display]). 'fullscreen' with the desktop size is SDL's desktop fullscreen (no video mode
# change); with any other size it switches the monitor's video mode. 'borderless' is an ordinary frameless window
# covering the monitor. 'windowed' is a framed window, centred. Keys: settings.py [keys_general].


class SceneError(Exception):
    """A scene can't open, for a reason the player should read (no traceback): the game isn't running,
    no replays... The engine then opens its home scene with the message in `error`."""


class GraphicsEngine:
    """Window, GL context and main loop. `scene_factory(app)` builds the scene once the engine's
    camera, light and meshes exist; the scene provides render() (its own draw order, sky included),
    labels, setup_light(), update() and handle_event(), optionally frame_log_fields() (lofigl/framelog.py),
    close() (release what it made, stop its threads) and on_escape() (True: it used the Esc key).
    switch_scene() replaces the scene at the start of the next frame. A factory with a `loading_message`
    attribute (a long replay is slow to load and to free) gets that message on screen while its scene opens
    and closes.

    `preload` is a list of (message, function) run before the F-19 models are loaded, each in a worker thread
    with its message on screen (run_tasks): view.py rebuilds the missing export there (resources.py).
    `home` is the factory of the scene Esc goes back to (the main menu); Esc in the home scene, or with no
    home, quits. A scene that fails to open is replaced by the home scene, with the message in `error`.
    `title` and `icon` (an image file, None: SDL's) are the window's.
    `framelog_dir` is where frame logs are saved; `framelog` starts one right away. `stats` prints the mean frame
    and scene times every STATS_FRAMES frames to the console (not to the log: they would flood it)."""
    def __init__(self, scene_factory, win_size=None, mode=None, framelog_dir=None, framelog=False, home=None,
                 stats=False, preload=(), title="LoFiGL", icon=None):
        self.stats = stats
        self.frame_time = 0
        self.counter = 0
        # Without DPI awareness Windows reports a scaled-down desktop and stretches the window (blurry)
        os.environ.setdefault('SDL_WINDOWS_DPI_AWARENESS', 'permonitorv2')
        # SDL minimises a fullscreen window when it loses focus (black frames in video capture)
        os.environ.setdefault('SDL_VIDEO_MINIMIZE_ON_FOCUS_LOSS', '0')
        os.environ.setdefault('SDL_VIDEO_CENTERED', '1')
        # NVIDIA Optimus laptops: render on the discrete GPU (read when the GL context is created; overrides the
        # NVIDIA Control Panel profile). Set SHIM_MCCOMPAT=0x800000000 beforehand to keep the integrated GPU.
        os.environ.setdefault('SHIM_MCCOMPAT', '0x800000001')
        pg.init()
        pg.display.set_caption(title)
        if icon and os.path.isfile(icon):
            pg.display.set_icon(pg.image.load(icon))    # before the window opens: some systems ignore it after
        mode = mode or settings.get('display.mode')
        desktops = pg.display.get_desktop_sizes()
        display = min(settings.get('display.monitor'), len(desktops) - 1)
        desktop = desktops[display]
        size = win_size
        if size is None and settings.get('display.size') != 'auto':
            size = tuple(int(v) for v in settings.get('display.size').split('x'))
        if size is None:
            size = (desktop[0] * 3 // 4, desktop[1] * 3 // 4) if mode == 'windowed' else desktop
        flags = pg.OPENGL | pg.DOUBLEBUF | {'fullscreen': pg.FULLSCREEN, 'borderless': pg.NOFRAME,
                                            'windowed': 0}[mode]

        # OpenGL context setup: 4.5 for glClipControl (reversed depth). The window framebuffer only
        # receives the resolved image, so it needs neither MSAA nor depth.
        pg.display.gl_set_attribute(pg.GL_CONTEXT_MAJOR_VERSION, 4)
        pg.display.gl_set_attribute(pg.GL_CONTEXT_MINOR_VERSION, 5)
        pg.display.gl_set_attribute(pg.GL_CONTEXT_PROFILE_MASK, pg.GL_CONTEXT_PROFILE_CORE)
        pg.display.gl_set_attribute(pg.GL_DEPTH_SIZE, 0)
        # create OpenGL context
        pg.display.set_mode(size, flags=flags, display=display, vsync=settings.get('display.vsync'))
        self.WIN_SIZE = pg.display.get_window_size()    # what we actually got
        logger.info(f"Display: {mode} {self.WIN_SIZE[0]}x{self.WIN_SIZE[1]} on monitor {display} "
                    f"(desktop {desktop[0]}x{desktop[1]})")

        pg.event.set_grab(True)
        pg.mouse.set_visible(False)

        self.ctx = mgl.create_context()
        logger.info(f"GPU: {self.ctx.info['GL_RENDERER']}, OpenGL {self.ctx.info['GL_VERSION']}")
        self.ctx.multisample = True
        self.ctx.enable(flags=mgl.DEPTH_TEST | mgl.CULL_FACE)
        self.ctx.gc_mode = 'auto'
        self.init_scene_framebuffer()

        # timing
        self.clock = pg.time.Clock()
        self.time = 0
        self.delta_time = 0
        self.fog_density = FOG_DENSITY
        self.show_labels = settings.get('graphics.hud')     # the labels key toggles, for clean screenshots
        self.framelog = FrameLog(framelog_dir or os.path.join(os.getcwd(), 'framelogs'),
                                 pg.display.get_current_refresh_rate())

        # scene components
        self.light = Light()
        self.camera = Camera(self)
        self.mesh   = Mesh(self)
        self.postfx = PostFX(self)
        self.postfx_on = settings.get('graphics.dof')
        self.scene_renderer = SceneRenderer(self)
        self.run_tasks(preload)
        self.mesh.load_f19()
        self.home = home
        self.scene = None
        self.scene_factory = None   # what made the scene: `home` tells the home scene
        self.next_scene = None      # factory for switch_scene()
        self.error = None           # why the last scene failed to open, for the home scene to show
        self.open_scene(scene_factory)
        if framelog:
            self.framelog.start()

    # --- progress screen -----------------------------------------------------------------------------

    def run_tasks(self, tasks):
        """Run (message, function) pairs one after another in a worker thread, showing the message meanwhile;
        the window keeps responding and closing it quits. A failure stays on screen until a key is pressed,
        then raises SceneError."""
        if not tasks:
            return
        lines = self._text_lines(4)
        for i, (message, function) in enumerate(tasks):
            logger.info(message)
            failure = []
            worker = threading.Thread(target=self._run_task, args=(function, failure), daemon=True)
            worker.start()
            start = perf_counter()
            while worker.is_alive():
                self._quit_on_close()
                dots = int((perf_counter() - start) * 3) % 4
                self._show(lines, ["Preparing the F-19 data (only once)", "",
                                   f"{i + 1}/{len(tasks)}  {message}{'.' * dots}{' ' * (3 - dots)}"])
                self.clock.tick(30)
            if failure:
                e = failure[0]
                logger.error(f"{message} failed: {e}", exc_info=e)
                for label in lines:
                    label.color = (1.0, 0.45, 0.4, 1.0)
                self._show(lines, [f"{message} failed:", str(e) or type(e).__name__, "", "Press any key to quit"])
                while not any(event.type == pg.KEYDOWN for event in pg.event.get()):
                    self._quit_on_close()
                    self.clock.tick(30)
                raise SceneError(f"{message} failed: {e}") from e
        for label in lines:
            label.release()

    def show_message(self, text):
        """One frame of `text` centred on a blank screen, left there while the caller does something slow."""
        lines = self._text_lines(1)
        self._show(lines, [text])
        lines[0].release()

    def _text_lines(self, n):
        """n empty labels in the progress screen font, sized to the window."""
        font = BitmapFont(self, os.path.join(ROOT, "cozette_hidpi.json"), "billboards_nearest/cozette_hidpi_atlas")
        scale = max(1, round(self.WIN_SIZE[1] / (PROGRESS_LINES * font.line_height)))
        return [Label(self, font, "", scale=scale) for _ in range(n)]

    @staticmethod
    def _run_task(function, failure):
        try:
            function()
        except Exception as e:
            failure.append(e)

    def _quit_on_close(self):
        if pg.event.get(pg.QUIT):
            pg.quit()
            sys.exit()

    def _show(self, labels, texts):
        """A frame of text lines centred on a blank screen."""
        font, scale = labels[0].font, labels[0].scale
        line = font.line_height * scale
        top = (self.WIN_SIZE[1] - line * len(texts)) // 2
        for k, (label, text) in enumerate(zip(labels, texts)):
            width = sum(font.get_glyph(c).advance for c in text) * scale
            label.set_text(text)
            label.set_pos(((self.WIN_SIZE[0] - width) // 2, top + k * line))
        self.ctx.screen.use()
        self.ctx.clear(color=(0.08, 0.16, 0.18, 1.0))
        self.ctx.disable(mgl.DEPTH_TEST | mgl.CULL_FACE)     # glyph quads face away (screen y grows down)
        self.ctx.enable(mgl.BLEND)
        self.ctx.blend_func = mgl.SRC_ALPHA, mgl.ONE_MINUS_SRC_ALPHA
        for label in labels:
            label.render()
        self.ctx.disable(mgl.BLEND)
        self.ctx.enable(mgl.DEPTH_TEST | mgl.CULL_FACE)
        pg.display.flip()

    # --- scenes ------------------------------------------------------------------------------------

    def switch_scene(self, factory):
        """Open the scene `factory(app)` makes at the start of the next frame (the current one finishes)."""
        self.next_scene = factory

    def go_home(self):
        if self.home is not None:
            self.switch_scene(self.home)

    def open_scene(self, factory):
        """Close the scene, reset the camera and the light and build the new one. If it fails and there is a
        home scene, open that one instead, with the message in `error`."""
        message = getattr(factory, 'loading_message', None) or self._closing_message()
        if message:
            self.show_message(message)
        self.close_scene()
        self.reset_state()
        try:
            self.scene = factory(self)
        except Exception as e:
            if self.home is None or factory is self.home:
                raise
            self.error = str(e) or type(e).__name__
            logger.error(f"Can't open the scene: {self.error}", exc_info=not isinstance(e, SceneError))
            self.close_scene()          # frees what the failed scene made before it raised
            self.reset_state()
            factory = self.home
            self.scene = factory(self)
        self.scene_factory = factory
        self.scene.setup_light()
        self.framelog.note(f"scene {type(self.scene).__name__}")
        pg.mouse.get_rel()              # drop the mouse motion made while loading
        self.clock.tick()               # and the loading time: the first frame doesn't jump
        self.delta_time = 0

    def _closing_message(self):
        """The loading message of the open scene's factory: freeing a big scene takes time too."""
        return getattr(self.scene_factory, 'loading_message', None) if self.scene is not None else None

    def close_scene(self):
        scene, self.scene = self.scene, None
        close = getattr(scene, 'close', None)
        if close:
            close()
        del scene
        gc.collect()                    # GL objects nobody holds are released now (gc_mode 'auto')

    def reset_state(self):
        """What a scene may have changed in the engine: the camera, the light, the fog and the palette."""
        self.camera.reset()
        self.mesh.texture.reset_f19_palette()
        self.light = Light()
        self.fog_density = FOG_DENSITY

    def quit(self):
        self.framelog.stop()
        message = self._closing_message()
        if message:
            self.show_message(message)
        self.close_scene()
        self.mesh.destroy()
        self.postfx.destroy()
        self.scene_renderer.destroy()
        pg.quit()
        sys.exit()

    def init_scene_framebuffer(self):
        """Multisampled scene framebuffer with a 32-bit float depth buffer and reversed depth:
        clip z in [0, 1] (glClipControl), near plane at 1, infinity at 0, test GEQUAL, cleared to 0.
        Float precision is densest near 0, which cancels the 1/distance falloff of perspective depth,
        so depth resolution stays ~1e-7 of the distance everywhere. ModernGL can't make a float depth
        buffer, so the renderbuffers and framebuffer are created with raw GL and wrapped."""
        w, h = self.WIN_SIZE
        self.msaa_samples = min(settings.get('display.msaa'), int(GL.glGetIntegerv(GL.GL_MAX_SAMPLES)))
        self.scene_rbos = GL.glGenRenderbuffers(2)
        attachments = ((GL.GL_COLOR_ATTACHMENT0, GL.GL_RGBA8), (GL.GL_DEPTH_ATTACHMENT, GL.GL_DEPTH_COMPONENT32F))
        fbo = GL.glGenFramebuffers(1)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, fbo)
        for rbo, (attachment, fmt) in zip(self.scene_rbos, attachments):
            GL.glBindRenderbuffer(GL.GL_RENDERBUFFER, rbo)
            GL.glRenderbufferStorageMultisample(GL.GL_RENDERBUFFER, self.msaa_samples, fmt, w, h)
            GL.glFramebufferRenderbuffer(GL.GL_FRAMEBUFFER, attachment, GL.GL_RENDERBUFFER, rbo)
        GL.glBindRenderbuffer(GL.GL_RENDERBUFFER, 0)
        status = GL.glCheckFramebufferStatus(GL.GL_FRAMEBUFFER)
        if status != GL.GL_FRAMEBUFFER_COMPLETE:
            raise RuntimeError(f"Scene framebuffer incomplete: {status:#x}")
        self.scene_fbo_id = int(fbo)
        self.scene_fbo = self.ctx.detect_framebuffer(self.scene_fbo_id)
        self.ctx.screen.use()

        GL.glClipControl(GL.GL_LOWER_LEFT, GL.GL_ZERO_TO_ONE)
        self.ctx.depth_func = '>='
        # Fragment alpha < 1 covers that share of the MSAA samples: order-independent fading with depth
        # writes intact. Opaque shaders must write alpha 1. No effect on the window (no MSAA there).
        GL.glEnable(GL.GL_SAMPLE_ALPHA_TO_COVERAGE)

    def check_events(self):
        for event in pg.event.get():
            if self.framelog.enabled and event.type != pg.MOUSEMOTION:
                key = f" {pg.key.name(event.key)}" if event.type in (pg.KEYDOWN, pg.KEYUP) else ''
                self.framelog.note(f"event {pg.event.event_name(event.type)}{key}")
            if event.type == pg.QUIT:
                self.quit()
            if event.type == pg.KEYDOWN and getattr(self.scene, 'capturing', None):    # a key being bound
                if event.key == pg.K_ESCAPE:
                    self.scene.on_escape()
                else:
                    self.scene.handle_event(event)
                continue
            if event.type == pg.KEYDOWN and event.key == settings.key('keys_general.labels'):
                self.show_labels = not self.show_labels
                continue
            if event.type == pg.KEYDOWN and event.key == settings.key('keys_general.postfx'):
                self.postfx_on = not self.postfx_on
            elif event.type == pg.KEYDOWN and event.key == settings.key('keys_general.aperture'):
                self.postfx.next_aperture()
            if event.type == pg.KEYDOWN and event.key == pg.K_ESCAPE:
                on_escape = getattr(self.scene, 'on_escape', None)
                if on_escape and on_escape():
                    continue
                if self.home is None or self.scene_factory is self.home:
                    self.quit()
                self.go_home()
                continue
            self.scene.handle_event(event)

    def get_time(self):
        self.time = pg.time.get_ticks() * 0.001

    def render(self):
        log = self.framelog
        log.gpu_begin()
        # 1) Forward pass: the scene draws its objects and the sky into the multisampled scene framebuffer
        self.scene_fbo.use()
        self.ctx.clear(color=(0.08, 0.16, 0.18, 0.0), depth=0.0)    # reversed depth: 0 is infinitely far
        self.scene_renderer.render_forward()
        log.lap('draw')

        # 2) Resolve MSAA to the window (through post-processing when on), then labels on top of everything
        if self.postfx_on:
            self.postfx.apply(self.scene_fbo_id, self.ctx.screen)
        else:
            self.ctx.copy_framebuffer(self.ctx.screen, self.scene_fbo)
        self.ctx.screen.use()
        if self.show_labels:
            self.scene_renderer.render_labels()
        log.gpu_end()
        log.lap('resolve')

        # Swap buffers
        pg.display.flip()
        log.lap('flip')
        log.presented()

    def run(self):
        log = self.framelog
        while True:
            if self.next_scene is not None:
                factory, self.next_scene = self.next_scene, None
                self.open_scene(factory)
            start_time = perf_counter()
            log.begin()
            self.get_time()
            self.check_events()
            log.lap('events')
            self.camera.update()
            log.lap('camera')
            self.scene.update()
            log.lap('update')
            self.render()
            if self.stats:
                self.frame_time += perf_counter() - start_time
                self.counter += 1
                if self.counter == STATS_FRAMES:
                    print("Frame:", self.frame_time / self.counter, "t:", self.time)
                    print("Camera :", self.camera.position, self.camera.forward)
                    self.frame_time = 0
                    self.counter = 0
            self.delta_time = self.clock.tick(settings.get('display.max_fps'))
            log.lap('tick')
            log.end(self.scene)

