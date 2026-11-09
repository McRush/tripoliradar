import os
import pygame as pg
import moderngl as mgl
from . import ROOT
from .vbo import F19_EXPORT_DIR, F19_PALETTE_SIZE, load_f19_palette
import settings


class TextureManager:
    """Automatically loads all image textures found in ``base_dir`` into a
    dictionary keyed by the file path **relative** to ``base_dir`` (without
    extension).

    • Any directory whose name *contains* the substring ``"nearest"`` (case
     ‑insensitive) — for example ``nearest``, ``billboards_nearest`` or
      ``NEAREST_UI`` — is loaded with **nearest‑neighbour** filtering.
    • All other ``.png`` and ``.jpg`` images are loaded as standard 2‑D textures
      with trilinear filtering + mip‑maps + 32× AF.
    • ``f19_palette`` is a 16×1 nearest-filtered texture of the EGA colours from
      the exported ``ega.mtl``, saturation scaled by the graphics.saturation setting (f19_colors());
      F-19 model uvs point at its texels. The scene may rewrite them (night colours);
      ``f19_palette_day`` is a copy that keeps the day colours (explosions, gun rounds).
    """

    def __init__(self, app, base_dir: str = os.path.join(ROOT, "textures")):
        self.app = app
        self.ctx = app.ctx
        self.base_dir = base_dir
        self.textures: dict[str, mgl.Texture] = {}

        # Scan disk once during construction --------------------------------------
        self._scan_directories()

    def load_f19_palette(self):
        """The F-19 palette textures (after the export is there, see GraphicsEngine)."""
        self._load_f19_palette(os.path.join(F19_EXPORT_DIR, "ega.mtl"))

    # ---------------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------------
    def __getitem__(self, key: str):
        return self.textures[key]

    def destroy(self):
        for tex in self.textures.values():
            tex.release()

    def reset_f19_palette(self):
        """Day colours at the current saturation in both palettes (a new scene; a scene may have changed them)."""
        if "f19_palette" not in self.textures:
            return
        data = bytes(round(c * 255) for rgb in f19_colors() for c in (*rgb, 1.0))
        for key in ("f19_palette", "f19_palette_day"):
            self.textures[key].write(data)

    # ------------------------------------------------------------------
    # Scanning & loading helpers
    # ------------------------------------------------------------------
    def _scan_directories(self):
        for dirpath, _dirnames, filenames in os.walk(self.base_dir):
            for fname in filenames:
                if not fname.lower().endswith((".png", ".jpg")):
                    continue

                full_path = os.path.join(dirpath, fname)
                key = self._texture_key(dirpath, fname)
                nearest = self._is_nearest_dir(dirpath)

                loader = self._get_texture_nearest if nearest else self._get_texture
                self.textures[key] = loader(full_path)

    # ------------------------------------------------------------------
    # Path & type helpers
    # ------------------------------------------------------------------
    def _is_nearest_dir(self, dirpath: str) -> bool:
        """Returns *True* if any path component contains 'nearest' (case‑insensitive)."""
        parts = os.path.relpath(dirpath, self.base_dir).split(os.sep)
        return any("nearest" in part.lower() for part in parts)

    def _texture_key(self, dirpath: str, filename: str) -> str:
        """Builds a stable dictionary key relative to *base_dir* (sans extension)."""
        rel_dir = os.path.relpath(dirpath, self.base_dir)
        stem = os.path.splitext(filename)[0]
        if rel_dir == ".":
            return stem
        return f"{rel_dir.replace(os.sep, '/')}/{stem}"

    # ------------------------------------------------------------------
    # OpenGL constructors
    # ------------------------------------------------------------------
    def _load_f19_palette(self, mtl_path: str):
        if not os.path.isfile(mtl_path):
            return
        data = bytes(round(c * 255) for rgb in f19_colors() for c in (*rgb, 1.0))
        for key in ("f19_palette", "f19_palette_day"):
            tex = self.ctx.texture((F19_PALETTE_SIZE, 1), components=4, data=data)
            tex.filter = (mgl.NEAREST, mgl.NEAREST)
            self.textures[key] = tex

    # ------------------------------ 2‑D textures ------------------------------
    def _get_texture(self, path: str):
        surf = pg.image.load(path).convert_alpha()
        surf = pg.transform.flip(surf, flip_x=False, flip_y=True)
        tex = self.ctx.texture(surf.get_size(), components=4,
                               data=pg.image.tostring(surf, "RGBA"))
        tex.filter = (mgl.LINEAR_MIPMAP_LINEAR, mgl.LINEAR)
        tex.build_mipmaps()
        tex.anisotropy = 32.0
        return tex

    def _get_texture_nearest(self, path: str):
        surf = pg.image.load(path).convert_alpha()
        surf = pg.transform.flip(surf, flip_x=False, flip_y=True)
        tex = self.ctx.texture(surf.get_size(), components=4,
                               data=pg.image.tostring(surf, "RGBA"))
        tex.filter = (mgl.NEAREST, mgl.NEAREST)
        return tex


def f19_colors():
    """The 16 EGA colours (0..1 RGB) at the graphics.saturation setting (1: original, 0: greys keeping the luma)."""
    amount = settings.get('graphics.saturation')
    return [saturate(rgb, amount) for rgb in load_f19_palette(os.path.join(F19_EXPORT_DIR, "ega.mtl"))]


def saturate(rgb, amount):
    """Blend a colour (0..1 components) with its Rec. 709 luma grey: amount 1 keeps it, 0 makes it grey."""
    y = 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]
    return tuple(min(1.0, max(0.0, y + (c - y) * amount)) for c in rgb)
