#version 330 core
layout (location = 0) in vec3 in_position;
layout (location = 1) in vec4 in_color;

out vec4 color;
out vec3 fragPos;       // camera-relative position (camera at origin)
out float fade;         // distance fade, 1 = opaque

uniform mat4 m_proj;
uniform mat4 m_view;    // rotation only
uniform vec3 u_offset;  // batch origin - camera position, computed in double on the CPU
uniform float u_size;           // pixels
uniform float u_world_size;     // world units, 0 = off
uniform float u_pixel_scale;    // pixels per world unit at distance 1: m_proj[1][1] * viewport height / 2
uniform int u_band_count;       // > 0: colour by distance instead of in_color
uniform float u_band_dist[4];
uniform vec4 u_band_color[4];
uniform float u_fade_far;       // horizontal distance where points are gone; 0 = no fade
uniform float u_fade_band;      // ... they fade over this much distance before that


void main() {
    fragPos = in_position + u_offset;
    gl_Position = m_proj * m_view * vec4(fragPos, 1.0);
    gl_PointSize = max(u_size, u_world_size * u_pixel_scale / max(gl_Position.w, 1e-6));
    color = in_color;
    fade = u_fade_far > 0.0 ? clamp((u_fade_far - length(fragPos.xz)) / u_fade_band, 0.0, 1.0) : 1.0;
    if (u_band_count > 0) {
        float dist = length(fragPos);
        color = u_band_color[u_band_count - 1];
        for (int i = 0; i < u_band_count - 1; i++) {
            if (dist < u_band_dist[i]) { color = u_band_color[i]; break; }
        }
    }
}
