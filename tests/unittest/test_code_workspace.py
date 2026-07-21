"""Repository isolation, Scope Guard, and native read-engine tests."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from oxygent.platform import (
    ChangeContract,
    CodeTaskCreate,
    CodingOperation,
    PlatformServices,
    ProjectCreate,
    RepositoryRegistration,
    ScopeGuard,
    ScopeViolation,
)


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def source_repository(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "user.name", "OxyGent Test")
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("def greeting():\n    return 'hello'\n")
    (root / "README.md").write_text("# Test repository\n")
    (root / ".env").write_text("TEST_SECRET=must-not-be-returned\n")
    git(root, "add", "src/app.py", "README.md", ".env")
    git(root, "commit", "-m", "initial")
    return root


def contract(**updates) -> ChangeContract:
    values = {
        "objective": "Inspect an isolated repository",
        "acceptanceCriteria": ["Source remains unchanged"],
        "allowedPaths": ["**"],
        "forbiddenPaths": ["private/**"],
        "maxChangedFiles": 2,
        "maxDiffLines": 20,
    }
    values.update(updates)
    return ChangeContract(**values)


@pytest.mark.asyncio
async def test_code_task_creates_distinct_worktree_and_keeps_source_clean(
    source_repository: Path, tmp_path: Path
):
    services = PlatformServices.with_code_workspace(
        repository_roots={"approved-source": source_repository},
        workspace_root=tmp_path / "worktrees",
    )
    project = await services.create_project(ProjectCreate(name="Generic Project"))
    repository = await services.register_repository(
        project.id,
        RepositoryRegistration(
            name="Approved Repository",
            rootReference="approved-source",
            defaultBranch="main",
            allowedBaseBranches=["main"],
        ),
    )
    source_head = git(source_repository, "rev-parse", "HEAD")

    task = await services.create_code_task(
        project.id,
        CodeTaskCreate(repositoryId=repository.id, changeContract=contract()),
    )

    worktree = Path(task.worktree_path)
    assert worktree != source_repository
    assert worktree.is_dir()
    assert task.base_commit == source_head
    assert task.branch.startswith("codex/code-")
    assert git(source_repository, "status", "--short") == ""
    assert git(worktree, "rev-parse", "HEAD") == source_head


@pytest.mark.asyncio
async def test_native_engine_filters_sensitive_tree_and_reads_allowed_file(
    source_repository: Path, tmp_path: Path
):
    services = PlatformServices.with_code_workspace(
        repository_roots={"approved-source": source_repository},
        workspace_root=tmp_path / "worktrees",
    )
    project = await services.create_project(ProjectCreate(name="Read Project"))
    repository = await services.register_repository(
        project.id,
        RepositoryRegistration(
            name="Read Repository",
            rootReference="approved-source",
            defaultBranch="main",
            allowedBaseBranches=["main"],
        ),
    )
    task = await services.create_code_task(
        project.id,
        CodeTaskCreate(repositoryId=repository.id, changeContract=contract()),
    )

    tree = await services.execute_code_read(
        project.id, task.id, operation=CodingOperation.TREE
    )
    assert "src/app.py" in tree.data["files"]
    assert ".env" not in tree.data["files"]
    content = await services.execute_code_read(
        project.id,
        task.id,
        operation=CodingOperation.READ_FILE,
        path="src/app.py",
    )
    assert "def greeting" in content.data["content"]
    with pytest.raises(ScopeViolation, match="denied by the platform"):
        await services.execute_code_read(
            project.id,
            task.id,
            operation=CodingOperation.READ_FILE,
            path=".env",
        )


def test_scope_guard_enforces_paths_dependencies_and_limits():
    guarded = contract(
        allowedPaths=["src/**", "package.json"],
        forbiddenPaths=["src/generated/**"],
    )
    assert ScopeGuard.check_path(guarded, "src/app.py") == "src/app.py"
    with pytest.raises(ScopeViolation, match="outside allowed scope"):
        ScopeGuard.check_path(guarded, "tests/test_app.py")
    with pytest.raises(ScopeViolation, match="forbidden"):
        ScopeGuard.check_path(guarded, "src/generated/api.py")
    with pytest.raises(ScopeViolation, match="dependency changes"):
        ScopeGuard.check_path(guarded, "package.json")
    with pytest.raises(ScopeViolation, match="changed file limit"):
        ScopeGuard.check_diff(guarded, ["src/a.py", "src/b.py", "src/c.py"], 3)
    with pytest.raises(ScopeViolation, match="diff line limit"):
        ScopeGuard.check_diff(guarded, ["src/a.py"], 21)


@pytest.mark.parametrize(
    "field,value",
    [
        ("allowedPaths", ["../outside/**"]),
        ("allowedPaths", ["/absolute/**"]),
        ("defaultBranch", "bad branch"),
    ],
)
def test_contract_and_repository_reject_unsafe_paths_or_branches(field, value):
    if field == "defaultBranch":
        with pytest.raises(ValidationError, match="invalid Git branch"):
            RepositoryRegistration(
                name="Unsafe",
                rootReference="source",
                defaultBranch=value,
                allowedBaseBranches=["main"],
            )
    else:
        values = contract().model_dump(by_alias=True)
        values[field] = value
        with pytest.raises(ValidationError, match="repository-relative"):
            ChangeContract(**values)


@pytest.mark.asyncio
async def test_repository_source_must_come_from_server_allow_list(
    source_repository: Path, tmp_path: Path
):
    services = PlatformServices.with_code_workspace(
        repository_roots={"approved-source": source_repository},
        workspace_root=tmp_path / "worktrees",
    )
    project = await services.create_project(ProjectCreate(name="Allow-list Project"))
    with pytest.raises(KeyError, match="not allowed"):
        await services.register_repository(
            project.id,
            RepositoryRegistration(
                name="Arbitrary path",
                rootReference=str(tmp_path / "other"),
                defaultBranch="main",
                allowedBaseBranches=["main"],
            ),
        )
