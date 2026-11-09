#version 330 core

layout (location = 0) out vec4 fragColor;

in vec2 uv_0;
in vec3 normal;
in vec3 fragPos;
in mat3 moonRotationMatrix;  // Incoming rotation matrix for the moon's orientation

struct Light {
    vec3 position;
};

uniform Light light;
uniform sampler2D u_texture_0;
uniform vec3 camPos;

const vec3 sunColorU = vec3(0.95, 0.95, 0.85);
const vec3 sunColorB = vec3(0.95, 0.7, 0.2); // Sunset color
const vec3 zenithColor = vec3(0.0, 0.45, 0.932);
const vec3 horizonColorU = vec3(0.4, 0.8, 0.932);
const vec3 horizonColorB = vec3(0.98, 0.48, 0.38); // Sunset color


void main() {
    float gamma = 2.2;
    vec4 colorRGBA = texture(u_texture_0, uv_0);
    //if (colorRGBA.a < 0.1) discard;
    vec3 color = colorRGBA.rgb;
    color = pow(color, vec3(gamma));

    // Calculate normals based on UV coordinates
    vec3 dynamicNormal;
    dynamicNormal.x = uv_0.x * 2 - 1; // Map u (0, 1) -> (-1, 1)
    dynamicNormal.y = uv_0.y * 2 - 1; // Map v (0, 1) -> (-1, 1)

    // Calculate square length of normal and discard if it looks outwards
    float lensqr = dot(dynamicNormal.xy, dynamicNormal.xy);
    if(lensqr > 1.0)
        discard;

    // Calculate z value of the normal
    dynamicNormal.z = sqrt(1.0 - lensqr);

    // Ensure the normal is normalized
    dynamicNormal = normalize(dynamicNormal);

    // Apply the moon's rotation matrix to the calculated normal to align it with the world
    dynamicNormal = normalize(moonRotationMatrix * dynamicNormal);

    // Compute lighting (diffuse lighting based on sun position)

    vec3 lightDir = normalize((light.position - camPos) * 100.0 - fragPos);  // Direction to the sun
    float diffuse = max(dot(dynamicNormal, lightDir), 0.0);  // Lambertian lighting

    // Apply lighting to the moon's color
    color = color * diffuse;

    color = pow(color, 1 / vec3(gamma));

    vec3 texCubeCoord = normalize(fragPos);
    vec3 lightPosNorm = normalize(light.position - camPos);
    float norm_y = max(0, texCubeCoord.y);
    float sunsetFactor = clamp(lightPosNorm.y * 3 - 0.2, 0.0, 1.0);
    float sunDot = dot(texCubeCoord, lightPosNorm);
    vec3 horizonColor = mix(horizonColorB, horizonColorU, sunsetFactor);
    float skyAlpha = clamp(((sunDot + 1) * 0.25 + (1 - norm_y) + lightPosNorm.y * 3.0), 0.0, 1.0);
    vec3 fogColor = mix(horizonColor, zenithColor, norm_y) * ((sunDot + 4) * 0.2 * skyAlpha);
    vec3 tex_col = mix(fogColor, color, max(pow(norm_y, 0.3), 0.2));
    tex_col.r = color.r;
    fragColor = vec4(max(tex_col, fogColor), 1.0);
}










