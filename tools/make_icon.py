"""Create the Windows ICO from the repository's datalog checker SVG design."""

from __future__ import annotations

import math
import struct
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "assets" / "datalog_checker.ico"
SIZES = (16, 24, 32, 48, 64, 128, 256)
SCALE = 4


def blend_pixel(pixels: list[list[tuple[int, int, int, int]]], x: float, y: float, color: tuple[int, int, int, int]) -> None:
    ix = int(round(x))
    iy = int(round(y))
    if 0 <= iy < len(pixels) and 0 <= ix < len(pixels[iy]):
        pixels[iy][ix] = color


def rounded_rect(
    pixels: list[list[tuple[int, int, int, int]]],
    left: float,
    top: float,
    right: float,
    bottom: float,
    radius: float,
    color: tuple[int, int, int, int],
) -> None:
    for y in range(max(0, int(top)), min(len(pixels), math.ceil(bottom))):
        for x in range(max(0, int(left)), min(len(pixels[y]), math.ceil(right))):
            cx = min(max(x, left + radius), right - radius)
            cy = min(max(y, top + radius), bottom - radius)
            if (x - cx) ** 2 + (y - cy) ** 2 <= radius**2:
                pixels[y][x] = color


def polygon(
    pixels: list[list[tuple[int, int, int, int]]],
    points: list[tuple[float, float]],
    color: tuple[int, int, int, int],
) -> None:
    min_y = max(0, int(min(y for _, y in points)))
    max_y = min(len(pixels), math.ceil(max(y for _, y in points)))
    for y in range(min_y, max_y):
        intersections: list[float] = []
        for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]):
            if (y1 <= y < y2) or (y2 <= y < y1):
                intersections.append(x1 + (y - y1) * (x2 - x1) / (y2 - y1))
        intersections.sort()
        for left, right in zip(intersections[::2], intersections[1::2]):
            for x in range(max(0, int(left)), min(len(pixels[y]), math.ceil(right))):
                pixels[y][x] = color


def line(
    pixels: list[list[tuple[int, int, int, int]]],
    points: list[tuple[float, float]],
    width: float,
    color: tuple[int, int, int, int],
) -> None:
    radius = width / 2
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        distance = max(abs(x2 - x1), abs(y2 - y1))
        for step in range(int(distance) + 1):
            fraction = step / max(distance, 1)
            x = x1 + (x2 - x1) * fraction
            y = y1 + (y2 - y1) * fraction
            for iy in range(int(y - radius), math.ceil(y + radius) + 1):
                for ix in range(int(x - radius), math.ceil(x + radius) + 1):
                    if (ix - x) ** 2 + (iy - y) ** 2 <= radius**2:
                        blend_pixel(pixels, ix, iy, color)


def circle(
    pixels: list[list[tuple[int, int, int, int]]],
    center: tuple[float, float],
    radius: float,
    color: tuple[int, int, int, int],
) -> None:
    cx, cy = center
    for y in range(int(cy - radius), math.ceil(cy + radius) + 1):
        for x in range(int(cx - radius), math.ceil(cx + radius) + 1):
            if (x - cx) ** 2 + (y - cy) ** 2 <= radius**2:
                blend_pixel(pixels, x, y, color)


def render(size: int) -> bytes:
    high = size * SCALE
    scale = high / 256
    red = (215, 25, 32, 255)
    light_red = (253, 235, 236, 255)
    white = (255, 255, 255, 255)
    graphite = (38, 50, 56, 255)
    pixels = [[(0, 0, 0, 0) for _ in range(high)] for _ in range(high)]
    rounded_rect(pixels, 0, 0, high, high, 48 * scale, red)
    polygon(
        pixels,
        [(56 * scale, 32 * scale), (152 * scale, 32 * scale),
         (200 * scale, 80 * scale), (200 * scale, 224 * scale),
         (56 * scale, 224 * scale)],
        white,
    )
    polygon(
        pixels,
        [(152 * scale, 32 * scale), (200 * scale, 80 * scale),
         (152 * scale, 80 * scale)],
        light_red,
    )
    line(pixels, [(82 * scale, 122 * scale), (160 * scale, 122 * scale)], 12 * scale, graphite)
    line(pixels, [(82 * scale, 148 * scale), (134 * scale, 148 * scale)], 12 * scale, graphite)
    line(
        pixels,
        [(82 * scale, 191 * scale), (105 * scale, 169 * scale),
         (125 * scale, 181 * scale), (151 * scale, 151 * scale)],
        10 * scale,
        red,
    )
    circle(pixels, (171 * scale, 180 * scale), 29 * scale, graphite)
    line(
        pixels,
        [(157 * scale, 180 * scale), (168 * scale, 191 * scale),
         (190 * scale, 166 * scale)],
        9 * scale,
        white,
    )

    output = bytearray()
    stride = ((size + 31) // 32) * 4
    mask = bytearray(stride * size)
    for output_row, y in enumerate(reversed(range(size))):
        for x in range(size):
            samples = [pixels[y * SCALE + sy][x * SCALE + sx] for sy in range(SCALE) for sx in range(SCALE)]
            alpha = sum(sample[3] for sample in samples) // len(samples)
            output.extend(bytes((
                sum(sample[2] for sample in samples) // len(samples),
                sum(sample[1] for sample in samples) // len(samples),
                sum(sample[0] for sample in samples) // len(samples),
                alpha,
            )))
            if alpha < 128:
                mask[output_row * stride + x // 8] |= 1 << (7 - x % 8)
    dib_header = struct.pack(
        "<IiiHHIIiiII",
        40, size, size * 2, 1, 32, 0, len(output), 0, 0, 0, 0
    )
    return dib_header + bytes(output) + bytes(mask)


def main() -> None:
    images = [render(size) for size in SIZES]
    header_size = 6 + 16 * len(images)
    entries = bytearray()
    offset = header_size
    for size, image in zip(SIZES, images):
        entries.extend(struct.pack(
            "<BBBBHHII",
            0 if size == 256 else size,
            0 if size == 256 else size,
            0,
            0,
            1,
            32,
            len(image),
            offset,
        ))
        offset += len(image)
    OUTPUT.write_bytes(struct.pack("<HHH", 0, 1, len(images)) + entries + b"".join(images))


if __name__ == "__main__":
    main()
