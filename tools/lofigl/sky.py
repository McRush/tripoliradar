import math
import glm
import pygame as pg
import settings
from .model import AdvancedSkyBox, Moon
from .stars import StarField

OBLIQUITY = math.radians(23.44)     # tilt of the ecliptic
SUNRISE = 6 * 3600                  # local solar time (s) at sun_time = 0: the sun at hour angle -6 h
DAY = 86400

class Sky:
    def __init__(self, app, latitude=30.0, day_of_year=172, sun_time=30000.0, moon_params=None):
        self.app = app
        self.latitude = -latitude
        self.day_of_year = day_of_year
        self.moon_params = moon_params or {
            'speed_multiplier': 1.4, # Moon is slower than sky (lags behind), relative speed is ~0.96, but 1.4 visualizes better
            'inclination_deg': 5.0,
            'phase_offset': glm.pi() # Start Full Moon
        }
        
        # Shared params
        self.sun_is_moving = False
        self.sun_switch_timeout = 0
        self.sun_time = sun_time
        self.sky_velocity_base = 1.0 / 100.0
        # Game clock (set_clock): while the sky moves it shows clock + offset; stopped, it stays put and the
        # offset is taken again when it starts, so it never jumps. None = no clock: the sky runs on its own.
        self.clock = None
        self.offset = 0.0
        
        # --- 1. CALCULATE ROTATION AXIS (The Sky Pole) ---
        # The axis depends ONLY on latitude.
        # Assuming Y is UP, Z is NORTH (Standard Right-Handed).
        lat_rad = glm.radians(self.latitude)
        
        # The vector points to the North Celestial Pole
        self.sky_pole = glm.normalize(glm.vec3(
            0.0,                 # East/West component is 0
            glm.sin(lat_rad),    # Up component
            glm.cos(lat_rad)     # North component
        ))
        
        # The sun (and the stars, StarField) turn about this axis
        self.sun_rotation_axis = self.sky_pole
        
        # Moon axis: The moon orbits the earth, roughly following the same axis 
        # but with a slight inclination relative to the equator.
        moon_inc_rad = glm.radians(self.moon_params['inclination_deg'])
        self.moon_rotation_axis = glm.normalize(glm.vec3(
            glm.sin(moon_inc_rad), # Tilt slightly sideways
            glm.sin(lat_rad),
            glm.cos(lat_rad)
        ))

        # Speeds
        self.sun_speed = 2 * glm.pi() / 86400 
        self.moon_speed = self.sun_speed * self.moon_params['speed_multiplier']

        self.init_sun()
        self.init_moon()
        self.init_skybox()

    def get_orbit_position(self, axis, declination_rad, distance):
        """
        Helper to find a start position perpendicular to the axis, 
        shifted by declination.
        """
        # 1. Define a vector pointing East (Perpendicular to North-South axis)
        # In a Y-Up, Z-North world, X is East.
        east = glm.vec3(1, 0, 0)
        
        # 2. We need a vector that is 'declination' radians away from the Equator plane.
        # The position is: East_Vector * cos(dec) + Axis_Vector * sin(dec)
        # This places the object on the Meridian plane (if we started South) 
        # or the Prime Vertical (if we start East).
        
        # Let's start "Forward/South" relative to the tilted axis to match 'noon' logic better,
        # but starting East is safer for rotation math.
        
        # Calculate the vector component parallel to the equatorial plane
        radius_on_plane = distance * glm.cos(declination_rad)
        height_on_axis = distance * glm.sin(declination_rad)
        
        # Combine: Start at East * radius + Axis * height
        return (east * radius_on_plane) + (axis * height_on_axis)

    def init_sun(self):
        # Solar declination
        declination_deg = 23.45 * glm.sin(glm.radians(360 * (self.day_of_year - 81) / 365))
        declination_rad = glm.radians(declination_deg)
        
        dist = 140.0
        
        # Place the sun relative to the pole. sky_pole is the south celestial pole (the rotation axis for the
        # westward daily motion), so a northern declination is measured towards -sky_pole.
        self.sun_init_pos = self.get_orbit_position(-self.sun_rotation_axis, declination_rad, dist)
        # The sun's right ascension (from its ecliptic longitude, 0 at the March equinox, ~day 79): with the sun at
        # hour angle -6 h when sun_time = 0 (rising, at sun_init_pos), local sidereal time = hour angle + RA.
        longitude = 2 * glm.pi() * (self.day_of_year - 79) / 365.25
        self.sun_ra = math.atan2(math.cos(OBLIQUITY) * math.sin(longitude), math.cos(longitude))
        
        self.m_rot_sun = glm.rotate(glm.mat4(), self.sun_time * self.sun_speed, self.sun_rotation_axis)

    def init_moon(self):
        moon_dist = 20.0
        # Moon declination varies, but for simple sky we can treat phase_offset 
        # as a position along the orbit, and use 0 declination (equator) 
        # or a slight fixed offset.
        
        # Start moon on the equator of its own axis
        self.initial_moon_pos = self.get_orbit_position(self.moon_rotation_axis, 0.0, moon_dist)
        
        # Apply phase offset (Initial rotation around the axis)
        phase_rot = glm.rotate(glm.mat4(), self.moon_params['phase_offset'], self.moon_rotation_axis)
        self.initial_moon_pos = glm.vec3(phase_rot * glm.vec4(self.initial_moon_pos, 1.0))
        
        self.moon = Moon(self.app, self.moon_rotation_axis, pos=self.initial_moon_pos, scale=(2, 2, 2), rot=(0, 0, 0), tex_id='moon2')

    def init_skybox(self):
        self.skybox = AdvancedSkyBox(self.app)
        self.stars = StarField(self.app, latitude=-self.latitude, pole=self.sky_pole)
        self.stars.set_sidereal_time(self.sidereal_time())

    def destroy(self):
        """The moon and the sky gradient draw shared VAOs; only the stars have buffers of their own."""
        self.stars.destroy()

    def sidereal_time(self):
        """Local sidereal time (radians) at the current sun_time, consistent with the sun's place."""
        return -glm.half_pi() + self.sun_ra + self.sun_time * self.sun_speed
    
    def set_clock(self, seconds_of_day):
        """Follow a clock (local solar time, s). The first call starts the sky moving with it."""
        if self.clock is None:
            self.sun_is_moving = True
        self.clock = seconds_of_day

    def clock_sun_time(self):
        return self.clock - SUNRISE

    def local_time(self):
        """Local solar time (s, 0..86400) the sky shows."""
        return (self.sun_time + SUNRISE) % DAY

    def update(self, delta_time):
        keys = pg.key.get_pressed()
        sky_velocity = delta_time * self.sky_velocity_base
        following = self.clock is not None

        # Controls: 5 start / stop, 8 / 2 forward / back (the offset from the clock while following), 0 reset
        if keys[settings.key('keys_camera.sun_stop')] and (self.sun_switch_timeout == 0):
            self.sun_is_moving = not self.sun_is_moving
            self.sun_switch_timeout = 20
            if following and self.sun_is_moving:
                self.offset = self.sun_time - self.clock_sun_time()
        else:
            self.sun_switch_timeout = max(0, self.sun_switch_timeout - 1)

        step = 0.0
        if keys[settings.key('keys_camera.sun_forward')]:
            step = sky_velocity * 100
        elif keys[settings.key('keys_camera.sun_back')]:
            step = -sky_velocity * 100

        if following:
            if keys[settings.key('keys_camera.sun_reset')]:
                self.offset, self.sun_is_moving = 0.0, True
            if self.sun_is_moving:
                self.offset += step
                self.sun_time = self.clock_sun_time() + self.offset
            else:
                self.sun_time += step
        else:
            self.sun_time += step
            if self.sun_is_moving:
                self.sun_time += sky_velocity
        
        # Update Sun Rotation
        self.m_rot_sun = glm.rotate(glm.mat4(), self.sun_time * self.sun_speed, self.sun_rotation_axis)
        
        # Update Moon Rotation
        m_rot_moon = glm.rotate(glm.mat4(), self.sun_time * self.moon_speed, self.moon_rotation_axis)
        self.moon.pos = glm.vec3(m_rot_moon * glm.vec4(self.initial_moon_pos, 1.0))
        
        # Update the stars
        self.stars.set_sidereal_time(self.sidereal_time())
        self.skybox.update()
