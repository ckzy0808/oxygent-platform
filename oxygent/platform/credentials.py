"""Credential resolvers that keep secret values out of serializable profiles."""

import os
import subprocess
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


class MacOSKeychainCredentialResolver:
    """Resolve ``keychain:SERVICE/ACCOUNT`` references through macOS Keychain."""

    def resolve(self, credential_reference: str) -> Optional[str]:
        if not credential_reference.startswith("keychain:"):
            return None
        target = credential_reference.removeprefix("keychain:")
        service, separator, account = target.partition("/")
        if not service or not separator or not account:
            raise ValueError(
                "keychain credential reference must use keychain:SERVICE/ACCOUNT"
            )
        try:
            result = subprocess.run(
                [
                    "/usr/bin/security",
                    "find-generic-password",
                    "-s",
                    service,
                    "-a",
                    account,
                    "-w",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            raise RuntimeError("referenced macOS Keychain credential is unavailable") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("macOS Keychain credential lookup timed out") from exc
        return result.stdout.rstrip("\r\n") or None


class CompositeCredentialResolver:
    """Try resolvers in order without exposing resolved values in its repr."""

    def __init__(self, *resolvers: CredentialResolver) -> None:
        self._resolvers = resolvers

    def resolve(self, credential_reference: str) -> Optional[str]:
        for resolver in self._resolvers:
            value = resolver.resolve(credential_reference)
            if value is not None:
                return value
        return None

    def __repr__(self) -> str:
        return "CompositeCredentialResolver(resolvers=[redacted])"


def default_credential_resolver() -> CompositeCredentialResolver:
    """Resolve environment and macOS Keychain references without plain config keys."""
    return CompositeCredentialResolver(
        EnvironmentCredentialResolver(),
        MacOSKeychainCredentialResolver(),
    )
