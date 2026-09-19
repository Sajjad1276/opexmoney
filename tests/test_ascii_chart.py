import pytest

from app.utils.ascii_chart import draw_ascii_chart, resample


def test_not_enough_data():
    with pytest.raises(ValueError, match="not_enough_data"):
        draw_ascii_chart([1.0, 1.1])


def test_flat_line():
    result = draw_ascii_chart([1.0] * 10)
    assert "▲" in result
    assert "▶" in result
    assert "─" in result


def test_rising_line():
    result = draw_ascii_chart([1.0, 1.1, 1.2, 1.3, 1.4, 1.5])
    assert "▲" in result
    assert "╭" in result


def test_falling_line():
    result = draw_ascii_chart([1.5, 1.4, 1.3, 1.2, 1.1, 1.0])
    assert "╰" in result


def test_resample_output_length():
    assert len(resample([1.0, 2.0, 3.0, 4.0, 5.0], 10)) == 10
    assert len(resample([1.0] * 30, 20)) == 20


def test_chart_has_correct_row_count():
    result = draw_ascii_chart(
        [1.0, 1.5, 1.2, 0.8, 1.3, 1.1, 1.4],
        height=8,
    )
    lines = result.split("\n")
    assert len(lines) >= 8
