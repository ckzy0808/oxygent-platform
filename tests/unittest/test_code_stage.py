import asyncio
import sys
import time
from pathlib import Path

import pytest

from oxygent.platform import (
    CodeStageRun,
    CodeStageRunRequest,
    CodeStageStatus,
    PlatformServices,
    ProjectCreate,
    SourceWorkspaceManager,
)
from oxygent.platform.code_stage import (
    build_aider_command,
    detect_aider_protocol_artifacts,
    deterministic_aider_files,
    extract_aider_suggested_paths,
    expand_aider_retry_files,
    parse_aider_file_plan,
    prepare_aider_editable_files,
    remove_empty_aider_placeholders,
    repository_file_list,
)
from oxygent.platform.coding import CodeWorkspaceError


def test_source_workspace_import_ignores_heavy_and_agent_internal_files(tmp_path):
    manager = SourceWorkspaceManager(tmp_path / "sources")
    source = manager.create(
        "project-1",
        "sample",
        [
            ("sample/app.py", b"print('ok')\n"),
            ("sample/node_modules/pkg/index.js", b"ignored"),
            ("sample/.conda-env/lib/python", b"ignored"),
            ("sample/.git/config", b"ignored"),
            ("sample/.aider.input.history", b"ignored"),
            ("sample/.env", b"SECRET=must-not-import"),
            ("sample/private.pem", b"must-not-import"),
            ("sample/.DS_Store", b"macOS metadata"),
        ],
    )

    assert source.file_count == 1
    assert source.selected_file_count == 8
    assert source.skipped_file_count == 7
    assert (Path(source.root_path) / "sample/app.py").is_file()
    assert not (Path(source.root_path) / "sample/.git").exists()
    assert not (Path(source.root_path) / "sample/.env").exists()


def test_source_analysis_context_uses_tree_and_bounded_text_excerpts(tmp_path):
    manager = SourceWorkspaceManager(tmp_path / "sources")
    source = manager.create(
        "project-1",
        "existing-project",
        [
            ("existing/README.md", b"# Existing App\nA useful service."),
            ("existing/pyproject.toml", b"[project]\nname='existing'\n"),
            ("existing/src/app.py", b"def run():\n    return 'ok'\n"),
            ("existing/logo.png", b"\x00binary"),
        ],
    )

    context = manager.build_analysis_context(source)

    assert "existing/README.md" in context
    assert "A useful service" in context
    assert "existing/src/app.py" in context
    assert "binary" not in context


def test_source_workspace_rejects_path_escape(tmp_path):
    manager = SourceWorkspaceManager(tmp_path / "sources")
    with pytest.raises(CodeWorkspaceError):
        manager.create("project-1", "bad", [("../secret.txt", b"no")])


def test_aider_file_plan_is_parsed_and_unsafe_paths_are_removed(tmp_path):
    output = """Plan:\n```json
    {"files":["index.html","src/app.js","../outside.py",".env",".gitignore",".oxygent/context.md"]}
    ```"""

    planned = parse_aider_file_plan(output)
    editable = prepare_aider_editable_files(tmp_path, planned, create_missing=True)

    assert editable == ["index.html", "src/app.js"]
    assert repository_file_list(tmp_path) == ["index.html", "src/app.js"]


def test_existing_project_file_plan_does_not_create_missing_files(tmp_path):
    (tmp_path / "app.py").write_text("print('ok')\n", encoding="utf-8")

    editable = prepare_aider_editable_files(
        tmp_path, ["app.py", "missing.py"], create_missing=False
    )

    assert editable == ["app.py"]
    assert not (tmp_path / "missing.py").exists()


def test_aider_suggested_paths_recovers_terminal_wrapped_java_names():
    output = """
    Please add or approve:
    - `javagpt/backend/src/main/java/example/DailyApprovalRecord
    .java`
    - `javagpt/backend/src/main/java/example/DailyApprovalRecordMapper.java`
    - `.git/config`
    """

    assert extract_aider_suggested_paths(output) == [
        "javagpt/backend/src/main/java/example/DailyApprovalRecord.java",
        "javagpt/backend/src/main/java/example/DailyApprovalRecordMapper.java",
    ]


def test_untouched_new_file_placeholders_are_removed(tmp_path):
    paths = ["src/New.java", "src/Implemented.java"]
    prepare_aider_editable_files(tmp_path, paths, create_missing=True)
    (tmp_path / "src/Implemented.java").write_text(
        "class Implemented {}\n", encoding="utf-8"
    )

    remove_empty_aider_placeholders(tmp_path, paths)

    assert not (tmp_path / "src/New.java").exists()
    assert (tmp_path / "src/Implemented.java").is_file()


