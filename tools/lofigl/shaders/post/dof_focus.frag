#version 330 core
// Autofocus: smoothed view distance at the screen centre, kept in a 1x1 texture (no CPU readback).
out float focus;

uniform sampler2D u_depth;      // reversed depth: distance = near / depth
uniform sampler2D u_prev;       // last frame's focus distance
uniform float u_near;
uniform float u_blend;          // weight of the new value this frame
uniform float u_max_dist;       // focus limit (sky at the centre)

// Scene depth (reversed: near / view distance) with the flat ground filled in: it is painted without
// depth writes (world_scene.py), so where the buffer holds 0 (infinity) the view ray is intersected with
// the ground plane y = 0, the only place flat ground is drawn.
uniform mat4 u_inv_proj_view;   // inverse of projection * view rotation (camera-relative world space)
uniform vec3 u_cam_forward;
uniform float u_cam_height;     // camera height above the ground plane

float sceneDepth(vec2 tc) {
    float depth = texture(u_depth, tc).r;
    if (depth > 0.0) return depth;
    vec4 p = u_inv_proj_view * vec4(tc * 2.0 - 1.0, 1.0, 1.0);   // point on the near plane
    vec3 dir = p.xyz / p.w;
    if (dir.y >= 0.0 || u_cam_height <= 0.0) return 0.0;
    float t = -u_cam_height / dir.y;
    return u_near / max(dot(dir * t, u_cam_forward), u_near);
}

float dist(vec2 uv) { return min(u_near / max(sceneDepth(uv), 1e-30), u_max_dist); }

void main() {
    // centre and four neighbours 1% of the screen away, averaged in 1/distance (as a lens focuses)
    float inv = 0.0;
    for (int i = 0; i < 5; i++) {
        vec2 o = i == 0 ? vec2(0.0) : 0.01 * vec2(i == 1 ? 1 : i == 2 ? -1 : 0, i == 3 ? 1 : i == 4 ? -1 : 0);
        inv += 1.0 / dist(vec2(0.5) + o);
    }
    float target = 5.0 / inv;
    float prev = texelFetch(u_prev, ivec2(0), 0).r;
    focus = 1.0 / mix(1.0 / prev, 1.0 / target, u_blend);
}
