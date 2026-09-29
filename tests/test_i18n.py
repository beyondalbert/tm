from __future__ import annotations

import pytest

from tm.i18n import get_language, set_language, t


@pytest.fixture(autouse=True)
def restore_language():
    set_language("en")
    yield
    set_language("en")


def test_translate_between_english_and_chinese() -> None:
    assert set_language("en") == "en"
    assert t("tui.stopped") == "Stopped by user."
    assert set_language("zh") == "zh"
    assert t("tui.stopped") == "已被用户停止。"
    assert get_language() == "zh"


def test_format_placeholders() -> None:
    set_language("en")
    assert t("cmd.lang_set", lang="zh") == "language set to zh"


def test_auto_language_follows_locale(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANG", "zh_CN.UTF-8")
    assert set_language("auto") == "zh"
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    assert set_language("auto") == "en"


def test_unknown_language_falls_back_to_english() -> None:
    assert set_language("klingon") == "en"


def test_unknown_key_returns_the_key() -> None:
    set_language("en")
    assert t("no.such.key") == "no.such.key"
