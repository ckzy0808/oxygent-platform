import asyncio
import json
import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from oxygent.platform import (
    AgentProfile,
    AgentProfileRegistry,
    CodeStageApprovalRequest,
    CodeStageRun,
    CodeStageStatus,
    ModelProfile,
    ModelRegistry,
    ModelResponse,
    PlatformControlPlane,
    PlatformServices,
    ProjectCreate,
    ProviderAdapterRegistry,
    ProviderProfile,
    ProviderRegistry,
    ProviderType,
    RoleModelPolicy,
    RoleModelPolicyRegistry,
    SourceWorkspace,
    SourceWorkspaceManager,
    build_platform_router,
)
from oxygent.platform.coding import ScopeViolation
from oxygent.platform.verification import (
    VerificationFailureCategory,
    VerificationRun,
    VerificationRunner,
    VerificationStatus,
)


class ReviewerAdapter:
    def __init__(self, *, verdict="pass") -> None:
        self.requests = []
        self.verdict = verdict

    async def complete(self, request):
        self.requests.append(request)
        if (
            "Select every project file Aider must edit or create"
            in request.messages[0]["content"]
        ):
            return ModelResponse(
                output='{"files":["app.py"]}',
                inputTokens=50,
                outputTokens=10,
                latencyMs=5,
            )
        changes_requested = self.verdict == "changesRequested"
        return ModelResponse(
            output=json.dumps(
                {
                    "verdict": self.verdict,
                    "summary": "Implementation review completed.",
                    "findings": [
                        {
                            "severity": (
                                "info" if self.verdict == "pass" else "warning"
                            ),
                            "message": "Add an edge-case test.",
                        }
                    ],
                    "requiredChanges": (
                        ["Add the missing edge-case test."] if changes_requested else []
                    ),
                }
            ),
            inputTokens=240,
            outputTokens=60,
            latencyMs=12,
        )


def _services(tmp_path, *, review_approved=True, review_verdict=None):
    provider = ProviderProfile(
        id="review-provider",
        name="Reviewer Provider",
        providerType=ProviderType.OPENAI_COMPATIBLE,
        baseUrl="https://provider.invalid/v1",
        credentialReference="env:CODE_STAGE_TEST_KEY",
        healthStatus="healthy",
    )
    model = ModelProfile(
        id="review-model",
        providerId=provider.id,
        modelName="review-model",
        displayName="Review Model",
        capabilities={"text", "structured-output"},
        healthStatus="healthy",
    )
    policy = RoleModelPolicy(
        id="review-policy",
        roleId="reviewer",
        primaryModelIds=[model.id],
    )
    profile = AgentProfile(
        id="review-agent",
        name="Reviewer",
        agentName="reviewer",
        roleId="reviewer",
        modelPolicyId=policy.id,
        toolPolicyId="no-tools",
        promptKey="reviewer.prompt",
    )
    lead_policy = RoleModelPolicy(
        id="lead-policy",
        roleId="technical_lead",
        primaryModelIds=[model.id],
    )
    lead_profile = AgentProfile(
        id="lead-agent",
        name="Technical Lead",
        agentName="technical-lead",
        roleId="technical_lead",
        modelPolicyId=lead_policy.id,
        toolPolicyId="no-tools",
        promptKey="technical-lead.prompt",
    )
    adapter = ReviewerAdapter(
        verdict=review_verdict or ("pass" if review_approved else "changesRequested")
    )
    adapters = ProviderAdapterRegistry()
    adapters.register(ProviderType.OPENAI_COMPATIBLE, adapter)
    control = PlatformControlPlane(
        providers=ProviderRegistry([provider]),
        models=ModelRegistry([model]),
        agents=AgentProfileRegistry([profile, lead_profile]),
        model_policies=RoleModelPolicyRegistry([policy, lead_policy]),
        adapters=adapters,
    )
    services = PlatformServices(
        control_plane=control,
        source_workspace_manager=SourceWorkspaceManager(tmp_path / "managed"),
    )
    return services, adapter


