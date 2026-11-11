from __future__ import annotations

from tm.utils.sanitize import sanitize


def test_sanitize_strips_common_sequences() -> None:
    assert sanitize("\x1b[31mred\x1b[0m\r\nok\x07") == "red\nok"
    assert sanitize("\x1b]0;title\x07text") == "text"
    assert sanitize("\x1b]8;;http://x\x07link\x1b]8;;\x07") == "link"
    assert sanitize("keep\ttabs\nand lines") == "keep\ttabs\nand lines"
    assert sanitize("") == ""


def test_sanitize_strips_256_color_and_truecolor() -> None:
    assert sanitize("a\x1b[38;5;32mb") == "ab"
    assert sanitize("a\x1b[38;2;85;5;32mb") == "ab"


def test_sanitize_strips_eight_bit_c1_sequences() -> None:
    assert sanitize("a\x9b38;5;32mb") == "ab"
    assert sanitize("a\x9d0;title\x07b") == "ab"


def test_sanitize_removes_a_sequence_cut_off_by_a_stream_boundary() -> None:
    # Removing only the ESC would leave the parameters visible as text.
    assert sanitize("abc\x1b[38;5;32") == "abc"
    assert sanitize("abc\x1b[") == "abc"
    assert sanitize("abc\x1b") == "abc"


def test_sanitize_removes_orphaned_parameters() -> None:
    assert sanitize("x[555;5;32My") == "xy"
    assert sanitize("x[38;5;32my") == "xy"


def test_sanitize_keeps_ordinary_brackets() -> None:
    assert sanitize("[1](http://x)") == "[1](http://x)"
    assert sanitize("[123abc](http://x)") == "[123abc](http://x)"
    assert sanitize("see [1;2] ok") == "see [1;2] ok"
