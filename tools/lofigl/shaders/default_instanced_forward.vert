#version 330 core

layout (location = 0) in vec2 in_texcoord_0;
layout (location = 1) in vec3 in_normal;
layout (location = 2) in vec3 in_position;
layout (location = 3) in mat4 m_model;

#ifdef LINE_GEOMETRY
// default_instanced_lines.geom runs between this and the fragment shader: it reads these as vs_* and
// writes the real names
#define uv_0 vs_uv_0
#define fragPos vs_fragPos
#define normal vs_normal
#define fade vs_fade
#endif

out vec2 uv_0;
out vec3 fragPos;       // camera-relative position (camera at origin)
out vec3 normal;        // world-space direction, not normalized (zero for lines)
out float fade;         // distance fade, 1 = opaque

uniform mat4 m_proj;
uniform mat4 m_view;    // rotation only
uniform vec3 u_offset;  // batch origin - camera position, computed in double on the CPU
uniform float u_fade_far;   // horizontal distance where the instance is gone; 0 = no fade
uniform float u_fade_band;  // ... it fades over this much distance before that


void main() {
    uv_0 = in_texcoord_0;
    normal = mat3(m_model) * in_normal;
    fragPos = (m_model * vec4(in_position, 1.0)).xyz + u_offset;
    // fade by the instance origin's horizontal distance, like the cells are picked
    vec3 origin = m_model[3].xyz + u_offset;
    fade = u_fade_far > 0.0 ? clamp((u_fade_far - length(origin.xz)) / u_fade_band, 0.0, 1.0) : 1.0;
    gl_Position = m_proj * m_view * vec4(fragPos, 1.0);
}
