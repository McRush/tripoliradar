#version 330 core

// GL_LINES segment -> camera-facing strip u_line_width world units wide. It is drawn over the same lines drawn
// plainly (1 pixel), so segments narrower than u_min_px pixels at both ends are left to those and skipped.
// Runs between default_instanced_forward.vert (built with LINE_GEOMETRY) and default_instanced_forward.frag.

layout (lines) in;
layout (triangle_strip, max_vertices = 4) out;

in vec2 vs_uv_0[];
in vec3 vs_fragPos[];
in vec3 vs_normal[];
in float vs_fade[];

out vec2 uv_0;
out vec3 fragPos;       // camera-relative position (camera at origin)
out vec3 normal;
out float fade;

uniform mat4 m_proj;
uniform mat4 m_view;        // rotation only
uniform float u_line_width; // world units, whatever the instance's scale
uniform float u_min_px;     // strips narrower than this many pixels are skipped
uniform float u_px_scale;   // size of one pixel at depth 1: 2 * tan(fov / 2) / viewport height


void main() {
    vec3 a = vs_fragPos[0], b = vs_fragPos[1];
    // nearest view depth of the segment (an end behind the camera counts as near)
    float depth = max(min(-(m_view * vec4(a, 1.0)).z, -(m_view * vec4(b, 1.0)).z), 1e-6);
    float width = u_line_width;
    if (width < u_min_px * u_px_scale * depth)
        return;
    // across the segment and across the view direction (the camera is at the origin)
    vec3 side = cross(b - a, a + b);
    float len = length(side);
    if (len < 1e-12)
        return;     // segment points at the camera
    side *= 0.5 * width / len;
    for (int i = 0; i < 2; i++) {
        for (int s = -1; s <= 1; s += 2) {
            uv_0 = vs_uv_0[i];
            normal = vs_normal[i];
            fade = vs_fade[i];
            fragPos = vs_fragPos[i] + side * float(s);
            gl_Position = m_proj * m_view * vec4(fragPos, 1.0);
            EmitVertex();
        }
    }
    EndPrimitive();
}
