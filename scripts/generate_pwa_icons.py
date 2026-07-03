#!/usr/bin/env python3
"""Generate PWA icons from Mérito icon design using Pillow."""
import os
from PIL import Image, ImageDraw

SIZES = [72, 96, 128, 144, 152, 192, 384, 512]
OUT_DIR = os.path.join(os.path.dirname(__file__), '..', 'static', 'icons')

BRAND_GRAPHITE = (0x11, 0x18, 0x27)
BRAND_BLUE = (0x14, 0x63, 0xFF)
ACCENT_CYAN = (0x15, 0xCF, 0xEA)
MID_BLUE = (0x3B, 0x82, 0xF6)
WHITE = (0xFF, 0xFF, 0xFF)


def draw_squircle(draw, cx, cy, size, fill, corner_radius):
    """Draw a rounded square (squircle) centered at (cx, cy)."""
    x0 = cx - size // 2
    y0 = cy - size // 2
    x1 = cx + size // 2
    y1 = cy + size // 2
    draw.rounded_rectangle([x0, y0, x1, y1], radius=corner_radius, fill=fill)


def draw_v_shape(draw, left_x, left_y, right_x, right_y, stem_w, fill):
    """Draw a V-shape monogram between the given points."""
    # Left stem
    draw.polygon([
        (left_x, left_y),
        (left_x + stem_w, left_y),
        (mid_x, mid_y),
        (left_x, left_y + (right_y - left_y)),
        (left_x + stem_w, left_y + (right_y - left_y)),
        (mid_x + stem_w // 2, mid_y),
    ], fill=fill)


def generate_icon(size):
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Padding: 12% inset for maskable
    pad = int(size * 0.12)
    content_size = size - 2 * pad
    cx, cy = size // 2, size // 2

    # Squircle background (graphite)
    corner_radius = int(content_size * 0.28)
    x0, y0 = pad, pad
    x1, y1 = size - pad, size - pad
    draw.rounded_rectangle([x0, y0, x1, y1], radius=corner_radius, fill=BRAND_GRAPHITE)

    # V monogram coordinates
    inner_size = content_size * 0.55
    v_cx, v_cy = cx, cy
    v_left = int(v_cx - inner_size * 0.35)
    v_right = int(v_cx + inner_size * 0.35)
    v_top = int(v_cy - inner_size * 0.5)
    v_bottom = int(v_cy + inner_size * 0.5)
    mid_y = int((v_top + v_bottom) / 2)

    # Left leg (white)
    stem_w = int(content_size * 0.04)
    draw.polygon([
        (v_left, v_top),
        (v_left + stem_w, v_top),
        (v_cx, mid_y),
        (v_left, v_bottom),
        (v_left + stem_w, v_bottom),
        (v_cx + stem_w // 2, mid_y),
    ], fill=WHITE)

    # Right leg (brand blue) - offset slightly to the right
    right_offset = int(content_size * 0.04)
    draw.polygon([
        (v_left + right_offset, v_top),
        (v_left + right_offset + stem_w, v_top),
        (v_cx + right_offset // 2, mid_y),
        (v_left + right_offset, v_bottom),
        (v_left + right_offset + stem_w, v_bottom),
        (v_cx + right_offset // 2 + stem_w // 2, mid_y),
    ], fill=BRAND_BLUE)

    # 3 decorative squares (pixels) at bottom-right area
    sq_size = max(2, int(content_size * 0.03))
    sq_x = int(cx + inner_size * 0.18)
    sq_base_y = int(v_bottom - inner_size * 0.1)

    draw.rounded_rectangle(
        [sq_x, sq_base_y, sq_x + sq_size, sq_base_y + sq_size],
        radius=max(1, sq_size // 2), fill=BRAND_BLUE,
    )
    draw.rounded_rectangle(
        [sq_x + sq_size + 1, sq_base_y - sq_size - 1,
         sq_x + 2 * sq_size + 1, sq_base_y - 1],
        radius=max(1, sq_size // 2), fill=MID_BLUE,
    )
    draw.rounded_rectangle(
        [sq_x + 2 * sq_size + 2, sq_base_y - 2 * sq_size - 2,
         sq_x + 3 * sq_size + 2, sq_base_y - sq_size - 2],
        radius=max(1, sq_size // 2), fill=ACCENT_CYAN,
    )

    return img


os.makedirs(OUT_DIR, exist_ok=True)
for size in SIZES:
    img = generate_icon(size)
    path = os.path.join(OUT_DIR, f'icon-{size}.png')
    img.save(path, 'PNG')
    print(f'Generated {path} ({size}x{size})')
