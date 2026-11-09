#version 330 core
// One triangle covering the screen, no vertex buffer: vertices from gl_VertexID (counter-clockwise).
out vec2 uv;

void main() {
    vec2 p = vec2((gl_VertexID & 1) * 4.0 - 1.0, (gl_VertexID >> 1) * 4.0 - 1.0);
    uv = p * 0.5 + 0.5;
    gl_Position = vec4(p, 0.0, 1.0);
}
