// Sky colour model, shared by the sky (advanced_skybox.frag) and the stars (star.vert, which needs the sky's
// brightness behind each star). Included by ShaderProgram's #include.

const vec3 sunColorU = vec3(0.95, 0.95, 0.85);
const vec3 sunColorB = vec3(0.95, 0.7, 0.2);        // sunset colour
const vec3 zenithColor = vec3(0.0, 0.45, 0.932);
const vec3 horizonColorU = vec3(0.4, 0.8, 0.932);
const vec3 horizonColorB = vec3(0.98, 0.48, 0.38);  // sunset colour
const float SKY_ALPHA_MIN = 0.05;                   // the night sky keeps this share of the day colour

// dir, sun: unit vectors (y up). skyColor: the day colour in that direction; skyAlpha: how much of it shows
// (SKY_ALPHA_MIN at night .. 1 by day); sunDot: cosine of the angle to the sun; sunsetFactor: 0 sunset .. 1 day.
void sky_model(vec3 dir, vec3 sun, out vec3 skyColor, out float skyAlpha, out float sunDot, out float sunsetFactor) {
    float norm_y = max(0.0, dir.y);
    sunsetFactor = clamp(sun.y * 3.0 - 0.2, 0.0, 1.0);
    sunDot = dot(dir, sun);
    vec3 horizonColor = mix(horizonColorB, horizonColorU, sunsetFactor);
    skyColor = mix(horizonColor, zenithColor, norm_y) * ((sunDot + 4.0) * 0.2);
    skyAlpha = clamp(((sunDot + 1.0) * 0.25 + (1.0 - norm_y) + sun.y * 3.0), SKY_ALPHA_MIN, 1.0);
}
