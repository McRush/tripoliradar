#version 330 core
layout (location = 0) in vec3 in_position;

out vec4 clipCoords;

//uniform mat4 m_model;


void main() {
    gl_Position = vec4(in_position, 1.0);
    //gl_Position = m_model * vec4(in_position, 1.0);
    clipCoords = gl_Position;
}