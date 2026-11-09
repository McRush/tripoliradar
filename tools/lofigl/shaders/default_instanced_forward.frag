#version 330 core

layout (location = 0) out vec4 fragColor;

in vec2 uv_0;
in vec3 fragPos;    // camera-relative position
in vec3 normal;     // world-space, not normalized (zero for lines)
in float fade;      // distance fade -> alpha -> MSAA coverage (alpha-to-coverage, see engine.py)

uniform sampler2D u_texture_0;
uniform float u_fog_density;  // fog = 1 - 2^(-density * distance^2)
uniform vec3 sunDir;    // normalized direction to the sun, used only to tint the fog like the sky

// Surface detail: brightness noise from world position, octave i read from channel i of u_detail_noise.
// Set by the scene (world_scene.py); all zero (the default) turns it off.
uniform float u_detail;             // strength of this batch
uniform sampler2D u_detail_noise;   // tiling value noise, 4 independent channels
uniform vec3 u_detail_origin;       // camera position modulo the longest octave period (double on the CPU)
uniform vec4 u_detail_scale;        // per octave: 1 / (world units per noise tile)
uniform vec4 u_detail_weight;       // per octave weight, summing to 1
uniform float u_detail_color[16];   // max brightness change per EGA colour (palette index = uv_0.x * 16)
uniform float u_detail_sharpness;   // triplanar blend exponent
uniform sampler2D u_dither_noise;   // blue noise, sampled per screen pixel
uniform float u_dither;             // dither amplitude in 8-bit steps

const vec3 zenithColor = vec3(0.0, 0.45, 0.932);
const vec3 horizonColorU = vec3(0.4, 0.8, 0.932);
const vec3 horizonColorB = vec3(0.98, 0.48, 0.38); // Sunset color


// Octave noise in [-1, 1] (mostly within +-0.5), projected along the three axes and blended by the normal.
float detailNoise(vec3 p, vec3 n) {
    vec3 w = pow(abs(n), vec3(u_detail_sharpness));
    w /= w.x + w.y + w.z + 1e-6;
    float sum = 0.0;
    for (int i = 0; i < 4; i++) {
        vec3 q = p * u_detail_scale[i];
        float v = w.y * texture(u_detail_noise, q.xz)[i]
                + w.x * texture(u_detail_noise, q.zy)[i]
                + w.z * texture(u_detail_noise, q.xy)[i];
        sum += u_detail_weight[i] * (v * 2.0 - 1.0);
    }
    return sum;
}


void main() {
    vec4 colorRGBA = texture(u_texture_0, uv_0);
    //if (colorRGBA.a < 0.1 || fade <= 0.0) discard;
    vec3 color = colorRGBA.rgb;
    if (u_detail > 0.0) {
        float amp = u_detail * u_detail_color[int(uv_0.x * 16.0)];
        color *= 1.0 + amp * detailNoise(fragPos + u_detail_origin, normal);
    }

    float fog_dist = length(fragPos);

    vec3 texCubeCoord = normalize(fragPos);
    vec3 sunPosNorm = sunDir;
    float norm_y = max(0, texCubeCoord.y);
    float sunsetFactor = clamp(sunPosNorm.y * 3 - 0.2, 0.0, 1.0);
    float sunDot = dot(texCubeCoord, sunPosNorm);
    vec3 horizonColor = mix(horizonColorB, horizonColorU, sunsetFactor);
    float skyAlpha = clamp(((sunDot + 1) * 0.25 + (1 - norm_y) + sunPosNorm.y * 3.0), 0.0, 1.0);
    vec3 fogColor = mix(horizonColor, zenithColor, norm_y) * ((sunDot + 4) * 0.2 * skyAlpha);
    vec3 tex_col = mix(color, fogColor, (1.0 - exp2(-u_fog_density * fog_dist * fog_dist)));
    if (u_dither > 0.0)
        tex_col += (texelFetch(u_dither_noise, ivec2(gl_FragCoord.xy) & 1023, 0).r - 0.5) * (u_dither / 255.0);
    fragColor = vec4(tex_col, fade);
}
