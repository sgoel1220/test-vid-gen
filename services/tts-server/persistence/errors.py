"""Typed exception hierarchy for the persistence layer."""

from __future__ import annotations


class PersistenceError(Exception):
    """Base class for all persistence errors."""


class TransientPersistenceError(PersistenceError):
    """Retryable errors: 5xx responses, network failures, timeouts, 429."""


class PermanentPersistenceError(PersistenceError):
    """Non-retryable errors: 4xx responses (other than 429)."""
