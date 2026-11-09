"""Night sky: the stars of a star catalogue drawn as point sprites at infinity. STAR_CATALOG picks the catalogue:
the Yale Bright Star Catalogue (data/bsc5.dat, ~9100 stars to magnitude 6.5) or HYG v4.4 (data/hyg_v44.csv,
~120000 stars, complete to about magnitude 8, fainter ones show when zooming in).

Each star is a unit direction fed to the projection with w = 0, so it lands at depth 0 (reversed depth: infinitely
far), behind everything. The scene draws the stars right after the sky, so whatever comes later paints over them.

Brightness: a star is shown against the sky behind it. The vertex shader evaluates the sky model there
(shaders/sky_common.glsl) and derives the limiting magnitude from the sky's brightness, so at dusk the brightest
stars come out first, and stars on the twilight side and near the horizon (atmospheric extinction) last.
Displayed intensity is flux^STAR_GAMMA (flux = 10^(-0.4 mag)), 1 at STAR_RANGE magnitudes above the limit; stars
brighter than that keep full intensity and grow instead (Gaussian width ~ sqrt(intensity)). Zooming in raises the
limit, like a telescope.

Positions: J2000 right ascension / declination, turned into the observer's sky (x east, y up, z south) at local
sidereal time 0 for the given latitude; set_sidereal_time() then turns the whole sky about the celestial pole.
Precession since 2000 (~0.35 deg) and proper motions are ignored."""
import csv
import math
import os
import glm
import moderngl as mgl
import numpy as np
from . import ROOT

STAR_CATALOGS = {
    "BSC5": os.path.join(ROOT, "data", "bsc5.dat"),         # Yale Bright Star Catalogue: ~9100 stars to mag 6.5
    "HYG": os.path.join(ROOT, "data", "hyg_v44.csv"),       # HYG v4.4 (Hipparcos-Yale-Gliese): ~120000 stars
}
STAR_CATALOG = "BSC5"        # which of STAR_CATALOGS the night sky uses

STAR_MAG_NIGHT = 7.5        # limiting magnitude of the darkest sky at the default FoV
STAR_RANGE = 4.0            # magnitudes from the limit up to full intensity (brighter stars grow instead)
STAR_GAMMA = 0.4            # displayed intensity = flux^gamma: < 1 compresses the range so faint stars show
STAR_FADE_MAG = 0.75        # stars fade in over this many magnitudes below the limit
STAR_SKY_CONTRAST = 6.5     # magnitudes the limit drops per tenfold sky brightness (the sky model's range is small)
STAR_EXTINCTION = 0.25      # magnitudes lost per air mass (1 at the zenith, ~40 at the horizon)
STAR_SIGMA = 1.1            # Gaussian width of a star in pixels (scaled to the window)
STAR_MAX_SIGMA = 4.0        # ... at most, for the brightest stars
STAR_ZOOM_MAG = 0.75        # limiting magnitude gained per halving of the FoV
STAR_ZOOM_SIZE = 0.15       # star size grows with (default FoV / FoV) to this power
STAR_SATURATION = 1.0       # 1 = black-body colours from the catalogue's B-V, 0 = all STAR_TINT
STAR_TINT = (0.85, 0.92, 1.0)   # colour of a star at saturation 0: white with a touch of blue
STAR_DEFAULT_BV = 0.4       # B-V for the few stars without one (yellowish white)
REFERENCE_FOV = 50.0        # degrees: the FoV the magnitudes above are for (the camera's default)


def load_bsc5(path=STAR_CATALOGS["BSC5"]):
    """(ra, dec in radians, V magnitude, B-V) arrays of the catalogue entries that have a position and magnitude.
    Fixed-width text, columns per data/bsc5_readme.txt (J2000 position: bytes 76-90, Vmag 103-107, B-V 110-114)."""
    ra, dec, mag, bv = [], [], [], []
    with open(path, encoding="latin-1") as f:
        for line in f:
            if len(line) < 114 or not line[75:77].strip() or not line[102:107].strip():
                continue                        # novae / galaxies removed from the catalogue: no position
            h, m, s = int(line[75:77]), int(line[77:79]), float(line[79:83])
            d, am, asec = int(line[84:86]), int(line[86:88]), int(line[88:90])
            sign = -1.0 if line[83] == "-" else 1.0
            ra.append(math.radians(15.0 * (h + m / 60 + s / 3600)))
            dec.append(sign * math.radians(d + am / 60 + asec / 3600))
            mag.append(float(line[102:107]))
            bv.append(float(line[109:114]) if line[109:114].strip() else STAR_DEFAULT_BV)
    return np.array(ra), np.array(dec), np.array(mag), np.array(bv)


def load_hyg(path=STAR_CATALOGS["HYG"]):
    """(ra, dec in radians, V magnitude, B-V) arrays of the HYG catalogue, the Sun left out.
    CSV with a header row; uses the columns rarad, decrad (J2000 epoch and equinox), mag and ci (B-V, may be empty)."""
    ra, dec, mag, bv = [], [], [], []
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        i_id, i_ra, i_dec, i_mag, i_ci = (header.index(c) for c in ("id", "rarad", "decrad", "mag", "ci"))
        for row in reader:
            if row[i_id] == "0" or not row[i_ra] or not row[i_dec] or not row[i_mag]:
                continue                        # id 0 is the Sun
            ra.append(float(row[i_ra]))
            dec.append(float(row[i_dec]))
            mag.append(float(row[i_mag]))
            bv.append(float(row[i_ci]) if row[i_ci] else STAR_DEFAULT_BV)
    return np.array(ra), np.array(dec), np.array(mag), np.array(bv)


