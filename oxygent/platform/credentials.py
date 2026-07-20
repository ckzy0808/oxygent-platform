"""Credential resolvers that keep secret values out of serializable profiles."""

import os
from typing import Mapping, Optional, Protocol


class CredentialResolver(Protocol):
    def resolve(self, credential_reference: str) -> Optional[str]: ...


class EnvironmentCredentialResolver:
    """Resolve ``env:VARIABLE`` or plain environment-variable references."""

    def resolve(self, credential_reference: str) -> Optional[str]:
        if not credential_reference:
            return None
        variable = credential_reference.removeprefix("env:")
        return os.environ.get(variable)


class MappingCredentialResolver:
    """Non-serializable resolver intended for tests and injected secret stores."""

    def __init__(self, credentials: Mapping[str, str]) -> None:
        self._credentials = dict(credentials)

    def resolve(self, credential_reference: str) -> Optional[str]:
        return self._credentials.get(credential_reference)

    def __repr__(self) -> str:
        return f"MappingCredentialResolver(keys={sorted(self._credentials)})"
