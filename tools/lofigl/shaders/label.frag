#version 330 core

in vec2 uv;
out vec4 fragColor;

uniform sampler2D u_font_atlas;
uniform vec4 u_color;

void main() {
    float alpha = texture(u_font_atlas, uv).a * u_color.a;
    if (alpha < 0.01) {
        discard;
    }
    fragColor = vec4(u_color.rgb, alpha);
}
