#version 330 core
// Stars as point sprites at infinity (lofigl/stars.py): brightness against the sky behind each star.
layout (location = 0) in vec3 in_dir;       // unit direction at local sidereal time 0 (x east, y up, z south)
layout (location = 1) in float in_mag;      // visual magnitude
layout (location = 2) in vec3 in_color;     // black-body colour, largest component 1

out vec3 v_color;       // colour times peak intensity
out float v_size;       // point size, pixels
out float v_sigma;      // Gaussian width, pixels

uniform mat4 m_proj;
uniform mat4 m_view;            // rotation only
uniform mat3 m_sky;             // the sky's turn (sidereal time)
uniform vec3 u_sun;             // unit sun direction
uniform float u_mag_night;      // limiting magnitude of the darkest sky (zoom included)
uniform float u_range;          // magnitudes from the limit to full intensity
uniform float u_gamma;          // displayed intensity = flux^gamma
uniform float u_fade_mag;       // fade-in width below the limit, magnitudes
uniform float u_contrast;       // limit drop per tenfold sky brightness, magnitudes
uniform float u_extinction;     // magnitudes per air mass
uniform float u_sigma;          // Gaussian width of a star at full intensity or less, pixels
uniform float u_max_sigma;
uniform float u_saturation;     // 1 = black-body colour, 0 = u_tint
uniform vec3 u_tint;

#include "sky_common.glsl"

void hide() {
    gl_Position = vec4(2.0, 2.0, 2.0, 1.0);     // outside the clip volume: dropped
    gl_PointSize = 1.0;
    v_color = vec3(0.0);
    v_size = 1.0;
    v_sigma = 1.0;
}

void main() {
    vec3 dir = m_sky * in_dir;
    if (dir.y <= 0.0) {
        hide();
        return;
    }
    // atmosphere: air mass (Kasten-Young-like fit), and the sky's brightness here sets the limiting magnitude
    float airmass = 1.0 / (dir.y + 0.025 * exp(-11.0 * dir.y));
    float mag = in_mag + u_extinction * (airmass - 1.0);
    vec3 skyColor;
    float skyAlpha, sunDot, sunsetFactor;
    sky_model(dir, u_sun, skyColor, skyAlpha, sunDot, sunsetFactor);
    float limit = u_mag_night - u_contrast * log(skyAlpha / SKY_ALPHA_MIN) / log(10.0);

    float visible = smoothstep(0.0, 1.0, (limit - mag) / u_fade_mag);
    float intensity = pow(10.0, -0.4 * u_gamma * (mag - (limit - u_range))) * visible;
    if (intensity < 1.0 / 512.0) {
        hide();
        return;
    }
    // brighter than full intensity: the star grows instead, its light spread over a wider Gaussian
    float sigma = min(u_sigma * sqrt(max(intensity, 1.0)), u_max_sigma);
    v_sigma = sigma;
    v_size = ceil(6.0 * sigma) + 1.0;           // the sprite holds the Gaussian out to 3 sigma
    gl_PointSize = v_size;
    v_color = mix(u_tint, in_color, u_saturation) * min(intensity, 1.0);
    gl_Position = m_proj * m_view * vec4(dir, 0.0);     // w = 0: depth 0, infinitely far
}
