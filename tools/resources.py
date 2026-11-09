"""The files the viewer reads that are made from the game installation, rebuilt when missing or older than
the game files they come from:
  unpacked/<B>/EGAME.EXE      EGAME.EXE without EXEPACK (unexepack.py): world.py reads its data
  export/3d/<LIB>.json        the model libraries (model3d.py) + ega.mtl, the palette
  export/world/<B>/<XX>.json  theater scenery placements (world.py): live mode places the game's
                              scenery swaps (destroyed and mission special objects) with them
<B> is the EGAME build of the game folder, ru or en (gamever.py): their EGAME and CE scenery differ.

tasks() lists what has to be done as (message, function) pairs; view.py runs them behind a progress screen
(GraphicsEngine `preload`). From the console: resources.py [--force] rebuilds what is out of date (or everything).
"""
import logging
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from paths import PROJECT_DIR, GAME_DIR, game_file, unpacked_egame_path, world_export_dir   # noqa: E402

logger = logging.getLogger(__name__)

MODELS_DIR = os.path.join(PROJECT_DIR, 'export', '3d')
LIBRARIES = ('STFLT', 'PHOTO', 'LB', 'PG', 'NC', 'CE')     # the .3D3 files


def out_of_date(target, sources):
    """True if `target` is missing or older than one of the game files `sources` (a missing source:
    game_file() raises when the target has to be made, and doesn't matter when it exists)."""
    if not os.path.exists(target):
        return True
    made = os.path.getmtime(target)
    return any(os.path.getmtime(p) > made for p in (os.path.join(GAME_DIR, s) for s in sources)
               if os.path.exists(p))


def unpack_egame():
    import unexepack
    img, relocs, regs, h = unexepack.unpack(open(game_file('EGAME.EXE'), 'rb').read())
    path = unpacked_egame_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    write_atomic(path, unexepack.build_mz(img, relocs, regs, h))


def export_library(lib):
    import model3d
    model3d.export(game_file(lib + '.3D3'), MODELS_DIR, objs=False)


def export_palette():
    import model3d
    os.makedirs(MODELS_DIR, exist_ok=True)
    model3d.write_mtl(os.path.join(MODELS_DIR, 'ega.mtl'))


def export_world(code):
    import world
    os.makedirs(world_export_dir(), exist_ok=True)
    path = os.path.join(world_export_dir(), code + '.json')
    world.export_json(world.World(code), path + '.tmp')
    os.replace(path + '.tmp', path)     # an interrupted export leaves no half-written file behind


def write_atomic(path, data):
    with open(path + '.tmp', 'wb') as f:
        f.write(data)
    os.replace(path + '.tmp', path)


def tasks(force=False):
    """[(message, function)] that bring the files up to date, in order (the EGAME unpack comes first:
    the world exports read it)."""
    from world import THEATERS
    todo = []
    if force or out_of_date(unpacked_egame_path(), ['EGAME.EXE']):
        todo.append(("Unpacking EGAME.EXE", unpack_egame))
    libraries = [lib for lib in LIBRARIES
                 if force or out_of_date(os.path.join(MODELS_DIR, lib + '.json'), [lib + '.3D3'])]
    for lib in libraries:
        todo.append((f"Exporting models {lib}", lambda lib=lib: export_library(lib)))
    if not libraries and not os.path.exists(os.path.join(MODELS_DIR, 'ega.mtl')):  # a library export writes it
        todo.append(("Exporting palette", export_palette))
    for code, (name, wld, _) in THEATERS.items():
        sources = [code + '.3DT', code + '.3DG', code + '.3D3', wld, 'EGAME.EXE']
        if force or out_of_date(os.path.join(world_export_dir(), code + '.json'), sources):
            todo.append((f"Exporting world {code} ({name})", lambda code=code: export_world(code)))
    return todo


if __name__ == '__main__':
    todo = tasks(force='--force' in sys.argv)
    for message, run in todo:
        print(message)
        run()
    print(f"{len(todo)} files rebuilt" if todo else "Everything is up to date")
