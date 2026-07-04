"""Shared in-memory fetchers for the injected-fetcher seam tests.

The ``tests`` directory is on ``pythonpath`` (see ``[tool.pytest.ini_options]``
in ``pyproject.toml``), so test modules import these as
``from fetchers import ...`` without a ``tests`` package.
"""

from collections.abc import Awaitable, Callable


def store_fetcher(store: dict[str, bytes]) -> Callable[[str], bytes]:
    """A Fetch backed by an in-memory dict of canned documents."""
    return store.__getitem__


def async_store_fetcher(store: dict[str, bytes]) -> Callable[[str], Awaitable[bytes]]:
    """An AsyncFetch backed by an in-memory dict of canned documents."""

    async def fetch(url: str) -> bytes:
        return store[url]

    return fetch
