#version 330 core

// Per-vertex: unit quad corner, (0,0) = top-left, (1,1) = bottom-right
layout (location = 0) in vec2 in_corner;
// Per-instance: glyph rectangle in screen pixels (x, y, w, h), y grows downwards
layout (location = 1) in vec4 in_rect;
// Per-instance: atlas UVs of the glyph's top-left (xy) and bottom-right (zw) corners
layout (location = 2) in vec4 in_uv_rect;

out vec2 uv;

uniform vec2 u_resolution;

void main() {
    uv = mix(in_uv_rect.xy, in_uv_rect.zw, in_corner);
    vec2 pixel = in_rect.xy + in_corner * in_rect.zw;
    vec2 ndc = vec2(pixel.x / u_resolution.x * 2.0 - 1.0,
                    1.0 - pixel.y / u_resolution.y * 2.0);
    gl_Position = vec4(ndc, 0.0, 1.0);
}
