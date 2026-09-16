from __future__ import annotations

from importlib.metadata import version

import tm


def test_version_matches_package_metadata() -> None:
    assert tm.__version__ == version("the-machine")


def test_version_is_not_hardcoded_placeholder() -> None:
    assert tm.__version__ != "0.0.0"
