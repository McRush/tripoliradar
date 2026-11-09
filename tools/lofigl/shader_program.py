import logging
import re
from pathlib import Path
from . import ROOT

logger = logging.getLogger(__name__)

SHADER_DIR = Path(ROOT) / 'shaders'
INCLUDE = re.compile(r'^\s*#include\s+"([^"]+)"\s*$', re.MULTILINE)
VERSION = re.compile(r'^\s*#version[^\n]*\n')

# Programs put together from other files: name -> (vertex, geometry, fragment shader file stems, #defines for all
# stages). GL_LINES models: the forward shaders with a geometry stage that gives the lines a width.
COMPOSED = {
    'default_instanced_lines': ('default_instanced_forward', 'default_instanced_lines', 'default_instanced_forward',
                                ['LINE_GEOMETRY']),
}


def load_source(path, seen=()):
    """Shader source with each line `#include "file"` replaced by that file (relative to the including file).
    GLSL has no includes; this keeps shared code (e.g. sky_common.glsl) in one place."""
    path = Path(path)
    if path in seen:
        raise SystemExit(f"Shader include loop: {' -> '.join(str(p) for p in seen + (path,))}")
    with open(path) as file:
        source = file.read()
    return INCLUDE.sub(lambda m: load_source(path.parent / m.group(1), seen + (path,)), source)

class ShaderProgram:
    def __init__(self, ctx):
        self.ctx = ctx
        self.programs = {}
        self._load_all_shaders()
    
    def _load_all_shaders(self):
        """Automatically load all shader programs from the shaders directory"""
        shader_dir = SHADER_DIR
        
        if not shader_dir.exists():
            raise SystemExit(f"Shaders directory '{shader_dir}' not found")
        
        # Find all .vert files
        for vert_file in shader_dir.glob('*.vert'):
            shader_name = vert_file.stem  # filename without extension
            frag_file = shader_dir / f'{shader_name}.frag'
            
            # A broken or missing shader stops the program here, with the compiler's message, instead of
            # failing later on a missing program
            if not frag_file.exists():
                raise SystemExit(f"Shader '{shader_name}': {frag_file} is missing")
            try:
                self.programs[shader_name] = self.get_program(shader_name)
            except Exception as e:
                raise SystemExit(f"Shader '{shader_name}' failed to compile or link:\n{e}")
            logger.debug(f"Loaded shader: {shader_name}")
        for shader_name, (vert, geom, frag, defines) in COMPOSED.items():
            try:
                self.programs[shader_name] = self.get_program(vert, frag, geom, defines)
            except Exception as e:
                raise SystemExit(f"Shader '{shader_name}' failed to compile or link:\n{e}")
            logger.debug(f"Loaded shader: {shader_name}")

    def get_program(self, shader_program_name, frag=None, geom=None, defines=()):
        """Program of <name>.vert and .frag, or of the given fragment and geometry shader file stems, with
        `defines` inserted after each stage's #version line."""
        def source(stem, ext):
            text = load_source(SHADER_DIR / f'{stem}.{ext}')
            lines = ''.join(f'#define {d}\n' for d in defines)
            m = VERSION.match(text)
            return text[:m.end()] + lines + text[m.end():] if m else lines + text
        return self.ctx.program(vertex_shader=source(shader_program_name, 'vert'),
                                fragment_shader=source(frag or shader_program_name, 'frag'),
                                geometry_shader=source(geom, 'geom') if geom else None)

    def destroy(self):
        [program.release() for program in self.programs.values()]