"""Service-layer exceptions."""

from __future__ import annotations


class ResourceNotFoundError(LookupError):
    """Raised when a service cannot find a requested resource."""

    def __init__(self, resource: str, identifier: object) -> None:
        self.resource = resource
        self.identifier = identifier
        super().__init__(f"{resource} {identifier} not found")
