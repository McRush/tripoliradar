# Licenses

Everything in this repository is covered by the MIT License in [`LICENSE`](LICENSE), copyright McRush,
except the third-party parts listed below. Their full license texts are in [`LICENSES/`](LICENSES).

## Third-party parts

| What | Where | Author | License | Text |
|---|---|---|---|---|
| 3D-Graphics-Engine: the code LoFiGL started from | `tools/lofigl/` (parts of the code) | StanislavPetrovV, <https://github.com/StanislavPetrovV/3D-Graphics-Engine> | MIT | [`LICENSES/MIT-StanislavPetrovV.txt`](LICENSES/MIT-StanislavPetrovV.txt) |
| Cozette bitmap font, and the atlas and metrics baked from it | `tools/lofigl/cozette_hidpi.bdf`, `tools/lofigl/cozette_hidpi.json`, `tools/lofigl/textures/billboards_nearest/cozette_hidpi_atlas.png` | Samhain & contributors, <https://github.com/the-moonwitch/Cozette> | MIT | [`LICENSES/MIT-Cozette.txt`](LICENSES/MIT-Cozette.txt) |
| Yale Bright Star Catalogue, 5th revised edition (BSC5) | `tools/lofigl/data/bsc5.dat`, `tools/lofigl/data/bsc5_readme.txt` | D. Hoffleit and W. H. Warren Jr., distributed by NASA's Astronomical Data Center; obtained from <http://tdc-www.harvard.edu/catalogs/bsc5.html> | Public domain (generally treated as a US government work) | — |
| Blue noise texture | `tools/lofigl/textures/LDR_RGBA_0.png` | Christoph Peters, <https://momentsingraphics.de/BlueNoise.html> | CC0 1.0 (public domain) | [`LICENSES/CC0-BlueNoise.txt`](LICENSES/CC0-BlueNoise.txt) |

Original assets by McRush, covered by [`LICENSE`](LICENSE): the moon texture (`tools/lofigl/textures/moon2.png`)
and the value noise texture (`tools/lofigl/textures/RGBA512.png`).

## Libraries in the Windows build

The repository doesn't contain the Python packages it uses ([`requirements.txt`](requirements.txt)): pip installs
them, each under its own license. The Windows build ([`TripoliRadar.spec`](TripoliRadar.spec)) packs copies of them,
of the Python runtime and of the PyInstaller bootloader. The build lists them with their versions, licenses and
license texts in `LICENSES/THIRD_PARTY_LIBRARIES.txt` next to the exe, written from the installed packages
(`tools/thirdparty.py`). These are pygame-ce, moderngl, glcontext, PyGLM, PyOpenGL, PyOpenGL-accelerate, numpy,
Python and PyInstaller.

License texts the packages don't carry themselves are kept here:

| What | License | Text |
|---|---|---|
| pygame-ce | LGPL 2.1 | [`LICENSES/pygame-ce/LGPL-2.1.txt`](LICENSES/pygame-ce/LGPL-2.1.txt) |
| The libraries in the pygame-ce wheel: SDL2, SDL2_image, SDL2_mixer (with Timidity), SDL2_ttf (with FreeType and HarfBuzz), FreeType, libjpeg-turbo, libpng, zlib, libtiff, libwebp, libogg, Opus, opusfile, WavPack, libxmp, PortMidi | zlib, BSD, MIT, FreeType, IJG, Artistic and others | [`LICENSES/pygame-ce/`](LICENSES/pygame-ce) |
| PyOpenGL | BSD | [`LICENSES/BSD-PyOpenGL.txt`](LICENSES/BSD-PyOpenGL.txt) |
| The software Python includes: OpenSSL, expat, libffi, zlib, libmpdec and others | Various | [`LICENSES/Python-incorporated-software.txt`](LICENSES/Python-incorporated-software.txt) |

The pygame-ce library texts come from the pygame-ce 2.5.8 source (`docs/licenses`) and from the SDL2_image 2.8.12,
SDL2_mixer 2.8.2 and SDL2_ttf 2.24.0 packages that its Windows wheel is built with. Refresh them when pygame-ce is
updated. The Python text is CPython 3.15.2's `Doc/license.rst`.

pygame-ce is used unmodified, as a separate library: in the build it is the files in `_internal/pygame/`, which can
be replaced with another build of it. Its source code is at
<https://github.com/pygame-community/pygame-ce/tree/2.5.8>.

The build leaves out PyOpenGL's GLUT and GLE DLLs, which the viewer doesn't use.

## F-19 Stealth Fighter

F-19 Stealth Fighter is © 1988 MicroProse. This project is not affiliated with or endorsed by its rights holders.
The repository contains no game files: the tools read them from your own copy of the game.