STAR_LOADERS = {"BSC5": load_bsc5, "HYG": load_hyg}


def load_catalog(name=STAR_CATALOG):
    """(ra, dec, mag, B-V) of the catalogue `name` (a key of STAR_CATALOGS)."""
    return STAR_LOADERS[name](STAR_CATALOGS[name])


def bv_to_rgb(bv):
    """Star colours (N, 3), 0..1 with the largest component 1: B-V -> temperature (Ballesteros 2012) -> black-body
    colour (Tanner Helland's fit of the CIE black-body curve in sRGB, 1000..40000 K)."""
    bv = np.clip(bv, -0.4, 2.0)
    t = 4600.0 * (1.0 / (0.92 * bv + 1.7) + 1.0 / (0.92 * bv + 0.62)) / 100.0
    r = np.where(t <= 66, 255.0, 329.698727446 * np.power(np.maximum(t - 60, 1e-6), -0.1332047592))
    g = np.where(t <= 66, 99.4708025861 * np.log(t) - 161.1195681661,
                 288.1221695283 * np.power(np.maximum(t - 60, 1e-6), -0.0755148492))
    b = np.where(t >= 66, 255.0, np.where(t <= 19, 0.0, 138.5177312231 * np.log(np.maximum(t - 10, 1e-6)) - 305.0447927307))
    rgb = np.clip(np.stack([r, g, b], axis=1), 0.0, 255.0) / 255.0
    return rgb / rgb.max(axis=1, keepdims=True)


def sky_directions(ra, dec, latitude_deg):
    """Unit directions (N, 3) in the observer's sky at local sidereal time 0: x east, y up, z south (north = -z),
    from equatorial coordinates (hour angle H = LST - RA = -RA)."""
    lat = math.radians(latitude_deg)
    h = -ra
    up = math.sin(lat) * np.sin(dec) + math.cos(lat) * np.cos(dec) * np.cos(h)
    north = math.cos(lat) * np.sin(dec) - math.sin(lat) * np.cos(dec) * np.cos(h)
    east = -np.cos(dec) * np.sin(h)
    return np.stack([east, up, -north], axis=1)


class StarField:
    """The star sprites of one observer latitude. `pole` is the axis the sky turns about (Sky.sky_pole, the south
    celestial pole: a positive turn about it is the westward daily motion). render() sets blending and point size
    but leaves the depth test to the scene."""
    def __init__(self, app, latitude, pole, catalog=STAR_CATALOG):
        self.app = app
        self.ctx = app.ctx
        self.program = app.mesh.vao.program.programs['star']
        self.pole = glm.vec3(pole)
        ra, dec, mag, bv = load_catalog(catalog)
        data = np.hstack([sky_directions(ra, dec, latitude), mag[:, None], bv_to_rgb(bv)]).astype('f4')
        self.count = len(data)
        self.vbo = self.ctx.buffer(data)
        self.vao = self.ctx.vertex_array(self.program, [(self.vbo, '3f 1f 3f', 'in_dir', 'in_mag', 'in_color')],
                                         skip_errors=True)
        self.m_sky = glm.mat3()

    def set_sidereal_time(self, angle):
        """Turn the sky to local sidereal time `angle` (radians)."""
        self.m_sky = glm.mat3(glm.rotate(glm.mat4(), angle, self.pole))

    def render(self):
        cam = self.app.camera
        zoom = REFERENCE_FOV / cam.fov
        scale = self.app.WIN_SIZE[1] / 1440.0
        p = self.program
        p['m_proj'].write(cam.m_proj)
        p['m_view'].write(cam.m_view)
        p['m_sky'].write(self.m_sky)
        p['u_sun'].write(self.app.light.normalized_pos)
        p['u_mag_night'].value = STAR_MAG_NIGHT + STAR_ZOOM_MAG * math.log2(zoom)
        p['u_range'].value = STAR_RANGE
        p['u_gamma'].value = STAR_GAMMA
        p['u_fade_mag'].value = STAR_FADE_MAG
        p['u_contrast'].value = STAR_SKY_CONTRAST
        p['u_extinction'].value = STAR_EXTINCTION
        p['u_sigma'].value = STAR_SIGMA * scale * zoom ** STAR_ZOOM_SIZE
        p['u_max_sigma'].value = STAR_MAX_SIGMA * scale
        p['u_saturation'].value = STAR_SATURATION
        p['u_tint'].value = STAR_TINT
        ctx = self.ctx
        ctx.enable(mgl.PROGRAM_POINT_SIZE | mgl.BLEND)
        ctx.blend_func = mgl.ONE, mgl.ONE           # starlight adds to the sky
        self.vao.render(mode=mgl.POINTS, vertices=self.count)
        ctx.disable(mgl.PROGRAM_POINT_SIZE | mgl.BLEND)

    def destroy(self):
        self.vao.release()
        self.vbo.release()
