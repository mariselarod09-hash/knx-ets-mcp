"""Abstract transport interface for KNX bridge communication."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Transport(ABC):
    """Base class for transports that carry JSON requests to the ETS6 AddIn."""

    @abstractmethod
    async def connect(self) -> None:
        """Establish the connection and obtain the session token."""

    @abstractmethod
    async def send(self, request: dict[str, Any]) -> dict[str, Any]:
        """Send a JSON request dict and return the JSON response dict.

        The request is a complete protocol message (id, token, method, params, ...).
        The response follows the protocol: {id, ok, result/error, projectRevision}.
        """

    @abstractmethod
    async def close(self) -> None:
        """Close the transport connection."""

    @property
    @abstractmethod
    def token(self) -> str:
        """Return the session token obtained during connect()."""
