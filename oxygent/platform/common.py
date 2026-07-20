"""Shared primitives for the project-centric multi-role platform layer."""

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


def to_camel(value: str) -> str:
    """Convert a snake_case field name to lower camelCase."""
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


class PlatformModel(BaseModel):
    """Base schema with camelCase API aliases and strict unknown-field checks."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )
