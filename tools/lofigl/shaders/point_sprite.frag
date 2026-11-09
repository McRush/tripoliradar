#version 330 core

layout (location = 0) out vec4 fragColor;

in vec4 color;
in vec3 fragPos;    // camera-relative position
in float fade;      // distance fade -> alpha -> MSAA coverage

uniform bool u_round;
uniform float u_fog_density;  // fog = 1 - 2^(-density * distance^2), as in default_instanced_forward.frag
uniform vec3 sunDir;

const vec3 zenithColor = vec3(0.0, 0.45, 0.932);
const vec3 horizonColorU = vec3(0.4, 0.8, 0.932);
const vec3 horizonColorB = vec3(0.98, 0.48, 0.38); // Sunset color


void main() {
    if (fade <= 0.0 || (u_round && length(gl_PointCoord - 0.5) > 0.5)) discard;

    // fog, same as default_instanced_forward.frag
    float fog_dist = length(fragPos);
    vec3 texCubeCoord = normalize(fragPos);
    float norm_y = max(0, texCubeCoord.y);
    float sunsetFactor = clamp(sunDir.y * 3 - 0.2, 0.0, 1.0);
    float sunDot = dot(texCubeCoord, sunDir);
    vec3 horizonColor = mix(horizonColorB, horizonColorU, sunsetFactor);
    float skyAlpha = clamp(((sunDot + 1) * 0.25 + (1 - norm_y) + sunDir.y * 3.0), 0.0, 1.0);
    vec3 fogColor = mix(horizonColor, zenithColor, norm_y) * ((sunDot + 4) * 0.2 * skyAlpha);
    fragColor = vec4(mix(color.rgb, fogColor, (1.0 - exp2(-u_fog_density * fog_dist * fog_dist))), color.a * fade);
}
