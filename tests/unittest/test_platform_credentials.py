"""Credential resolver safety tests."""

from types import SimpleNamespace

import pytest

from oxygent.platform import (
    CompositeCredentialResolver,
    MacOSKeychainCredentialResolver,
    MappingCredentialResolver,
)


def test_keychain_resolver_uses_fixed_argv_and_strips_newline(monkeypatch):
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return SimpleNamespace(stdout="resolved-secret\n")

    monkeypatch.setattr("oxygent.platform.credentials.subprocess.run", fake_run)
    resolver = MacOSKeychainCredentialResolver()

    assert resolver.resolve("keychain:service-name/account-name") == "resolved-secret"
    assert captured["argv"] == [
        "/usr/bin/security",
        "find-generic-password",
        "-s",
        "service-name",
        "-a",
        "account-name",
        "-w",
    ]
    assert captured["kwargs"]["capture_output"] is True


def test_keychain_resolver_rejects_malformed_reference():
    with pytest.raises(ValueError, match="keychain:SERVICE/ACCOUNT"):
        MacOSKeychainCredentialResolver().resolve("keychain:missing-account")


def test_composite_resolver_repr_never_contains_resolved_values():
    resolver = CompositeCredentialResolver(
        MappingCredentialResolver({"env:KEY": "do-not-print-this"})
    )
    assert resolver.resolve("env:KEY") == "do-not-print-this"
    assert "do-not-print-this" not in repr(resolver)
