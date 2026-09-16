"""The Machine (TM): a local AI agent that can control your machine."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("the-machine")
except PackageNotFoundError:  # pragma: no cover - running without an install
    __version__ = "0.0.0"