def _git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


async def _completed_run(services, tmp_path):
    project = await services.create_project(ProjectCreate(name="Lifecycle"))
    root = tmp_path / "result"
    root.mkdir()
    (root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(root, "init")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", "app.py")
    _git(root, "commit", "-m", "baseline")
    base_commit = _git(root, "rev-parse", "HEAD")
    (root / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    run = CodeStageRun(
        projectId=project.id,
        sourceWorkspaceId="source",
        instructions="Change the value",
        status=CodeStageStatus.COMPLETED,
        changedFiles=["app.py"],
        baseCommit=base_commit,
        runPath=str(root),
    )
    await services.code_stage_runs.create(run)
    return project, run, root


@pytest.mark.asyncio
async def test_code_stage_requires_real_verification_review_and_human_approval(
    tmp_path,
):
    services, adapter = _services(tmp_path)
    project, run, _root = await _completed_run(services, tmp_path)

    with pytest.raises(ScopeViolation, match="verification"):
        await services.review_code_stage(project.id, run.id)

    verification, command_runs = await services.run_code_stage_verification(
        project.id, run.id
    )
    assert verification.status.value == "passed"
    assert command_runs
    assert all(item.exit_code == 0 for item in command_runs)
    assert all(isinstance(item.argv, list) for item in command_runs)

    review = await services.review_code_stage(project.id, run.id)
    assert review.approved is True
    assert review.provider_id == "review-provider"
    system_message = adapter.requests[0].messages[0]["content"]
    assert "final cumulative implementation" in system_message
    assert "never demand that a failure must exist" in system_message
    assert "exit=0" in adapter.requests[0].messages[1]["content"]

    approval = await services.approve_code_stage(
        project.id,
        run.id,
        CodeStageApprovalRequest(actorId="local-user", reason="Checked locally"),
    )
    assert approval.status.value == "approved"
    usage = services.control_plane.usage.for_run(run.id)
    assert [(item.input_tokens, item.output_tokens) for item in usage] == [(240, 60)]
    records = await services.approvals.list(run.id)
    assert records[-1].actor_type.value == "human"


@pytest.mark.asyncio
async def test_code_stage_api_exposes_lifecycle_and_stale_code_blocks_approval(
    tmp_path,
):
    services, _adapter = _services(tmp_path)
    project, run, root = await _completed_run(services, tmp_path)
    app = FastAPI()
    app.include_router(build_platform_router(services))

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        verified = await client.post(
            f"/api/v1/platform/projects/{project.id}/code-stage-runs/{run.id}/verify"
        )
        assert verified.status_code == 201
        assert verified.json()["data"]["verification"]["status"] == "passed"
        reviewed = await client.post(
            f"/api/v1/platform/projects/{project.id}/code-stage-runs/{run.id}/review"
        )
        assert reviewed.status_code == 201

        (root / "app.py").write_text("VALUE = 3\n", encoding="utf-8")
        blocked = await client.post(
            f"/api/v1/platform/projects/{project.id}/code-stage-runs/{run.id}/approve",
            json={"actorId": "local-user", "reason": "stale"},
        )
        assert blocked.status_code == 422
        assert "fresh successful verification" in blocked.json()["detail"]

        lifecycle = await client.get(
            f"/api/v1/platform/projects/{project.id}/code-stage-runs/{run.id}/lifecycle"
        )
        data = lifecycle.json()["data"]
        assert data["verification"]["status"] == "passed"
        assert data["review"]["status"] == "approved"
        assert data["approval"] is None


@pytest.mark.asyncio
async def test_human_can_auditably_bypass_change_request_before_final_approval(
    tmp_path,
):
    services, _adapter = _services(tmp_path, review_approved=False)
    project, run, _root = await _completed_run(services, tmp_path)
    app = FastAPI()
    app.include_router(build_platform_router(services))

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post(
            f"/api/v1/platform/projects/{project.id}/code-stage-runs/{run.id}/verify"
        )
        reviewed = await client.post(
            f"/api/v1/platform/projects/{project.id}/code-stage-runs/{run.id}/review"
        )
        review = reviewed.json()["data"]["review"]
        assert review["status"] == "changesRequested"
        assert review["requiredChanges"] == ["Add the missing edge-case test."]

        blocked = await client.post(
            f"/api/v1/platform/projects/{project.id}/code-stage-runs/{run.id}/approve",
            json={"actorId": "local-user", "reason": "before override"},
        )
        assert blocked.status_code == 422

        overridden = await client.post(
            f"/api/v1/platform/projects/{project.id}/code-stage-runs/{run.id}/review-override",
            json={
                "actorId": "human-reviewer",
                "reason": "This edge case is outside the accepted scope.",
            },
        )
        assert overridden.status_code == 201
        override_review = overridden.json()["data"]["review"]
        assert override_review["status"] == "changesRequested"
        assert override_review["humanOverride"] is True
        assert override_review["overrideActorId"] == "human-reviewer"

        approved = await client.post(
            f"/api/v1/platform/projects/{project.id}/code-stage-runs/{run.id}/approve",
            json={"actorId": "final-approver", "reason": "Accepted"},
        )
        assert approved.status_code == 201

    records = await services.approvals.list(run.id)
    assert [item.action.value for item in records] == [
        "acceptReviewRisk",
        "approveChanges",
    ]
    assert all(item.actor_type.value == "human" for item in records)


@pytest.mark.asyncio
async def test_review_revision_endpoint_builds_contract_from_stored_review(
    monkeypatch, tmp_path
):
    services, _adapter = _services(tmp_path, review_approved=False)
    project, run, _root = await _completed_run(services, tmp_path)
    source_root = tmp_path / "source-root"
    source_root.mkdir()
    await services.source_workspaces.create(
        SourceWorkspace(
            id=run.source_workspace_id,
            projectId=project.id,
            name="source",
            rootPath=str(source_root),
        )
    )
    executed = []

    async def fake_execute(run_id):
        executed.append(run_id)

    monkeypatch.setattr(services, "_execute_code_stage", fake_execute)
    await services.run_code_stage_verification(project.id, run.id)
    review = await services.review_code_stage(project.id, run.id)
    app = FastAPI()
    app.include_router(build_platform_router(services))

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            f"/api/v1/platform/projects/{project.id}/code-stage-runs/{run.id}/review-revision"
        )

    assert response.status_code == 202
    revision = response.json()["data"]["run"]
    stored = await services.code_stage_runs.get(revision["id"])
    marker, raw_contract = stored.instructions.split("\n", 1)
    contract = json.loads(raw_contract)
    await asyncio.sleep(0)
    assert marker == "[OXYGENT_REVIEW_REVISION_CONTRACT_V1]"
    assert stored.parent_run_id == run.id
    assert contract["reviewId"] == review.id
    assert contract["requiredChanges"] == [
        {
            "id": "REV-001",
            "instruction": "Add the missing edge-case test.",
            "mandatory": True,
        }
    ]
    assert executed == [stored.id]


@pytest.mark.asyncio
async def test_basically_qualified_review_can_proceed_or_start_optional_revision(
    monkeypatch, tmp_path
):
    services, _adapter = _services(tmp_path, review_verdict="basicallyQualified")
    project, run, _root = await _completed_run(services, tmp_path)
    source_root = tmp_path / "basic-source"
    source_root.mkdir()
    await services.source_workspaces.create(
        SourceWorkspace(
            id=run.source_workspace_id,
            projectId=project.id,
            name="source",
            rootPath=str(source_root),
        )
    )
    executed = []

    async def fake_execute(run_id):
        executed.append(run_id)

    monkeypatch.setattr(services, "_execute_code_stage", fake_execute)
    await services.run_code_stage_verification(project.id, run.id)
    review = await services.review_code_stage(project.id, run.id)

    assert review.status.value == "basicallyQualified"
    assert review.approved is False
    revision = await services.start_code_stage_review_revision(project.id, run.id)
    _, raw_contract = revision.instructions.split("\n", 1)
    contract = json.loads(raw_contract)
    await asyncio.sleep(0)
    assert contract["requiredChanges"][0]["instruction"] == "Add an edge-case test."
    assert executed == [revision.id]

    approval = await services.approve_code_stage(
        project.id,
        run.id,
        CodeStageApprovalRequest(actorId="human", reason="Core scope is complete"),
    )
    assert approval.status.value == "approved"


@pytest.mark.asyncio
async def test_failed_verification_exposes_real_error_output_for_aider_revision(
    tmp_path,
):
    services, _adapter = _services(tmp_path)
    project, run, root = await _completed_run(services, tmp_path)
    (root / "app.py").write_text("def broken(:\n", encoding="utf-8")

    verification, command_runs = await services.run_code_stage_verification(
        project.id, run.id
    )

    assert verification.status.value == "failed"
    failed = [item for item in command_runs if item.status.value == "failed"]
    assert failed
    assert failed[0].exit_code != 0
    output = failed[0].stdout_preview + failed[0].stderr_preview
    assert "SyntaxError" in output


@pytest.mark.asyncio
async def test_maven_dependency_transfer_failure_reuses_cache_and_retries_once(
    monkeypatch, tmp_path
):
    services, _adapter = _services(tmp_path)
    project, run, root = await _completed_run(services, tmp_path)
    (root / "pom.xml").write_text("<project/>", encoding="utf-8")
    monkeypatch.setattr(
        "oxygent.platform.services._verification_executable",
        lambda name: "/usr/bin/true" if name == "mvn" else None,
    )
    original_run = VerificationRunner.run
    maven_attempts = []
    maven_options = []

    async def run_with_transient_maven_failure(runner, **kwargs):
        command = kwargs["command"]
        if not command.id.startswith("maven-build-"):
            return await original_run(runner, **kwargs)
        maven_attempts.append(command.id)
        maven_options.append(runner.environment["MAVEN_OPTS"])
        failed = len(maven_attempts) == 1
        return (
            VerificationRun(
                projectId=kwargs["project_id"],
                taskId=kwargs["task_id"],
                profileId=kwargs["profile_id"],
                commandId=command.id,
                commandName=command.name,
                slot=command.slot,
                argv=command.argv,
                workingDirectory=command.working_directory,
                commandDefinitionHash="a" * 64,
                status=(
                    VerificationStatus.FAILED if failed else VerificationStatus.PASSED
                ),
                exitCode=1 if failed else 0,
                durationMs=1,
                stderrPreview=(
                    "Could not transfer artifact: Premature end of Content-Length"
                    if failed
                    else ""
                ),
                failureReason=("dependency transfer failed" if failed else None),
                failureCategory=(
                    VerificationFailureCategory.INFRASTRUCTURE if failed else None
                ),
                changedFiles=kwargs["diff"].changed_files,
                diffLineCount=kwargs["diff"].diff_line_count,
                contentHash="b" * 64,
            ),
            [],
        )

    monkeypatch.setattr(VerificationRunner, "run", run_with_transient_maven_failure)

    verification, command_runs = await services.run_code_stage_verification(
        project.id, run.id
    )

    maven_run = next(
        item for item in command_runs if item.command_id.startswith("maven-build-")
    )
    assert verification.status.value == "passed"
    assert len(maven_attempts) == 2
    assert maven_run.attempt_count == 2
    assert maven_run.automatic_retry_reason
    assert len(set(maven_options)) == 1
    assert ".verification-cache" in maven_options[0]
    assert Path(maven_options[0].split("=", 1)[1]).is_dir()


@pytest.mark.asyncio
async def test_dependency_manifest_requires_real_host_tooling(monkeypatch, tmp_path):
    services, _adapter = _services(tmp_path)
    project, run, root = await _completed_run(services, tmp_path)
    monkeypatch.setattr(
        "oxygent.platform.services._verification_executable", lambda _name: None
    )
    (root / "package.json").write_text(
        '{"name":"calculator","scripts":{"test":"vitest"}}\n', encoding="utf-8"
    )

    verification, command_runs = await services.run_code_stage_verification(
        project.id, run.id
    )

    assert verification.status.value == "failed"
    missing_tool = next(
        item for item in command_runs if item.command_id == "npm-missing-1"
    )
    assert missing_tool.exit_code == 2
    assert "未安装 Node.js/npm" in missing_tool.stderr_preview


@pytest.mark.asyncio
async def test_reviewer_receives_root_objective_and_cumulative_revision_diff(tmp_path):
    services, adapter = _services(tmp_path)
    project, parent, _parent_root = await _completed_run(services, tmp_path)
    child_root = tmp_path / "revision-result"
    child_root.mkdir()
    (child_root / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    _git(child_root, "init")
    _git(child_root, "config", "user.email", "test@example.invalid")
    _git(child_root, "config", "user.name", "Test")
    _git(child_root, "add", "app.py")
    _git(child_root, "commit", "-m", "revision baseline")
    base_commit = _git(child_root, "rev-parse", "HEAD")
    (child_root / "app.py").write_text("VALUE = 3\n", encoding="utf-8")
    child = CodeStageRun(
        projectId=project.id,
        sourceWorkspaceId=parent.source_workspace_id,
        parentRunId=parent.id,
        instructions="[OXYGENT_REVIEW_REVISION_CONTRACT_V1] fix VALUE",
        status=CodeStageStatus.COMPLETED,
        changedFiles=["app.py"],
        baseCommit=base_commit,
        runPath=str(child_root),
    )
    await services.code_stage_runs.create(child)
    await services.run_code_stage_verification(project.id, child.id)

    await services.review_code_stage(project.id, child.id)

    user_message = adapter.requests[-1].messages[1]["content"]
    assert "Root implementation objective:\nChange the value" in user_message
    assert "Implementation round 1/2" in user_message
    assert "Implementation round 2/2" in user_message
    assert "-VALUE = 1" in user_message
    assert "+VALUE = 3" in user_message


@pytest.mark.asyncio
async def test_aider_no_change_automatically_retries_with_safe_existing_project_format(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("CODE_STAGE_TEST_KEY", "test-only-key")
    services, _adapter = _services(tmp_path)
    services.aider_proxy_base_url = "http://127.0.0.1:18080/proxy"
    project = await services.create_project(ProjectCreate(name="Retry"))
    source = services.source_workspace_manager.create(
        project.id, "source", [("app.py", b"VALUE = 1\n")]
    )
    await services.source_workspaces.create(source)
    run = CodeStageRun(
        projectId=project.id,
        sourceWorkspaceId=source.id,
        instructions="Change VALUE to 2",
    )
    await services.code_stage_runs.create(run)
    commands = []

    async def fake_aider(command, *, cwd, environment):
        commands.append(command)
        assert environment["AIDER_OPENAI_API_KEY"] == "local-oxygent-proxy"
        if len(commands) == 2:
            (cwd / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
        return 0, b"completed", b""

    monkeypatch.setattr(services, "_run_aider_subprocess", fake_aider)

    await services._execute_code_stage(run.id)

    completed = await services.code_stage_runs.get(run.id)
    assert completed.status is CodeStageStatus.COMPLETED
    assert completed.changed_files == ["app.py"]
    assert len(commands) == 2
    assert commands[0][commands[0].index("--edit-format") + 1] == "diff"
    assert commands[1][commands[1].index("--edit-format") + 1] == "diff"


@pytest.mark.asyncio
async def test_aider_protocol_pollution_is_restored_and_retried_as_diff(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("CODE_STAGE_TEST_KEY", "test-only-key")
    services, _adapter = _services(tmp_path)
    services.aider_proxy_base_url = "http://127.0.0.1:18080/proxy"
    project = await services.create_project(ProjectCreate(name="Safe retry"))
    source = services.source_workspace_manager.create(
        project.id, "source", [("src/utils/daily.ts", b"export const value = 1\n")]
    )
    await services.source_workspaces.create(source)
    run = CodeStageRun(
        projectId=project.id,
        sourceWorkspaceId=source.id,
        instructions="Fix the Vite syntax error in src/utils/daily.ts",
    )
    await services.code_stage_runs.create(run)
    commands = []

    async def fake_aider(command, *, cwd, environment):
        commands.append(command)
        target = cwd / "src/utils/daily.ts"
        if len(commands) == 1:
            target.write_text(
                "````ts\n<<<<<<< SEARCH\nexport const value = 2\n>>>>>>> REPLACE\n",
                encoding="utf-8",
            )
        else:
            assert target.read_text(encoding="utf-8") == "export const value = 1\n"
            target.write_text("export const value = 2\n", encoding="utf-8")
        return 0, b"completed", b""

    monkeypatch.setattr(services, "_run_aider_subprocess", fake_aider)

    await services._execute_code_stage(run.id)

    completed = await services.code_stage_runs.get(run.id)
    assert completed.status is CodeStageStatus.COMPLETED
    assert completed.changed_files == ["src/utils/daily.ts"]
    assert len(commands) == 2
    assert commands[1][commands[1].index("--edit-format") + 1] == "diff"


@pytest.mark.asyncio
async def test_existing_project_planner_can_authorize_and_create_new_java_file(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("CODE_STAGE_TEST_KEY", "test-only-key")
    services, adapter = _services(tmp_path)
    services.aider_proxy_base_url = "http://127.0.0.1:18080/proxy"
    project = await services.create_project(ProjectCreate(name="Java extension"))
    source = services.source_workspace_manager.create(
        project.id,
        "javagpt",
        [("javagpt/backend/src/main/java/example/App.java", b"class App {}\n")],
    )
    await services.source_workspaces.create(source)
    run = CodeStageRun(
        projectId=project.id,
        sourceWorkspaceId=source.id,
        instructions="Add a persistent approval record entity",
    )
    await services.code_stage_runs.create(run)
    original_complete = adapter.complete
    new_path = "javagpt/backend/src/main/java/example/DailyApprovalRecord.java"

    async def complete_with_new_file_plan(request):
        if "Select every project file" in request.messages[0]["content"]:
            return ModelResponse(
                output=json.dumps(
                    {
                        "files": [
                            "javagpt/backend/src/main/java/example/App.java",
                            new_path,
                        ]
                    }
                ),
                inputTokens=80,
                outputTokens=20,
                latencyMs=2,
            )
        return await original_complete(request)

    async def fake_aider(command, *, cwd, environment):
        attached = [
            command[index + 1]
            for index, value in enumerate(command)
            if value == "--file"
        ]
        assert new_path in attached
        assert (cwd / new_path).is_file()
        (cwd / new_path).write_text("class DailyApprovalRecord {}\n", encoding="utf-8")
        return 0, b"implemented", b""

    monkeypatch.setattr(adapter, "complete", complete_with_new_file_plan)
    monkeypatch.setattr(services, "_run_aider_subprocess", fake_aider)

    await services._execute_code_stage(run.id)

    completed = await services.code_stage_runs.get(run.id)
    assert completed.status is CodeStageStatus.COMPLETED
    assert completed.changed_files == [new_path]
