from __future__ import annotations

from io import BytesIO
from math import isfinite
import struct
import zlib


RGBA = tuple[int, int, int, int]


def _png_bytes(width: int, height: int, pixels: bytearray) -> bytes:
    raw = bytearray()
    stride = width * 4
    for y in range(height):
        raw.append(0)
        start = y * stride
        raw.extend(pixels[start : start + stride])

    def chunk(name: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + name
            + payload
            + struct.pack(">I", zlib.crc32(name + payload) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw), level=6))
        + chunk(b"IEND", b"")
    )


def _set_pixel(
    pixels: bytearray,
    width: int,
    x: int,
    y: int,
    color: RGBA,
) -> None:
    if x < 0 or y < 0:
        return
    height = len(pixels) // (width * 4)
    if x >= width or y >= height:
        return

    index = (y * width + x) * 4
    pixels[index : index + 4] = bytes(color)


def _blend_pixel(
    pixels: bytearray,
    width: int,
    x: int,
    y: int,
    color: RGBA,
) -> None:
    if x < 0 or y < 0:
        return
    height = len(pixels) // (width * 4)
    if x >= width or y >= height:
        return

    index = (y * width + x) * 4
    alpha = color[3] / 255.0
    old = pixels[index : index + 4]
    for offset in range(3):
        pixels[index + offset] = int(
            old[offset] * (1.0 - alpha) + color[offset] * alpha
        )
    pixels[index + 3] = 255


def _line(
    pixels: bytearray,
    width: int,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    color: RGBA,
    thickness: int = 3,
) -> None:
    dx = abs(x2 - x1)
    dy = -abs(y2 - y1)
    sx = 1 if x1 < x2 else -1
    sy = 1 if y1 < y2 else -1
    error = dx + dy

    radius = max(0, thickness // 2)

    while True:
        for ox in range(-radius, radius + 1):
            for oy in range(-radius, radius + 1):
                if ox * ox + oy * oy <= radius * radius:
                    _blend_pixel(pixels, width, x1 + ox, y1 + oy, color)

        if x1 == x2 and y1 == y2:
            break

        twice = 2 * error
        if twice >= dy:
            error += dy
            x1 += sx
        if twice <= dx:
            error += dx
            y1 += sy


def _fill_polygon(
    pixels: bytearray,
    width: int,
    points: list[tuple[int, int]],
    color: RGBA,
) -> None:
    if len(points) < 3:
        return

    height = len(pixels) // (width * 4)
    min_y = max(0, min(y for _, y in points))
    max_y = min(height - 1, max(y for _, y in points))

    for y in range(min_y, max_y + 1):
        intersections: list[int] = []
        for index, (x1, y1) in enumerate(points):
            x2, y2 = points[(index + 1) % len(points)]
            if y1 == y2:
                continue
            if y < min(y1, y2) or y >= max(y1, y2):
                continue
            x = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            intersections.append(int(x))

        intersections.sort()
        for start in range(0, len(intersections) - 1, 2):
            left = max(0, intersections[start])
            right = min(width - 1, intersections[start + 1])
            for x in range(left, right + 1):
                _blend_pixel(pixels, width, x, y, color)


def _circle(
    pixels: bytearray,
    width: int,
    cx: int,
    cy: int,
    radius: int,
    color: RGBA,
) -> None:
    radius_sq = radius * radius
    for y in range(cy - radius, cy + radius + 1):
        for x in range(cx - radius, cx + radius + 1):
            dx = x - cx
            dy = y - cy
            if dx * dx + dy * dy <= radius_sq:
                _blend_pixel(pixels, width, x, y, color)


def _resample(values: list[float], target: int = 110) -> list[float]:
    if not values:
        raise ValueError("empty_rates")
    if len(values) == target:
        return values[:]
    if len(values) < target:
        return values[:]

    last = len(values) - 1
    result: list[float] = []
    for index in range(target):
        position = index * last / (target - 1)
        low = int(position)
        high = min(last, low + 1)
        fraction = position - low
        result.append(
            values[low] + (values[high] - values[low]) * fraction
        )
    return result


def render_price_chart(
    rates: list[float],
    *,
    width: int = 1000,
    height: int = 520,
) -> BytesIO:
    if len(rates) < 3:
        raise ValueError("not_enough_data")
    if not all(isfinite(float(rate)) for rate in rates):
        raise ValueError("invalid_rate")

    values = _resample([float(rate) for rate in rates])
    minimum = min(values)
    maximum = max(values)

    if maximum == minimum:
        padding = max(abs(maximum) * 0.02, 0.1)
        minimum -= padding
        maximum += padding

    pixels = bytearray(width * height * 4)
    background = (10, 15, 20, 255)
    grid = (50, 60, 70, 155)
    axis = (100, 112, 124, 220)
    area = (45, 220, 128, 35)
    line = (61, 235, 136, 255)
    current = (245, 199, 66, 255)

    for index in range(0, len(pixels), 4):
        pixels[index : index + 4] = bytes(background)

    left = 52
    right = width - 26
    top = 30
    bottom = height - 42
    chart_width = right - left
    chart_height = bottom - top

    for fraction in (0.0, 0.25, 0.5, 0.75, 1.0):
        y = int(bottom - fraction * chart_height)
        _line(pixels, width, left, y, right, y, grid, thickness=1)

    for fraction in (0.0, 0.25, 0.5, 0.75, 1.0):
        x = int(left + fraction * chart_width)
        _line(pixels, width, x, top, x, bottom, grid, thickness=1)

    _line(pixels, width, left, top, left, bottom, axis, thickness=1)
    _line(pixels, width, left, bottom, right, bottom, axis, thickness=1)

    points: list[tuple[int, int]] = []
    span = maximum - minimum
    for index, value in enumerate(values):
        x = int(left + index * chart_width / max(1, len(values) - 1))
        normalized = (value - minimum) / span
        y = int(bottom - normalized * chart_height)
        points.append((x, y))

    area_points = points + [(points[-1][0], bottom), (points[0][0], bottom)]
    _fill_polygon(pixels, width, area_points, area)

    for first, second in zip(points, points[1:]):
        _line(
            pixels,
            width,
            first[0],
            first[1],
            second[0],
            second[1],
            line,
            thickness=4,
        )

    _circle(pixels, width, points[0][0], points[0][1], 4, line)
    _circle(pixels, width, points[-1][0], points[-1][1], 8, current)
    _circle(pixels, width, points[-1][0], points[-1][1], 4, background)

    output = BytesIO(_png_bytes(width, height, pixels))
    output.name = "opex-market-chart.png"
    output.seek(0)
    return output
