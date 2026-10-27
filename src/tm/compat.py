"""Standard-library gaps between Python 3.8 and newer releases.

TM supports Python 3.8+, so the few newer stdlib features it relies on live
here instead of being branched on throughout the codebase. Each name mirrors
its stdlib counterpart.
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import sys
from collections.abc import Callable
from enum import Enum
from typing import Any, TypeVar

if sys.version_info >= (3, 11):
    from enum import StrEnum as StrEnum

    from tomllib import TOMLDecodeError as TOMLDecodeError
    from tomllib import load as load_toml
    from tomllib import loads as loads_toml
else:
    from tomli import TOMLDecodeError as TOMLDecodeError
    from tomli import load as load_toml
    from tomli import loads as loads_toml

    class StrEnum(str, Enum):
        """Python 3.11's ``StrEnum``: a string enum whose members format as their value.

        Enum already formats str-mixin members by value on every version; only
        ``str()`` (and therefore f-strings via ``__str__``) differs.
        """

        def __str__(self) -> str:
            return str.__str__(self)


if sys.version_info >= (3, 9):
    from typing import Annotated as Annotated
else:
    from typing_extensions import Annotated as Annotated

T = TypeVar("T")


async def to_thread(func: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """Run a blocking callable in the default executor, like ``asyncio.to_thread``.

    Python added ``asyncio.to_thread`` in 3.9. This mirrors it, including the
    copy of the current context variables into the worker thread.
    """
    loop = asyncio.get_running_loop()
    call = functools.partial(contextvars.copy_context().run, func, *args, **kwargs)
    return await loop.run_in_executor(None, call)


__all__ = [
    "Annotated",
    "StrEnum",
    "TOMLDecodeError",
    "load_toml",
    "loads_toml",
    "to_thread",
]
