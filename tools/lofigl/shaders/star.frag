#version 330 core
// A star: a Gaussian disc over the point sprite, added to the sky (blending ONE, ONE).
layout (location = 0) out vec4 fragColor;

in vec3 v_color;
in float v_size;
in float v_sigma;

void main() {
    vec2 d = (gl_PointCoord - 0.5) * v_size;    // pixels from the star's centre
    float g = exp(-dot(d, d) / (2.0 * v_sigma * v_sigma));
    if (g < 1.0 / 1024.0) discard;
    fragColor = vec4(v_color * g, 1.0);
}
