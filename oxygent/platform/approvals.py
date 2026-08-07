"""Immutable approval audit records and recovery patch storage."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from datetime import datetime
from enum import Enum

from pydantic import ConfigDict, Field, field_validator

from oxygent.utils.common_utils import generate_uuid

from .common import PlatformModel, to_camel, utc_now


class ApprovalAction(str, Enum):
    REQUEST_REVISION = "requestRevision"
    ACCEPT_REVIEW_RISK = "acceptReviewRisk"
    APPROVE_CHANGES = "approveChanges"
    APPLY_TO_BRANCH = "applyToBranch"
    EXPORT_PATCH = "exportPatch"
    DISCARD = "discard"


class ApprovalActorType(str, Enum):
    HUMAN = "human"
    AGENT = "agent"
    SYSTEM = "system"


class ApprovalRecord(PlatformModel):
    """Frozen, append-only audit entry for one explicit action."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        frozen=True,
    )

    id: str = Field(default_factory=generate_uuid)
    project_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    action: ApprovalAction
    actor_id: str = Field(min_length=1, max_length=160)
    actor_type: ApprovalActorType
    reason: str = Field(default="", max_length=2000)
    content_hash: str | None = Field(default=None, min_length=64, max_length=64)
    verification_run_ids: list[str] = Field(default_factory=list, max_length=100)
    applied_commit: str | None = Field(default=None, min_length=7, max_length=64)
    recovery_patch_id: str | None = Field(default=None, max_length=160)
    created_at: datetime = Field(default_factory=utc_now)


class ApprovalActionRequest(PlatformModel):
    actor_id: str = Field(min_length=1, max_length=160)
    actor_type: ApprovalActorType = ApprovalActorType.HUMAN
    reason: str = Field(default="", max_length=2000)


class ApplyChangesRequest(ApprovalActionRequest):
    commit_message: str = Field(min_length=1, max_length=200)

    @field_validator("commit_message")
    @classmethod
    def single_line_message(cls, value: str) -> str:
        value = value.strip()
        if not value or "\n" in value or "\r" in value:
            raise ValueError("commit message must be one non-empty line")
        return value


class DiscardChangesRequest(ApprovalActionRequest):
    confirmation: str

    @field_validator("confirmation")
    @classmethod
    def explicit_confirmation(cls, value: str) -> str:
        if value != "DISCARD":
            raise ValueError("confirmation must exactly equal DISCARD")
        return value


class RecoveryPatch(PlatformModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        frozen=True,
    )

    id: str = Field(default_factory=generate_uuid)
    project_id: str
    task_id: str
    base_commit: str
    content_hash: str
    content: str
    created_at: datetime = Field(default_factory=utc_now)


class InMemoryApprovalStore:
    def __init__(self, records: Iterable[ApprovalRecord] | None = None) -> None:
        self._records = list(records or [])
        self._ids = {record.id for record in self._records}
        self._lock = asyncio.Lock()

    async def append(self, record: ApprovalRecord) -> ApprovalRecord:
        async with self._lock:
            if record.id in self._ids:
                raise ValueError(f"approval record already exists: {record.id}")
            self._records.append(record)
            self._ids.add(record.id)
        return record

    async def list(self, task_id: str | None = None) -> list[ApprovalRecord]:
        async with self._lock:
            records = list(self._records)
        if task_id is not None:
            records = [record for record in records if record.task_id == task_id]
        return sorted(records, key=lambda item: item.created_at)


class InMemoryRecoveryPatchStore:
    def __init__(self) -> None:
        self._patches: dict[str, RecoveryPatch] = {}
        self._lock = asyncio.Lock()

    async def append(self, patch: RecoveryPatch) -> RecoveryPatch:
        async with self._lock:
            if patch.id in self._patches:
                raise ValueError(f"recovery patch already exists: {patch.id}")
            self._patches[patch.id] = patch
        return patch

    async def get(self, patch_id: str) -> RecoveryPatch:
        async with self._lock:
            try:
                return self._patches[patch_id]
            except KeyError as exc:
                raise KeyError(f"recovery patch not found: {patch_id}") from exc