def test_aider_protocol_artifacts_are_detected_in_source_files(tmp_path):
    polluted = tmp_path / "src/utils/daily.ts"
    polluted.parent.mkdir(parents=True)
    polluted.write_text(
        "javagpt/frontend/src/api/daily.ts\n````ts\n<<<<<<< SEARCH\n",
        encoding="utf-8",
    )
    documentation = tmp_path / "docs/example.md"
    documentation.parent.mkdir()
    documentation.write_text("<<<<<<< SEARCH\n", encoding="utf-8")

    assert detect_aider_protocol_artifacts(
        tmp_path, ["src/utils/daily.ts", "docs/example.md"]
    ) == ["src/utils/daily.ts"]


def test_aider_command_uses_diff_format_and_explicit_editable_files():
    command = build_aider_command(
        python_executable="/runtime/python",
        model_name="gpt-test",
        prompt="Implement it",
        editable_files=["src/app.py", "tests/test_app.py"],
    )

    assert command[:5] == [
        "/runtime/python",
        "-m",
        "aider",
        "--model",
        "openai/gpt-test",
    ]
    assert command[command.index("--edit-format") + 1] == "diff"
    assert "--no-check-model-accepts-settings" in command
    assert "--no-show-model-warnings" in command
    assert command[command.index("--timeout") + 1] == "300"
    assert command[command.index("--reasoning-effort") + 1] == "low"
    assert command.count("--file") == 2
    assert command[-4:] == [
        "--file",
        "src/app.py",
        "--file",
        "tests/test_app.py",
    ]


def test_aider_command_supports_whole_format_for_no_change_retry():
    command = build_aider_command(
        python_executable="/runtime/python",
        model_name="gpt-test",
        prompt="Retry with real edits",
        editable_files=["app.py"],
        edit_format="whole",
    )

    assert command[command.index("--edit-format") + 1] == "whole"


def test_aider_command_accepts_bounded_timeout_and_reasoning_override():
    command = build_aider_command(
        python_executable="/runtime/python",
        model_name="gpt-test",
        prompt="Implement",
        editable_files=["app.py"],
        api_timeout_seconds=900,
        reasoning_effort="medium",
    )

    assert command[command.index("--timeout") + 1] == "600"
    assert command[command.index("--reasoning-effort") + 1] == "medium"


def test_no_change_retry_prioritizes_parent_and_error_named_files(tmp_path):
    for relative in (
        "src/app.py",
        "src/other.py",
        "tests/test_app.py",
        "README.md",
        "requirements.txt",
    ):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("content\n", encoding="utf-8")

    expanded = expand_aider_retry_files(
        tmp_path,
        ["README.md"],
        preferred=["src/app.py"],
        request="pytest failed in tests/test_app.py",
        limit=4,
    )

    assert expanded == [
        "README.md",
        "src/app.py",
        "tests/test_app.py",
        "requirements.txt",
    ]


def test_deterministic_files_balance_backend_and_frontend_without_planner(tmp_path):
    (tmp_path / "backend").mkdir()
    (tmp_path / "frontend/src").mkdir(parents=True)
    (tmp_path / "backend/pom.xml").write_text("<project/>", encoding="utf-8")
    (tmp_path / "frontend/package.json").write_text("{}", encoding="utf-8")
    for index in range(35):
        (tmp_path / f"backend/Service{index}.java").write_text(
            "class Service {}", encoding="utf-8"
        )
    for index in range(12):
        (tmp_path / f"frontend/src/View{index}.vue").write_text(
            "<template />", encoding="utf-8"
        )
    (tmp_path / "frontend/package-lock.json").write_text("{}", encoding="utf-8")

    selected = deterministic_aider_files(tmp_path)

    assert len(selected) == 24
    assert "backend/pom.xml" in selected
    assert "frontend/package.json" in selected
    assert any(path.endswith(".java") for path in selected)
    assert any(path.endswith(".vue") for path in selected)
    assert "frontend/package-lock.json" not in selected


def test_project_aware_verification_builds_maven_and_npm_commands(
    monkeypatch, tmp_path
):
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend/pom.xml").write_text("<project/>", encoding="utf-8")
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend/package.json").write_text("{}", encoding="utf-8")
    (tmp_path / "frontend/package-lock.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "oxygent.platform.services._verification_executable",
        lambda executable: (
            f"/tools/{executable}" if executable in {"mvn", "npm"} else None
        ),
    )

    commands = PlatformServices._code_stage_verification_commands(tmp_path)
    by_id = {command.id: command for command in commands}

    assert by_id["maven-build-1"].argv == [
        "/tools/mvn",
        "--batch-mode",
        "--no-transfer-progress",
        "-Dmaven.wagon.http.retryHandler.count=3",
        "-q",
        "package",
    ]
    assert by_id["maven-build-1"].working_directory == "backend"
    assert by_id["npm-install-1"].argv[1:3] == ["ci", "--ignore-scripts"]
    assert by_id["npm-build-1"].argv == [
        "/tools/npm",
        "run",
        "build",
        "--if-present",
    ]
    assert by_id["npm-build-1"].working_directory == "frontend"


