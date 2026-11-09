#version 330 core
out vec4 fragColor;

in vec4 clipCoords;

uniform mat4 m_invProjView;
uniform vec3 light_pos;

#include "sky_common.glsl"

/* Gradient noise from Jorge Jimenez's presentation: */
/* http://www.iryoku.com/next-generation-post-processing-in-call-of-duty-advanced-warfare */

float gradientNoise(vec2 uv)
{
    return fract(52.9829189 * fract(dot(uv, vec2(0.06711056, 0.00583715))));
}

void main() {
    vec4 worldCoords = m_invProjView * clipCoords;
    vec3 dir = normalize(worldCoords.xyz);   // w >= 0 (0 at infinity), so no divide
    vec3 sun = normalize(light_pos);
    vec3 skyColor;
    float skyAlpha, sunDot, sunsetFactor;
    sky_model(dir, sun, skyColor, skyAlpha, sunDot, sunsetFactor);
    float sunAlpha = clamp(sunDot * 700.0 - 698.0, 0.0, 1.0);
    vec3 sunColor = mix(sunColorB, sunColorU, sunsetFactor);
    // the stars (lofigl/stars.py) are drawn over this, against the sky's brightness
    vec3 finalColor = mix(skyColor, sunColor, sunAlpha) * skyAlpha
                      + ((1.0 / 255.0) * gradientNoise(gl_FragCoord.xy) - (0.5 / 255.0));
    fragColor = vec4(finalColor, 1.0);
}
