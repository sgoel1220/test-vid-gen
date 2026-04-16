"""Pod-side persistence layer — typed httpx client + SQLite outbox.

Public API::

    from persistence import PersistenceClient, Outbox, get_client, get_outbox, is_enabled

    if is_enabled():
        client = get_client()
        outbox = Outbox()
        await outbox.open()
        set_outbox(outbox)
        await outbox.drain_once(client)
        task = asyncio.create_task(outbox.background_drain_loop(client))
        ...
"""

from __future__ import annotations

from typing import Optional

from persistence.client import PersistenceClient
from persistence.config import get_settings
from persistence.outbox import Outbox

__all__ = [
    "Outbox",
    "PersistenceClient",
    "get_client",
    "get_outbox",
    "is_enabled",
    "set_outbox",
]

_outbox_instance: Optional[Outbox] = None


def is_enabled() -> bool:
    """Return True iff METADATA_API_URL and METADATA_API_KEY are configured."""
    return get_settings().is_enabled()


def get_client() -> PersistenceClient:
    """Return a new PersistenceClient using the current settings.

    Raises RuntimeError if persistence is not configured.
    The caller is responsible for calling aclose() (or using as async context manager).
    """
    settings = get_settings()
    if not settings.is_enabled():
        raise RuntimeError(
            "Persistence is not configured — set METADATA_API_URL and METADATA_API_KEY."
        )
    return PersistenceClient(settings)


def get_outbox() -> Optional[Outbox]:
    """Return the running outbox instance, or None if persistence is not started."""
    return _outbox_instance


def set_outbox(outbox: Optional[Outbox]) -> None:
    """Set (or clear) the module-level outbox singleton.

    Called by app.py lifespan on startup and teardown.
    """
    global _outbox_instance
    _outbox_instance = outbox
