"""Named-tool allow list. Matter text is not handed to anything else."""

from __future__ import annotations

from collections.abc import Callable

from intake.constants import ALLOWED_TOOLS
from intake.errors import AccessDenied


def assert_allowed(name: str, version: str) -> None:
    expected = ALLOWED_TOOLS.get(name)
    if expected is None or expected != version:
        raise AccessDenied(
            f"Tool '{name}' {version} is not on the allow list. Matter text was not sent."
        )


def invoke(name: str, version: str, callback: Callable, *args, **kwargs):
    """Run a callback only after the tool is checked. The callback sees the text."""
    assert_allowed(name, version)
    return callback(*args, **kwargs)