def test_project_aware_verification_fails_visibly_when_tooling_is_missing(
    monkeypatch, tmp_path
):
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend/pom.xml").write_text("<project/>", encoding="utf-8")
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend/package.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "oxygent.platform.services._verification_executable", lambda _name: None
    )

    commands = PlatformServices._code_stage_verification_commands(tmp_path)
    missing = [command for command in commands if "missing" in command.id]

    assert {command.id for command in missing} == {
        "maven-missing-1",
        "npm-missing-1",
    }
    assert all(command.argv[0] == sys.executable for command in missing)


@pytest.mark.asyncio
async def test_aider_timeout_stops_process_group(monkeypatch, tmp_path):
    services = PlatformServices(
        source_workspace_manager=SourceWorkspaceManager(tmp_path / "sources")
    )
    monkeypatch.setenv("OXYGENT_AIDER_TIMEOUT_SECONDS", "1")
    started = time.monotonic()

    with pytest.raises(CodeWorkspaceError, match="1-second execution limit"):
        await services._run_aider_subprocess(
            [sys.executable, "-c", "import time; time.sleep(20)"],
            cwd=tmp_path,
            environment={"PATH": ""},
        )

    assert time.monotonic() - started < 4


def test_code_result_archive_excludes_internal_git_and_aider_files(tmp_path):
    run_root = tmp_path / "run"
    (run_root / ".git").mkdir(parents=True)
    (run_root / ".git/config").write_text("internal", encoding="utf-8")
    (run_root / ".aider.tags.cache.v4").write_text("internal", encoding="utf-8")
    (run_root / ".oxygent").mkdir()
    (run_root / ".oxygent/WORKFLOW_CONTEXT.md").write_text("internal", encoding="utf-8")
    (run_root / "app.py").write_text("print('done')\n", encoding="utf-8")
    run = CodeStageRun(
        projectId="project-1",
        sourceWorkspaceId="source-1",
        instructions="Implement",
        status=CodeStageStatus.COMPLETED,
        changedFiles=["app.py"],
        runPath=str(run_root),
    )

    archive = SourceWorkspaceManager.build_archive(run, tmp_path / "archives")

    import zipfile

    with zipfile.ZipFile(archive) as result:
        assert result.namelist() == ["app.py"]


def test_revision_run_starts_from_previous_completed_code(tmp_path):
    manager = SourceWorkspaceManager(tmp_path / "sources")
    source = manager.create(
        "project-1", "original", [("app.py", b"print('original')\n")]
    )
    parent_root = tmp_path / "parent-result"
    parent_root.mkdir()
    (parent_root / "app.py").write_text("print('first revision')\n", encoding="utf-8")
    (parent_root / "new.py").write_text("ENABLED = True\n", encoding="utf-8")
    parent = CodeStageRun(
        id="parent-run",
        projectId="project-1",
        sourceWorkspaceId=source.id,
        instructions="First implementation",
        status=CodeStageStatus.COMPLETED,
        changedFiles=["app.py", "new.py"],
        runPath=str(parent_root),
    )

    revision_root = manager.prepare_run(source, "revision-run", parent_run=parent)

    assert (revision_root / "app.py").read_text(
        encoding="utf-8"
    ) == "print('first revision')\n"
    assert (revision_root / "new.py").read_text(encoding="utf-8") == "ENABLED = True\n"


@pytest.mark.asyncio
async def test_revision_request_records_parent_run(monkeypatch, tmp_path):
    manager = SourceWorkspaceManager(tmp_path / "sources")
    services = PlatformServices(source_workspace_manager=manager)
    project = await services.create_project(ProjectCreate(name="Revision"))
    source = manager.create(project.id, "source", [("app.py", b"VALUE = 1\n")])
    await services.source_workspaces.create(source)
    parent_root = tmp_path / "completed"
    parent_root.mkdir()
    (parent_root / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    parent = CodeStageRun(
        projectId=project.id,
        sourceWorkspaceId=source.id,
        workflowRunId="workflow-1",
        instructions="Initial implementation",
        status=CodeStageStatus.COMPLETED,
        changedFiles=["app.py"],
        runPath=str(parent_root),
    )
    await services.code_stage_runs.create(parent)
    executed = []

    async def fake_execute(run_id):
        executed.append(run_id)

    monkeypatch.setattr(services, "_execute_code_stage", fake_execute)
    revision = await services.start_code_stage(
        project.id,
        CodeStageRunRequest(
            sourceWorkspaceId=source.id,
            parentRunId=parent.id,
            instructions="Change the color",
        ),
    )
    await asyncio.sleep(0)

    assert revision.parent_run_id == parent.id
    assert revision.workflow_run_id == "workflow-1"
    assert executed == [revision.id]
