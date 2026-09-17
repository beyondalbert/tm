from __future__ import annotations

from tm.cli.main import _mask_key


def test_mask_key_hides_the_middle() -> None:
    masked = _mask_key("sk-00000000000000000000000000000000")
    assert masked == "sk-000...0000 (35 chars)"
    assert "0000000000" not in masked


def test_mask_key_short_values_are_fully_masked() -> None:
    assert _mask_key("") == "<empty>"
    assert _mask_key("short") == "*****"
    assert _mask_key("0123456789") == "**********"
