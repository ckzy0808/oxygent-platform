"""In-memory registries for platform profiles.

The interfaces intentionally do not depend on a database. A later persistence
adapter can replace these stores without changing ModelRouter or workflows.
"""

from typing import Generic, Iterable, TypeVar

from .profiles import (
    AgentProfile,
    ModelProfile,
    ProviderProfile,
    RoleDefinition,
    RoleModelPolicy,
    ToolPolicy,
)

T = TypeVar("T")


class RegistryError(ValueError):
    pass


class InMemoryRegistry(Generic[T]):
    """Small deterministic registry keyed by a profile's ``id`` field."""

    def __init__(self, values: Iterable[T] = ()) -> None:
        self._values: dict[str, T] = {}
        for value in values:
            self.register(value)

    def register(self, value: T) -> T:
        value_id = getattr(value, "id")
        if value_id in self._values:
            raise RegistryError(f"profile already registered: {value_id}")
        self._values[value_id] = value
        return value

    def upsert(self, value: T) -> T:
        self._values[getattr(value, "id")] = value
        return value

    def get(self, value_id: str) -> T:
        try:
            return self._values[value_id]
        except KeyError as exc:
            raise RegistryError(f"profile not found: {value_id}") from exc

    def has(self, value_id: str) -> bool:
        return value_id in self._values

    def list(self) -> list[T]:
        return list(self._values.values())


class ProviderRegistry(InMemoryRegistry[ProviderProfile]):
    pass


class ModelRegistry(InMemoryRegistry[ModelProfile]):
    def for_provider(self, provider_id: str) -> list[ModelProfile]:
        return [m for m in self.list() if m.provider_id == provider_id]


class RoleRegistry(InMemoryRegistry[RoleDefinition]):
    pass


class AgentProfileRegistry(InMemoryRegistry[AgentProfile]):
    pass


class ToolPolicyRegistry(InMemoryRegistry[ToolPolicy]):
    pass


class RoleModelPolicyRegistry(InMemoryRegistry[RoleModelPolicy]):
    def for_role(self, role_id: str) -> RoleModelPolicy:
        matches = [policy for policy in self.list() if policy.role_id == role_id]
        if len(matches) != 1:
            raise RegistryError(
                f"expected exactly one model policy for role {role_id}, found {len(matches)}"
            )
        return matches[0]
