#version 330 core

layout (location = 0) in vec2 in_texcoord_0;
layout (location = 1) in vec3 in_normal;
layout (location = 2) in vec4 in_position;

out vec2 uv_0;
out vec3 normal;
out vec3 fragPos;
out mat3 moonRotationMatrix;  // Pass rotation matrix to fragment shader

uniform mat4 m_proj;
uniform mat4 m_view;
uniform mat4 m_model;
uniform vec3 u_celestial_axis;


void main() {
    uv_0 = in_texcoord_0;
    // Extracting world coordinates of moon from model matrix
    vec3 moonPos = (m_model * vec4(0.0, 0.0, 0.0, 1.0)).xyz;

    //Removing translations from model matrix to keep moon in one place in the sky
    mat4 rot_matrix = m_view;

    rot_matrix[3][0] = 0.0;
    rot_matrix[3][1] = 0.0;
    rot_matrix[3][2] = 0.0;

    // Compute the direction to the origin (0, 0, 0) from the moon's position
    vec3 forward = normalize(-moonPos);  // Direction towards origin

    // OLD: Unstable at zenith
    //vec3 right = normalize(cross(vec3(0.0, 1.0, 0.0), forward));  // Perpendicular to world up

    // NEW: Unstable at Horizon North/South (where moon likely never goes)
    // Use sky's axis of rotation here (e.g., 0,0,1 or 1,0,0)
    //vec3 celestialPole = vec3(0.0, 0.0, 1.0); Replaced with uniform
    vec3 right = normalize(cross(u_celestial_axis, forward));
    vec3 up = cross(forward, right);  // Calculate the up vector

    // Create the LookAt matrix manually
    mat4 lookAtMatrix = mat4(vec4(right, 0.0), vec4(up, 0.0), vec4(forward, 0.0), vec4(moonPos, 1.0));

    // Extract the rotation part of the LookAt matrix as a mat3
    moonRotationMatrix = mat3(right, up, forward);  // Pass this to the fragment shader

    // Apply transformations
    fragPos = vec4(m_model * lookAtMatrix * in_position).xyz;
    //fragPos = moonPos;
    normal = mat3(transpose(inverse(m_model))) * normalize(in_normal);

    // Apply the lookAt transformation to orient the moon towards (0,0,0)
    vec4 pos = m_proj * rot_matrix * lookAtMatrix * in_position;
    gl_Position = vec4(pos.xy, 3e-10 * pos.w, pos.w);   // reversed depth: in front of the sky

}