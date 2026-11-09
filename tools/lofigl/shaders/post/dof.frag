#version 330 core
// Depth of field as a single-pass scatter-as-gather bokeh (after D. Gustafsson, "Bokeh depth of field
// in a single pass"): samples on a golden-angle spiral; a sample counts if its own blur disc reaches the
// pixel; a sample farther than the pixel is limited to twice the pixel's blur so a blurred background
// doesn't spill over a sharp foreground.
in vec2 uv;
out vec4 fragColor;

uniform sampler2D u_color;
uniform sampler2D u_depth;      // reversed depth: distance = near / depth
uniform sampler2D u_focus;      // 1x1 focus distance
uniform float u_near;
uniform float u_max_blur;       // blur radius in pixels of something infinitely far
uniform float u_rad_scale;      // spiral step, sets the sample count: about (max blur)^2 / (2 * scale)
uniform vec2 u_pixel;           // 1 / resolution

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

const float GOLDEN_ANGLE = 2.39996323;

float blurSize(float depth, float focus) {
    float d = u_near / max(depth, 1e-30);
    return min(abs(1.0 - focus / d), 1.0) * u_max_blur;   // thin lens: circle of confusion ~ |1 - focus / distance|
}

void main() {
    float focus = texelFetch(u_focus, ivec2(0), 0).r;
    float centerDepth = sceneDepth(uv);
    float centerSize = blurSize(centerDepth, focus);
    vec3 color = texture(u_color, uv).rgb;
    float tot = 1.0;
    float radius = u_rad_scale;
    for (float ang = 0.0; radius < u_max_blur; ang += GOLDEN_ANGLE) {
        vec2 tc = uv + vec2(cos(ang), sin(ang)) * u_pixel * radius;
        vec3 sampleColor = texture(u_color, tc).rgb;
        float sampleDepth = sceneDepth(tc);
        float sampleSize = blurSize(sampleDepth, focus);
        if (sampleDepth < centerDepth)              // reversed depth: smaller = farther
            sampleSize = clamp(sampleSize, 0.0, centerSize * 2.0);
        float m = smoothstep(radius - 0.5, radius + 0.5, sampleSize);
        color += mix(color / tot, sampleColor, m);
        tot += 1.0;
        radius += u_rad_scale / radius;
    }
    fragColor = vec4(color / tot, 1.0);
}
