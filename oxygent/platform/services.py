"""Application services for Project and Artifact product APIs."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import shutil
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import Field

from oxygent.schemas import EstimationMethod, OxyRequest, OxyState
from oxygent.utils.common_utils import generate_uuid
from oxygent.utils.token_utils import build_token_usage

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
from .code_stage import (
    CodeStageApproval,
    CodeStageApprovalRequest,
    CodeStageApprovalStatus,
    CodeStageReview,
    CodeStageReviewFinding,
    CodeStageReviewOverrideRequest,
    CodeStageReviewStatus,
    CodeStageRun,
    CodeStageRunRequest,
    CodeStageStatus,
    CodeStageVerificationBatch,
    CodeStageVerificationStatus,
    InMemoryCodeStageRunStore,
    InMemorySourceWorkspaceAnalysisStore,
    InMemorySourceWorkspaceStore,
    SourceWorkspaceAnalysis,
    SourceWorkspace,
    SourceWorkspaceManager,
    build_aider_command,
    detect_aider_protocol_artifacts,
    deterministic_aider_files,
    extract_aider_suggested_paths,
    expand_aider_retry_files,
    parse_aider_file_plan,
    parse_model_json_object,
    prepare_aider_editable_files,
    remove_empty_aider_placeholders,
    repository_file_list,
)
from .control_plane import PlatformControlPlane
from .credentials import default_credential_resolver
from .coding import (
    ApprovalState,
    ChangeContract,
    CodeTask,
    CodeTaskCreate,
    CodeWorkspaceError,
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
    normalize_relative_path,
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
from .provider_adapters import ModelRequest
from .routing import ModelRouter
from .tracing import (
    EngineeringStatus,
    ExecutionTrace,
    WorkflowEvent,
    WorkflowPhase,
)
from .usage import InvocationStatus, ModelUsage
from .verification import (
    DiffSnapshot,
    InMemoryVerificationProfileStore,
    InMemoryVerificationRunStore,
    VerificationCommand,
    VerificationFailureCategory,
    VerificationOutput,
    VerificationProfile,
    VerificationProfileCreate,
    VerificationRun,
    VerificationRunner,
    VerificationSlot,
    VerificationStatus,
    capture_diff,
    classify_verification_failure,
    diff_content_hash,
)
from .workflow_runtime import (
    WorkflowExecutionRequest,
    WorkflowExecutor,
    WorkflowLaunchRequest,
)

_PROJECT_TOOLCHAIN = Path(__file__).resolve().parents[2] / ".toolchain"


def _bounded_environment_float(
    name: str, default: float, *, minimum: float, maximum: float
) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except ValueError:
        value = default
    return min(maximum, max(minimum, value))


def _aider_provider_timeout(provider_timeout: float) -> float:
    configured = _bounded_environment_float(
        "OXYGENT_AIDER_PROVIDER_TIMEOUT_SECONDS",
        300.0,
        minimum=60.0,
        maximum=600.0,
    )
    return min(600.0, max(provider_timeout, configured))


def _verification_executable(name: str) -> str | None:
    """Resolve host tooling, including the project-local no-sudo toolchain."""

    override = os.environ.get(f"OXYGENT_{name.upper()}_EXECUTABLE", "").strip()
    if override:
        candidate = Path(override).expanduser().resolve()
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    executable = shutil.which(name)
    if executable:
        return executable
    local = _PROJECT_TOOLCHAIN / "bin" / name
    if local.is_file() and os.access(local, os.X_OK):
        return str(local)
    return None


class ProjectActivity(PlatformModel):
    id: str
    project_id: str
    event_type: str
    summary: str
    entity_id: str = ""
    created_at: datetime = Field(default_factory=utc_now)


def _string_list(value: Any, *, limit: int = 100) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = str(item).strip()[:1000]
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


@dataclass
class PlatformServices:
    """Non-global service container passed explicitly to the FastAPI router."""

    projects: ProjectRepository = field(default_factory=InMemoryProjectRepository)
    tasks: ProjectTaskRepository = field(default_factory=InMemoryProjectTaskRepository)
    artifacts: InMemoryArtifactStore = field(default_factory=InMemoryArtifactStore)
    control_plane: PlatformControlPlane = field(default_factory=PlatformControlPlane)
    workflow_executor: WorkflowExecutor | None = None
    repositories: InMemoryRepositoryProfileStore = field(
        default_factory=InMemoryRepositoryProfileStore
    )
    code_tasks: InMemoryCodeTaskStore = field(default_factory=InMemoryCodeTaskStore)
    coding_engine: CodingEngine = field(default_factory=NativeCodingEngine)
    worktrees: WorktreeManager | None = None
    code_authorization_enabled: bool = False
    aider_proxy_base_url: str = ""
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
    source_workspaces: InMemorySourceWorkspaceStore = field(
        default_factory=InMemorySourceWorkspaceStore
    )
    source_analyses: InMemorySourceWorkspaceAnalysisStore = field(
        default_factory=InMemorySourceWorkspaceAnalysisStore
    )
    code_stage_runs: InMemoryCodeStageRunStore = field(
        default_factory=InMemoryCodeStageRunStore
    )
    source_workspace_manager: SourceWorkspaceManager = field(
        default_factory=lambda: SourceWorkspaceManager(
            Path("/tmp/oxygent-project-sources")
        )
    )
    _activities: list[ProjectActivity] = field(default_factory=list, init=False)
    _activity_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    _code_action_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    _workflow_run_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    _workflow_tasks: dict[str, asyncio.Task[None]] = field(
        default_factory=dict, init=False
    )
    _code_stage_tasks: dict[str, asyncio.Task[None]] = field(
        default_factory=dict, init=False
    )
    _code_stage_verifications: dict[str, CodeStageVerificationBatch] = field(
        default_factory=dict, init=False
    )
    _code_stage_reviews: dict[str, CodeStageReview] = field(
        default_factory=dict, init=False
    )
    _code_stage_approvals: dict[str, CodeStageApproval] = field(
        default_factory=dict, init=False
    )
    _code_stage_lifecycle_lock: asyncio.Lock = field(
        default_factory=asyncio.Lock, init=False
    )

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
            source_workspace_manager=SourceWorkspaceManager(
                workspace_root.parent / "oxygent-project-sources"
            ),
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

    @property
    def workflow_execution_configured(self) -> bool:
        return self.workflow_executor is not None

    async def create_project(self, payload: ProjectCreate) -> Project:
        project = Project(**payload.model_dump())
        await self.projects.create(project)
        await self._record_activity(project.id, "project.created", "Project created")
        return project

    async def import_source_workspace(
        self,
        project_id: str,
        name: str,
        files: list[tuple[str, bytes]],
    ) -> SourceWorkspace:
        """Import a browser-selected folder into a managed project copy."""
        await self.projects.get(project_id)
        workspace = await asyncio.to_thread(
            self.source_workspace_manager.create, project_id, name, files
        )
        await self.source_workspaces.create(workspace)
        await self._touch_project(project_id)
        await self._record_activity(
            project_id,
            "code.sourceImported",
            f"项目源码已导入：{workspace.name}（{workspace.file_count} 个文件）",
            workspace.id,
        )
        return workspace

    def _role_model(self, role_id: str) -> tuple[Any, Any, Any]:
        profiles = [
            item for item in self.control_plane.agents.list() if item.role_id == role_id
        ]
        if not profiles:
            raise CodeWorkspaceError(f"{role_id} Agent is not configured")
        profile = profiles[0]
        policy = self.control_plane.model_policies.get(profile.model_policy_id)
        if not policy.primary_model_ids:
            raise CodeWorkspaceError(f"{role_id} model policy has no primary model")
        model = self.control_plane.models.get(policy.primary_model_ids[0])
        provider = self.control_plane.providers.get(model.provider_id)
        return profile, provider, model

    async def analyze_source_workspace(
        self, project_id: str, source_workspace_id: str
    ) -> SourceWorkspaceAnalysis:
        """Use the Product Manager model to understand an imported project."""

        await self.projects.get(project_id)
        source = await self.source_workspaces.get(source_workspace_id)
        if source.project_id != project_id:
            raise ValueError("source workspace must belong to the target project")
        if source.file_count == 0:
            raise ValueError("an existing project analysis requires uploaded files")
        profile, provider, model = self._role_model("product_manager")
        context = await asyncio.to_thread(
            self.source_workspace_manager.build_analysis_context, source
        )
        run_id = generate_uuid()
        task_id = generate_uuid()
        started = time.perf_counter()
        adapter = self.control_plane.adapters.get(provider.provider_type)
        try:
            response = await adapter.complete(
                ModelRequest(
                    provider=provider,
                    model=model,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You are a Product Manager analyzing an existing software "
                                "project before requirements work. Use only the supplied file "
                                "tree and excerpts. Return JSON only with: summary, "
                                "projectType, technologies, architecture, mainFeatures, "
                                "keyFiles, risks, suggestedFocus. Array fields must contain "
                                "short factual strings. Do not claim files or behavior that "
                                "are not evidenced by the supplied project snapshot. Write "
                                "all user-facing values in Simplified Chinese."
                            ),
                        },
                        {"role": "user", "content": context},
                    ],
                    parameters={"max_output_tokens": 4000},
                )
            )
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - started) * 1000
            self.control_plane.usage.append(
                ModelUsage(
                    projectId=project_id,
                    taskId=task_id,
                    runId=run_id,
                    roleId="product_manager",
                    agentId=profile.id,
                    providerId=provider.id,
                    modelId=model.id,
                    latencyMs=elapsed_ms,
                    status=InvocationStatus.FAILED,
                    failureReason=type(exc).__name__,
                )
            )
            raise CodeWorkspaceError(
                f"project analysis failed: {type(exc).__name__}"
            ) from exc
        payload = parse_model_json_object(response.output)
        analysis = SourceWorkspaceAnalysis(
            projectId=project_id,
            sourceWorkspaceId=source.id,
            summary=str(payload.get("summary") or "").strip(),
            projectType=str(payload.get("projectType") or "").strip(),
            technologies=_string_list(payload.get("technologies")),
            architecture=_string_list(payload.get("architecture")),
            mainFeatures=_string_list(payload.get("mainFeatures")),
            keyFiles=_string_list(payload.get("keyFiles")),
            risks=_string_list(payload.get("risks")),
            suggestedFocus=_string_list(payload.get("suggestedFocus")),
            providerId=provider.id,
            modelId=model.id,
        )
        await self.source_analyses.create(analysis)
        self.control_plane.usage.append(
            ModelUsage(
                projectId=project_id,
                taskId=task_id,
                runId=run_id,
                roleId="product_manager",
                agentId=profile.id,
                providerId=provider.id,
                modelId=model.id,
                inputTokens=response.input_tokens,
                outputTokens=response.output_tokens,
                tokenCountMethod=response.token_count_method,
                invocationType="project-analysis",
                latencyMs=response.latency_ms,
                costAvailable=False,
                status=InvocationStatus.SUCCEEDED,
            )
        )
        self.control_plane.traces.append_event(
            ExecutionTrace(
                id=generate_uuid(),
                projectId=project_id,
                taskId=task_id,
                runId=run_id,
                roleId="product_manager",
                agentId=profile.id,
                eventType="source_workspace_analysis",
                status="succeeded",
                providerId=provider.id,
                modelId=model.id,
                details={"sourceWorkspaceId": source.id, "analysisId": analysis.id},
            )
        )
        await self._touch_project(project_id)
        await self._record_activity(
            project_id,
            "project.sourceAnalyzed",
            f"智能体已完成现有项目分析：{source.name}",
            analysis.id,
        )
        return analysis

    async def start_code_stage(
        self, project_id: str, payload: CodeStageRunRequest
    ) -> CodeStageRun:
        """Queue the implementation phase; Aider runs against the managed copy."""
        await self.projects.get(project_id)
        source = await self.source_workspaces.get(payload.source_workspace_id)
        if source.project_id != project_id:
            raise ValueError("source workspace must belong to the target project")
        parent_run: CodeStageRun | None = None
        if payload.parent_run_id:
            parent_run = await self.code_stage_runs.get(payload.parent_run_id)
            if (
                parent_run.project_id != project_id
                or parent_run.source_workspace_id != source.id
            ):
                raise ValueError(
                    "parent code run must belong to the selected project source"
                )
            if parent_run.status is not CodeStageStatus.COMPLETED:
                raise ValueError("only a completed code run can be revised")
        workflow_run_id = payload.workflow_run_id
        if not workflow_run_id and parent_run:
            workflow_run_id = parent_run.workflow_run_id
        if not workflow_run_id:
            runs = self.control_plane.traces.workflow_runs(project_id=project_id)
            workflow_run_id = runs[0].run_id if runs else None
        run = CodeStageRun(
            projectId=project_id,
            sourceWorkspaceId=source.id,
            workflowRunId=workflow_run_id,
            parentRunId=parent_run.id if parent_run else None,
            instructions=payload.instructions,
        )
        await self.code_stage_runs.create(run)
        task = asyncio.create_task(self._execute_code_stage(run.id))
        self._code_stage_tasks[run.id] = task
        task.add_done_callback(lambda _task: self._code_stage_tasks.pop(run.id, None))
        await self._record_activity(
            project_id, "code.implementationQueued", "代码实现已进入执行队列", run.id
        )
        return run

    async def get_code_stage_changes(
        self, project_id: str, run_id: str
    ) -> DiffSnapshot:
        """Return the bounded diff produced by one implementation revision."""
        run = await self.code_stage_runs.get(run_id)
        if run.project_id != project_id:
            raise KeyError(f"code stage run not found: {run_id}")
        if (
            run.status is not CodeStageStatus.COMPLETED
            or not run.run_path
            or not run.base_commit
        ):
            raise CodeWorkspaceError("code changes are not ready for preview")
        return await capture_diff(Path(run.run_path), run.base_commit)

    async def get_code_stage_file_change(
        self,
        project_id: str,
        run_id: str,
        relative_path: str,
        *,
        max_bytes: int = 512_000,
    ) -> dict[str, Any]:
        """Return before/after text for a changed file without forcing a download."""
        run = await self.code_stage_runs.get(run_id)
        if run.project_id != project_id:
            raise KeyError(f"code stage run not found: {run_id}")
        if (
            run.status is not CodeStageStatus.COMPLETED
            or not run.run_path
            or not run.base_commit
        ):
            raise CodeWorkspaceError("code changes are not ready for preview")
        path = normalize_relative_path(relative_path)
        if path not in run.changed_files:
            raise CodeWorkspaceError("only generated or changed files can be previewed")
        root = Path(run.run_path).resolve(strict=True)
        target = (root / path).resolve()
        if not target.is_relative_to(root):
            raise CodeWorkspaceError("generated file escaped its managed workspace")

        before_code, before_text, _before_error, before_truncated = await run_git(
            root,
            "show",
            f"{run.base_commit}:{path}",
            output_limit=max_bytes,
            allowed_exit_codes={0, 128},
        )
        before_exists = before_code == 0
        after_exists = target.is_file()
        if after_exists:
            with target.open("rb") as stream:
                after_bytes = stream.read(max_bytes + 1)
        else:
            after_bytes = b""
        after_truncated = len(after_bytes) > max_bytes
        after_bytes = after_bytes[:max_bytes]
        binary = "\x00" in before_text or b"\x00" in after_bytes
        if not before_exists and after_exists:
            change_type = "added"
        elif before_exists and not after_exists:
            change_type = "deleted"
        else:
            change_type = "modified"
        return {
            "path": path,
            "changeType": change_type,
            "binary": binary,
            "beforeContent": "" if binary or not before_exists else before_text,
            "afterContent": (
                ""
                if binary or not after_exists
                else after_bytes.decode("utf-8", errors="replace")
            ),
            "beforeTruncated": before_truncated,
            "afterTruncated": after_truncated,
        }

    async def get_code_stage_verification(
        self, project_id: str, run_id: str
    ) -> tuple[CodeStageVerificationBatch | None, list[VerificationRun]]:
        run = await self.code_stage_runs.get(run_id)
        if run.project_id != project_id:
            raise KeyError(f"code stage run not found: {run_id}")
        batch = self._code_stage_verifications.get(run_id)
        command_runs = await self.verification_runs.list(run_id)
        command_runs = [
            item.model_copy(
                update={
                    "failure_category": classify_verification_failure(
                        status=item.status,
                        stdout=item.stdout_preview,
                        stderr=item.stderr_preview,
                        failure_reason=item.failure_reason or "",
                    )
                }
            )
            if item.failure_category is None
            else item
            for item in command_runs
        ]
        return batch, command_runs

    async def get_code_stage_review(
        self, project_id: str, run_id: str
    ) -> CodeStageReview | None:
        run = await self.code_stage_runs.get(run_id)
        if run.project_id != project_id:
            raise KeyError(f"code stage run not found: {run_id}")
        return self._code_stage_reviews.get(run_id)

    async def get_code_stage_approval(
        self, project_id: str, run_id: str
    ) -> CodeStageApproval | None:
        run = await self.code_stage_runs.get(run_id)
        if run.project_id != project_id:
            raise KeyError(f"code stage run not found: {run_id}")
        return self._code_stage_approvals.get(run_id)

    @staticmethod
    def _code_stage_verification_commands(root: Path) -> list[VerificationCommand]:
        files = repository_file_list(root, limit=3000)
        verification_environment = [
            "HOME",
            "TMPDIR",
            "NPM_CONFIG_CACHE",
            "MAVEN_OPTS",
            "JAVA_HOME",
        ]
        commands = [
            VerificationCommand(
                id="project-files",
                name="项目文件检查",
                slot=VerificationSlot.COMPILE,
                argv=[
                    sys.executable,
                    "-c",
                    (
                        "from pathlib import Path; "
                        "files=[p for p in Path('.').rglob('*') if p.is_file() "
                        "and '.git' not in p.parts and '.oxygent' not in p.parts]; "
                        "assert files, 'project contains no files'; "
                        "print(f'{len(files)} project files are readable')"
                    ),
                ],
                timeoutSeconds=30,
            )
        ]
        python_files = [path for path in files if path.endswith(".py")]
        if python_files:
            commands.append(
                VerificationCommand(
                    id="python-compile",
                    name="Python 语法与编译检查",
                    slot=VerificationSlot.COMPILE,
                    argv=[sys.executable, "-m", "compileall", "-q", "."],
                    timeoutSeconds=120,
                )
            )
        test_files = [
            path
            for path in python_files
            if path.startswith("tests/")
            or Path(path).name.startswith("test_")
            or Path(path).name.endswith("_test.py")
        ]
        if test_files:
            commands.append(
                VerificationCommand(
                    id="python-tests",
                    name="Python 单元测试",
                    slot=VerificationSlot.UNIT,
                    argv=[sys.executable, "-m", "pytest", "-q"],
                    timeoutSeconds=300,
                )
            )
        for index, path in enumerate(sorted(root.rglob("pom.xml")), start=1):
            relative_directory = path.parent.relative_to(root).as_posix() or "."
            maven = _verification_executable("mvn")
            if maven:
                commands.append(
                    VerificationCommand(
                        id=f"maven-build-{index}",
                        name=f"Maven 后端构建与测试：{relative_directory}",
                        slot=VerificationSlot.BUILD,
                        argv=[
                            maven,
                            "--batch-mode",
                            "--no-transfer-progress",
                            "-Dmaven.wagon.http.retryHandler.count=3",
                            "-q",
                            "package",
                        ],
                        workingDirectory=relative_directory,
                        allowedEnvironment=verification_environment,
                        timeoutSeconds=600,
                    )
                )
            else:
                commands.append(
                    VerificationCommand(
                        id=f"maven-missing-{index}",
                        name=f"Maven 后端构建与测试：{relative_directory}",
                        slot=VerificationSlot.BUILD,
                        argv=[
                            sys.executable,
                            "-c",
                            (
                                "import sys; print('无法运行 Maven 项目：宿主环境未安装 "
                                "mvn/JDK，验证未通过。', file=sys.stderr); sys.exit(2)"
                            ),
                        ],
                        workingDirectory=relative_directory,
                        timeoutSeconds=30,
                    )
                )
        package_files = [
            path
            for path in sorted(root.rglob("package.json"))
            if "node_modules" not in path.parts
        ]
        for index, path in enumerate(package_files, start=1):
            relative_directory = path.parent.relative_to(root).as_posix() or "."
            npm = _verification_executable("npm")
            if npm:
                if (path.parent / "package-lock.json").is_file():
                    commands.append(
                        VerificationCommand(
                            id=f"npm-install-{index}",
                            name=f"前端依赖安装：{relative_directory}",
                            slot=VerificationSlot.BUILD,
                            argv=[
                                npm,
                                "ci",
                                "--ignore-scripts",
                                "--no-audit",
                                "--no-fund",
                            ],
                            workingDirectory=relative_directory,
                            allowedEnvironment=verification_environment,
                            timeoutSeconds=600,
                        )
                    )
                commands.append(
                    VerificationCommand(
                        id=f"npm-build-{index}",
                        name=f"前端真实构建：{relative_directory}",
                        slot=VerificationSlot.BUILD,
                        argv=[npm, "run", "build", "--if-present"],
                        workingDirectory=relative_directory,
                        allowedEnvironment=verification_environment,
                        timeoutSeconds=300,
                    )
                )
            else:
                commands.append(
                    VerificationCommand(
                        id=f"npm-missing-{index}",
                        name=f"前端真实构建：{relative_directory}",
                        slot=VerificationSlot.BUILD,
                        argv=[
                            sys.executable,
                            "-c",
                            (
                                "import sys; print('无法运行前端项目：宿主环境未安装 "
                                "Node.js/npm，验证未通过。', file=sys.stderr); sys.exit(2)"
                            ),
                        ],
                        workingDirectory=relative_directory,
                        timeoutSeconds=30,
                    )
                )
        return commands

    def _verification_cache_root(self, project_id: str) -> Path:
        """Return a persistent cache outside disposable verification sandboxes."""

        cache_root = (
            self.source_workspace_manager.root / ".verification-cache" / project_id
        ).resolve()
        if not cache_root.is_relative_to(self.source_workspace_manager.root):
            raise CodeWorkspaceError("verification cache escaped its managed root")
        return cache_root

    @staticmethod
    def _clear_incomplete_maven_downloads(repository: Path) -> None:
        """Remove only Maven transfer markers, preserving valid cached artifacts."""

        if not repository.is_dir():
            return
        for pattern in ("*.lastUpdated", "*.part", "*.tmp"):
            for path in repository.rglob(pattern):
                try:
                    path.unlink()
                except OSError:
                    continue

    async def run_code_stage_verification(
        self, project_id: str, run_id: str
    ) -> tuple[CodeStageVerificationBatch, list[VerificationRun]]:
        async with self._code_stage_lifecycle_lock:
            run = await self.code_stage_runs.get(run_id)
            if run.project_id != project_id:
                raise KeyError(f"code stage run not found: {run_id}")
            if run.status is not CodeStageStatus.COMPLETED or not run.run_path:
                raise CodeWorkspaceError("code must be completed before verification")
            snapshot = await self.get_code_stage_changes(project_id, run_id)
            content_hash = diff_content_hash(snapshot)
            batch = CodeStageVerificationBatch(
                projectId=project_id,
                codeStageRunId=run.id,
                status=CodeStageVerificationStatus.RUNNING,
                contentHash=content_hash,
            )
            self._code_stage_verifications[run.id] = batch
            self._code_stage_reviews.pop(run.id, None)
            self._code_stage_approvals.pop(run.id, None)
            sandbox = (
                self.source_workspace_manager.root
                / project_id
                / "verification"
                / batch.id
            ).resolve()
            source_root = Path(run.run_path).resolve(strict=True)
            sandbox.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(
                shutil.copytree,
                source_root,
                sandbox,
                ignore=shutil.ignore_patterns(
                    ".git", ".oxygent", ".aider*", "__pycache__"
                ),
            )
            verification_home = sandbox / ".verification-home"
            verification_tmp = sandbox / ".verification-tmp"
            cache_root = self._verification_cache_root(project_id)
            maven_repository = cache_root / "maven" / "repository"
            npm_cache = cache_root / "npm"
            verification_home.mkdir()
            verification_tmp.mkdir()
            maven_repository.mkdir(parents=True, exist_ok=True)
            npm_cache.mkdir(parents=True, exist_ok=True)
            commands = self._code_stage_verification_commands(sandbox)
            local_toolchain_bin = _PROJECT_TOOLCHAIN / "bin"
            inherited_path = os.environ.get("PATH", "")
            verification_path = (
                f"{local_toolchain_bin}{os.pathsep}{inherited_path}"
                if local_toolchain_bin.is_dir()
                else inherited_path
            )
            local_java_home = _PROJECT_TOOLCHAIN / "jdk"
            runner = VerificationRunner(
                allowed_executables={command.argv[0] for command in commands},
                environment={
                    "PATH": verification_path,
                    "LANG": os.environ.get("LANG", "en_US.UTF-8"),
                    "HOME": str(verification_home),
                    "TMPDIR": str(verification_tmp),
                    "NPM_CONFIG_CACHE": str(npm_cache),
                    "MAVEN_OPTS": (f"-Dmaven.repo.local={maven_repository}"),
                    "JAVA_HOME": (
                        str(local_java_home)
                        if (local_java_home / "bin" / "java").is_file()
                        else os.environ.get("JAVA_HOME", "")
                    ),
                },
            )
            contract = ChangeContract(
                objective="Verify the generated Code Stage result",
                acceptanceCriteria=["All selected checks return exit code 0"],
                allowedPaths=["**"],
                dependencyChangesAllowed=True,
                maxChangedFiles=min(1000, max(1, len(snapshot.changed_files) + 100)),
                maxDiffLines=min(100_000, max(1, snapshot.diff_line_count + 10_000)),
            )
            results: list[VerificationRun] = []
            try:
                for command in commands:
                    command_run, outputs = await runner.run(
                        project_id=project_id,
                        task_id=run.id,
                        profile_id="code-stage-auto",
                        command=command,
                        worktree=sandbox,
                        contract=contract,
                        diff=snapshot,
                    )
                    if (
                        command.id.startswith("maven-build-")
                        and command_run.failure_category
                        is VerificationFailureCategory.INFRASTRUCTURE
                    ):
                        await asyncio.to_thread(
                            self._clear_incomplete_maven_downloads,
                            maven_repository,
                        )
                        command_run, outputs = await runner.run(
                            project_id=project_id,
                            task_id=run.id,
                            profile_id="code-stage-auto",
                            command=command,
                            worktree=sandbox,
                            contract=contract,
                            diff=snapshot,
                        )
                        command_run = command_run.model_copy(
                            update={
                                "attempt_count": 2,
                                "automatic_retry_reason": (
                                    "Maven dependency transfer failed; incomplete "
                                    "cache markers were cleared before retry"
                                ),
                            }
                        )
                    await self.verification_runs.append(command_run, outputs)
                    results.append(command_run)
            finally:
                await asyncio.to_thread(shutil.rmtree, sandbox, True)
            passed = bool(results) and all(
                item.status is VerificationStatus.PASSED for item in results
            )
            completed = batch.model_copy(
                update={
                    "status": (
                        CodeStageVerificationStatus.PASSED
                        if passed
                        else CodeStageVerificationStatus.FAILED
                    ),
                    "command_run_ids": [item.id for item in results],
                    "updated_at": utc_now(),
                }
            )
            self._code_stage_verifications[run.id] = completed
            if run.workflow_run_id:
                self.control_plane.traces.append_workflow_event(
                    WorkflowEvent(
                        eventId=generate_uuid(),
                        projectId=project_id,
                        taskId=run.task_id,
                        runId=run.workflow_run_id,
                        agentId="verification-runner",
                        role="qa",
                        phase=WorkflowPhase.VERIFICATION,
                        eventType="code.verificationCompleted",
                        payload={
                            "status": (
                                EngineeringStatus.COMPLETED.value
                                if passed
                                else EngineeringStatus.FAILED.value
                            ),
                            "summary": (
                                "代码验证全部通过。"
                                if passed
                                else "代码验证存在失败项，请查看真实退出码。"
                            ),
                            "toolsUsed": ["FixedArgvVerificationRunner"],
                        },
                    )
                )
            await self._record_activity(
                project_id,
                "code.verificationCompleted",
                "代码验证通过" if passed else "代码验证失败",
                run.id,
            )
            return completed, results

    async def _code_stage_run_chain(self, run: CodeStageRun) -> list[CodeStageRun]:
        chain = [run]
        seen = {run.id}
        current = run
        while current.parent_run_id:
            parent = await self.code_stage_runs.get(current.parent_run_id)
            if parent.id in seen or parent.project_id != run.project_id:
                raise CodeWorkspaceError("invalid code revision ancestry")
            seen.add(parent.id)
            chain.append(parent)
            current = parent
        chain.reverse()
        return chain

    async def _code_stage_review_change_context(
        self, run: CodeStageRun, *, limit: int = 100_000
    ) -> tuple[str, str]:
        """Return the root objective and cumulative revision diffs for review."""
        chain = await self._code_stage_run_chain(run)
        chunks: list[str] = []
        used = 0
        for index, item in enumerate(chain, start=1):
            snapshot = await self.get_code_stage_changes(item.project_id, item.id)
            header = (
                f"\n## Implementation round {index}/{len(chain)}\n"
                f"Request: {item.instructions}\n"
                f"Changed files: {', '.join(snapshot.changed_files) or 'none'}\n"
            )
            chunk = header + snapshot.diff
            remaining = limit - used
            if remaining <= 0:
                break
            chunks.append(chunk[:remaining])
            used += len(chunks[-1])
        return chain[0].instructions, "".join(chunks)

    async def review_code_stage(self, project_id: str, run_id: str) -> CodeStageReview:
        async with self._code_stage_lifecycle_lock:
            run = await self.code_stage_runs.get(run_id)
            if run.project_id != project_id:
                raise KeyError(f"code stage run not found: {run_id}")
            verification = self._code_stage_verifications.get(run.id)
            snapshot = await self.get_code_stage_changes(project_id, run.id)
            content_hash = diff_content_hash(snapshot)
            if (
                verification is None
                or verification.status is not CodeStageVerificationStatus.PASSED
                or verification.content_hash != content_hash
            ):
                raise ScopeViolation(
                    "fresh successful verification is required before review"
                )
            profile, provider, model = self._role_model("reviewer")
            (
                root_objective,
                cumulative_changes,
            ) = await self._code_stage_review_change_context(run)
            command_runs = await self.verification_runs.list(run.id)
            verification_text = "\n".join(
                f"- {item.command_name}: {item.status.value}, exit={item.exit_code}; "
                f"stdout={item.stdout_preview[-2000:]}; stderr={item.stderr_preview[-2000:]}"
                for item in reversed(command_runs)
            )
            artifact_context = "\n\n".join(
                f"{artifact.type.value}: {artifact.content.model_dump_json(by_alias=True)[:6000]}"
                for artifact in self.artifacts.list(project_id)[-8:]
                if artifact.type.value
                in {"RequirementSpec", "ArchitectureDecision", "TaskGraph"}
            )
            adapter = self.control_plane.adapters.get(provider.provider_type)
            started = time.perf_counter()
            try:
                response = await adapter.complete(
                    ModelRequest(
                        provider=provider,
                        model=model,
                        messages=[
                            {
                                "role": "system",
                                "content": (
                                    "Review the implemented code against the explicit current "
                                    "root objective, the final cumulative implementation, and "
                                    "real verification results. A revision request describes "
                                    "corrections, but it does not replace the root objective. "
                                    "Prior project artifacts are supporting context only: do "
                                    "not demand unrelated features solely because they appear "
                                    "in an older or broader artifact. A passed verification is "
                                    "valid evidence; never demand that a failure must exist. "
                                    "Judge the final cumulative result instead of requiring every "
                                    "revision round to modify product code. Classify the result "
                                    "using exactly one verdict: pass when all material requirements "
                                    "are met and no blocking issue remains; basicallyQualified when "
                                    "the core objective works and only non-blocking low/medium "
                                    "improvements remain; changesRequested when verification fails, "
                                    "a material requirement is unmet, or a high/critical risk remains. "
                                    "Do not use changesRequested merely because optional improvements "
                                    "are possible. For pass and basicallyQualified, requiredChanges "
                                    "must be empty. Return JSON only with keys verdict (pass, "
                                    "basicallyQualified, or changesRequested), summary (string), "
                                    "findings (array of "
                                    "{severity,message}), requiredChanges (array of strings). "
                                    "Do not claim tests passed beyond supplied exit codes."
                                ),
                            },
                            {
                                "role": "user",
                                "content": (
                                    f"Root implementation objective:\n{root_objective}\n\n"
                                    f"Current revision request:\n{run.instructions}\n\n"
                                    f"Artifacts:\n{artifact_context or 'None'}\n\n"
                                    f"Verification:\n{verification_text}\n\n"
                                    f"Cumulative implementation changes:\n{cumulative_changes}"
                                ),
                            },
                        ],
                        parameters={"max_output_tokens": 4000},
                    )
                )
            except Exception as exc:
                self._append_model_usage(
                    project_id=project_id,
                    task_id=run.task_id,
                    run_id=run.id,
                    role_id="reviewer",
                    agent_id=profile.id,
                    provider=provider,
                    model=model,
                    response=None,
                    latency_ms=(time.perf_counter() - started) * 1000,
                    invocation_type="code-review",
                    status=InvocationStatus.FAILED,
                    failure_reason=type(exc).__name__,
                )
                raise CodeWorkspaceError(
                    f"code review failed: {type(exc).__name__}"
                ) from exc
            self._append_model_usage(
                project_id=project_id,
                task_id=run.task_id,
                run_id=run.id,
                role_id="reviewer",
                agent_id=profile.id,
                provider=provider,
                model=model,
                response=response,
                latency_ms=response.latency_ms,
                invocation_type="code-review",
            )
            payload = parse_model_json_object(response.output)
            findings = []
            finding_items = payload.get("findings", [])
            if not isinstance(finding_items, list):
                finding_items = []
            for item in finding_items:
                if (
                    not isinstance(item, dict)
                    or not str(item.get("message", "")).strip()
                ):
                    continue
                findings.append(
                    CodeStageReviewFinding(
                        severity=str(item.get("severity") or "info")[:40],
                        message=str(item["message"])[:4000],
                    )
                )
            required_change_items = payload.get("requiredChanges", [])
            if not isinstance(required_change_items, list):
                required_change_items = []
            required_changes = [
                str(item)[:4000] for item in required_change_items if str(item).strip()
            ][:100]
            blocking_finding = any(
                item.severity.strip().lower() in {"high", "critical", "blocker"}
                for item in findings
            )
            actionable_finding = any(
                item.severity.strip().lower() not in {"info", "note"}
                for item in findings
            )
            verdict = str(payload.get("verdict") or "").strip().lower()
            if verdict in {"pass", "approved", "fullyapproved"}:
                review_status = CodeStageReviewStatus.APPROVED
            elif verdict in {
                "basicallyqualified",
                "basic",
                "conditional",
                "conditionallyapproved",
            }:
                review_status = CodeStageReviewStatus.BASICALLY_QUALIFIED
            elif verdict in {"changesrequested", "changes", "reject", "rejected"}:
                review_status = CodeStageReviewStatus.CHANGES_REQUESTED
            elif payload.get("approved") is True:
                review_status = CodeStageReviewStatus.APPROVED
            elif not required_changes and not blocking_finding:
                review_status = CodeStageReviewStatus.BASICALLY_QUALIFIED
            else:
                review_status = CodeStageReviewStatus.CHANGES_REQUESTED
            if required_changes or blocking_finding:
                review_status = CodeStageReviewStatus.CHANGES_REQUESTED
            elif review_status is CodeStageReviewStatus.APPROVED and actionable_finding:
                review_status = CodeStageReviewStatus.BASICALLY_QUALIFIED
            approved = review_status is CodeStageReviewStatus.APPROVED
            review = CodeStageReview(
                projectId=project_id,
                codeStageRunId=run.id,
                status=review_status,
                approved=approved,
                summary=str(payload.get("summary") or "")[:8000],
                findings=findings,
                requiredChanges=required_changes,
                providerId=provider.id,
                modelId=model.id,
                contentHash=content_hash,
            )
            self._code_stage_reviews[run.id] = review
            if run.workflow_run_id:
                self.control_plane.traces.append_workflow_event(
                    WorkflowEvent(
                        eventId=generate_uuid(),
                        projectId=project_id,
                        taskId=run.task_id,
                        runId=run.workflow_run_id,
                        agentId=profile.id,
                        role="reviewer",
                        providerId=provider.id,
                        modelId=model.id,
                        phase=WorkflowPhase.REVIEW,
                        eventType="code.reviewCompleted",
                        payload={
                            "status": (
                                EngineeringStatus.COMPLETED.value
                                if review_status
                                in {
                                    CodeStageReviewStatus.APPROVED,
                                    CodeStageReviewStatus.BASICALLY_QUALIFIED,
                                }
                                else EngineeringStatus.BLOCKED.value
                            ),
                            "summary": review.summary or "代码审查已完成。",
                        },
                    )
                )
            await self._record_activity(
                project_id,
                "code.reviewCompleted",
                (
                    "代码审查完全通过"
                    if review_status is CodeStageReviewStatus.APPROVED
                    else "代码审查基本合格"
                    if review_status is CodeStageReviewStatus.BASICALLY_QUALIFIED
                    else "代码审查要求修改"
                ),
                run.id,
            )
            return review

    async def override_code_stage_review(
        self,
        project_id: str,
        run_id: str,
        payload: CodeStageReviewOverrideRequest,
    ) -> CodeStageReview:
        """Record a human decision to proceed despite Reviewer change requests."""
        async with self._code_stage_lifecycle_lock:
            run = await self.code_stage_runs.get(run_id)
            if run.project_id != project_id:
                raise KeyError(f"code stage run not found: {run_id}")
            snapshot = await self.get_code_stage_changes(project_id, run.id)
            content_hash = diff_content_hash(snapshot)
            verification = self._code_stage_verifications.get(run.id)
            review = self._code_stage_reviews.get(run.id)
            if (
                verification is None
                or verification.status is not CodeStageVerificationStatus.PASSED
                or verification.content_hash != content_hash
            ):
                raise ScopeViolation(
                    "fresh successful verification is required before review override"
                )
            if review is None or review.content_hash != content_hash:
                raise ScopeViolation(
                    "fresh code review is required before review override"
                )
            if review.status is not CodeStageReviewStatus.CHANGES_REQUESTED:
                raise ScopeViolation(
                    "only a review requesting changes can be overridden"
                )
            overridden = review.model_copy(
                update={
                    "human_override": True,
                    "override_actor_id": payload.actor_id,
                    "override_reason": payload.reason,
                    "overridden_at": utc_now(),
                    "updated_at": utc_now(),
                }
            )
            self._code_stage_reviews[run.id] = overridden
            self._code_stage_approvals.pop(run.id, None)
            await self.approvals.append(
                ApprovalRecord(
                    projectId=project_id,
                    taskId=run.id,
                    action=ApprovalAction.ACCEPT_REVIEW_RISK,
                    actorId=payload.actor_id,
                    actorType=ApprovalActorType.HUMAN,
                    reason=payload.reason,
                    contentHash=content_hash,
                    verificationRunIds=verification.command_run_ids,
                )
            )
            if run.workflow_run_id:
                self.control_plane.traces.append_workflow_event(
                    WorkflowEvent(
                        eventId=generate_uuid(),
                        projectId=project_id,
                        taskId=run.task_id,
                        runId=run.workflow_run_id,
                        agentId=payload.actor_id,
                        role="approver",
                        phase=WorkflowPhase.REVIEW,
                        eventType="code.reviewOverrideAccepted",
                        payload={
                            "status": EngineeringStatus.COMPLETED.value,
                            "summary": "人工确认 Reviewer 意见无需阻止最终审批。",
                        },
                    )
                )
            await self._record_activity(
                project_id,
                "code.reviewOverrideAccepted",
                "人工确认无需按 Reviewer 意见修改，进入最终审批",
                run.id,
            )
            return overridden

    async def start_code_stage_review_revision(
        self, project_id: str, run_id: str
    ) -> CodeStageRun:
        """Create a child implementation run from the stored Reviewer decision."""
        run = await self.code_stage_runs.get(run_id)
        if run.project_id != project_id:
            raise KeyError(f"code stage run not found: {run_id}")
        snapshot = await self.get_code_stage_changes(project_id, run.id)
        review = self._code_stage_reviews.get(run.id)
        if review is None or review.content_hash != diff_content_hash(snapshot):
            raise ScopeViolation("fresh code review is required before revision")
        if (
            review.status
            not in {
                CodeStageReviewStatus.CHANGES_REQUESTED,
                CodeStageReviewStatus.BASICALLY_QUALIFIED,
            }
            or review.human_override
        ):
            raise ScopeViolation(
                "current review has no applicable revision suggestions"
            )
        revision_instructions = list(review.required_changes)
        if not revision_instructions:
            revision_instructions = [item.message for item in review.findings]
        contract = {
            "schemaVersion": "1.0",
            "type": "ReviewerRevisionContract",
            "reviewId": review.id,
            "objective": review.summary[:2000],
            "requiredChanges": [
                {
                    "id": f"REV-{index:03d}",
                    "instruction": instruction[:700],
                    "mandatory": True,
                }
                for index, instruction in enumerate(revision_instructions[:15], start=1)
            ],
            "findings": [
                {
                    "id": f"FIND-{index:03d}",
                    "severity": finding.severity,
                    "message": finding.message[:400],
                }
                for index, finding in enumerate(review.findings[:8], start=1)
            ],
            "executionRules": [
                "Implement every unmet mandatory required change in actual project files.",
                "Update only tests directly related to the implementation changes.",
                "Do not delete valid tests, skip verification, or hide errors to claim success.",
                "Preserve existing behavior unrelated to this revision.",
                "If an item is already satisfied, verify it and avoid unrelated edits.",
            ],
        }
        instructions = "[OXYGENT_REVIEW_REVISION_CONTRACT_V1]\n" + json.dumps(
            contract, ensure_ascii=False, indent=2
        )
        return await self.start_code_stage(
            project_id,
            CodeStageRunRequest(
                sourceWorkspaceId=run.source_workspace_id,
                workflowRunId=run.workflow_run_id,
                parentRunId=run.id,
                instructions=instructions,
            ),
        )

    async def approve_code_stage(
        self,
        project_id: str,
        run_id: str,
        payload: CodeStageApprovalRequest,
    ) -> CodeStageApproval:
        async with self._code_stage_lifecycle_lock:
            run = await self.code_stage_runs.get(run_id)
            if run.project_id != project_id:
                raise KeyError(f"code stage run not found: {run_id}")
            snapshot = await self.get_code_stage_changes(project_id, run.id)
            content_hash = diff_content_hash(snapshot)
            verification = self._code_stage_verifications.get(run.id)
            review = self._code_stage_reviews.get(run.id)
            if (
                verification is None
                or verification.status is not CodeStageVerificationStatus.PASSED
                or verification.content_hash != content_hash
            ):
                raise ScopeViolation(
                    "fresh successful verification is required before approval"
                )
            if (
                review is None
                or (
                    review.status
                    not in {
                        CodeStageReviewStatus.APPROVED,
                        CodeStageReviewStatus.BASICALLY_QUALIFIED,
                    }
                    and not review.human_override
                )
                or review.content_hash != content_hash
            ):
                raise ScopeViolation("approved fresh code review is required")
            approval = CodeStageApproval(
                projectId=project_id,
                codeStageRunId=run.id,
                status=CodeStageApprovalStatus.APPROVED,
                actorId=payload.actor_id,
                reason=payload.reason,
                contentHash=content_hash,
            )
            self._code_stage_approvals[run.id] = approval
            await self.approvals.append(
                ApprovalRecord(
                    projectId=project_id,
                    taskId=run.id,
                    action=ApprovalAction.APPROVE_CHANGES,
                    actorId=payload.actor_id,
                    actorType=ApprovalActorType.HUMAN,
                    reason=payload.reason,
                    contentHash=content_hash,
                    verificationRunIds=verification.command_run_ids,
                )
            )
            if run.workflow_run_id:
                self.control_plane.traces.append_workflow_event(
                    WorkflowEvent(
                        eventId=generate_uuid(),
                        projectId=project_id,
                        taskId=run.task_id,
                        runId=run.workflow_run_id,
                        agentId=payload.actor_id,
                        role="approver",
                        phase=WorkflowPhase.APPROVAL,
                        eventType="code.approved",
                        payload={
                            "status": EngineeringStatus.COMPLETED.value,
                            "summary": "用户已批准当前代码版本。",
                        },
                    )
                )
            await self._record_activity(
                project_id, "code.approved", "当前代码版本已人工批准", run.id
            )
            return approval

    def _technical_lead_model(self) -> tuple[Any, Any]:
        _profile, provider, model = self._role_model("technical_lead")
        return provider, model

    def _aider_proxy_url(self, run_id: str) -> str:
        if not self.aider_proxy_base_url:
            raise CodeWorkspaceError("Aider protocol bridge is not configured")
        base = self.aider_proxy_base_url.rstrip("/")
        if base.endswith("/aider-proxy/v1"):
            base = base[: -len("/v1")]
        return f"{base}/runs/{run_id}/v1"

    def _append_model_usage(
        self,
        *,
        project_id: str,
        task_id: str,
        run_id: str,
        role_id: str,
        agent_id: str,
        provider: Any,
        model: Any,
        response: Any | None,
        latency_ms: float,
        invocation_type: str,
        status: InvocationStatus = InvocationStatus.SUCCEEDED,
        failure_reason: str | None = None,
    ) -> None:
        """Append one credential-safe record immediately after a provider call."""
        self.control_plane.usage.append(
            ModelUsage(
                projectId=project_id,
                taskId=task_id,
                runId=run_id,
                roleId=role_id,
                agentId=agent_id,
                providerId=provider.id,
                modelId=model.id,
                inputTokens=response.input_tokens if response else 0,
                outputTokens=response.output_tokens if response else 0,
                tokenCountMethod=(
                    response.token_count_method
                    if response
                    else EstimationMethod.APPROXIMATE
                ),
                invocationType=invocation_type,
                latencyMs=max(0.0, latency_ms),
                status=status,
                failureReason=failure_reason,
                costAvailable=False,
            )
        )

    async def record_mas_model_usage(
        self, llm: Any, oxy_request: OxyRequest, usage: Any
    ) -> None:
        """Record each legacy Web/CLI LLM call without changing old agents."""
        if getattr(llm, "model_name", "") == "model-router":
            return
        model_name = str(getattr(llm, "model_name", "") or llm.name)
        matches = [
            model
            for model in self.control_plane.models.list()
            if model.model_name == model_name
        ]
        model = matches[0] if matches else None
        provider_id = model.provider_id if model else "legacy-web"
        model_id = model.id if model else model_name
        shared = (
            oxy_request.shared_data if isinstance(oxy_request.shared_data, dict) else {}
        )
        project_id = str(
            shared.get("projectId")
            or shared.get("project_id")
            or oxy_request.group_data.get("projectId", "")
        )
        self.control_plane.usage.append(
            ModelUsage(
                projectId=project_id,
                taskId=oxy_request.node_id or oxy_request.request_id or generate_uuid(),
                runId=oxy_request.current_trace_id or generate_uuid(),
                roleId=oxy_request.caller or "chat",
                agentId=oxy_request.caller or "web-chat",
                providerId=provider_id,
                modelId=model_id,
                inputTokens=usage.input_tokens,
                outputTokens=usage.output_tokens,
                tokenCountMethod=usage.estimation_method,
                invocationType="chat",
                status=InvocationStatus.SUCCEEDED,
                costAvailable=False,
            )
        )

    async def complete_aider_proxy(
        self,
        messages: list[dict[str, Any]],
        parameters: dict[str, Any],
        *,
        project_id: str = "",
        task_id: str = "",
        run_id: str = "",
    ) -> dict[str, Any]:
        """Bridge Aider's Chat Completions request to the configured adapter."""
        provider, model = self._technical_lead_model()
        request_provider = provider.model_copy(
            update={"timeout": _aider_provider_timeout(provider.timeout)}
        )
        adapter = self.control_plane.adapters.get(provider.provider_type)
        allowed_parameters = {
            key: value
            for key, value in parameters.items()
            if key
            in {
                "temperature",
                "top_p",
                "max_tokens",
                "max_output_tokens",
                "reasoning_effort",
            }
        }
        started = time.perf_counter()
        estimated_usage = build_token_usage(None, messages, "", model.model_name)
        live_usage = ModelUsage(
            projectId=project_id,
            taskId=task_id or run_id,
            runId=run_id or task_id or generate_uuid(),
            roleId="technical_lead",
            agentId="aider-coding-engine",
            providerId=provider.id,
            modelId=model.id,
            inputTokens=estimated_usage.input_tokens,
            outputTokens=0,
            tokenCountMethod=estimated_usage.estimation_method,
            invocationType="aider",
            latencyMs=0,
            status=InvocationStatus.RUNNING,
            costAvailable=False,
        )
        self.control_plane.usage.append(live_usage)
        try:
            response = await adapter.complete(
                ModelRequest(
                    provider=request_provider,
                    model=model,
                    messages=messages,
                    parameters=allowed_parameters,
                )
            )
        except Exception as exc:
            self.control_plane.usage.upsert(
                live_usage.model_copy(
                    update={
                        "status": InvocationStatus.FAILED,
                        "latency_ms": (time.perf_counter() - started) * 1000,
                        "failure_reason": type(exc).__name__,
                    }
                )
            )
            raise
        self.control_plane.usage.upsert(
            live_usage.model_copy(
                update={
                    "input_tokens": response.input_tokens,
                    "output_tokens": response.output_tokens,
                    "token_count_method": response.token_count_method,
                    "latency_ms": response.latency_ms,
                    "status": InvocationStatus.SUCCEEDED,
                }
            )
        )
        return {
            "id": f"aider-{generate_uuid()}",
            "object": "chat.completion",
            "created": int(utc_now().timestamp()),
            "model": model.model_name,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": response.output},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": response.input_tokens,
                "completion_tokens": response.output_tokens,
                "total_tokens": response.input_tokens + response.output_tokens,
            },
        }

    @staticmethod
    async def _run_aider_subprocess(
        command: list[str], *, cwd: Path, environment: dict[str, str]
    ) -> tuple[int, bytes, bytes]:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(cwd),
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        configured_timeout = os.environ.get("OXYGENT_AIDER_TIMEOUT_SECONDS", "").strip()
        try:
            timeout = float(configured_timeout) if configured_timeout else 420.0
        except ValueError:
            timeout = 420.0
        timeout = min(900.0, max(1.0, timeout))
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError as exc:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            stdout, stderr = await process.communicate()
            detail = (stdout + b"\n" + stderr).decode("utf-8", errors="replace")[-1000:]
            raise CodeWorkspaceError(
                f"Aider exceeded the {timeout:g}-second execution limit and was stopped. "
                "For unusually large tasks, increase OXYGENT_AIDER_TIMEOUT_SECONDS; "
                f"otherwise reduce the requested change scope.\n{detail}"
            ) from exc
        return process.returncode, stdout, stderr

    @staticmethod
    async def _restore_aider_attempt(run_path: Path, snapshot: DiffSnapshot) -> None:
        """Restore only the disposable run copy to its committed parent baseline."""

        _, tracked_output, _, _ = await run_git(run_path, "ls-files", "--cached")
        tracked = set(tracked_output.splitlines())
        tracked_changes = [path for path in snapshot.changed_files if path in tracked]
        if tracked_changes:
            await run_git(
                run_path,
                "restore",
                "--source=HEAD",
                "--worktree",
                "--",
                *tracked_changes,
            )
        for path in snapshot.changed_files:
            if path in tracked:
                continue
            target = (run_path / path).resolve()
            if target.is_relative_to(run_path) and target.is_file():
                target.unlink()

    async def _execute_code_stage(self, run_id: str) -> None:
        run = await self.code_stage_runs.get(run_id)
        source = await self.source_workspaces.get(run.source_workspace_id)
        started = run.model_copy(
            update={
                "status": CodeStageStatus.RUNNING,
                "summary": "正在准备隔离项目副本，尚未调用模型。",
                "updated_at": utc_now(),
            }
        )
        await self.code_stage_runs.update(started)
        workflow_task_id = run.task_id
        if run.workflow_run_id:
            events = self.control_plane.traces.workflow_events(
                run_id=run.workflow_run_id
            )
            if events:
                workflow_task_id = events[0].task_id
            self.control_plane.traces.append_workflow_event(
                WorkflowEvent(
                    eventId=generate_uuid(),
                    projectId=run.project_id,
                    taskId=workflow_task_id,
                    runId=run.workflow_run_id,
                    agentId="aider-coding-engine",
                    role="engineer",
                    phase=WorkflowPhase.IMPLEMENTATION,
                    eventType="code.implementationStarted",
                    payload={
                        "status": EngineeringStatus.IMPLEMENTING.value,
                        "summary": "Aider 正在读取项目源码和前序结构化产物并实现代码。",
                        "toolsUsed": ["Aider"],
                    },
                )
            )
        try:
            provider, model = self._technical_lead_model()
            if provider.provider_type.value not in {
                "openai-compatible",
                "openai-responses",
            }:
                raise CodeWorkspaceError(
                    "Aider currently requires an OpenAI-compatible Provider"
                )
            api_key = default_credential_resolver().resolve(
                provider.credential_reference
            )
            if not api_key:
                raise CodeWorkspaceError(
                    "Aider cannot resolve the configured credential"
                )
            parent_run = (
                await self.code_stage_runs.get(run.parent_run_id)
                if run.parent_run_id
                else None
            )
            run_path = await asyncio.to_thread(
                self.source_workspace_manager.prepare_run,
                source,
                run.id,
                parent_run=parent_run,
            )
            artifacts = self.artifacts.list(run.project_id)
            artifact_parts = [
                f"## {artifact.type.value}\n"
                f"{artifact.content.model_dump_json(by_alias=True, indent=2)[:6000]}"
                for artifact in artifacts[-8:]
                if artifact.type.value
                in {
                    "RequirementSpec",
                    "ArchitectureDecision",
                    "TaskGraph",
                    "ReviewReport",
                }
            ]
            artifact_context = "\n\n".join(artifact_parts)[:24_000]
            project_mode = (
                "REVISION: Start from the previous completed Aider result. Preserve its "
                "working behavior and apply the user's new feedback as a focused revision."
                if parent_run
                else "NEW PROJECT: The source workspace is intentionally empty. Create all "
                "necessary runnable project files from scratch. Do not ask the user to "
                "provide an existing repository."
                if source.file_count == 0
                else (
                    "EXISTING PROJECT: Inspect the imported source files and implement "
                    "the requested change in that codebase."
                )
            )
            review_contract_mode = run.instructions.startswith(
                "[OXYGENT_REVIEW_REVISION_CONTRACT_V1]"
            )
            context_path = run_path / ".oxygent" / "WORKFLOW_CONTEXT.md"
            context_path.parent.mkdir(parents=True, exist_ok=True)
            context_path.write_text(
                "# OxyGent implementation context\n\n"
                f"{project_mode}\n\n"
                f"## User implementation request\n{run.instructions}\n\n"
                + (
                    f"## Previous implementation request\n{parent_run.instructions}\n\n"
                    if parent_run
                    else ""
                )
                + "## Prior workflow artifacts\n"
                f"{artifact_context or 'No prior artifacts are available.'}\n",
                encoding="utf-8",
            )
            adapter = self.control_plane.adapters.get(provider.provider_type)
            available_files = repository_file_list(run_path)
            deterministic_files = deterministic_aider_files(run_path)
            planner_limit = 40 if review_contract_mode else 24
            file_plan_response = await adapter.complete(
                ModelRequest(
                    provider=provider,
                    model=model,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "Select every project file Aider must edit or create to "
                                'implement the request. Return JSON only as {"files":["path"]}. '
                                "Paths must be repository-relative. Existing paths must use the "
                                "exact prefix shown in the supplied file list. You may include "
                                "new source, test, DTO, entity, mapper, controller, migration, "
                                "or configuration paths when implementation requires them. "
                                "Select the smallest complete set and do not stop merely because "
                                "a required file does not exist yet. Never select .git, .oxygent, "
                                ".aider files, credentials, .env, keys, or PEMs."
                            ),
                        },
                        {
                            "role": "user",
                            "content": (
                                f"{project_mode}\n\nImplementation request:\n"
                                f"{run.instructions}\n\nWorkflow artifacts:\n"
                                f"{artifact_context or 'None'}\n\nRecommended existing files:\n"
                                + ("\n".join(deterministic_files) or "(none)")
                                + "\n\nAvailable project files:\n"
                                + ("\n".join(available_files) or "(empty project)")
                            )[:40_000],
                        },
                    ],
                    parameters={"max_output_tokens": 1000},
                )
            )
            self._append_model_usage(
                project_id=run.project_id,
                task_id=workflow_task_id,
                run_id=run.id,
                role_id="technical_lead",
                agent_id="aider-file-planner",
                provider=provider,
                model=model,
                response=file_plan_response,
                latency_ms=file_plan_response.latency_ms,
                invocation_type="aider-file-plan",
            )
            planned_files = parse_aider_file_plan(
                file_plan_response.output, limit=planner_limit
            )
            if parent_run:
                surrounding_files = expand_aider_retry_files(
                    run_path,
                    parent_run.changed_files,
                    preferred=deterministic_files,
                    request=run.instructions,
                    limit=planner_limit,
                )
                for path in surrounding_files:
                    if path not in planned_files and len(planned_files) < planner_limit:
                        planned_files.append(path)
            await run_git(run_path, "init")
            await run_git(run_path, "config", "user.name", "OxyGent Code Stage")
            await run_git(run_path, "config", "user.email", "code-stage@localhost")
            exclude = run_path / ".git" / "info" / "exclude"
            exclude.write_text(".aider*\n", encoding="utf-8")
            await run_git(run_path, "add", "-A")
            await run_git(
                run_path, "commit", "--allow-empty", "-m", "Imported project baseline"
            )
            _, base_commit, _, _ = await run_git(run_path, "rev-parse", "HEAD")
            missing_planned_files = [
                path for path in planned_files if not (run_path / path).is_file()
            ]
            editable_files = prepare_aider_editable_files(
                run_path, planned_files, create_missing=True
            )
            prompt = (
                "Implement the requested change in this project now. You are the code "
                "implementation stage of a structured workflow. Read the attached read-only "
                "WORKFLOW_CONTEXT.md before editing. "
                "Treat the current user implementation request as authoritative; prior "
                "artifacts provide context but must not replace it with unrelated work. "
                "Inspect the project before editing. Create or modify the actual project files. "
                "If this is a new project, choose the simplest conventional implementation "
                "that satisfies the artifacts, document reasonable assumptions in the project, "
                "and proceed without asking for repository contents. Reviewer findings are "
                "risks to resolve during implementation, not a reason to stop. "
                "Do not only explain or return a patch. Never modify .gitignore, .git, .aider* "
                "or credential files. Do not commit.\n\n"
                f"{project_mode}\n\nUser implementation request:\n{run.instructions}"
            )
            if review_contract_mode:
                prompt += (
                    "\n\nThis request is a structured ReviewerRevisionContract. Treat every "
                    "requiredChanges item with mandatory=true as an explicit implementation "
                    "obligation. Inspect the current final code before editing, implement each "
                    "unmet item in actual project files, and update only directly relevant "
                    "tests. Before finishing, check every mandatory item against the final "
                    "files and implement all unmet items; changing only one item is not "
                    "completion. Do not merely restate the contract."
                )
            environment = {
                "PATH": os.environ.get("PATH", ""),
                "LANG": os.environ.get("LANG", "en_US.UTF-8"),
                "AIDER_OPENAI_API_KEY": "local-oxygent-proxy",
                "AIDER_OPENAI_API_BASE": self._aider_proxy_url(run.id),
                "AIDER_ANALYTICS": "false",
                "AIDER_CHECK_MODEL_ACCEPTS_SETTINGS": "false",
                "AIDER_SHOW_MODEL_WARNINGS": "false",
            }
            started = started.model_copy(
                update={
                    "provider_id": provider.id,
                    "model_id": model.id,
                    "summary": "Aider 已启动，正在读取项目文件并准备模型请求。",
                    "updated_at": utc_now(),
                }
            )
            await self.code_stage_runs.update(started)
            aider_api_timeout = _aider_provider_timeout(provider.timeout)
            aider_reasoning_effort = (
                os.environ.get("OXYGENT_AIDER_REASONING_EFFORT", "low").strip().lower()
            )
            if aider_reasoning_effort not in {"minimal", "low", "medium", "high"}:
                aider_reasoning_effort = "low"
            command = build_aider_command(
                python_executable=sys.executable,
                model_name=model.model_name,
                prompt=prompt,
                editable_files=editable_files,
                api_timeout_seconds=aider_api_timeout,
                reasoning_effort=aider_reasoning_effort,
            )
            return_code, stdout, stderr = await self._run_aider_subprocess(
                command, cwd=run_path, environment=environment
            )
            if return_code:
                detail = stderr.decode("utf-8", errors="replace")[-1600:]
                raise CodeWorkspaceError(f"Aider implementation failed: {detail}")
            remove_empty_aider_placeholders(run_path, missing_planned_files)
            snapshot = await capture_diff(run_path, base_commit.strip())
            protocol_artifacts = detect_aider_protocol_artifacts(
                run_path, repository_file_list(run_path, limit=3000)
            )
            if not snapshot.changed_files or protocol_artifacts:
                suggested_files = extract_aider_suggested_paths(
                    (stdout + b"\n" + stderr).decode("utf-8", errors="replace")
                )
                retry_candidates = list(suggested_files)
                for path in protocol_artifacts:
                    if path not in retry_candidates:
                        retry_candidates.append(path)
                for path in editable_files:
                    if path not in retry_candidates:
                        retry_candidates.append(path)
                if snapshot.changed_files:
                    await self._restore_aider_attempt(run_path, snapshot)
                retry_missing_files = [
                    path for path in retry_candidates if not (run_path / path).is_file()
                ]
                prepared_retry_files = prepare_aider_editable_files(
                    run_path, retry_candidates, create_missing=True
                )
                retry_files = expand_aider_retry_files(
                    run_path,
                    prepared_retry_files,
                    preferred=parent_run.changed_files if parent_run else (),
                    request=run.instructions,
                    limit=40 if parent_run else 24,
                )
                retry_prompt = (
                    prompt
                    + "\n\nThe previous Aider attempt was rejected because it either made "
                    "no project change or left raw edit-protocol markers in source files. "
                    "Retry from the committed parent baseline using the broader attached "
                    "file set. Apply the concrete verification error as valid source code. "
                    "Never write Markdown fences, file-list headings, <<<<<<< SEARCH, "
                    "=======, or >>>>>>> REPLACE into project files."
                )
                retry_edit_format = "whole" if source.file_count == 0 else "diff"
                retry_command = build_aider_command(
                    python_executable=sys.executable,
                    model_name=model.model_name,
                    prompt=retry_prompt,
                    editable_files=retry_files,
                    edit_format=retry_edit_format,
                    api_timeout_seconds=aider_api_timeout,
                    reasoning_effort=aider_reasoning_effort,
                )
                (
                    retry_return_code,
                    retry_stdout,
                    retry_stderr,
                ) = await self._run_aider_subprocess(
                    retry_command, cwd=run_path, environment=environment
                )
                if retry_return_code:
                    detail = retry_stderr.decode("utf-8", errors="replace")[-1600:]
                    raise CodeWorkspaceError(
                        f"Aider implementation retry failed: {detail}"
                    )
                stdout += b"\n--- automatic safe-format retry ---\n" + retry_stdout
                stderr += b"\n" + retry_stderr
                remove_empty_aider_placeholders(run_path, retry_missing_files)
                snapshot = await capture_diff(run_path, base_commit.strip())
                protocol_artifacts = detect_aider_protocol_artifacts(
                    run_path, repository_file_list(run_path, limit=3000)
                )
                if protocol_artifacts:
                    await self._restore_aider_attempt(run_path, snapshot)
                    raise CodeWorkspaceError(
                        "Aider returned unapplied edit-protocol text instead of valid code "
                        "for: " + ", ".join(protocol_artifacts[:10])
                    )
            if not snapshot.changed_files:
                aider_detail = (stdout + b"\n" + stderr).decode(
                    "utf-8", errors="replace"
                )[-1200:]
                if source.file_count == 0:
                    raise CodeWorkspaceError(
                        "Aider did not create files for this new project. Retry the run; "
                        f"the workflow context is attached automatically.\n{aider_detail}"
                    )
                raise CodeWorkspaceError(
                    "Aider made no project changes after an automatic broader-file retry. "
                    "Review whether the request is already satisfied or conflicts with the "
                    f"selected project.\n{aider_detail}"
                )
            completed = started.model_copy(
                update={
                    "status": CodeStageStatus.COMPLETED,
                    "provider_id": provider.id,
                    "model_id": model.id,
                    "changed_files": snapshot.changed_files,
                    "summary": f"已生成 {len(snapshot.changed_files)} 个变更文件。",
                    "base_commit": base_commit.strip(),
                    "run_path": str(run_path),
                    "updated_at": utc_now(),
                }
            )
            await self.code_stage_runs.update(completed)
            if run.workflow_run_id:
                self.control_plane.traces.append_workflow_event(
                    WorkflowEvent(
                        eventId=generate_uuid(),
                        projectId=run.project_id,
                        taskId=workflow_task_id,
                        runId=run.workflow_run_id,
                        agentId="aider-coding-engine",
                        role="engineer",
                        providerId=provider.id,
                        modelId=model.id,
                        phase=WorkflowPhase.IMPLEMENTATION,
                        eventType="code.implementationCompleted",
                        payload={
                            "status": EngineeringStatus.COMPLETED.value,
                            "summary": completed.summary,
                            "toolsUsed": ["Aider"],
                            "changedFiles": snapshot.changed_files,
                        },
                    )
                )
            await self._record_activity(
                run.project_id,
                "code.implementationCompleted",
                completed.summary,
                run.id,
            )
        except Exception as exc:
            reason = str(exc)
            credential_values = [
                value
                for key, value in os.environ.items()
                if value
                and any(token in key.upper() for token in ("KEY", "TOKEN", "SECRET"))
            ]
            for value in credential_values:
                reason = reason.replace(value, "[redacted]")
            failed = started.model_copy(
                update={
                    "status": CodeStageStatus.FAILED,
                    "failure_reason": reason[-2000:],
                    "updated_at": utc_now(),
                }
            )
            await self.code_stage_runs.update(failed)
            if run.workflow_run_id:
                self.control_plane.traces.append_workflow_event(
                    WorkflowEvent(
                        eventId=generate_uuid(),
                        projectId=run.project_id,
                        taskId=workflow_task_id,
                        runId=run.workflow_run_id,
                        agentId="aider-coding-engine",
                        role="engineer",
                        phase=WorkflowPhase.IMPLEMENTATION,
                        eventType="code.implementationFailed",
                        payload={
                            "status": EngineeringStatus.FAILED.value,
                            "summary": "代码实现失败；请在代码阶段查看可操作的错误信息。",
                            "toolsUsed": ["Aider"],
                        },
                    )
                )
            await self._record_activity(
                run.project_id, "code.implementationFailed", "代码实现失败", run.id
            )

    async def generate_code_preview(
        self, project_id: str, task_id: str, instructions: str = ""
    ) -> dict[str, Any]:
        """Generate a bounded, read-only unified-diff proposal for a Code Task."""
        task = await self.code_tasks.get(task_id)
        if task.project_id != project_id:
            raise KeyError(f"code task not found: {task_id}")
        if not self.code_workspace_configured:
            raise CodeWorkspaceError("Code Workspace is not configured")
        tree = await self.execute_code_read(
            project_id, task_id, operation=CodingOperation.TREE
        )
        files = tree.data.get("files", [])[:120]
        context_files = [
            path
            for path in files
            if path in {"README.md", "pyproject.toml", "package.json"}
            or path.endswith((".py", ".js", ".ts", ".tsx", ".md"))
        ][:6]
        excerpts: list[str] = []
        for path in context_files:
            try:
                content = await self.execute_code_read(
                    project_id, task_id, operation=CodingOperation.READ_FILE, path=path
                )
            except CodeWorkspaceError:
                continue
            excerpts.append(f"--- {path}\n{content.data.get('content', '')[:6000]}")
        profile = self.control_plane.agents.get("technical_lead_agent_profile")
        router = ModelRouter(
            name="code_workspace_router",
            provider_registry=self.control_plane.providers,
            model_registry=self.control_plane.models,
            role_registry=self.control_plane.roles,
            policy_registry=self.control_plane.model_policies,
            agent_profile_registry=self.control_plane.agents,
            adapter_registry=self.control_plane.adapters,
            usage_store=self.control_plane.usage,
            trace_store=self.control_plane.traces,
        )
        artifacts = self.artifacts.list(project_id)
        artifact_context = "\n\n".join(
            f"{artifact.type.value}:\n{artifact.content.model_dump_json(by_alias=True)[:8000]}"
            for artifact in artifacts[-8:]
            if artifact.type.value
            in {"RequirementSpec", "ArchitectureDecision", "TaskGraph"}
        )
        prompt = (
            "You are implementing a bounded Code Task. Return only a unified Git diff "
            "that implements the objective. Do not use Markdown fences, explanations, "
            "shell commands, or files outside allowed paths. If insufficient context exists, "
            "return an empty diff.\n\n"
            f"Objective:\n{task.change_contract.objective}\n\n"
            f"Acceptance criteria:\n"
            + "\n".join(
                f"- {item}" for item in task.change_contract.acceptance_criteria
            )
            + "\n\nAllowed paths:\n"
            + "\n".join(task.change_contract.allowed_paths)
            + "\n\nAdditional instructions:\n"
            + instructions[:4000]
            + "\n\nProject artifacts:\n"
            + artifact_context
            + "\n\nRepository files:\n"
            + "\n".join(files[:60])
            + "\n\nSelected file contents:\n"
            + "\n\n".join(excerpts)
        )
        try:
            response = await router._execute(
                OxyRequest(
                    arguments={
                        "messages": [
                            {
                                "role": "system",
                                "content": "Generate safe code patches.",
                            },
                            {"role": "user", "content": prompt},
                        ],
                        "_routing_context": {
                            "projectId": project_id,
                            "taskId": task_id,
                            "runId": generate_uuid(),
                            "roleId": profile.role_id,
                            "agentId": profile.id,
                            "policyId": profile.model_policy_id,
                            "taskType": "code-preview",
                            "requiredCapabilities": ["text"],
                        },
                    }
                )
            )
        except Exception as exc:
            raise CodeWorkspaceError(
                "code proposal model call failed; check the configured Provider and retry"
            ) from exc
        if response.state is not OxyState.COMPLETED:
            raise CodeWorkspaceError("code proposal model call failed")
        await self._record_activity(
            project_id, "codeTask.previewGenerated", "Code proposal generated", task_id
        )
        return {
            "diff": str(response.output),
            "providerId": response.extra.get("provider_id"),
            "modelId": response.extra.get("model_id"),
            "selectionReason": response.extra.get("selection_reason", ""),
        }

    async def run_aider_implementation(
        self, project_id: str, task_id: str, instructions: str = ""
    ) -> dict[str, Any]:
        """Run Aider only inside a clean task Worktree and return the real diff."""
        task = await self.code_tasks.get(task_id)
        if task.project_id != project_id:
            raise KeyError(f"code task not found: {task_id}")
        worktree = Path(task.worktree_path)
        before = await capture_diff(worktree, task.base_commit)
        if before.changed_files:
            raise CodeWorkspaceError(
                "Worktree already has changes; review or discard them before running Aider"
            )
        profile = self.control_plane.agents.get("technical_lead_agent_profile")
        policy = self.control_plane.model_policies.get(profile.model_policy_id)
        model = self.control_plane.models.get(policy.primary_model_ids[0])
        provider = self.control_plane.providers.get(model.provider_id)
        if provider.provider_type.value not in {
            "openai-compatible",
            "openai-responses",
        }:
            raise CodeWorkspaceError(
                "Aider currently requires an OpenAI-compatible Provider"
            )
        api_key = default_credential_resolver().resolve(provider.credential_reference)
        if not api_key:
            raise CodeWorkspaceError(
                "Aider could not resolve the configured credential reference"
            )
        artifacts = self.artifacts.list(project_id)
        artifact_context = "\n\n".join(
            f"{artifact.type.value}:\n{artifact.content.model_dump_json(by_alias=True)[:8000]}"
            for artifact in artifacts[-8:]
            if artifact.type.value
            in {"RequirementSpec", "ArchitectureDecision", "TaskGraph"}
        )
        prompt = (
            "Implement this Code Task now. Work only within the allowed paths. "
            "Never modify .gitignore, .aiderignore, .aider* files, or any file outside "
            "the allowed paths. Do not commit, do not run arbitrary shell commands, and "
            "leave a clean diff for review.\n\n"
            f"Objective:\n{task.change_contract.objective}\n\nAcceptance criteria:\n"
            + "\n".join(
                f"- {item}" for item in task.change_contract.acceptance_criteria
            )
            + "\n\nAllowed paths:\n"
            + "\n".join(task.change_contract.allowed_paths)
            + "\n\nProject artifacts:\n"
            + artifact_context
            + "\n\nAdditional instructions:\n"
            + instructions[:4000]
        )
        environment = {
            "PATH": os.environ.get("PATH", ""),
            "LANG": os.environ.get("LANG", "en_US.UTF-8"),
            "AIDER_OPENAI_API_KEY": "local-oxygent-proxy",
            "AIDER_OPENAI_API_BASE": self._aider_proxy_url(task.id),
            "AIDER_ANALYTICS": "false",
        }
        command = [
            sys.executable,
            "-m",
            "aider",
            "--model",
            f"openai/{model.model_name}",
            "--yes-always",
            "--no-auto-commits",
            "--no-analytics",
            "--no-check-update",
            "--no-gitignore",
            "--no-add-gitignore-files",
            "--map-tokens",
            "0",
            "--input-history-file",
            os.devnull,
            "--chat-history-file",
            os.devnull,
            "--llm-history-file",
            os.devnull,
            "--no-pretty",
            "--no-stream",
            "--message",
            prompt,
        ]
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(worktree),
                env=environment,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=600)
        except asyncio.TimeoutError as exc:
            raise CodeWorkspaceError(
                "Aider implementation timed out after 10 minutes"
            ) from exc
        if process.returncode:
            raise CodeWorkspaceError(
                "Aider implementation failed: "
                + stderr.decode("utf-8", errors="replace")[-1200:]
            )
        snapshot = await capture_diff(worktree, task.base_commit)
        ScopeGuard.check_diff(
            task.change_contract, snapshot.changed_files, snapshot.diff_line_count
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
        await self._record_activity(
            project_id,
            "codeTask.aiderCompleted",
            "Aider implementation completed",
            task.id,
        )
        return {
            "diff": snapshot.diff,
            "providerId": provider.id,
            "modelId": model.id,
            "selectionReason": "Aider executed in the isolated task Worktree.",
            "stdout": stdout.decode("utf-8", errors="replace")[-4000:],
        }

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

    async def start_role_workflow(
        self, project_id: str, payload: WorkflowLaunchRequest
    ) -> str:
        """Start one real four-role run and return its traceable run ID."""
        await self.projects.get(project_id)
        if self.workflow_executor is None:
            raise RuntimeError("Real multi-role workflow is not configured")
        idea = payload.idea.strip()
        if not idea:
            raise ValueError("workflow idea must not be empty")
        workflow_input = idea
        if payload.source_analysis_id:
            analysis = await self.source_analyses.get(payload.source_analysis_id)
            if analysis.project_id != project_id:
                raise ValueError("source analysis must belong to the target project")
            if (
                payload.source_workspace_id
                and analysis.source_workspace_id != payload.source_workspace_id
            ):
                raise ValueError(
                    "source analysis does not match the selected project files"
                )
            source_context = (
                "The user imported an existing software project. Treat the following "
                "verified project analysis as current-state context. Requirements must "
                "describe an improvement to this project rather than a greenfield system.\n\n"
                f"Project summary: {analysis.summary}\n"
                f"Project type: {analysis.project_type}\n"
                f"Technologies: {', '.join(analysis.technologies)}\n"
                f"Architecture: {'; '.join(analysis.architecture)}\n"
                f"Current features: {'; '.join(analysis.main_features)}\n"
                f"Known risks: {'; '.join(analysis.risks)}"
            )
            context_budget = max(0, 19_950 - len(idea))
            workflow_input = (
                f"{source_context[:context_budget]}\n\nUser requested improvement:\n{idea}"
            )[-20_000:]
        run_id = generate_uuid()
        task_id = generate_uuid()
        run_name = payload.name.strip() or idea[:80]
        self.control_plane.traces.append_workflow_event(
            WorkflowEvent(
                eventId=generate_uuid(),
                projectId=project_id,
                taskId=task_id,
                runId=run_id,
                agentId="workflow-orchestrator",
                role="product_manager",
                phase=WorkflowPhase.REQUIREMENT,
                eventType="workflow.queued",
                payload={
                    "runName": run_name,
                    "status": EngineeringStatus.ANALYZING.value,
                    "summary": "四角色工作流已进入执行队列。",
                },
            )
        )
        execution_request = WorkflowExecutionRequest(
            projectId=project_id,
            taskId=task_id,
            runId=run_id,
            idea=workflow_input,
            name=run_name,
        )
        task = asyncio.create_task(
            self._execute_role_workflow(execution_request),
            name=f"platform-workflow-{run_id}",
        )
        self._workflow_tasks[run_id] = task
        await self._touch_project(project_id)
        await self._record_activity(
            project_id,
            "workflow.started",
            f"四角色工作流已启动：{run_name}",
            run_id,
        )
        return run_id

    async def _execute_role_workflow(self, request: WorkflowExecutionRequest) -> None:
        executor = self.workflow_executor
        if executor is None:
            return
        try:
            # The first implementation serializes runs on one MAS. This avoids
            # shared-request state races while preserving asynchronous Web APIs.
            async with self._workflow_run_lock:
                await executor.execute(request)
        except Exception:
            events = self.control_plane.traces.workflow_events(run_id=request.run_id)
            latest = events[-1]
            self.control_plane.traces.append_workflow_event(
                WorkflowEvent(
                    eventId=generate_uuid(),
                    projectId=request.project_id,
                    taskId=latest.task_id,
                    runId=request.run_id,
                    agentId=latest.agent_id or "workflow-orchestrator",
                    role=latest.role,
                    providerId=latest.provider_id,
                    modelId=latest.model_id,
                    phase=latest.phase,
                    eventType="workflow.failed",
                    payload={
                        "status": EngineeringStatus.FAILED.value,
                        "summary": "工作流执行失败，请在执行详情中检查安全元数据。",
                    },
                )
            )
            await self._record_activity(
                request.project_id,
                "workflow.failed",
                f"四角色工作流执行失败：{request.name}",
                request.run_id,
            )
            return
        self.control_plane.traces.append_workflow_event(
            WorkflowEvent(
                eventId=generate_uuid(),
                projectId=request.project_id,
                taskId=request.task_id,
                runId=request.run_id,
                agentId="workflow-orchestrator",
                role="engineer",
                phase=WorkflowPhase.IMPLEMENTATION,
                eventType="workflow.awaitingImplementation",
                payload={
                    "status": EngineeringStatus.AWAITING_IMPLEMENTATION.value,
                    "summary": (
                        "需求、架构、任务图和方案审查已完成；"
                        "等待创建代码任务并进入实现阶段。"
                    ),
                },
            )
        )
        await self._touch_project(request.project_id)
        await self._record_activity(
            request.project_id,
            "workflow.awaitingImplementation",
            f"规划工作流已完成，等待实现：{request.name}",
            request.run_id,
        )

    async def wait_for_workflow(self, run_id: str) -> None:
        """Wait for a background run; intended for lifecycle hooks and tests."""
        try:
            task = self._workflow_tasks[run_id]
        except KeyError as exc:
            raise KeyError(f"workflow run not found: {run_id}") from exc
        await task

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
