from __future__ import annotations

from math import isfinite


_FA_DIGITS = str.maketrans("0123456789.-", "۰۱۲۳۴۵۶۷۸۹٫−")


def _to_fa(value: str) -> str:
    return value.translate(_FA_DIGITS)


def _format_axis_value(value: float) -> str:
    return _to_fa(f"{value:.2f}")


def resample(rates: list[float], target: int) -> list[float]:
    """Linearly interpolate or downsample to exactly target points."""
    if target <= 0:
        raise ValueError("invalid_target")
    if not rates:
        raise ValueError("empty_rates")
    if len(rates) == target:
        return list(rates)

    if target == 1:
        return [float(rates[0])]

    result: list[float] = []
    last_index = len(rates) - 1
    for i in range(target):
        pos = i * last_index / (target - 1)
        lo = int(pos)
        hi = min(lo + 1, last_index)
        frac = pos - lo
        result.append(float(rates[lo] + frac * (rates[hi] - rates[lo])))
    return result


def _plot_rows(rates: list[float], height: int) -> list[int]:
    minimum = min(rates)
    maximum = max(rates)

    if maximum == minimum:
        middle = (height - 1) // 2
        return [middle] * len(rates)

    span = maximum - minimum
    rows: list[int] = []
    for rate in rates:
        normalized = (maximum - rate) / span
        row = int(round(normalized * (height - 1)))
        rows.append(max(0, min(height - 1, row)))
    return rows


def draw_ascii_chart(
    rates: list[float],
    width: int = 20,
    height: int = 8,
    *,
    age_label: str | None = None,
) -> str:
    if len(rates) < 3:
        raise ValueError("not_enough_data")
    if width < 1:
        raise ValueError("invalid_width")
    if height < 2:
        raise ValueError("invalid_height")
    if not all(isfinite(float(rate)) for rate in rates):
        raise ValueError("invalid_rate")

    points = resample(rates, width) if len(rates) > width else list(rates)
    rows = _plot_rows(points, height)

    grid = [[" " for _ in range(width)] for _ in range(height)]

    def put(row: int, column: int, char: str) -> None:
        if 0 <= row < height and 0 <= column < width:
            grid[row][column] = char

    for x, row in enumerate(rows):
        if x == 0:
            put(row, x, "─")
            continue

        previous_row = rows[x - 1]
        if row == previous_row:
            put(row, x - 1, "─")
            put(row, x, "─")
        elif row < previous_row:
            put(previous_row, x - 1, "╭")
            for connector_row in range(row + 1, previous_row):
                put(connector_row, x, "│")
            put(row, x, "╯")
        else:
            put(previous_row, x - 1, "╰")
            for connector_row in range(previous_row + 1, row):
                put(connector_row, x, "│")
            put(row, x, "╮")

    top_label = f"▲ {_format_axis_value(max(rates))}"
    min_label = _format_axis_value(min(rates))
    label_width = max(len(top_label), len(min_label) + 1, 8)

    chart_lines = [
        f"{top_label.rjust(label_width)} ┤{''.join(grid[0])}",
    ]
    for row in range(1, height):
        chart_lines.append(
            f"{' ' * label_width} │{''.join(grid[row])}"
        )

    x_axis = f"{min_label.rjust(label_width)} ───┘" + "─" * width + " ▶"
    chart_lines.append(x_axis)

    if age_label is None:
        estimated_hours = max(1, round((len(rates) - 1) / 4))
        age_label = f"-{estimated_hours}h"
    age_label = _to_fa(age_label)

    label_line = (
        f"{age_label}"
        f"{' ' * max(1, label_width + 4 + width - len(age_label) - 3)}"
        "الان"
    )
    chart_lines.append(label_line)

    return "\n".join(chart_lines)
