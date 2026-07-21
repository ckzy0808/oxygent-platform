"""Application services for Project and Artifact product APIs."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import Field

from oxygent.utils.common_utils import generate_uuid

from .artifacts import ArtifactBase, InMemoryArtifactStore, ValidationStatus
from .approvals import (
    ApplyChangesRequest,
    ApprovalAction,
    ApprovalActionRequest,
    ApprovalActorType,
    ApprovalRecord,
    DiscardChangesRequest,
    InMemoryApprovalStore,
    InMemoryRecoveryPatchStore,
    RecoveryPatch,
)
from .common import PlatformModel, utc_now
from .control_plane import PlatformControlPlane
from .coding import (
    ApprovalState,
    CodeTask,
    CodeTaskCreate,
    CodingEngine,
    CodingOperation,
    CodingRunRequest,
    CodingRunResult,
    InMemoryCodeTaskStore,
    InMemoryRepositoryProfileStore,
    NativeCodingEngine,
    RepositoryProfile,
    RepositoryRegistration,
    ScopeGuard,
    ScopeViolation,
    WorktreeManager,
    run_git,
)
from .projects import (
    InMemoryProjectRepository,
    InMemoryProjectTaskRepository,
    Project,
    ProjectCreate,
    ProjectRepository,
    ProjectTask,
    ProjectTaskFromChat,
    ProjectTaskRepository,
    ProjectUpdate,
)
from .verification import (
    DiffSnapshot,
    InMemoryVerificationProfileStore,
    InMemoryVerificationRunStore,
    VerificationProfile,
    VerificationProfileCreate,
    VerificationRun,
    VerificationRunner,
    VerificationOutput,
    VerificationStatus,
    capture_diff,
    diff_content_hash,
)


class ProjectActivity(PlatformModel):
    id: str
    project_id: str
    event_type: str
    summary: str
    entity_id: str = ""
    created_at: datetime = Field(default_factory=utc_now)


@dataclass
class PlatformServices:
    """Non-global service container passed explicitly to the FastAPI router."""

    projects: ProjectRepository = field(default_factory=InMemoryProjectRepository)
    tasks: ProjectTaskRepository = field(default_factory=InMemoryProjectTaskRepository)
    artifacts: InMemoryArtifactStore = field(default_factory=InMemoryArtifactStore)
    control_plane: PlatformControlPlane = field(default_factory=PlatformControlPlane)
    repositories: InMemoryRepositoryProfileStore = field(
        default_factory=InMemoryRepositoryProfileStore
    )
    code_tasks: InMemoryCodeTaskStore = field(default_factory=InMemoryCodeTaskStore)
    coding_engine: CodingEngine = field(default_factory=NativeCodingEngine)
    worktrees: WorktreeManager | None = None
    code_authorization_enabled: bool = False
    verification_profiles: InMemoryVerificationProfileStore = field(
        default_factory=InMemoryVerificationProfileStore
    )
    verification_runs: InMemoryVerificationRunStore = field(
        default_factory=InMemoryVerificationRunStore
    )
    verification_runner: VerificationRunner = field(default_factory=VerificationRunner)
    approvals: InMemoryApprovalStore = field(default_factory=InMemoryApprovalStore)
    recovery_patches: InMemoryRecoveryPatchStore = field(
        default_factory=InMemoryRecoveryPatchStore
    )
    _activities: list[ProjectActivity] = field(default_factory=list, init=False)
    _activity_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    _code_action_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    @classmethod
    def with_code_workspace(
        cls,
        *,
        repository_roots: dict[str, Path],
        workspace_root: Path,
        code_authorization_enabled: bool = False,
        verification_executables: set[str] | None = None,
        verification_environment: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> PlatformServices:
        """Build services with an explicit repository allow-list and worktree root."""
        return cls(
            worktrees=WorktreeManager(repository_roots, workspace_root),
            code_authorization_enabled=code_authorization_enabled,
            verification_runner=VerificationRunner(
                allowed_executables=verification_executables or set(),
                environment=verification_environment
                or {
                    key: value
                    for key, value in os.environ.items()
                    if key in {"PATH", "LANG", "LC_ALL"}
                },
            ),
            **kwargs,
        )

    @property
    def code_workspace_configured(self) -> bool:
        return bool(self.worktrees and self.worktrees.configured)

    async def create_project(self, payload: ProjectCreate) -> Project:
        project = Project(**payload.model_dump())
        await self.projects.create(project)
        await self._record_activity(project.id, "project.created", "Project created")
        return project

    async def update_project(self, project_id: str, payload: ProjectUpdate) -> Project:
        project = await self.projects.get(project_id)
        updates = {
            key: value
            for key, value in payload.model_dump().items()
            if key in payload.model_fields_set
        }
        now = utc_now()
        updated = project.model_copy(
            update={**updates, "updated_at": now, "last_activity_at": now}
        )
        await self.projects.update(updated)
        await self._record_activity(project_id, "project.updated", "Project updated")
        return updated

    async def delete_project(self, project_id: str) -> None:
        await self.projects.get(project_id)
        if await self.tasks.list(project_id) or self.artifacts.list(project_id):
            raise ValueError("only empty projects can be deleted; archive this project")
        await self.projects.delete(project_id)

    async def create_task_from_chat(
        self, project_id: str, payload: ProjectTaskFromChat
    ) -> ProjectTask:
        project = await self.projects.get(project_id)
        self._validate_source_artifacts(project_id, payload.source_artifact_ids)
        task = ProjectTask(project_id=project_id, **payload.model_dump())
        await self.tasks.create(task)
        now = utc_now()
        await self.projects.update(
            project.model_copy(
                update={
                    "active_tasks": project.active_tasks + 1,
                    "updated_at": now,
                    "last_activity_at": now,
                }
            )
        )
        await self._record_activity(
            project_id,
            "task.createdFromChat",
            f"Task created from Chat: {task.title}",
            task.id,
        )
        return task

    async def register_repository(
        self, project_id: str, payload: RepositoryRegistration
    ) -> RepositoryProfile:
        await self.projects.get(project_id)
        if not self.worktrees:
            raise ValueError("Code Workspace is not configured")
        self.worktrees.resolve_repository(payload.root_reference)
        for branch in payload.allowed_base_branches:
            root = self.worktrees.resolve_repository(payload.root_reference)
            await run_git(root, "rev-parse", "--verify", f"{branch}^{{commit}}")
        profile = RepositoryProfile(project_id=project_id, **payload.model_dump())
        await self.repositories.create(profile)
        await self._touch_project(project_id)
        await self._record_activity(
            project_id,
            "repository.registered",
            f"Repository registered: {profile.name}",
            profile.id,
        )
        return profile

    async def create_code_task(
        self, project_id: str, payload: CodeTaskCreate
    ) -> CodeTask:
        await self.projects.get(project_id)
        repository = await self.repositories.get(payload.repository_id)
        if repository.project_id != project_id:
            raise ValueError("repository must belong to the target project")
        if not repository.enabled:
            raise ValueError("repository is disabled")
        if payload.project_task_id:
            project_task = await self.tasks.get(payload.project_task_id)
            if project_task.project_id != project_id:
                raise ValueError("Project Task must belong to the target project")
        if not self.worktrees:
            raise ValueError("Code Workspace is not configured")
        task_id = generate_uuid()
        base_branch = payload.base_branch or repository.default_branch
        base_commit, branch, worktree = await self.worktrees.create_worktree(
            repository, task_id, base_branch
        )
        task = CodeTask(
            id=task_id,
            projectId=project_id,
            projectTaskId=payload.project_task_id,
            repositoryId=repository.id,
            baseBranch=base_branch,
            baseCommit=base_commit,
            branch=branch,
            worktreePath=str(worktree),
            changeContract=payload.change_contract,
        )
        await self.code_tasks.create(task)
        await self._touch_project(project_id)
        await self._record_activity(
            project_id,
            "codeTask.worktreeCreated",
            f"Isolated worktree created for {task.branch}",
            task.id,
        )
        return task

    async def execute_code_read(
        self,
        project_id: str,
        task_id: str,
        *,
        operation: CodingOperation,
        path: str | None = None,
        query: str | None = None,
        max_results: int = 200,
        max_output_bytes: int = 256_000,
    ) -> CodingRunResult:
        task = await self.code_tasks.get(task_id)
        if task.project_id != project_id:
            raise KeyError(f"code task not found: {task_id}")
        if operation is CodingOperation.READ_FILE:
            ScopeGuard.check_path(task.change_contract, path or "")
        result = await self.coding_engine.execute(
            CodingRunRequest(
                taskId=task.id,
                operation=operation,
                worktreePath=task.worktree_path,
                baseCommit=task.base_commit,
                path=path,
                query=query,
                maxResults=max_results,
                maxOutputBytes=max_output_bytes,
            )
        )
        if operation is CodingOperation.TREE:
            files = []
            for item in result.data.get("files", []):
                try:
                    files.append(ScopeGuard.check_path(task.change_contract, item))
                except ScopeViolation:
                    continue
            result.data["files"] = files
        elif operation is CodingOperation.SEARCH:
            matches = []
            for item in result.data.get("matches", []):
                candidate = item.split(":", 1)[0]
                try:
                    ScopeGuard.check_path(task.change_contract, candidate)
                except ScopeViolation:
                    continue
                matches.append(item)
            result.data["matches"] = matches
        return result

    async def get_code_diff(self, project_id: str, task_id: str) -> DiffSnapshot:
        task = await self.code_tasks.get(task_id)
        if task.project_id != project_id:
            raise KeyError(f"code task not found: {task_id}")
        snapshot = await capture_diff(Path(task.worktree_path), task.base_commit)
        try:
            ScopeGuard.check_diff(
                task.change_contract,
                snapshot.changed_files,
                snapshot.diff_line_count,
            )
        except ScopeViolation as exc:
            snapshot = snapshot.model_copy(
                update={"diff": "", "scope_status": f"blocked: {exc}"}
            )
        await self.code_tasks.update(
            task.model_copy(
                update={
                    "changed_files": snapshot.changed_files,
                    "diff_line_count": snapshot.diff_line_count,
                    "updated_at": utc_now(),
                }
            )
        )
        return snapshot

    async def register_verification_profile(
        self, project_id: str, payload: VerificationProfileCreate
    ) -> VerificationProfile:
        await self.projects.get(project_id)
        repository = await self.repositories.get(payload.repository_id)
        if repository.project_id != project_id:
            raise ValueError("repository must belong to the target project")
        profile = VerificationProfile(projectId=project_id, **payload.model_dump())
        await self.verification_profiles.create(profile)
        await self._record_activity(
            project_id,
            "verificationProfile.created",
            f"Verification profile created: {profile.name}",
            profile.id,
        )
        return profile

    async def run_verification(
        self,
        project_id: str,
        task_id: str,
        profile_id: str,
        command_id: str,
    ) -> VerificationRun:
        task = await self.code_tasks.get(task_id)
        if task.project_id != project_id:
            raise KeyError(f"code task not found: {task_id}")
        profile = await self.verification_profiles.get(profile_id)
        if (
            profile.project_id != project_id
            or profile.repository_id != task.repository_id
        ):
            raise ValueError("verification profile does not match this Code Task")
        if (
            task.change_contract.verification_profile_id
            and task.change_contract.verification_profile_id != profile.id
        ):
            raise ScopeViolation(
                "verification profile differs from the Change Contract"
            )
        try:
            command = next(item for item in profile.commands if item.id == command_id)
        except StopIteration as exc:
            raise KeyError(f"verification command not found: {command_id}") from exc
        snapshot = await capture_diff(Path(task.worktree_path), task.base_commit)
        run, outputs = await self.verification_runner.run(
            project_id=project_id,
            task_id=task.id,
            profile_id=profile.id,
            command=command,
            worktree=Path(task.worktree_path),
            contract=task.change_contract,
            diff=snapshot,
        )
        await self.verification_runs.append(run, outputs)
        await self.code_tasks.update(
            task.model_copy(
                update={
                    "changed_files": snapshot.changed_files,
                    "diff_line_count": snapshot.diff_line_count,
                    "approval_state": (
                        ApprovalState.AWAITING_APPROVAL
                        if run.status is VerificationStatus.PASSED
                        and task.approval_state
                        in {
                            ApprovalState.DRAFT,
                            ApprovalState.REVISION_REQUESTED,
                            ApprovalState.AWAITING_APPROVAL,
                        }
                        else task.approval_state
                    ),
                    "updated_at": utc_now(),
                }
            )
        )
        await self._record_activity(
            project_id,
            "verification.completed",
            f"{command.name}: {run.status.value}",
            run.id,
        )
        return run

    async def request_code_revision(
        self,
        project_id: str,
        task_id: str,
        payload: ApprovalActionRequest,
    ) -> tuple[CodeTask, ApprovalRecord]:
        async with self._code_action_lock:
            task = await self._get_actionable_code_task(project_id, task_id)
            if task.approval_state is ApprovalState.APPLIED:
                raise ValueError("applied changes cannot return to revision state")
            record = ApprovalRecord(
                projectId=project_id,
                taskId=task.id,
                action=ApprovalAction.REQUEST_REVISION,
                actorId=payload.actor_id,
                actorType=payload.actor_type,
                reason=payload.reason,
                contentHash=task.approved_content_hash,
            )
            await self.approvals.append(record)
            updated = task.model_copy(
                update={
                    "approval_state": ApprovalState.REVISION_REQUESTED,
                    "approved_content_hash": None,
                    "updated_at": utc_now(),
                }
            )
            await self.code_tasks.update(updated)
            await self._record_activity(
                project_id,
                "codeTask.revisionRequested",
                "Revision requested",
                task.id,
            )
            return updated, record

    async def approve_code_changes(
        self,
        project_id: str,
        task_id: str,
        payload: ApprovalActionRequest,
    ) -> tuple[CodeTask, ApprovalRecord]:
        async with self._code_action_lock:
            task = await self._get_actionable_code_task(project_id, task_id)
            if task.approval_state is ApprovalState.APPLIED:
                raise ValueError("applied changes are already final on the task branch")
            if (
                task.change_contract.risk.value == "high"
                and payload.actor_type is not ApprovalActorType.HUMAN
            ):
                raise ScopeViolation("high-risk changes require human approval")
            snapshot = await capture_diff(Path(task.worktree_path), task.base_commit)
            self._validate_approval_snapshot(task, snapshot)
            content_hash = diff_content_hash(snapshot)
            matching_runs = await self._matching_verification_runs(
                task.id, content_hash
            )
            record = ApprovalRecord(
                projectId=project_id,
                taskId=task.id,
                action=ApprovalAction.APPROVE_CHANGES,
                actorId=payload.actor_id,
                actorType=payload.actor_type,
                reason=payload.reason,
                contentHash=content_hash,
                verificationRunIds=[run.id for run in matching_runs],
            )
            await self.approvals.append(record)
            updated = task.model_copy(
                update={
                    "approval_state": ApprovalState.APPROVED,
                    "approved_content_hash": content_hash,
                    "updated_at": utc_now(),
                }
            )
            await self.code_tasks.update(updated)
            await self._record_activity(
                project_id,
                "codeTask.approved",
                "Changes approved; no Git mutation performed",
                task.id,
            )
            return updated, record

    async def apply_code_changes(
        self,
        project_id: str,
        task_id: str,
        payload: ApplyChangesRequest,
    ) -> tuple[CodeTask, ApprovalRecord]:
        async with self._code_action_lock:
            task = await self._get_actionable_code_task(project_id, task_id)
            if task.approval_state is not ApprovalState.APPROVED:
                raise ValueError("changes must be approved before Apply to branch")
            snapshot = await capture_diff(Path(task.worktree_path), task.base_commit)
            self._validate_approval_snapshot(task, snapshot)
            content_hash = diff_content_hash(snapshot)
            if content_hash != task.approved_content_hash:
                raise ScopeViolation(
                    "approved content is stale; approve the current diff again"
                )
            matching_runs = await self._matching_verification_runs(
                task.id, content_hash
            )
            if not matching_runs:
                raise ScopeViolation(
                    "verification is missing or stale for the approved diff"
                )
            worktree = Path(task.worktree_path)
            await run_git(worktree, "add", "--all")
            await run_git(
                worktree,
                "-c",
                "user.name=OxyGent",
                "-c",
                "user.email=oxygent@localhost",
                "commit",
                "-m",
                payload.commit_message,
                timeout=60.0,
            )
            _, commit, _, _ = await run_git(worktree, "rev-parse", "HEAD")
            applied_commit = commit.strip()
            record = ApprovalRecord(
                projectId=project_id,
                taskId=task.id,
                action=ApprovalAction.APPLY_TO_BRANCH,
                actorId=payload.actor_id,
                actorType=payload.actor_type,
                reason=payload.reason,
                contentHash=content_hash,
                verificationRunIds=[run.id for run in matching_runs],
                appliedCommit=applied_commit,
            )
            await self.approvals.append(record)
            updated = task.model_copy(
                update={
                    "approval_state": ApprovalState.APPLIED,
                    "applied_commit": applied_commit,
                    "updated_at": utc_now(),
                }
            )
            await self.code_tasks.update(updated)
            await self._record_activity(
                project_id,
                "codeTask.appliedToBranch",
                f"Approved changes committed to {task.branch}",
                task.id,
            )
            return updated, record

    async def export_code_patch(
        self,
        project_id: str,
        task_id: str,
        payload: ApprovalActionRequest,
    ) -> tuple[RecoveryPatch, ApprovalRecord]:
        async with self._code_action_lock:
            task = await self._get_actionable_code_task(project_id, task_id)
            patch = await self._create_recovery_patch(task)
            record = ApprovalRecord(
                projectId=project_id,
                taskId=task.id,
                action=ApprovalAction.EXPORT_PATCH,
                actorId=payload.actor_id,
                actorType=payload.actor_type,
                reason=payload.reason,
                contentHash=patch.content_hash,
                recoveryPatchId=patch.id,
            )
            await self.approvals.append(record)
            await self._record_activity(
                project_id,
                "codeTask.patchExported",
                "Recovery patch exported",
                patch.id,
            )
            return patch, record

    async def discard_code_task(
        self,
        project_id: str,
        task_id: str,
        payload: DiscardChangesRequest,
    ) -> tuple[CodeTask, ApprovalRecord, RecoveryPatch]:
        async with self._code_action_lock:
            task = await self._get_actionable_code_task(project_id, task_id)
            patch = await self._create_recovery_patch(task)
            repository = await self.repositories.get(task.repository_id)
            if not self.worktrees:
                raise ValueError("Code Workspace is not configured")
            await self.worktrees.remove_worktree(repository, task)
            record = ApprovalRecord(
                projectId=project_id,
                taskId=task.id,
                action=ApprovalAction.DISCARD,
                actorId=payload.actor_id,
                actorType=payload.actor_type,
                reason=payload.reason,
                contentHash=patch.content_hash,
                recoveryPatchId=patch.id,
            )
            await self.approvals.append(record)
            now = utc_now()
            updated = task.model_copy(
                update={
                    "approval_state": ApprovalState.DISCARDED,
                    "recovery_patch_id": patch.id,
                    "discarded_at": now,
                    "updated_at": now,
                }
            )
            await self.code_tasks.update(updated)
            await self._record_activity(
                project_id,
                "codeTask.discarded",
                "Worktree discarded after recovery patch export",
                task.id,
            )
            return updated, record, patch

    async def get_recovery_patch(
        self, project_id: str, task_id: str, patch_id: str
    ) -> RecoveryPatch:
        patch = await self.recovery_patches.get(patch_id)
        if patch.project_id != project_id or patch.task_id != task_id:
            raise KeyError(f"recovery patch not found: {patch_id}")
        return patch

    async def _get_actionable_code_task(
        self, project_id: str, task_id: str
    ) -> CodeTask:
        task = await self.code_tasks.get(task_id)
        if task.project_id != project_id:
            raise KeyError(f"code task not found: {task_id}")
        if task.approval_state is ApprovalState.DISCARDED:
            raise ValueError("discarded Code Task is immutable")
        return task

    @staticmethod
    def _validate_approval_snapshot(task: CodeTask, snapshot: DiffSnapshot) -> None:
        if snapshot.truncated:
            raise ScopeViolation("truncated diff cannot be approved or applied")
        if not snapshot.changed_files:
            raise ValueError("Code Task has no changes")
        ScopeGuard.check_diff(
            task.change_contract,
            snapshot.changed_files,
            snapshot.diff_line_count,
        )

    async def _matching_verification_runs(
        self, task_id: str, content_hash: str
    ) -> list[VerificationRun]:
        return [
            run
            for run in await self.verification_runs.list(task_id)
            if run.status is VerificationStatus.PASSED
            and run.content_hash == content_hash
        ]

    async def _create_recovery_patch(self, task: CodeTask) -> RecoveryPatch:
        snapshot = await capture_diff(Path(task.worktree_path), task.base_commit)
        if snapshot.truncated:
            raise ScopeViolation("truncated diff cannot be exported as recovery patch")
        patch = RecoveryPatch(
            projectId=task.project_id,
            taskId=task.id,
            baseCommit=task.base_commit,
            contentHash=diff_content_hash(snapshot),
            content=snapshot.diff,
        )
        return await self.recovery_patches.append(patch)

    async def get_verification_output(
        self, project_id: str, task_id: str, output_id: str
    ) -> VerificationOutput:
        output = await self.verification_runs.get_output(output_id)
        if output.project_id != project_id or output.task_id != task_id:
            raise KeyError(f"verification output not found: {output_id}")
        return output

    async def list_artifacts(
        self, project_id: str, *, latest_only: bool = False
    ) -> list[ArtifactBase]:
        await self.projects.get(project_id)
        values = self.artifacts.list(project_id)
        if latest_only:
            superseded = {
                artifact.supersedes_artifact_id
                for artifact in values
                if artifact.supersedes_artifact_id
            }
            values = [artifact for artifact in values if artifact.id not in superseded]
        return sorted(values, key=lambda item: item.created_at, reverse=True)

    async def revise_artifact(
        self,
        project_id: str,
        artifact_id: str,
        content: dict[str, Any],
        *,
        producer_role: str | None = None,
        producer_agent: str | None = None,
        provider_id: str | None = None,
        model_id: str | None = None,
        validation_status: ValidationStatus = ValidationStatus.UNVALIDATED,
    ) -> ArtifactBase:
        await self.projects.get(project_id)
        artifact = self.artifacts.get(artifact_id)
        if artifact.project_id != project_id:
            raise KeyError(f"artifact not found: {artifact_id}")
        revision = self.artifacts.revise(
            artifact_id,
            content,
            producer_role=producer_role,
            producer_agent=producer_agent,
            provider_id=provider_id,
            model_id=model_id,
            validation_status=validation_status,
        )
        await self._touch_project(project_id)
        await self._record_activity(
            project_id,
            "artifact.revised",
            f"{revision.type.value} revision {revision.revision} created",
            revision.id,
        )
        return revision

    async def list_activity(self, project_id: str) -> list[ProjectActivity]:
        await self.projects.get(project_id)
        async with self._activity_lock:
            values = [
                item for item in self._activities if item.project_id == project_id
            ]
        return sorted(values, key=lambda item: item.created_at, reverse=True)

    def _validate_source_artifacts(
        self, project_id: str, artifact_ids: list[str]
    ) -> None:
        for artifact_id in artifact_ids:
            try:
                artifact = self.artifacts.get(artifact_id)
            except KeyError as exc:
                raise ValueError(f"source artifact not found: {artifact_id}") from exc
            if artifact.project_id != project_id:
                raise ValueError("source artifacts must belong to the target project")

    async def _touch_project(self, project_id: str) -> None:
        project = await self.projects.get(project_id)
        now = utc_now()
        await self.projects.update(
            project.model_copy(update={"updated_at": now, "last_activity_at": now})
        )

    async def _record_activity(
        self,
        project_id: str,
        event_type: str,
        summary: str,
        entity_id: str = "",
    ) -> None:
        activity = ProjectActivity(
            id=generate_uuid(),
            project_id=project_id,
            event_type=event_type,
            summary=summary,
            entity_id=entity_id,
        )
        async with self._activity_lock:
            self._activities.append(activity)
